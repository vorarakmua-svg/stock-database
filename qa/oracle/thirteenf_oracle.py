"""Independent check of the gurus' 13F holdings (build task M3-4) against the filings themselves.

See the "13F" section of docs/superpowers/specs/2026-10-22-sec-ownership-design.md. It reads SQLite and the
cached full-submission texts only, and never imports `src`: the code parses each filing's XML, this reads the same
text with plain patterns, so agreeing means something only because the two can't share a parser's bugs.

Gates, per stored filing (any that fails makes the exit code 1):
- The cached text exists (<cache>/13f/<accession>.txt), and its SEC header names the stored form, filing date
  and period of report.
- The units: thousands for a filing made before 2023-01-03 (the SEC's switch to dollars), dollars after, unless
  the median price its share rows imply (value / shares) falls outside $1 to $100,000 that way and inside it the
  other way (Baupost and Duquesne still state thousands). The stored multiplier is that one.
- The stored cover totals equal the cover's tableEntryTotal and tableValueTotal, in dollars by that multiplier.
- The stored rows: as many as the information table's infoTable entries, the same CUSIPs (as a multiset), and
  their dollar values summing to the table's value sum times the multiplier.
- checksum_ok is set exactly when the table's own count equals the cover's, and its value sum is within rounding
  of the cover's total: half a unit per entry and half for the total, in the units as filed.

Reported, not gated: each guru's filings, holdings and latest period; the filings whose checksum fails, with both
sides; the filings whose units the prices decide; and the range of the filings' median implied prices.

    python -I qa/oracle/thirteenf_oracle.py --db PATH --cache-dir DIR
"""
import argparse
import re
import sqlite3
import statistics
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

DOLLARS_FROM = "2023-01-03"
PRICE_LOW, PRICE_HIGH = 1.0, 100000.0
_TAG = r"<(?:[\w.-]+:)?{name}(?:\s[^>]*)?>\s*([^<]*?)\s*</(?:[\w.-]+:)?{name}>"
_ENTRY = re.compile(r"<(?:[\w.-]+:)?infoTable[\s>](.*?)</(?:[\w.-]+:)?infoTable>", re.S)


def expected_multiplier(filed, entries):
    """The units the filing must be read in: the date's, unless the median share price says otherwise."""
    by_date = 1000 if (filed or "") < DOLLARS_FROM else 1
    prices = []
    for block in entries:
        value, shares, kind = (tag_values(block, n) for n in ("value", "sshPrnamt", "sshPrnamtType"))
        if value and shares and kind and kind[0] == "SH" and Decimal(shares[0]) > 0:
            prices.append(float(Decimal(value[0]) / Decimal(shares[0])))
    if not prices:
        return by_date, None
    median = statistics.median(prices)
    other = 1 if by_date == 1000 else 1000
    if not PRICE_LOW <= median * by_date <= PRICE_HIGH and PRICE_LOW <= median * other <= PRICE_HIGH:
        return other, median * other
    return by_date, median * by_date


def tag_values(text, name):
    return re.findall(_TAG.format(name=name), text)


def documents(text):
    """{type: text} for each <DOCUMENT> of a full submission."""
    out = {}
    for block in text.split("<DOCUMENT>")[1:]:
        kind = re.search(r"<TYPE>([^\n<]+)", block)
        if kind:
            out.setdefault(kind.group(1).strip(), block)
    return out


def header_field(text, label):
    m = re.search(re.escape(label) + r":\s*(\S+)", text)
    return m.group(1) if m else None


def iso(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}" if yyyymmdd and len(yyyymmdd) == 8 else None


def check(db, cache):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    filings = conn.execute("SELECT * FROM guru_filings ORDER BY guru_cik, period, filed").fetchall()
    holdings = {}
    for row in conn.execute("SELECT accession, cusip, value_usd, shares, share_type FROM guru_holdings"):
        holdings.setdefault(row["accession"], []).append(row)
    names = {r["guru_cik"]: r["manager"] for r in conn.execute("SELECT guru_cik, manager FROM guru_filers")}
    failures, checksum_bad, rounded, inferred, medians = [], [], [], [], []
    per_guru = Counter()
    rows_per_guru = Counter()
    latest = {}
    for f in filings:
        acc = f["accession"]
        per_guru[f["guru_cik"]] += 1
        rows = holdings.get(acc, [])
        rows_per_guru[f["guru_cik"]] += len(rows)
        latest[f["guru_cik"]] = max(latest.get(f["guru_cik"], ""), f["period"] or "")
        path = cache / "13f" / f"{acc}.txt"
        if not path.exists():
            failures.append(f"{acc}: no cached text")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        filed = iso(header_field(text, "FILED AS OF DATE"))
        period = iso(header_field(text, "CONFORMED PERIOD OF REPORT"))
        form = header_field(text, "CONFORMED SUBMISSION TYPE")
        if (filed, period, form) != (f["filed"], f["period"], f["form"]):
            failures.append(f"{acc}: header {form} filed {filed} for {period}, stored {f['form']} filed {f['filed']} "
                            f"for {f['period']}")
        docs = documents(text)
        table = docs.get("INFORMATION TABLE", "")
        cover = next((body for kind, body in docs.items() if kind.startswith("13F-HR")), "")
        blocks = _ENTRY.findall(table)
        multiplier, median = expected_multiplier(filed, blocks)
        scale = Decimal(multiplier)
        if f["multiplier"] != multiplier:
            failures.append(f"{acc}: multiplier {f['multiplier']}, the prices say {multiplier}")
        if multiplier != (1000 if (filed or "") < DOLLARS_FROM else 1):
            inferred.append(f"{acc} ({names.get(f['guru_cik'], f['guru_cik'])}, {f['period']}, filed {filed}): "
                            f"{'thousands' if multiplier == 1000 else 'dollars'}, median price {median:.2f}")
        if median is not None:
            medians.append(median)
        entry_total = [int(v) for v in tag_values(cover, "tableEntryTotal") if v.isdigit()]
        value_total = [Decimal(v) for v in tag_values(cover, "tableValueTotal") if v]
        values = [Decimal(v) for v in tag_values(table, "value") if v]
        cusips = [c.upper() for c in tag_values(table, "cusip")]
        entries = len(blocks)
        if entry_total[:1] != [f["entry_total"]]:
            failures.append(f"{acc}: cover entries {entry_total[:1]}, stored {f['entry_total']}")
        if not value_total or Decimal(str(f["value_total"])) != value_total[0] * scale:
            failures.append(f"{acc}: cover value {value_total[:1]} x{scale}, stored {f['value_total']}")
        if entries != len(rows) or f["rows"] != len(rows):
            failures.append(f"{acc}: {entries} infoTable entries, {len(rows)} rows stored (rows column {f['rows']})")
        if Counter(cusips) != Counter((r["cusip"] or "").upper() for r in rows):
            failures.append(f"{acc}: the stored CUSIPs differ from the table's")
        if sum(values) * scale != sum(Decimal(str(r["value_usd"])) for r in rows if r["value_usd"] is not None):
            failures.append(f"{acc}: values sum {sum(values) * scale}, stored rows sum differs")
        # Each entry's value is rounded to the unit, and so is the total: half a unit apiece.
        ok = bool(entry_total and value_total and entries == entry_total[0]
                  and abs(sum(values) - value_total[0]) <= (Decimal(entry_total[0]) + 1) / 2)
        if bool(f["checksum_ok"]) != ok:
            failures.append(f"{acc}: checksum_ok {f['checksum_ok']}, the table says {ok}")
        if not ok:
            checksum_bad.append(f"{acc} ({names.get(f['guru_cik'], f['guru_cik'])}, {f['period']}): cover "
                                f"{entry_total[:1]} entries / {value_total[:1]}, table {entries} / {sum(values)}")
        elif value_total and sum(values) != value_total[0]:
            rounded.append(f"{acc}: {sum(values) - value_total[0]:+} on {entries} entries")
    print(f"filings {len(filings)}, holdings {sum(rows_per_guru.values())}, gurus {len(per_guru)}")
    for guru in sorted(per_guru, key=lambda g: names.get(g, g)):
        print(f"  {names.get(guru, guru)[:32]:32} {per_guru[guru]:3} filings {rows_per_guru[guru]:6} rows, "
              f"latest period {latest[guru]}")
    print(f"checksum failures (reported): {len(checksum_bad)}")
    for line in checksum_bad:
        print("  " + line)
    print(f"totals off by rounding only (reported): {len(rounded)}")
    for line in rounded:
        print("  " + line)
    print(f"units the prices decide (reported): {len(inferred)}")
    for line in inferred:
        print("  " + line)
    if medians:
        print(f"median implied prices: ${min(medians):,.2f} to ${max(medians):,.2f} over {len(medians)} filings")
    print(f"gate failures: {len(failures)}")
    for line in failures:
        print("  " + line)
    print("all gates pass" if not failures else "GATES FAIL")
    return 0 if not failures else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args()
    return check(args.db, args.cache_dir)


if __name__ == "__main__":
    sys.exit(main())

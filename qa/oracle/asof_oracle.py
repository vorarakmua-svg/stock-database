"""Any-day oracle for the as-of view (docs/superpowers/specs/2026-11-18-asof-snapshots-design.md, Verification 2).

The as-of view of a ticker on day D must be exactly the annual record our code builds from the facts filed on or
before D. For N random (ticker, D) pairs, D drawn uniformly over the ticker's filing span (any day, not only a filing
day), this compares what the stored view reads (``AsOfReader.history_as_of``, so every year's ``as_of_annual``) with
a fresh ``build_annual_record(facts_known_by(facts, D))``:

- the same fiscal years;
- every canonical figure (NaN and None are one: SQLite stores NaN as NULL), ``period_end``, ``calendar_year``;
- the provenance: ``accn``/``filed_date``/``form`` (the latest filing among the year's figures), ``carried_from``,
  and the stored row's ``source_tags`` and ``source_filings``;
- no stored figure's filing is dated after D (look-ahead).

The facts are the collection's: ``SECHandler.fetch_all`` from the read-only SEC cache (predecessor facts and the
filings' own XBRL merged as a run merges them), with the network blocked and no cache write. This oracle uses the
code under test on purpose: the as-of view's one rule *is* the annual record, so the check is that the stored view
is that rule replayed with the look-ahead gate, at any day.

Usage: python qa/oracle/asof_oracle.py DB CACHE_DIR --sample N --seed S [--workers W] [--tickers T ...]
       [--at TICKER:YYYY-MM-DD ...]   (pinned pairs, checked beside the sample: a bug's repro dates)
Exit code 1 on any difference (each one listed), 0 when all N agree.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import socket
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.exporters.sqlite_store import _CANONICAL_COLUMNS, read_only_uri  # noqa: E402
from src.fetchers.sec_handler import SECHandler  # noqa: E402
from src.parsers.annual_record import build_annual_record  # noqa: E402
from src.parsers.known_facts import facts_known_by, filing_dates  # noqa: E402
from src.parsers.xbrl_parser import XBRLParser  # noqa: E402
from src.query.asof import AsOfReader  # noqa: E402

Pick = Tuple[int, str, Union[float, str]]  # (index, ticker, where in its filing span 0 <= u < 1, or a pinned day)


def _block_network() -> None:
    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise OSError("network blocked (asof_oracle reads the SEC cache only)")

    import requests

    socket.socket.connect = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    requests.Session.request = blocked  # type: ignore[assignment]


def _handler(cache_dir: Path) -> SECHandler:
    # Quiet: a filing's own XBRL the cache lacks goes unread here as in an offline collection, and says so each time.
    quiet = logging.getLogger("asof_oracle.sec")
    quiet.setLevel(logging.CRITICAL)
    handler = SECHandler(cache_dir=cache_dir, cache_ttl_seconds=10 ** 12, max_retries=0, logger=quiet)
    handler._write_payload_cache = lambda path, data: None  # type: ignore[assignment]  # the cache is read-only
    return handler


def _none(value: Any) -> Any:
    """A figure as SQLite stores it: NaN is NULL."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _equal(a: Any, b: Any) -> bool:
    a, b = _none(a), _none(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return bool(a == b)


def _expected(period: Dict[str, Any]) -> Dict[str, Any]:
    """The as-of period the reader must give for the record's *period*."""
    filings = {k: v for k, v in (period.get("_source_filings") or {}).items() if v}
    latest: Dict[str, Any] = max(filings.values(), key=lambda f: (f.get("filed") or "", f.get("accn") or ""),
                                 default={})
    out: Dict[str, Any] = {key: _none(period.get(key)) for key in _CANONICAL_COLUMNS}
    out.update({"accn": latest.get("accn"), "filed_date": latest.get("filed"), "form": latest.get("form"),
                "period_end": period.get("period_end"), "calendar_year": period.get("calendar_year")})
    out["carried_from"] = {
        key: {"accn": f.get("accn"), "filed_date": f.get("filed"), "form": f.get("form")}
        for key, f in sorted(filings.items())
        if key in out and out[key] is not None and f.get("accn") != latest.get("accn")}
    return out


def _day_in_span(facts: Dict[str, Any], u: float) -> Optional[str]:
    days = filing_dates(facts)
    if not days:
        return None
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    return (first + timedelta(days=int(u * ((last - first).days + 1)))).isoformat()


def _check(ticker: str, day: str, facts: Dict[str, Any], reader: AsOfReader,
           parser: XBRLParser) -> List[str]:
    """Every difference between the stored view of *ticker* on *day* and the record built on the facts known then."""
    diffs: List[str] = []
    record = build_annual_record(parser, facts_known_by(facts, day), ticker=ticker, years_back=None).periods
    stored = reader.history_as_of(ticker, day)
    want_years = {int(fy) for fy in record}
    if set(stored) != want_years:
        diffs.append(f"years: stored {sorted(set(stored) - want_years)} not in the record, "
                     f"record {sorted(want_years - set(stored))} not stored")
    for fy in sorted(want_years & set(stored)):
        want, got = _expected(record[str(fy)]), stored[fy]
        for key, value in want.items():
            if key == "carried_from":
                if got.get(key) != value:
                    diffs.append(f"FY{fy} carried_from: stored {got.get(key)} record {value}")
            elif not _equal(got.get(key), value):
                diffs.append(f"FY{fy} {key}: stored {got.get(key)!r} record {value!r}")
        row = reader.conn.execute(
            "SELECT source_tags, source_filings FROM financials_annual_known "
            "WHERE ticker = ? AND fiscal_year = ? AND known_from <= ? ORDER BY known_from DESC LIMIT 1",
            (ticker, fy, day)).fetchone()
        tags = json.loads(row["source_tags"]) if row["source_tags"] else {}
        filings = json.loads(row["source_filings"]) if row["source_filings"] else {}
        if tags != (record[str(fy)].get("_source_tags") or {}):
            diffs.append(f"FY{fy} source_tags differ")
        if filings != (record[str(fy)].get("_source_filings") or {}):
            diffs.append(f"FY{fy} source_filings differ")
        late = sorted(f"{k} {f.get('filed')}" for k, f in filings.items() if f and (f.get("filed") or "") > day)
        if late:
            diffs.append(f"FY{fy} look-ahead: figures filed after {day}: {', '.join(late)}")
    return diffs


def _run(args: Tuple[str, str, str, List[Pick]]) -> List[Tuple[int, str, str, List[str], bool]]:
    """One ticker's picks: ``(index, ticker, day, differences, day is a filing day)`` each."""
    db, cache_dir, ticker, picks = args
    _block_network()
    data = _handler(Path(cache_dir)).fetch_all(ticker)
    facts = data.get("company_facts")
    if not facts:
        why = data.get("error") or data.get("company_facts_error")
        return [(i, ticker, "", [f"no companyfacts in the cache ({why})"], False) for i, _, _ in picks]
    days = set(filing_dates(facts))
    parser = XBRLParser()
    out = []
    with AsOfReader(db) as reader:
        for i, _, u in picks:
            day = u if isinstance(u, str) else _day_in_span(facts, u)
            if day is None:
                out.append((i, ticker, "", ["no dated fact"], False))
                continue
            out.append((i, ticker, day, _check(ticker, day, facts, reader, parser), day in days))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db")
    ap.add_argument("cache_dir")
    ap.add_argument("--sample", type=int, default=200, help="number of (ticker, day) pairs")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1, help="processes (one ticker's facts each at a time)")
    ap.add_argument("--tickers", nargs="*", help="draw from these tickers only (default: every ticker with snapshots)")
    ap.add_argument("--at", nargs="*", default=[], help="pinned (ticker, day) pairs as TICKER:YYYY-MM-DD")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(read_only_uri(a.db), uri=True)
    built = sorted(t for (t,) in conn.execute("SELECT DISTINCT ticker FROM financials_annual_known"))
    conn.close()
    pool = sorted(set(a.tickers) & set(built)) if a.tickers else built
    if not pool:
        print("no ticker with snapshots to sample")
        return 1
    rng = random.Random(a.seed)
    picks: List[Pick] = [(i, rng.choice(pool), rng.random()) for i in range(a.sample)]
    for pin in a.at:
        ticker, _, day = pin.rpartition(":")
        date.fromisoformat(day)
        picks.append((len(picks), ticker, day))
    by_ticker: Dict[str, List[Pick]] = {}
    for pick in picks:
        by_ticker.setdefault(pick[1], []).append(pick)
    jobs = [(a.db, a.cache_dir, t, by_ticker[t]) for t in sorted(by_ticker)]
    if a.workers > 1:
        from multiprocessing import Pool
        with Pool(a.workers) as p:
            results = [r for chunk in p.imap_unordered(_run, jobs) for r in chunk]
    else:
        results = [r for job in jobs for r in _run(job)]
    results.sort()
    failed = [r for r in results if r[3]]
    on_filing_day = sum(1 for r in results if r[4])
    print(f"asof_oracle: {len(results)} (ticker, day) pairs over {len(by_ticker)} tickers (seed {a.seed}); "
          f"{on_filing_day} on a filing day, {len(results) - on_filing_day} between; "
          f"{len(failed)} with differences")
    for i, ticker, day, diffs, _ in failed:
        print(f"  #{i} {ticker} as of {day}:")
        for d in diffs[:20]:
            print(f"    {d}")
        if len(diffs) > 20:
            print(f"    ... {len(diffs) - 20} more")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

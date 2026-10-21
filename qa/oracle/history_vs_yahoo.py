"""Independent check of the valuation history (build task M1-4) against Yahoo's own figures.

See the "Real-data validation" section of docs/superpowers/specs/2026-10-21-valuation-history-design.md.
It reads SQLite and the JSON exports only, and never imports `src`: agreeing with the code under test means
something only if the check can't share its bugs.

Gates; any that fails makes the exit code 1:
- Current P/E against Yahoo's trailing P/E: at least 75% within 5%, and 88% within 10%.
- Current P/S and P/B against Yahoo's: at least 80% within 5%, and 90% within 10%.
- Current EV/EBITDA against Yahoo's (in the JSON exports): at least 55% within 10%, and 75% within 20%.
- Our latest share count against Yahoo's (market cap / price): at least 90% within 5%.
- Each fiscal year's trailing twelve months at its year end against metrics_annual (EBITDA, FCF, net
  debt): at least 95% within 1%.
- Market cap continuous at each declared share split's seam in the filed quarters, where the history's
  trailing quarter crosses it: within 10% of the price's move.
- No monthly market-cap move within 3% of the ratio of a declared split near it: a seam left unfolded.

Reported, not gated:
- The misses beyond 20% (35% for EV/EBITDA).
- The monthly market-cap moves above 12% that the price doesn't explain, each traced to a price factor
  or a filed share-count change.
- The declared share splits whose seam the filed quarters don't show.
- The restatement look-ahead: annual figures as first filed against the latest.
- With --reference, whether the valuations and summaries equal another copy's.

The current multiples are compared at Yahoo's price: ours are on the latest bar, a day before the quote.

Usage: python qa/oracle/history_vs_yahoo.py --db PATH [--json-dir DIR] [--reference PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: Later filings restate the quarters before a split for three years, so a split's seam sits in the filed
#: quarters up to three years and a filing's lag before it; a year more of slack.
REACH_DAYS = 4 * 365
#: The filed jump at a split's seam is its ratio, give or take a quarter's issuance and buybacks.
SEAM_MATCH = 0.15
#: A market-cap move within this of a nearby declared split's ratio is that split's seam left unfolded.
UNFOLDED_MATCH = 0.03
COUNT = "COALESCE(weighted_avg_shares_basic, shares_outstanding, weighted_avg_shares_diluted)"


def share_split(ratio: float) -> bool:
    """A share split's ratio is a simple fraction (2:1, 3:2, 1:8); spin-off price factors aren't."""
    if not ratio or ratio <= 0 or abs(ratio - 1) < 1e-9:
        return False
    return any(abs(float(Fraction(r).limit_denominator(4)) - r) <= 1e-9 * r for r in (ratio, 1 / ratio))


def split_runs(events: List[Tuple[str, float]]) -> List[Tuple[str, str, float]]:
    """Every run of consecutive declared share splits: (first date, last date, the product of the ratios)."""
    s = sorted((d, r) for d, r in events if share_split(r))
    runs = []
    for i in range(len(s)):
        product = 1.0
        for j in range(i, len(s)):
            product *= s[j][1]
            runs.append((s[i][0], s[j][0], product))
    return runs


def runs_near(runs: List[Tuple[str, str, float]], older: str, newer: str) -> List[Tuple[str, str, float]]:
    """The runs a seam between the quarters ending *older* and *newer* can come from: starting after the older
    quarter and ending no more than REACH_DAYS after the newer one."""
    reach = (date.fromisoformat(newer[:10]) + timedelta(days=REACH_DAYS)).isoformat()
    return [run for run in runs if run[0] > older[:10] and run[1] <= reach]


def within(a: float, b: float, tol: float) -> bool:
    return abs(math.log(a / b)) <= math.log(1 + tol)


def share(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def gate(name: str, ok: bool, detail: str, failures: List[str]) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        failures.append(name)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", required=True)
    p.add_argument("--json-dir", default="data/output/json")
    p.add_argument("--reference", default=None, help="another copy whose valuations should be unchanged")
    args = p.parse_args(argv)
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    failures: List[str] = []

    snaps: Dict[str, Tuple] = {}
    for t, at, price, cap, pe, ps, pb in db.execute(
            "SELECT m.ticker, m.collected_at, m.current_price, m.market_cap, m.pe_trailing, m.price_to_sales, "
            "m.price_to_book FROM market_snapshots m JOIN (SELECT ticker, MAX(collected_at) c FROM "
            "market_snapshots GROUP BY ticker) x ON x.ticker = m.ticker AND x.c = m.collected_at"):
        snaps[t] = (at, price, cap, pe, ps, pb)
    splits: Dict[str, List[Tuple[str, float]]] = {}
    for t, d, r in db.execute("SELECT ticker, substr(date, 1, 10), ratio FROM split_events WHERE ratio > 0"):
        splits.setdefault(t, []).append((d, r))
    latest: Dict[str, Tuple[str, float, float, Optional[float]]] = {}
    for t, d, close, cap, ev in db.execute(
            "SELECT h.ticker, h.date, h.close, h.market_cap, h.enterprise_value FROM valuation_history h JOIN "
            "(SELECT ticker, MAX(date) d FROM valuation_history GROUP BY ticker) x ON x.ticker = h.ticker AND "
            "x.d = h.date"):
        latest[t] = (d, close, cap, ev)
    ours = {(t, m): v for t, m, v in db.execute(
        "SELECT ticker, metric, current_value FROM valuation_ranges WHERE current_value IS NOT NULL")}

    def bars_to_price(t: str) -> float:
        at, last = snaps[t][0][:10], latest[t][0]
        f = 1.0
        for d, r in splits.get(t, []):
            if at < d <= last:
                f *= r
            elif last < d <= at:
                f /= r
        return f

    # 1. Current multiples against Yahoo's, at Yahoo's price.
    print("== current multiples against Yahoo's, at its price")
    yahoo_ev: Dict[str, float] = {}
    for f in sorted(Path(args.json_dir).glob("*.json")):
        try:
            v = (json.loads(f.read_text(encoding="utf-8")).get("valuation") or {}).get("ev_to_ebitda")
        except (ValueError, OSError):
            continue
        if isinstance(v, (int, float)) and v > 0:
            yahoo_ev[f.stem] = float(v)
    misses: List[str] = []
    for metric, col, tol, lo, hi, miss in (("pe", 3, (0.05, 0.10), 0.75, 0.88, 0.20),
                                           ("ps", 4, (0.05, 0.10), 0.80, 0.90, 0.20),
                                           ("pb", 5, (0.05, 0.10), 0.80, 0.90, 0.20),
                                           ("ev_ebitda", None, (0.10, 0.20), 0.55, 0.75, 0.35)):
        pairs = []
        for t, s in snaps.items():
            if t not in latest or (t, metric) not in ours or not s[1] or not latest[t][1]:
                continue
            k = s[1] / (latest[t][1] * bars_to_price(t))  # Yahoo's price over our latest close
            value = ours[(t, metric)]
            if metric == "ev_ebitda":
                d, close, cap, ev = latest[t]
                if cap is None or ev is None:
                    continue
                value = value * (cap * k + (ev - cap)) / ev
                theirs = yahoo_ev.get(t)
            else:
                value *= k
                theirs = s[col]
            if theirs and theirs > 0 and value and value > 0:
                pairs.append((t, value, float(theirs)))
        n = len(pairs)
        a = sum(within(v, y, tol[0]) for _, v, y in pairs)
        b = sum(within(v, y, tol[1]) for _, v, y in pairs)
        gate(f"{metric} against Yahoo", n > 0 and a >= lo * n and b >= hi * n,
             f"within {tol[0]:.0%} {share(a, n)} (gate {lo:.0%}), within {tol[1]:.0%} {share(b, n)} (gate {hi:.0%})",
             failures)
        misses += [f"{t}/{metric} ours {v:.2f} Yahoo {y:.2f}" for t, v, y in pairs if not within(v, y, miss)]
    print(f"misses beyond 20% (35% for EV/EBITDA): {len(misses)}")
    for m in misses:
        print("   ", m)

    # 2. Share counts.
    pairs = []
    for t, s in snaps.items():
        if t in latest and latest[t][2] and s[1] and s[2]:
            ours_count = latest[t][2] / latest[t][1]
            yahoo_count = s[2] / s[1] * bars_to_price(t)
            pairs.append((t, ours_count, yahoo_count))
    a = sum(within(o, y, 0.05) for _, o, y in pairs)
    gate("share counts against Yahoo's", a >= 0.90 * len(pairs), f"within 5% {share(a, len(pairs))} (gate 90%)",
         failures)
    for t, o, y in sorted(pairs, key=lambda x: -abs(math.log(x[1] / x[2])))[:8]:
        print(f"    widest: {t} ours {o / 1e6:,.0f}M Yahoo {y / 1e6:,.0f}M ({o / y:.3f})")

    # 3. Trailing twelve months at each fiscal year end against the annual metrics.
    rows = db.execute(
        "SELECT t.ticker, a.fiscal_year, t.operating_income, t.depreciation_amortization, t.operating_cash_flow, "
        "t.capex, t.long_term_debt, t.short_term_debt, t.commercial_paper, t.cash_and_equivalents, "
        "t.short_term_investments, m.ebitda, m.free_cash_flow, m.net_debt FROM financials_ttm t "
        "JOIN financials_annual a ON a.ticker = t.ticker AND a.period_end = t.period_end "
        "JOIN metrics_annual m ON m.ticker = a.ticker AND m.fiscal_year = a.fiscal_year").fetchall()
    checked = agreed = 0
    off: List[str] = []
    for t, fy, oi, da, ocf, capex, ltd, std, cp, cash, sti, m_ebitda, m_fcf, m_nd in rows:
        mine = {"ebitda": oi + da if oi is not None and da is not None else None,
                "fcf": ocf - abs(capex) if ocf is not None and capex is not None else None,
                "net_debt": (sum(v for v in (ltd, std, cp) if v is not None) - (cash or 0) - (sti or 0)
                             if any(v is not None for v in (ltd, std, cp)) else None)}
        for key, annual in (("ebitda", m_ebitda), ("fcf", m_fcf), ("net_debt", m_nd)):
            if mine[key] is None or annual is None:
                continue
            checked += 1
            if abs(mine[key] - annual) <= 0.01 * max(abs(annual), abs(mine[key]), 1.0):
                agreed += 1
            else:
                off.append(f"{t} FY{fy} {key} {mine[key] / 1e6:,.0f}M vs {annual / 1e6:,.0f}M")
    gate("trailing years at fiscal year ends against metrics_annual", checked > 0 and agreed >= 0.95 * checked,
         f"within 1% {share(agreed, checked)} (gate 95%)", failures)
    for o in off[:12]:
        print("   ", o)

    # 4. Market cap at each declared share split's seam (BUG-177). Filings restate the quarters before a split,
    # so it shows in the filed counts as a jump of about its ratio between two quarters up to three years before
    # it, not on its date. Where the history's trailing quarter crosses that jump, market cap must move with the
    # price; a seam left unfolded moves by the split's ratio.
    print("== market cap at each declared share split's seam in the filed quarters")
    ok = total = 0
    declared: List[Tuple[str, str, float]] = []
    seen, early = set(), set()
    for t in sorted(splits):
        hist = db.execute("SELECT date, close, market_cap, ttm_period_end FROM valuation_history WHERE ticker = ? "
                          "AND market_cap IS NOT NULL AND ttm_period_end IS NOT NULL ORDER BY date",
                          (t,)).fetchall()
        filed = [(str(pe)[:10], c) for pe, c in db.execute(
            f"SELECT period_end, {COUNT} FROM financials_quarterly WHERE ticker = ? AND {COUNT} > 0 "
            "ORDER BY period_end", (t,))]
        if not hist or not filed:
            continue
        own = [(d, r) for d, r in splits[t] if share_split(r) and d > filed[0][0]]
        declared += [(t, d, r) for d, r in own]
        runs = split_runs(own)
        for (a, ca), (b, cb) in zip(filed, filed[1:]):
            match = [run for run in runs_near(runs, a, b) if within(cb / ca, run[2], SEAM_MATCH)]
            if not match:
                continue
            first, last, ratio = min(match, key=lambda run: abs(math.log(cb / ca / run[2])))
            before = [h for h in hist if str(h[3])[:10] < b]
            after = [h for h in hist if str(h[3])[:10] >= b]
            if not before or not after:  # the history doesn't cross this seam
                early.update((t, d) for d, r in own if first <= d <= last)
                continue
            x, y = before[-1], after[0]
            factors = [r for d, r in splits[t] if x[0] < d <= y[0] and not share_split(r)]
            move = (y[2] / x[2]) / (y[1] / x[1]) * math.prod(factors)
            total += 1
            good = within(move, 1.0, 0.10)
            ok += good
            seen.update((t, d) for d, r in own if first <= d <= last)
            print(f"    {t} x{ratio:g} ({first}{'' if first == last else '..' + last}): filed "
                  f"{ca / 1e6:,.0f}M -> {cb / 1e6:,.0f}M ({a} -> {b}); market cap / price {x[0]} -> {y[0]}: "
                  f"x{move:.3f}{'' if good else '  UNFOLDED'}")
    gate("market cap at each declared share split's seam", total > 0 and ok == total,
         f"continuous within 10% {share(ok, total)}", failures)
    outside = [f"{t} {d} x{r:g}" for t, d, r in declared if (t, d) in early and (t, d) not in seen]
    unseen = [f"{t} {d} x{r:g}" for t, d, r in declared if (t, d) not in early | seen]
    print(f"declared share splits after the first filed quarter: {len(declared)}; their seams the history "
          f"crosses: {len(declared) - len(outside) - len(unseen)}; seams before the history: "
          f"{', '.join(outside) or 'none'}; no seam in the filed quarters (restated throughout, or after the "
          f"latest quarter): {', '.join(unseen) or 'none'}")

    # 5. Monthly market-cap moves the price doesn't explain.
    print("== monthly market-cap moves above 12% that the price doesn't explain")
    jumps = traced = unfolded = 0
    lines = []
    for t in sorted({k for k, _ in ours} | set(latest)):
        hist = db.execute("SELECT date, close, market_cap, ttm_period_end FROM valuation_history WHERE ticker = ? "
                          "AND market_cap IS NOT NULL ORDER BY date", (t,)).fetchall()
        for prev, cur in zip(hist, hist[1:]):
            move = (cur[2] / prev[2]) / (cur[1] / prev[1])
            if abs(math.log(move)) <= math.log(1.12):
                continue
            jumps += 1
            factors = [r for d, r in splits.get(t, []) if prev[0] < d <= cur[0] and not share_split(r)]
            fac = math.prod(factors) if factors else 1.0
            why = None
            seam = [run for run in runs_near(split_runs(splits.get(t, [])), str(prev[3]), str(cur[3]))
                    if within(move * fac, run[2], UNFOLDED_MATCH)] if prev[3] and cur[3] else []
            if seam:
                unfolded += 1
                lines.append(f"    {t} {prev[0]} -> {cur[0]}: x{move:.3f}: UNFOLDED SEAM of the declared "
                             f"x{seam[0][2]:g} ({seam[0][0]}), between {prev[3]} and {cur[3]}")
                continue
            if factors and within(move * fac, 1.0, 0.05):
                why = f"price factor {fac:.3f} (a spin-off)"
            elif prev[3] != cur[3]:
                q = {pe: c for pe, c in db.execute(
                    "SELECT period_end, COALESCE(weighted_avg_shares_basic, shares_outstanding, "
                    "weighted_avg_shares_diluted) FROM financials_quarterly WHERE ticker = ? AND period_end IN "
                    "(?, ?)", (t, prev[3], cur[3]))}
                a, b = q.get(prev[3]), q.get(cur[3])
                if a and b and within(b / a * (1 / fac if factors else 1.0), move, 0.05):
                    why = f"filed share count {a / 1e6:,.0f}M -> {b / 1e6:,.0f}M ({prev[3]} -> {cur[3]})"
            traced += why is not None
            lines.append(f"    {t} {prev[0]} -> {cur[0]}: x{move:.3f}: {why or 'UNEXPLAINED'}")
    print(f"moves {jumps}, traced {traced}, unfolded seams {unfolded}")
    for line in lines:
        print(line)
    gate("no market-cap move at a nearby declared split's ratio", unfolded == 0, f"{unfolded} moves", failures)

    # 6. The restatement look-ahead: annual figures as first filed against the latest.
    print("== restatement look-ahead (annual figures, first filed vs latest)")
    first = db.execute(
        "SELECT v.ticker, v.fiscal_year, v.revenue, v.net_income FROM financials_annual_vintages v JOIN "
        "(SELECT ticker, fiscal_year, MIN(filed_date) f FROM financials_annual_vintages GROUP BY ticker, "
        "fiscal_year) x ON x.ticker = v.ticker AND x.fiscal_year = v.fiscal_year AND x.f = v.filed_date").fetchall()
    now = {(t, fy): (r, ni) for t, fy, r, ni in db.execute(
        "SELECT ticker, fiscal_year, revenue, net_income FROM financials_annual")}
    for i, name in ((0, "revenue"), (1, "net income")):
        gaps = []
        for t, fy, *as_filed in first:
            latest_value = now.get((t, fy), (None, None))[i]
            if latest_value and as_filed[i] is not None:
                gaps.append(abs(latest_value - as_filed[i]) / abs(latest_value))
        if gaps:
            over1, over5 = sum(g > 0.01 for g in gaps), sum(g > 0.05 for g in gaps)
            print(f"    {name}: {len(gaps)} years; restated by more than 1% {share(over1, len(gaps))}, "
                  f"by more than 5% {share(over5, len(gaps))}; median {statistics.median(gaps):.4%}")

    # 7. The valuations and summaries, against another copy.
    if args.reference:
        ref = sqlite3.connect(f"file:{args.reference}?mode=ro", uri=True)
        for q in ("SELECT ticker, model, applicable, na_reason, value_bear, value_base, value_bull, assumptions, "
                  "basis_fiscal_year FROM valuations ORDER BY 1, 2",
                  "SELECT ticker, n_applicable, median_bear, median_base, median_bull FROM valuation_summary "
                  "ORDER BY 1"):
            same = db.execute(q).fetchall() == ref.execute(q).fetchall()
            gate(f"unchanged against the reference ({q.split('FROM ')[1].split()[0]})", same,
                 "identical" if same else "different", failures)

    print("FAILED gates: " + ", ".join(failures) if failures else "all gates pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

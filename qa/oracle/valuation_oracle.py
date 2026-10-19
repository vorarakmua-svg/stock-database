"""Independent valuation oracle for the QA loop (qa/LOOP_PROMPT.md §7).

Re-implements the five valuation models from the specs
(docs/superpowers/specs/2026-07-13-valuation-layer-design.md) and checks the stored
`valuations` rows. It reads SQLite only and must never import `src` — agreeing with the
code under test is only meaningful if the oracle can't share its bugs.

Level 1 ("formula"): recompute value_bear/base/bull from the inputs each row recorded in its
`assumptions` JSON. Where the spec is ambiguous, every reading is tried and the one that
matches is reported, so an ambiguity shows up as a note rather than a false alarm.

Level 2 ("inputs"): re-derive the inputs from the raw tables per the spec (≤10-FY CAGR windows,
median 3-yr FCF, CAPM discount, TTM dividends, reported diluted EPS on today's split basis via
declared split ratios, FY-end prices) and value each model with them. Rows whose spec inputs are
undefined are listed, never skipped silently. Known limitation: per-share history around reverse
splits, spin-offs and stock dividends (GE, WDC, SCCO) is not modelled, so those need adjudication.

Usage: python qa/oracle/valuation_oracle.py --db PATH [TICKERS...] [--tol 0.005]
Exit code 1 if any applicable value disagrees with every reading beyond the tolerance.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

ERP = 0.045
TERMINAL = 0.025
DISCOUNT_CLAMP = (0.08, 0.14)
SPREAD_G, SPREAD_R = 0.03, 0.01

Triple = Tuple[Optional[float], Optional[float], Optional[float]]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- model formulas (spec)
def dcf_per_share(fcf: float, g: float, r: float, shares: float, terminal: float = TERMINAL) -> float:
    """Years 1–5 at g, years 6–10 linear fade to terminal, Gordon terminal value at year 10."""
    pv, cash = 0.0, fcf
    for t in range(1, 11):
        gt = g if t <= 5 else g + (terminal - g) * (t - 5) / 5
        cash *= 1 + gt
        pv += cash / (1 + r) ** t
    pv += cash * (1 + terminal) / (r - terminal) / (1 + r) ** 10
    return pv / shares


def ddm_per_share(d0: float, g: float, r: float, terminal: float = TERMINAL) -> float:
    """5 years at g, then a Gordon terminal value at 2.5%."""
    pv, d = 0.0, d0
    for t in range(1, 6):
        d *= 1 + g
        pv += d / (1 + r) ** t
    return pv + d * (1 + terminal) / (r - terminal) / (1 + r) ** 5


def scenarios(g: float, r: float, cap: float, reclamp_r: bool) -> List[Tuple[float, float]]:
    """(growth, discount) for bear, base, bull: growth ±3pp (floor 0, cap), discount ±1pp."""
    rb, ru = r + SPREAD_R, r - SPREAD_R
    if reclamp_r:
        rb, ru = clamp(rb, *DISCOUNT_CLAMP), clamp(ru, *DISCOUNT_CLAMP)
    return [(max(g - SPREAD_G, 0.0), rb), (g, r), (min(g + SPREAD_G, cap), ru)]


def level1_candidates(model: str, a: Dict) -> Dict[str, Triple]:
    """Every spec reading of a model's bear/base/bull, computed from its stored assumptions."""
    out: Dict[str, Triple] = {}
    if model in ("dcf", "ddm"):
        g, r, cap = a["growth_base"], a["discount_base"], a["growth_cap"]
        for reclamp in (False, True):
            trio = scenarios(g, r, cap, reclamp)
            if model == "dcf":
                vals = [dcf_per_share(a["fcf_basis"], gi, ri, a["shares_outstanding"], a["terminal_growth"])
                        for gi, ri in trio]
            else:
                vals = [ddm_per_share(a["ttm_dps"], gi, ri, a["terminal_growth"]) for gi, ri in trio]
            out["scenario discount re-clamped" if reclamp else "scenario discount unclamped"] = tuple(vals)
    elif model == "graham":
        m, b = a["multiplier"], a["bvps"]
        out["spec"] = tuple(math.sqrt(m * a[k] * b) if a[k] > 0 and b > 0 else None
                            for k in ("eps_bear", "eps_base", "eps_bull"))
    elif model == "lynch":
        g, cap, lo, hi, eps = a["growth_base"], a["growth_cap"], a["fair_pe_floor"], a["fair_pe_cap"], a["eps_base"]
        trio = [max(g - SPREAD_G, 0.0), g, min(g + SPREAD_G, cap)]
        out["spec"] = tuple(clamp(gi * 100, lo, hi) * eps for gi in trio)
    elif model == "multiples":
        basis = a["latest_basis"]
        out["spec"] = (a["band_low"] * basis, a["band_median"] * basis, a["band_high"] * basis)
    return out


def rel(a: Optional[float], b: Optional[float]) -> float:
    if a is None or b is None:
        return 0.0 if a is None and b is None else math.inf
    return abs(a - b) / max(abs(b), 1e-12)


def check_level1(db: sqlite3.Connection, tickers: List[str], tol: float) -> int:
    where = f"and ticker in ({','.join('?' * len(tickers))})" if tickers else ""
    rows = db.execute("select ticker, model, value_bear, value_base, value_bull, assumptions from valuations "
                      f"where applicable = 1 and model != 'owner_earnings' {where} order by ticker, model",
                      tickers).fetchall()
    bad, readings = 0, {}
    for ticker, model, *stored, assumptions in rows:
        cands = level1_candidates(model, json.loads(assumptions))
        best = min(cands.items(), key=lambda kv: max(rel(o, s) for o, s in zip(kv[1], stored)))
        worst = max(rel(o, s) for o, s in zip(best[1], stored))
        readings.setdefault(model, {}).setdefault(best[0], 0)
        readings[model][best[0]] += 1
        if worst > tol:
            bad += 1
            print(f"MISMATCH {ticker:6} {model:9} stored={[round(v, 4) if v else v for v in stored]} "
                  f"oracle({best[0]})={[round(v, 4) if v else v for v in best[1]]} worst diff {worst:.2%}")
    print(f"level 1: {len(rows)} applicable rows checked, {bad} beyond {tol:.1%}")
    for model, counts in sorted(readings.items()):
        print(f"  {model:9} matched by: {counts}")
    return bad


# ---------------------------------------------------------------- Level 2: inputs from raw tables
# Level 2 re-derives each model's inputs from the raw tables, per the spec, then values the model
# with them. Per-share history uses the *declared* split ratios in split_events: a fiscal year's
# share count is moved to today's basis only if it is still on a pre-split basis (it may already
# be restated, depending on which filing the value came from), judged by which candidate is
# closer to the latest share count in log space.


def cagr(series: List[float]) -> Optional[float]:
    """Compound growth from the first to the last value (None if undefined)."""
    if len(series) < 2 or series[0] <= 0 or series[-1] <= 0:
        return None
    return (series[-1] / series[0]) ** (1 / (len(series) - 1)) - 1


def median(xs: List[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


class Inputs:
    """Raw-table lookups for one ticker."""

    def __init__(self, db: sqlite3.Connection, ticker: str):
        self.db, self.t = db, ticker
        snap = db.execute("select beta, risk_free_rate, shares_outstanding, current_price from market_snapshots "
                          "where ticker=? order by collected_at desc limit 1", (ticker,)).fetchone() or (None,) * 4
        self.beta, self.rf, self.snap_shares, self.price = snap
        row = db.execute("select earnings_growth from analyst_snapshots where ticker=? order by collected_at desc "
                         "limit 1", (ticker,)).fetchone()
        self.analyst = row[0] if row else None
        self.splits = db.execute("select date, ratio from split_events where ticker=? order by date",
                                 (ticker,)).fetchall()
        self.annual = db.execute("select fiscal_year, period_end, net_income, total_equity, shares_outstanding, "
                                 "weighted_avg_shares_diluted, eps_diluted from financials_annual where ticker=? "
                                 "order by fiscal_year", (ticker,)).fetchall()

    def discount(self) -> Tuple[float, float]:
        raw = self.rf + (self.beta if self.beta is not None else 1.0) * ERP
        return raw, clamp(raw, *DISCOUNT_CLAMP)

    def growth(self, hist: Optional[float], cap: float) -> Optional[float]:
        if hist is None:
            # Spec gap (Q5): with no measurable CAGR the spec doesn't say what growth to use, so the row is
            # not derivable from it. (The code follows the plan's analyst-only fallback until the user decides.)
            return None
        raw = min(hist, self.analyst) if self.analyst is not None else hist
        return clamp(raw, 0.0, cap)

    def to_current_basis(self, shares: Optional[float], period_end: str) -> Optional[float]:
        """Shares in today's split basis (declared splits after period_end, only if not yet restated)."""
        if not shares:
            return None
        factor = 1.0
        for date, ratio in self.splits:
            # Share splits are at least 3-for-2 (or 1-for-2 reversed). Smaller ratios in Yahoo's split
            # history are spin-off price factors or stock dividends (HON 0.9535, SPGI 1.057; lead L12),
            # which don't change the share count.
            if date > period_end and ratio and (ratio >= 1.5 or ratio <= 1 / 1.5):
                factor *= ratio
        # Today's basis is today's share count (the latest market snapshot: the basis the price is quoted on).
        # The latest FY's count is still pre-split when a split came after its filing (KLAC, BKNG; BUG-014),
        # so it is only the fallback when there is no snapshot.
        latest = self.snap_shares or next((r[5] for r in reversed(self.annual) if r[5]), None)
        if factor == 1.0 or not latest:
            return shares
        return min((shares, shares * factor), key=lambda s: abs(math.log(s / latest)))

    def eps_series(self) -> List[Tuple[int, float]]:
        """Reported diluted EPS (it nets out preferred dividends) moved to today's split basis;
        net income / diluted shares only when EPS wasn't reported."""
        out = []
        for fy, pe, ni, _eq, _so, wd, eps in self.annual:
            # No share count reported (e.g. GOOGL before FY2022): judge the basis from implied shares.
            basis_shares = wd or (ni / eps if ni and eps else None)
            sh = self.to_current_basis(basis_shares, pe)
            if eps is not None:
                out.append((fy, eps * (basis_shares / sh) if basis_shares and sh else eps))
            elif ni is not None and sh:
                out.append((fy, ni / sh))
        return out

    def bvps(self, fy_row: Tuple, which: str) -> Optional[float]:
        fy, pe, _ni, eq, so, wd, _eps = fy_row
        sh = self.to_current_basis(wd if which == "diluted" else so, pe)
        return eq / sh if eq is not None and sh else None

    def fy_end_price(self, period_end: str) -> Optional[float]:
        row = self.db.execute("select close from price_bars where ticker=? and date<=? order by date desc limit 1",
                              (self.t, period_end[:10])).fetchone()
        return row[0] if row else None


def level2(db: sqlite3.Connection, ticker: str, model: str, stored: Dict) -> Optional[Dict]:
    """Oracle inputs and bear/base/bull for one applicable model, or None if not derivable."""
    x = Inputs(db, ticker)
    if model in ("dcf", "ddm", "lynch") and x.rf is None:
        return None
    if model == "dcf":
        rows = db.execute("select coalesce(levered_fcf, free_cash_flow) from metrics_annual where ticker=? "
                          "and coalesce(levered_fcf, free_cash_flow) is not null order by fiscal_year",
                          (ticker,)).fetchall()
        fcf = [r[0] for r in rows][-10:]
        if len(fcf) < 4:
            return None
        g = x.growth(cagr(fcf), 0.15)
        _, r = x.discount()
        if g is None or not x.snap_shares:
            return {"inputs": {"fcf_basis": median(fcf[-3:]), "hist_cagr": None}}
        vals = [dcf_per_share(median(fcf[-3:]), gi, ri, x.snap_shares) for gi, ri in scenarios(g, r, 0.15, False)]
        return {"inputs": {"fcf_basis": median(fcf[-3:]), "hist_cagr": cagr(fcf), "cagr_years": len(fcf) - 1,
                           "growth_base": g, "discount_base": r, "shares_outstanding": x.snap_shares}, "values": vals}
    if model == "ddm":
        anchor = stored.get("ttm_anchor")
        if not anchor:
            return None
        ttm = db.execute("select coalesce(sum(amount),0) from dividend_events where ticker=? "
                         "and date>date(?,'-365 days') and date<=?", (ticker, anchor, anchor)).fetchone()[0]
        years = db.execute("select substr(date,1,4) y, sum(amount), count(*) from dividend_events where ticker=? "
                           "and substr(date,1,4) < substr(?,1,4) group by y order by y", (ticker, anchor)).fetchall()
        # The spec's "<= 10 yrs" is a calendar window (complete years before the anchor year), and the
        # exponent is the calendar span, so a payment gap can't stretch or shrink the growth rate.
        window = [(int(y), v, n) for y, v, n in years if int(y) >= int(anchor[:4]) - 10]
        # Growth compares full years (BUG-009): a first year with fewer payments than the window's usual
        # count is partial (dividend started or restarted mid-year, or an ex-date slipped a year back).
        if len(window) >= 2 and window[0][2] < median([n for _, _, n in window]):
            window = window[1:]
        # The spec's historical CAGR "requires >= 4 FYs" (BUG-010): fewer full years measure nothing.
        span = window[-1][0] - window[0][0] if len(window) >= 4 else None
        hist = (window[-1][1] / window[0][1]) ** (1 / span) - 1 if span and window[0][1] > 0 else None
        g = x.growth(hist, 0.10)
        _, r = x.discount()
        if g is None or not ttm:
            return {"inputs": {"ttm_dps": ttm, "hist_cagr": hist, "cagr_years": span}}
        vals = [ddm_per_share(ttm, gi, ri) for gi, ri in scenarios(g, r, 0.10, False)]
        return {"inputs": {"ttm_dps": ttm, "hist_cagr": hist, "cagr_years": span,
                           "growth_base": g, "discount_base": r}, "values": vals}
    eps = x.eps_series()
    if model == "graham":
        last3 = [e for _fy, e in eps[-3:]]
        if not last3 or not x.annual:
            return None
        out = {}
        for which in ("diluted", "period_end"):
            b = x.bvps(x.annual[-1], which)
            trio = (min(last3), last3[-1], max(last3))
            out[which] = [math.sqrt(22.5 * e * b) if e > 0 and b and b > 0 else None for e in trio]
        return {"inputs": {"eps_bear": min(last3), "eps_base": last3[-1], "eps_bull": max(last3),
                           "bvps_diluted": x.bvps(x.annual[-1], "diluted"),
                           "bvps_period_end": x.bvps(x.annual[-1], "period_end")},
                "values": out["diluted"], "alt_values": out["period_end"]}
    if model == "lynch":
        window = [e for _fy, e in eps][-10:]
        if len(window) < 4 or window[-1] <= 0:
            return None
        if cagr(window) is None:  # Lynch publishes nothing without a measurable growth rate
            return {"inputs": {"hist_cagr": None}}
        g = x.growth(cagr(window), 0.15)
        trio = [max(g - SPREAD_G, 0.0), g, min(g + SPREAD_G, 0.15)]
        return {"inputs": {"eps_base": window[-1], "hist_cagr": cagr(window), "cagr_years": len(window) - 1,
                           "growth_base": g}, "values": [clamp(gi * 100, 5, 25) * window[-1] for gi in trio]}
    if model == "multiples":
        kind = stored.get("multiple_kind")
        basis_by_fy: Dict[int, Optional[float]] = {}
        if kind == "pe":
            basis_by_fy = dict(eps)
        elif kind == "pb":
            basis_by_fy = {row[0]: x.bvps(row, "diluted") for row in x.annual}
        elif kind == "pffo":
            for fy, v in db.execute("select fiscal_year, ffo_per_share from metrics_annual where ticker=?", (ticker,)):
                basis_by_fy[fy] = v
        mults = []
        for row in x.annual[-5:]:
            fy, pe = row[0], row[1]
            b, price = basis_by_fy.get(fy), x.fy_end_price(pe) if pe else None
            if b and b > 0 and price:
                mults.append(price / b)
        latest = basis_by_fy.get(x.annual[-1][0]) if x.annual else None
        if len(mults) < 3 or not latest or latest <= 0:
            return None
        band = (min(mults), median(mults), max(mults))
        return {"inputs": {"band_low": band[0], "band_median": band[1], "band_high": band[2], "latest_basis": latest,
                           "n_years": len(mults)}, "values": [m * latest for m in band]}
    return None


def check_level2(db: sqlite3.Connection, tickers: List[str], tol: float) -> int:
    where = f"and ticker in ({','.join('?' * len(tickers))})" if tickers else ""
    rows = db.execute("select ticker, model, value_bear, value_base, value_bull, assumptions from valuations "
                      f"where applicable = 1 and model != 'owner_earnings' {where} order by model, ticker",
                      tickers).fetchall()
    bad, checked, skipped = 0, 0, []
    for ticker, model, *stored_vals, assumptions in rows:
        stored = json.loads(assumptions)
        o = level2(db, ticker, model, stored)
        if not o or "values" not in o:
            # The spec-derived inputs are undefined (e.g. no valid CAGR) yet the code produced a value:
            # never silently pass these; list them.
            skipped.append(f"{ticker}/{model}")
            continue
        checked += 1
        worst = max(rel(ov, sv) for ov, sv in zip(o["values"], stored_vals))
        alt = o.get("alt_values")
        alt_note = ""
        if alt:
            alt_worst = max(rel(ov, sv) for ov, sv in zip(alt, stored_vals))
            alt_note = f" | alt (period-end shares) diff {alt_worst:.2%}"
        if worst > tol:
            bad += 1
            diffs = {k: (round(v, 6) if isinstance(v, float) else v, stored.get(k))
                     for k, v in o["inputs"].items() if k in stored and rel(v, stored.get(k)) > tol}
            fmt = lambda v: "None" if v is None else f"{v:.4g}"  # noqa: E731
            print(f"L2 MISMATCH {ticker:6} {model:9} base stored={fmt(stored_vals[1])} oracle={fmt(o['values'][1])} "
                  f"worst {worst:.1%}{alt_note}; inputs (oracle, stored) differing: {diffs}")
    print(f"level 2: {checked} applicable rows re-derived from raw tables, {bad} beyond {tol:.1%}")
    if skipped:
        print(f"  not derivable from the spec ({len(skipped)}; the code still valued them): {' '.join(skipped)}")
    return bad + len(skipped)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", required=True)
    p.add_argument("--tol", type=float, default=0.005)
    p.add_argument("--level", choices=("1", "2", "both"), default="both")
    p.add_argument("tickers", nargs="*")
    args = p.parse_args(argv)
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    tickers = [t.upper() for t in args.tickers]
    bad = 0
    if args.level in ("1", "both"):
        bad += check_level1(db, tickers, args.tol)
    if args.level in ("2", "both"):
        bad += check_level2(db, tickers, args.tol)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

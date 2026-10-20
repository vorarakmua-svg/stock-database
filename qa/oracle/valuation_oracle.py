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
import datetime
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


def cagr(window: List[Tuple[int, float]]) -> Optional[float]:
    """Compound growth from the first to the last (fiscal year, value) over their fiscal-year span (None if
    undefined), so a year without a value neither stretches nor shrinks the rate."""
    if len(window) < 2 or window[0][1] <= 0 or window[-1][1] <= 0:
        return None
    return (window[-1][1] / window[0][1]) ** (1 / (window[-1][0] - window[0][0])) - 1


def simple_ratio(ratio: float) -> bool:
    """A share split's ratio is a simple fraction (2:1, 3:2, 9:4, 1:8, 10:1); Yahoo's spin-off and merger price
    factors aren't. On the real-data copy all 430 simple factors of 3:2 or more are splits."""
    return any(abs(r * d - round(r * d)) < 1e-9 * max(1.0, r * d) for r in (ratio, 1 / ratio) for d in (1, 2, 3, 4))


def median(xs: List[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def ads_ratio(ratio: float) -> float:
    """The ADS ratio a filed-to-Yahoo share-count ratio shows: a whole number n >= 2 of shares per ADS (or of ADSs
    per share) within 5%, else 1."""
    for candidate, per_ads in ((ratio, True), (1 / ratio, False)):
        n = round(candidate)
        if n >= 2 and abs(candidate / n - 1) <= 0.05:
            return float(n) if per_ads else 1 / n
    return 1.0


class Inputs:
    """Raw-table lookups for one ticker."""

    def __init__(self, db: sqlite3.Connection, ticker: str):
        self.db, self.t = db, ticker
        snap = db.execute("select beta, risk_free_rate, shares_outstanding, current_price, market_cap, collected_at "
                          "from market_snapshots where ticker=? order by collected_at desc limit 1",
                          (ticker,)).fetchone() or (None,) * 6
        self.beta, self.rf, self.snap_shares, self.price, market_cap, collected_at = snap
        row = db.execute("select earnings_growth from analyst_snapshots where ticker=? order by collected_at desc "
                         "limit 1", (ticker,)).fetchone()
        self.analyst = row[0] if row else None
        self.splits = db.execute("select date, ratio from split_events where ticker=? order by date",
                                 (ticker,)).fetchall()
        self.annual = db.execute("select fiscal_year, period_end, net_income, total_equity, shares_outstanding, "
                                 "weighted_avg_shares_diluted, eps_diluted from financials_annual where ticker=? "
                                 "order by fiscal_year", (ticker,)).fetchall()
        row = db.execute("select filed_date, form from financials_annual where ticker=? and fiscal_year is not null "
                         "order by fiscal_year desc limit 1", (ticker,)).fetchone()
        self.last_filed = row[0] if row and row[0] else None
        self.last_form = row[1] if row and row[1] else ""
        # Per-share values are compared with the latest price (VAL-07's upside), so they sit on that price's
        # basis (VAL-03 "same share basis", VAL-09 "per-share consistency" across a split). A split dated after
        # both the latest filing and the price's day (a quote failed since; BUG-048) is in neither: no year moves
        # by it.
        # The filing each year's figures were last taken from, per field (the vintages, every filing's view of each
        # year): a share split restates the figures filed after it and no other (VAL-09's per-share consistency).
        self.filed_by_field: Dict[Tuple[int, str], str] = {}
        try:
            for fy, *dates in db.execute(
                    "select fiscal_year, max(case when weighted_avg_shares_diluted is not null then filed_date end), "
                    "max(case when eps_diluted is not null then filed_date end), "
                    "max(case when shares_outstanding is not null then filed_date end) "
                    "from financials_annual_vintages where ticker=? group by fiscal_year", (ticker,)):
                for field, filed in zip(("weighted_avg_shares_diluted", "eps_diluted", "shares_outstanding"), dates):
                    if filed:
                        self.filed_by_field[(fy, field)] = filed
        except sqlite3.OperationalError:  # a database from before vintages were kept
            pass
        self.priced_on = str(collected_at)[:10] if self.price is not None and collected_at else None
        # Yahoo adjusts its dividends and price bars for every split it knew when fetched; one made after the
        # price's day is taken back out. A share split the latest filing restated after that day (BUG-060) is in
        # the filed figures but not in the price: years come to today's basis and go back by it.
        self.unpriced, self.since_price = 1.0, 1.0
        for date, ratio in self.splits:
            if ratio and self.priced_on and self.last_filed and date[:10] > self.priced_on:
                self.unpriced *= ratio
                if date[:10] <= self.last_filed[:10] and (ratio >= 1.5 or ratio <= 1 / 1.5) and simple_ratio(ratio):
                    self.since_price *= ratio
        # Per-share values divide by every share class (BUG-022). Yahoo's share count can cover one class of
        # several (GOOGL's class A: 5.87B of 12.23B), while market cap / price counts them all; and a count still
        # under 3/4 of the latest FY's diluted shares on today's basis is incomplete too (HON, whose market cap is
        # halved as well). A gap under 5% can be Yahoo's two figures disagreeing (MRVL, one class, 2.5%), so the
        # one nearer the latest FY's count wins (BUG-035); every wider gap in the data is a class or units
        # Yahoo's count leaves out (META 15%, BX 65%). The FY count is the diluted one, else the period-end one.
        latest_fy = next(((r[5] or r[4], r[1], self.filed_of(r[0], "weighted_avg_shares_diluted" if r[5]
                                                             else "shares_outstanding"))
                          for r in reversed(self.annual) if r[5] or r[4]), None)
        self.ads_ratio = 1.0
        fy_today = self.to_current_basis(*latest_fy) if self.snap_shares and latest_fy else None
        # A foreign issuer's filings count ordinary shares, while the price is per ADS and Yahoo counts
        # ADS-equivalents (TSM: 25.93B ordinary, 5.186B ADS; BUG-065), so per-share values sit on the ADS basis
        # (VAL-03's same share basis): a filed count within 5% of a whole multiple of Yahoo's gives the ratio.
        if self.last_form[:4] in ("20-F", "40-F") and self.snap_shares and fy_today:
            self.ads_ratio = ads_ratio(fy_today / self.snap_shares)
            fy_today /= self.ads_ratio
        by_value = market_cap / self.price if market_cap and self.price else None
        if self.snap_shares and by_value and by_value > 1.02 * self.snap_shares and not (
                fy_today and by_value < 1.05 * self.snap_shares
                and abs(math.log(self.snap_shares / fy_today)) < abs(math.log(by_value / fy_today))):
            self.snap_shares = by_value
        if self.snap_shares and fy_today and self.snap_shares < 0.75 * fy_today:
            self.snap_shares = fy_today

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

    def after_price(self, date: str) -> bool:
        """A split dated after both the latest filing and the latest price's day (BUG-048)."""
        return bool(self.priced_on and self.last_filed and date[:10] > max(self.priced_on, self.last_filed[:10]))

    def filed_of(self, fy: int, field: str) -> Optional[str]:
        """When the year's figure for *field* was last filed, if the vintages say."""
        return self.filed_by_field.get((fy, field))

    def to_current_basis(self, shares: Optional[float], period_end: str,
                         filed: Optional[str] = None) -> Optional[float]:
        """Shares in today's split basis (declared splits after period_end, only if not yet restated).
        *filed* is when the figure was last filed, if known."""
        if not shares:
            return None
        factor, unrestated = 1.0, 1.0
        for date, ratio in self.splits:
            if self.after_price(date):
                continue
            # Share splits are at least 3-for-2 (or 1-for-2 reversed). Smaller ratios in Yahoo's split
            # history are spin-off price factors or stock dividends (HON 0.9535, SPGI 1.057; lead L12),
            # which don't change the share count.
            if date > period_end and ratio and (ratio >= 1.5 or ratio <= 1 / 1.5):
                if filed and simple_ratio(ratio):
                    # A filing restates its figures for every share split made before it, so the figure's own
                    # filing says whether this split is in it. Today's count can't: a stock-paid merger or an IPO
                    # moves it too (BUG-059).
                    if date[:10] > filed[:10]:
                        unrestated *= ratio
                    continue
                # A simple ratio dated after the latest filing is a share split no filing has restated yet
                # (KLAC's 10:1): it applies whatever today's count says, since that count can lag a split
                # (BUG-036). Spin-off and merger factors aren't simple (ABT 2.0842, DELL 1.806, RTX 1.589).
                if self.last_filed and date[:10] > self.last_filed[:10] and simple_ratio(ratio):
                    unrestated *= ratio
                else:
                    factor *= ratio
        # Today's basis is today's share count (the latest market snapshot: the basis the price is quoted on),
        # moved by a share split the filings restated since the price's day. The latest FY's count is still
        # pre-split when a split came after its filing (KLAC, BKNG; BUG-014), so it is only the fallback when there
        # is no snapshot.
        latest = (self.snap_shares * self.since_price if self.snap_shares else None) or next(
            (r[5] for r in reversed(self.annual) if r[5]), None)
        shares *= unrestated
        if factor != 1.0 and latest:
            shares = min((shares, shares * factor), key=lambda s: abs(math.log(s / latest)))
        return shares / self.since_price / self.ads_ratio

    def last_10_fys(self, series: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
        """The spec's "<= 10-FY CAGR windows": the values of the last 10 fiscal years the filings cover. A year
        without a value leaves a gap; the window doesn't reach further back for it (SCCO has no EPS for FY2013-18,
        so 10 values went back to FY2010)."""
        latest = self.annual[-1][0] if self.annual else None
        return [(fy, v) for fy, v in series if latest is not None and latest - 10 < fy <= latest]

    def eps_series(self) -> List[Tuple[int, float]]:
        """Reported diluted EPS (it nets out preferred dividends) moved to today's split basis;
        net income / shares only when EPS wasn't reported."""
        out = []
        for fy, pe, ni, _eq, so, wd, eps in self.annual:
            # No share count reported (e.g. GOOGL before FY2022): judge the basis from implied shares. With neither
            # EPS nor a diluted count, the spec's "EPS = net income / shares" takes the period-end count (DELL
            # FY2017-19, when it reported EPS only per tracking-stock class).
            basis_shares = wd or (ni / eps if ni and eps else None) or so
            field = ("weighted_avg_shares_diluted" if wd else "eps_diluted" if ni and eps else "shares_outstanding")
            sh = self.to_current_basis(basis_shares, pe, self.filed_of(fy, field))
            if eps is not None:
                out.append((fy, eps * (basis_shares / sh) if basis_shares and sh else eps))
            elif ni is not None and sh:
                out.append((fy, ni / sh))
        return out

    def bvps(self, fy_row: Tuple, which: str) -> Optional[float]:
        fy, pe, _ni, eq, so, wd, _eps = fy_row
        # The spec's BVPS is "equity / shares". A filer that reports no diluted count (XOM since FY2014: its
        # diluted EPS equals basic) leaves the period-end count for the diluted reading too.
        field = "weighted_avg_shares_diluted" if which == "diluted" and wd else "shares_outstanding"
        sh = self.to_current_basis((wd or so) if which == "diluted" else so, pe, self.filed_of(fy, field))
        return eq / sh if eq is not None and sh else None

    def fy_end_price(self, period_end: str) -> Optional[float]:
        row = self.db.execute("select close from price_bars where ticker=? and date<=? order by date desc limit 1",
                              (self.t, period_end[:10])).fetchone()
        return row[0] * self.unpriced if row and row[0] is not None else None


def level2(db: sqlite3.Connection, ticker: str, model: str, stored: Dict) -> Optional[Dict]:
    """Oracle inputs and bear/base/bull for one applicable model, or None if not derivable."""
    x = Inputs(db, ticker)
    if model in ("dcf", "ddm", "lynch") and x.rf is None:
        return None
    if model == "dcf":
        rows = db.execute("select fiscal_year, coalesce(levered_fcf, free_cash_flow) from metrics_annual where "
                          "ticker=? and coalesce(levered_fcf, free_cash_flow) is not null order by fiscal_year",
                          (ticker,)).fetchall()
        window = x.last_10_fys([(fy, v) for fy, v in rows])
        fcf = [v for _fy, v in window]
        if len(fcf) < 4:
            return None
        g = x.growth(cagr(window), 0.15)
        _, r = x.discount()
        if g is None or not x.snap_shares:
            return {"inputs": {"fcf_basis": median(fcf[-3:]), "hist_cagr": None}}
        vals = [dcf_per_share(median(fcf[-3:]), gi, ri, x.snap_shares) for gi, ri in scenarios(g, r, 0.15, False)]
        return {"inputs": {"fcf_basis": median(fcf[-3:]), "hist_cagr": cagr(window),
                           "cagr_years": window[-1][0] - window[0][0], "growth_base": g, "discount_base": r,
                           "shares_outstanding": x.snap_shares}, "values": vals}
    if model == "ddm":
        anchor = stored.get("ttm_anchor")
        if not anchor:
            return None
        events = db.execute("select substr(date,1,10), amount from dividend_events where ticker=? and amount is not "
                            "null order by date", (ticker,)).fetchall()
        # A spin-off isn't a cash dividend. Yahoo records some twice: a price factor between 2:3 and 3:2 in its
        # split history and, that day, a "dividend" worth the spun-off shares (DHR 2016: 1.319 and $24.56, 228x
        # its regular payment; BUG-011). Such a payment, over 5x the median within 400 days, is dropped.
        spin_days = {d[:10] for d, ratio in x.splits if ratio and ratio != 1 and 1 / 1.5 < ratio < 1.5}

        def spun_off(day: str, amount: float) -> bool:
            near = [a for d, a in events if d != day
                    and abs((datetime.date.fromisoformat(d) - datetime.date.fromisoformat(day)).days) <= 400]
            return day in spin_days and bool(near) and amount > 5 * median(near)

        events = [(d, a * x.unpriced) for d, a in events if not spun_off(d, a)]
        ttm_start = (datetime.date.fromisoformat(anchor[:10]) - datetime.timedelta(days=365)).isoformat()
        ttm = sum(a for d, a in events if ttm_start < d <= anchor[:10])
        by_year: Dict[int, List[float]] = {}
        for d, a in events:
            if d[:4] < anchor[:4]:
                by_year.setdefault(int(d[:4]), []).append(a)
        # The spec's "<= 10 yrs" is a calendar window (complete years before the anchor year), and the
        # exponent is the calendar span, so a payment gap can't stretch or shrink the growth rate.
        window = [(y, sum(v), len(v)) for y, v in sorted(by_year.items()) if y >= int(anchor[:4]) - 10]
        # Growth compares full years (BUG-009, BUG-017). Each end is judged by the cadence of the (up to 3) years
        # next to it, since a payer can change frequency (PGR and TSM: annual, then quarterly from 2019). A first
        # year with fewer payments than those is partial (a dividend started, paused or restarted, or an ex-date
        # slipped a year back), unless it continues a lower-frequency schedule: the year before paid as often,
        # and each payment is larger than the later years' typical one (an annual payment is several quarterly
        # ones; a suspension restarted with the same count pays the same size, BUG-017's third fix). An end year
        # with exactly one payment more, next to a year still on the usual schedule and each within 1.5x of that
        # year's typical payment, holds one that slipped across the year boundary (O 2025's Dec 31 ex-date), so
        # it counts at the usual number. A neighbour that already pays more often means the schedule changed (KO
        # semiannual from 2024); a special is larger and counts in full (Q3); a higher frequency pays less each.
        def cadence(years: List[Tuple[int, float, int]]) -> float:
            return median([float(n) for _, _, n in years])

        def lower_frequency(first: Tuple[int, float, int], later: List[Tuple[int, float, int]]) -> bool:
            typical = median([a for y, _, _ in later for a in by_year[y]])
            return (len(by_year.get(first[0] - 1, [])) == first[2]
                    and all(a >= 1.5 * typical for a in by_year[first[0]]))

        while (len(window) >= 2 and window[0][2] < cadence(window[1:4])
               and not lower_frequency(window[0], window[1:4])):
            window = window[1:]
        for end, adjacent, near in ((0, 1, window[1:4]), (-1, -2, window[-4:-1])):
            if len(window) >= 2:
                year, total, n = window[end]
                usual, typical = cadence(near), median(by_year[window[adjacent][0]])
                if (n == usual + 1 and window[adjacent][2] <= usual
                        and all(typical / 1.5 < a < typical * 1.5 for a in by_year[year])):
                    window[end] = (year, total * usual / n, n)
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
        window = x.last_10_fys(eps)
        if len(window) < 4 or window[-1][1] <= 0:
            return None
        if cagr(window) is None:  # Lynch publishes nothing without a measurable growth rate
            return {"inputs": {"hist_cagr": None}}
        g = x.growth(cagr(window), 0.15)
        trio = [max(g - SPREAD_G, 0.0), g, min(g + SPREAD_G, 0.15)]
        latest = window[-1][1]
        return {"inputs": {"eps_base": latest, "hist_cagr": cagr(window), "cagr_years": window[-1][0] - window[0][0],
                           "growth_base": g}, "values": [clamp(gi * 100, 5, 25) * latest for gi in trio]}
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

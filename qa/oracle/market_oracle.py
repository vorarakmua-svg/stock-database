"""Independent check of Milestone 4's market data and views (build task M4-5) against the database's own series.

See "The market data", "The market views" and "Validation" in
docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md. It reads SQLite (and, with --api, a saved answer
of GET /api/market) and never imports `src`: every figure below is re-derived from the spec, so agreeing with the
code under test means something only because the check can't share its bugs.

Gates (any that fails makes the exit code 1):
- CAPE: every month of the stored shiller:cape against CAPE recomputed from the stored shiller:price,
  shiller:earnings and shiller:cpi: the real price over the mean real earnings of the 120 months ending the month
  before, within 0.1% (this checks the workbook reader too: the four columns came from the same sheet).
- Shiller's monthly price (the month's mean of daily closes) against the mean of the database's own ^GSPC daily
  closes (price_bars) for each month both have: the median of the absolute differences under 0.1%, and at least 99%
  of the months within 1% (the misses are printed, each explained where it can be: Shiller's figure is one of the
  month's closes rather than their mean, or price_bars has only part of the month).
- The 10-year rate: ust:10y on market_snapshots' latest risk_free_rate_date against each stored rate there x 100,
  within 0.005 points.
- With --api (the views are computed at read time, so the API is what they're compared with):
  - Buffett indicator: this oracle's NCBEILQ027S / 1000 / GDP x 100 on the quarters both FRED series have, its own
    least-squares fit of ln(ratio) on its own time variable, the residuals' sample standard deviation, the latest
    quarter's z and its zone by the spec's cut-offs: the API's date (and quarters) equal, its ratio, trend and
    percentile within 0.5%, its z within 0.02 and its zone equal.
  - Yield curve: this oracle's latest day and day a year earlier, both curves, and for the 10-year less 2-year and
    less 3-month spreads their days, latest value, inverted share, last inverted day and latest sustained inversion:
    all equal to the API's (the latest value within 1e-6: the API rounds a spread to 6 decimals).
  - The CAPE section: its latest value and month, first month, months, mean, median, min and max (with their first
    months) and percentile, against the stored series' own.
  Without --api these are not run (and not counted), saying so; their figures are still printed.

Reported, not gated: the latest quarter's Buffett ratio by hand from the two raw values and this oracle's Buffett and
yield-curve figures, the CAPE months whose window lacks earnings (the latest), the price comparison's distribution,
and each spread's inversion episodes of 20 days or more.

Readings of the spec where it leaves a choice (each also noted where it's used):
- A month's CAPE window is the 120 calendar months before it; a month in it without earnings is skipped (Excel's
  AVERAGE skips blank cells); real means over that month's CPI (the CPI's base cancels); a month whose window has no
  earnings has no CAPE.
- The ^GSPC month is the mean of the closes price_bars has in that calendar month; the difference is Shiller's price
  / that mean - 1, and "the median difference" is the median of its absolute value.
- The Buffett ratio's quarters are those both series have with positive values. Time is days since 1970-01-01 /
  365.25 (the code uses its own decimal year: the two agree to well within the z tolerance); the fit has an
  intercept, so the residuals' standard deviation is their sample one (n - 1) about 0. The percentile is the share
  of the quarters at or below the latest, the latest included. Zones: z < -2 significantly undervalued, -2 <= z < -1
  modestly undervalued, -1 <= z <= 1 fairly valued, 1 < z <= 2 modestly overvalued, z > 2 significantly overvalued
  (the spec names the ranges, not which side an exact cut-off falls on; a z within 0.02 of one is flagged).
- The yield curve's latest day is the latest day any maturity has; a year earlier is the latest such day on or
  before 365 calendar days before it. A spread is the long yield less the short one on each day both have; inverted
  means below 0 (0 itself is not). An episode starts with a run of inverted days and takes in the next run when at
  most 20 of the spread's days lie between them; its days are its inverted days, not its span; the latest sustained
  inversion is the latest episode with at least 20.
- The 10-year check takes every snapshot dated the latest risk_free_rate_date (they should agree with each other).

Usage: python qa/oracle/market_oracle.py --db PATH [--api market.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

Num = Optional[float]

#: Tolerances, from the task brief (M4-5). None may be loosened to make a gate pass.
CAPE_TOL = 0.001  # relative
PRICE_MEDIAN_TOL = 0.001  # the median absolute relative difference
PRICE_MONTH_TOL = 0.01  # a month within 1%
PRICE_SHARE = 0.99  # of the months
API_REL_TOL = 0.005  # ratio, trend, percentile
Z_TOL = 0.02
RATE_TOL = 0.005  # percentage points
SPREAD_TOL = 1e-6  # the API rounds a spread to 6 decimals
EXACT = 1e-12

#: The spec's figures.
CAPE_MONTHS = 120
CUTOFFS = (-2.0, -1.0, 1.0, 2.0)
ZONES = ("significantly undervalued", "modestly undervalued", "fairly valued", "modestly overvalued",
         "significantly overvalued")
YEAR_AGO_DAYS = 365
EPISODE_GAP = 20
EPISODE_MIN_DAYS = 20
SPREADS = (("10y", "2y"), ("10y", "3m"))
EPOCH = date(1970, 1, 1)

#: Every gate run, for the summary line.
GATES: List[str] = []


def gate(name: str, ok: bool, detail: str, failures: List[str]) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    GATES.append(name)
    if not ok:
        failures.append(name)


def skipped(name: str) -> None:
    print(f"SKIP  {name}: no --api answer given (not run, not counted)")


def fmt(x: Any) -> str:
    if x is None:
        return "N/A"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, (int, float)):
        return f"{x:.8g}"
    return str(x)


def rel(a: float, b: float) -> float:
    return 0.0 if a == b else abs(a - b) / max(abs(a), abs(b))


def month_of(day: str) -> int:
    """A month's index: year x 12 + month - 1."""
    return int(day[:4]) * 12 + int(day[5:7]) - 1


def month_name(index: int) -> str:
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


# ---------------------------------------------------------------------------------------------------------------
# Loading


class Data:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.series: Dict[str, Dict[str, float]] = {}
        for name, day, value in db.execute("SELECT series, date, value FROM market_series WHERE value IS NOT NULL"):
            self.series.setdefault(name, {})[str(day)[:10]] = float(value)
        self.gspc: Dict[int, List[float]] = {}
        self.gspc_days: Dict[int, List[Tuple[str, float]]] = {}
        for day, close in db.execute("SELECT substr(date, 1, 10), close FROM price_bars WHERE ticker = '^GSPC' "
                                     "AND close IS NOT NULL ORDER BY date"):
            self.gspc.setdefault(month_of(day), []).append(float(close))
            self.gspc_days.setdefault(month_of(day), []).append((day, float(close)))
        latest = db.execute("SELECT MAX(risk_free_rate_date) FROM market_snapshots").fetchone()[0]
        self.rf_date: Optional[str] = str(latest)[:10] if latest else None
        self.rf_rows: List[Tuple[str, float, Optional[str]]] = [
            (r[0], float(r[1]), r[2]) for r in db.execute(
                "SELECT ticker, risk_free_rate, risk_free_rate_source FROM market_snapshots "
                "WHERE risk_free_rate_date = ? AND risk_free_rate IS NOT NULL", (latest,))] if latest else []

    def monthly(self, name: str) -> Dict[int, float]:
        return {month_of(day): value for day, value in self.series.get(name, {}).items()}


# ---------------------------------------------------------------------------------------------------------------
# CAPE and Shiller's price


def check_cape(d: Data, failures: List[str]) -> None:
    print("== CAPE: the real price over the mean real earnings of the 120 months ending the month before")
    price, earnings, cpi = d.monthly("shiller:price"), d.monthly("shiller:earnings"), d.monthly("shiller:cpi")
    stored = d.monthly("shiller:cape")
    checked = agreed = 0
    misses: List[str] = []
    short: List[str] = []
    worst = (0.0, "")
    for m in sorted(stored):
        checked += 1
        window = [earnings[j] / cpi[j] for j in range(m - CAPE_MONTHS, m) if j in earnings and cpi.get(j)]
        mine: Num = None
        if window and price.get(m) is not None and cpi.get(m):
            mine = (price[m] / cpi[m]) / statistics.fmean(window)
        if len(window) < CAPE_MONTHS:
            short.append(f"{month_name(m)} ({len(window)})")
        if mine is not None and rel(mine, stored[m]) <= CAPE_TOL:
            agreed += 1
            if rel(mine, stored[m]) > worst[0]:
                worst = (rel(mine, stored[m]), month_name(m))
        else:
            misses.append(f"    {month_name(m)}: stored {fmt(stored[m])} oracle {fmt(mine)}"
                          + (f" (gap {rel(mine, stored[m]):.3%})" if mine is not None else "")
                          + f"; price {fmt(price.get(m))}, CPI {fmt(cpi.get(m))}, {len(window)} months of earnings")
    first = month_name(min(stored)) if stored else "none"
    last = month_name(max(stored)) if stored else "none"
    gate("CAPE recomputed from the stored price, earnings and CPI within 0.1%", checked > 0 and agreed == checked,
         f"agree {agreed}/{checked} months ({first} to {last}); the largest gap among those agreeing {worst[0]:.2e} "
         f"({worst[1]})", failures)
    for line in misses[:60]:
        print(line)
    if len(misses) > 60:
        print(f"    ... and {len(misses) - 60} more")
    print(f"    months whose window lacks earnings (AVERAGE skips the blanks), with the months it has: "
          f"{', '.join(short) or 'none'}")
    for name in ("shiller:price", "shiller:earnings", "shiller:cpi", "shiller:dividend", "shiller:cape"):
        s = d.series.get(name, {})
        print(f"    {name}: {len(s)} months, {min(s) if s else '-'} to {max(s) if s else '-'}")


def explain(shiller: float, closes: List[Tuple[str, float]]) -> str:
    """Why a month misses: Shiller's figure is one of the month's closes (not their mean), or price_bars has only part
    of the month."""
    hits = [day for day, close in closes if abs(close - shiller) <= 0.005]
    if hits:
        last = " (the month's last)" if hits[-1] == closes[-1][0] else ""
        return f"Shiller's figure is the close of {hits[-1]}{last}, not the month's mean"
    if len(closes) < 15:
        return f"price_bars has only part of the month: {closes[0][0]} to {closes[-1][0]}"
    return "unexplained"


def check_price(d: Data, failures: List[str]) -> None:
    print("== Shiller's monthly price against the mean of the database's ^GSPC daily closes, month by month")
    price = d.monthly("shiller:price")
    both = sorted(m for m in price if m in d.gspc)
    diffs = [(price[m] / statistics.fmean(d.gspc[m]) - 1.0, m) for m in both]
    absolute = sorted(abs(x) for x, _ in diffs)
    median = statistics.median(absolute) if absolute else None
    within = sum(1 for x in absolute if x <= PRICE_MONTH_TOL)
    ok = bool(diffs) and median is not None and median < PRICE_MEDIAN_TOL and within >= PRICE_SHARE * len(diffs)
    gate("Shiller's price against ^GSPC: median |difference| under 0.1%, at least 99% of months within 1%", ok,
         f"{len(diffs)} months ({month_name(both[0]) if both else '-'} to {month_name(both[-1]) if both else '-'}); "
         f"median {fmt(median and 100 * median)}%; within 1%: {within}/{len(diffs)} "
         f"({100 * within / len(diffs) if diffs else 0:.2f}%)", failures)
    for x, m in diffs:
        if abs(x) > PRICE_MONTH_TOL:
            print(f"    miss {month_name(m)}: Shiller {fmt(price[m])}, ^GSPC mean {fmt(statistics.fmean(d.gspc[m]))} "
                  f"of {len(d.gspc[m])} closes ({100 * x:+.3f}%): {explain(price[m], d.gspc_days[m])}")
    if absolute:
        bands = ", ".join(f"<= {label}: {sum(1 for x in absolute if x <= tol)}"
                          for label, tol in (("0.01%", 1e-4), ("0.1%", 1e-3), ("0.5%", 5e-3), ("1%", 1e-2)))
        print(f"    (reported) the months' |difference|: {bands} (of {len(absolute)}); the largest "
              f"{100 * absolute[-1]:.3f}%")
        last = both[-1]
        latest = max(price)
        closes = d.gspc_days.get(latest, [])
        print(f"    (reported) Shiller's latest month {month_name(latest)} (his footnotes: a single day's close): "
              f"{fmt(price[latest])}; ^GSPC's closes that month: {len(closes)}"
              + (f", {closes[0][0]} to {closes[-1][0]}; {explain(price[latest], closes)}" if closes else ""))
        decades: Dict[int, List[float]] = {}
        for x, m in diffs:
            decades.setdefault((m // 12) // 10 * 10, []).append(abs(x))
        print("    (reported) median |difference| by decade: " + ", ".join(
            f"{k}s {100 * statistics.median(v):.3f}% ({len(v)})" for k, v in sorted(decades.items()))
            + f"; latest month compared {month_name(last)}")


# ---------------------------------------------------------------------------------------------------------------
# Buffett


def zone_of(z: float) -> str:
    if z < CUTOFFS[0]:
        return ZONES[0]
    if z < CUTOFFS[1]:
        return ZONES[1]
    if z <= CUTOFFS[2]:
        return ZONES[2]
    if z <= CUTOFFS[3]:
        return ZONES[3]
    return ZONES[4]


def buffett(d: Data) -> Dict[str, Any]:
    eq, gdp = d.series.get("fred:NCBEILQ027S", {}), d.series.get("fred:GDP", {})
    days = sorted(day for day in set(eq) & set(gdp) if eq[day] > 0 and gdp[day] > 0)
    if len(days) < 3:
        return {"days": days}
    ratio = [eq[day] / 1000.0 / gdp[day] * 100.0 for day in days]
    t = [(date.fromisoformat(day) - EPOCH).days / 365.25 for day in days]
    y = [math.log(r) for r in ratio]
    n = len(days)
    # Least squares by the normal equations on raw sums (the code centres its time; the two must agree).
    st, sy = math.fsum(t), math.fsum(y)
    stt, sty = math.fsum(a * a for a in t), math.fsum(a * b for a, b in zip(t, y))
    slope = (n * sty - st * sy) / (n * stt - st * st)
    intercept = (sy - slope * st) / n
    residuals = [b - (intercept + slope * a) for a, b in zip(t, y)]
    sd = math.sqrt(math.fsum(r * r for r in residuals) / (n - 1))
    z = residuals[-1] / sd
    return {"days": days, "ratio": ratio, "latest": ratio[-1], "date": days[-1], "slope": slope, "sd": sd, "z": z,
            "trend": math.exp(intercept + slope * t[-1]), "zone": zone_of(z),
            "percentile": sum(1 for r in ratio if r <= ratio[-1]) / n, "eq": eq[days[-1]], "gdp": gdp[days[-1]],
            "mean_residual": math.fsum(residuals) / n}


def check_buffett(d: Data, api: Optional[Dict[str, Any]], failures: List[str]) -> None:
    print("== Buffett indicator: NCBEILQ027S / 1000 / GDP x 100, against its own log-linear trend")
    b = buffett(d)
    if "ratio" not in b:
        gate("Buffett indicator computed", False, f"{len(b['days'])} quarters both series have", failures)
        return
    near = min(abs(b["z"] - c) for c in CUTOFFS)
    print(f"    by hand, the latest quarter {b['date']}: {b['eq']:,.0f} / 1000 / {b['gdp']:,.3f} x 100 = "
          f"{b['latest']:.4f}%")
    print(f"    this oracle: {len(b['days'])} quarters ({b['days'][0]} to {b['date']}); trend {b['trend']:.4f}% "
          f"(growth {math.exp(b['slope']) - 1:.4%} a year); sd {b['sd']:.6f}; z {b['z']:.4f} ({b['zone']}"
          f"{'; within 0.02 of a cut-off' if near < Z_TOL else ''}); percentile {b['percentile']:.4f}; the residuals' "
          f"mean {b['mean_residual']:.1e}")
    if api is None:
        skipped("Buffett indicator against the API")
        return
    a = api.get("buffett") or {}
    bad = []
    if not a.get("available"):
        bad.append(f"the API's section is unavailable: {a.get('reason')}")
    else:
        if a.get("date") != b["date"]:
            bad.append(f"date {a.get('date')} vs {b['date']}")
        for key in ("ratio", "trend", "percentile"):
            mine = b["latest"] if key == "ratio" else b[key]
            if a.get(key) is None or rel(float(a[key]), mine) > API_REL_TOL:
                bad.append(f"{key} {fmt(a.get(key))} vs {fmt(mine)}")
        if a.get("z") is None or abs(float(a["z"]) - b["z"]) > Z_TOL:
            bad.append(f"z {fmt(a.get('z'))} vs {fmt(b['z'])}")
        if a.get("zone") != b["zone"]:
            bad.append(f"zone {a.get('zone')} vs {b['zone']}")
        if a.get("quarters") != len(b["days"]) or a.get("first_date") != b["days"][0]:
            bad.append(f"quarters {a.get('quarters')} from {a.get('first_date')} vs {len(b['days'])} from "
                       f"{b['days'][0]}")
    gate("Buffett: the API's date, ratio, trend and percentile (0.5%), z (0.02) and zone", not bad,
         f"API ratio {fmt(a.get('ratio'))}, trend {fmt(a.get('trend'))}, percentile {fmt(a.get('percentile'))}, "
         f"z {fmt(a.get('z'))} (oracle {b['z']:.6f}, gap {abs(float(a.get('z') or 0) - b['z']):.2e}), zone "
         f"{a.get('zone')}" + ("; differ: " + "; ".join(bad) if bad else ""), failures)
    if a.get("sd") is not None:
        print(f"    (reported) the API's sd {fmt(a.get('sd'))} vs {b['sd']:.8g}; growth {fmt(a.get('growth'))} vs "
              f"{math.exp(b['slope']) - 1:.8g} (each on its own time variable)")


# ---------------------------------------------------------------------------------------------------------------
# The yield curve


def episodes(days: Sequence[str], inverted: Sequence[bool]) -> List[Dict[str, Any]]:
    """The runs of inverted days, each joined to the next when at most EPISODE_GAP of the spread's days lie
    between them: [{first, last, days}] by day index."""
    runs: List[List[int]] = []  # [start, end] indexes of consecutive inverted days
    for i, below in enumerate(inverted):
        if below:
            if runs and runs[-1][1] == i - 1:
                runs[-1][1] = i
            else:
                runs.append([i, i])
    joined: List[Dict[str, Any]] = []
    for start, end in runs:
        if joined and start - joined[-1]["end"] - 1 <= EPISODE_GAP:
            joined[-1]["end"] = end
            joined[-1]["days"] += end - start + 1
        else:
            joined.append({"start": start, "end": end, "days": end - start + 1})
    return [{"first": days[e["start"]], "last": days[e["end"]], "days": e["days"]} for e in joined]


def curve_view(d: Data) -> Dict[str, Any]:
    ust = {name[4:]: values for name, values in d.series.items() if name.startswith("ust:")}
    days = sorted({day for values in ust.values() for day in values})
    if not days:
        return {}
    latest = days[-1]
    cutoff = (date.fromisoformat(latest) - timedelta(days=YEAR_AGO_DAYS)).isoformat()
    earlier = [day for day in days if day <= cutoff]
    year_ago = earlier[-1] if earlier else None

    def curve(day: Optional[str]) -> Dict[str, float]:
        return {m: v[day] for m, v in ust.items() if day in v} if day else {}

    spreads = []
    for long, short in SPREADS:
        a, b = ust.get(long, {}), ust.get(short, {})
        common = sorted(set(a) & set(b))
        values = [a[day] - b[day] for day in common]
        inverted = [v < 0 for v in values]
        eps = episodes(common, inverted)
        sustained = [e for e in eps if e["days"] >= EPISODE_MIN_DAYS]
        spreads.append({
            "key": f"{long}_{short}", "days": len(common), "first_date": common[0] if common else None,
            "latest_date": common[-1] if common else None, "latest_value": values[-1] if values else None,
            "inverted": inverted[-1] if inverted else None,
            "inverted_share": sum(inverted) / len(inverted) if inverted else None,
            "last_inverted_day": max((day for day, below in zip(common, inverted) if below), default=None),
            "latest_episode": sustained[-1] if sustained else None, "episodes": eps})
    return {"date": latest, "first_date": days[0], "curve": curve(latest), "year_ago": year_ago,
            "year_ago_curve": curve(year_ago), "spreads": spreads}


def check_curve(d: Data, api: Optional[Dict[str, Any]], failures: List[str]) -> None:
    print("== yield curve: the latest day against a year earlier; the 10y-2y and 10y-3m spreads")
    v = curve_view(d)
    if not v:
        gate("yield curve computed", False, "no ust: series stored", failures)
        return
    print(f"    this oracle: latest {v['date']} ({len(v['curve'])} maturities), a year earlier {v['year_ago']} "
          f"({len(v['year_ago_curve'])} maturities); first day {v['first_date']}")
    for s in v["spreads"]:
        long_ones = [e for e in s["episodes"] if e["days"] >= EPISODE_MIN_DAYS]
        print(f"    {s['key']}: {s['days']} days {s['first_date']} to {s['latest_date']}; latest "
              f"{fmt(s['latest_value'])} (inverted {s['inverted']}); inverted share {s['inverted_share']:.6f}; last "
              f"inverted {s['last_inverted_day']}; latest sustained {s['latest_episode']}; {len(s['episodes'])} "
              f"episodes, {len(long_ones)} of 20+ days: "
              + ", ".join(f"{e['first']}..{e['last']} ({e['days']})" for e in long_ones))
    if api is None:
        skipped("yield curve against the API")
        return
    a = api.get("yield_curve") or {}
    bad = []
    if not a.get("available"):
        bad.append(f"the API's section is unavailable: {a.get('reason')}")
    else:
        for key, mine in (("date", v["date"]), ("first_date", v["first_date"])):
            if a.get(key) != mine:
                bad.append(f"{key} {a.get(key)} vs {mine}")
        ya = a.get("year_ago") or {}
        if ya.get("date") != v["year_ago"]:
            bad.append(f"year_ago {ya.get('date')} vs {v['year_ago']}")
        for label, theirs, mine_curve in (("curve", a.get("curve") or [], v["curve"]),
                                          ("year_ago curve", ya.get("curve") or [], v["year_ago_curve"])):
            got = {c["maturity"]: c["value"] for c in theirs}
            if got != mine_curve:
                bad.append(f"{label} {got} vs {mine_curve}")
        by_key = {s.get("key"): s for s in a.get("spreads") or []}
        for s in v["spreads"]:
            t = by_key.get(s["key"])
            if t is None:
                bad.append(f"{s['key']}: not in the API")
                continue
            for key in ("days", "first_date", "latest_date", "inverted", "last_inverted_day"):
                if t.get(key) != s[key]:
                    bad.append(f"{s['key']} {key} {t.get(key)} vs {s[key]}")
            if t.get("latest_value") is None or abs(t["latest_value"] - s["latest_value"]) > SPREAD_TOL:
                bad.append(f"{s['key']} latest_value {t.get('latest_value')} vs {s['latest_value']}")
            if t.get("inverted_share") is None or abs(t["inverted_share"] - s["inverted_share"]) > EXACT:
                bad.append(f"{s['key']} inverted_share {t.get('inverted_share')} vs {s['inverted_share']}")
            mine_ep = s["latest_episode"]
            mine_ep = {k: mine_ep[k] for k in ("first", "last", "days")} if mine_ep else None
            if t.get("latest_episode") != mine_ep:
                bad.append(f"{s['key']} latest_episode {t.get('latest_episode')} vs {mine_ep}")
    gate("yield curve: the API's days, curves and spreads equal this oracle's", not bad,
         "equal" if not bad else "differ: " + "; ".join(bad), failures)


def check_rate(d: Data, failures: List[str]) -> None:
    print("== the 10-year rate: ust:10y on market_snapshots' latest risk_free_rate_date against the stored rate")
    ten = d.series.get("ust:10y", {})
    if not d.rf_date or not d.rf_rows:
        gate("the 10-year rate", False, "no snapshot has a risk_free_rate_date", failures)
        return
    yield_ = ten.get(d.rf_date)
    rates = sorted({rate for _, rate, _ in d.rf_rows})
    sources = sorted({str(source) for _, _, source in d.rf_rows})
    bad = [rate for rate in rates if yield_ is None or abs(yield_ - 100 * rate) > RATE_TOL]
    gate("the 10-year on the latest risk_free_rate_date equals the stored rate x 100 (0.005)", not bad,
         f"{d.rf_date}: ust:10y {fmt(yield_)}; stored {', '.join(f'{r:g}' for r in rates)} on {len(d.rf_rows)} "
         f"snapshots (source {', '.join(sources)})", failures)


def check_cape_api(d: Data, api: Optional[Dict[str, Any]], failures: List[str]) -> None:
    if api is None:
        skipped("CAPE section against the API")
        return
    rows_ = sorted(d.series.get("shiller:cape", {}).items())
    values = [v for _, v in rows_]
    a = api.get("cape") or {}
    bad = []
    if not rows_ or not a.get("available"):
        bad.append(f"stored {len(rows_)} months, the API's section available {a.get('available')}")
    else:
        latest = values[-1]
        low, high = min(values), max(values)
        mine = {"date": rows_[-1][0], "value": latest, "first_date": rows_[0][0], "months": len(rows_),
                "mean": math.fsum(values) / len(values), "median": statistics.median(values), "min": low, "max": high,
                "min_date": next(day for day, v in rows_ if v == low),
                "max_date": next(day for day, v in rows_ if v == high),
                "percentile": sum(1 for v in values if v <= latest) / len(values)}
        for key, value in mine.items():
            theirs = a.get(key)
            if isinstance(value, float):
                if theirs is None or rel(float(theirs), value) > 1e-9:
                    bad.append(f"{key} {fmt(theirs)} vs {fmt(value)}")
            elif theirs != value:
                bad.append(f"{key} {theirs} vs {value}")
    gate("CAPE section: the API's latest value and month, months, mean, median, min, max and percentile", not bad,
         (f"latest {fmt(a.get('value'))} ({a.get('date')}), percentile {fmt(a.get('percentile'))}, mean "
          f"{fmt(a.get('mean'))}, median {fmt(a.get('median'))}, min {fmt(a.get('min'))} ({a.get('min_date')}), max "
          f"{fmt(a.get('max'))} ({a.get('max_date')})") + ("; differ: " + "; ".join(bad) if bad else ""), failures)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", required=True)
    p.add_argument("--api", default=None, help="a saved answer of GET /api/market (JSON)")
    args = p.parse_args(argv)
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    api = None
    if args.api:
        with open(args.api, encoding="utf-8") as f:
            api = json.load(f)
    failures: List[str] = []
    GATES.clear()
    d = Data(db)
    print(f"database {args.db}: {len(d.series)} market series, {sum(len(v) for v in d.series.values())} values; "
          f"^GSPC closes in {len(d.gspc)} months; API answer: {args.api or 'none'}")
    check_cape(d, failures)
    check_price(d, failures)
    check_rate(d, failures)
    check_buffett(d, api, failures)
    check_curve(d, api, failures)
    print("== the CAPE section of the API against the stored series")
    check_cape_api(d, api, failures)
    print(f"gates: {len(GATES) - len(failures)} of {len(GATES)} pass; "
          + ("FAILED: " + "; ".join(failures) if failures else "all gates pass"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

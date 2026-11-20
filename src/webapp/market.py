"""The market views (Milestone 4), shared by ``GET /api/market`` and the market page ``/market``: the Buffett indicator
against its own long-run trend, Shiller's CAPE against its history, and the Treasury yield curve against the curve a
year earlier with its 10-year spreads. Each is computed at read time from the series ``src.market.collect`` stores
(``market_series``), with its source's latest fetch (``market_fetches``)
(docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md, "The market views").

Every section has ``available``, its ``method`` and its latest ``date``. One that isn't available has none of its
figures, only its ``reason`` and, when its series aren't stored, the command that collects them (``collect``, also in
the reason). The view's own ``available`` says whether any section is. Read-only: a database from before the market
tables has every section unavailable.
"""
from __future__ import annotations

import calendar
import math
import statistics
from bisect import bisect_right
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..valuation.history import window_stats
from .repository import Reader

#: The command that collects the market data; a section whose series aren't stored adds its source's flag.
COLLECT_COMMAND = "python -m src.market.collect"
#: Each section's flag of the collect command.
COLLECT_FLAGS: Dict[str, str] = {"buffett": "--fred", "cape": "--shiller", "yield_curve": "--treasury"}

#: FRED's series behind the Buffett indicator: the nonfinancial corporate business's corporate equities (a liability
#: level, quarterly, $ millions) and GDP (quarterly, $ billions at an annual rate).
EQUITIES_SERIES = "fred:NCBEILQ027S"
GDP_SERIES = "fred:GDP"
#: The equities are in $ millions and GDP in $ billions: the equities over this are in GDP's unit.
MILLIONS_PER_BILLION = 1000.0
#: The least quarters the trend is fitted on: a line through two leaves no residual to measure the latest by.
MIN_TREND_QUARTERS = 3
#: The cut-offs of the Buffett indicator's zones, in standard deviations of the ratio's residuals from its trend (z).
BUFFETT_CUTOFFS: Tuple[float, float, float, float] = (-2.0, -1.0, 1.0, 2.0)
#: The zones by z: below -2; from -2 to under -1; from -1 to +1, both included; over +1 to +2; above +2.
BUFFETT_ZONES: Tuple[str, str, str, str, str] = ("significantly undervalued", "modestly undervalued",
                                                  "fairly valued", "modestly overvalued", "significantly overvalued")
#: Each zone's pill, as the valuation verdicts' pills (verdict-cheap, -fair, -expensive).
ZONE_PILLS: Dict[str, str] = {"significantly undervalued": "cheap", "modestly undervalued": "cheap",
                              "fairly valued": "fair", "modestly overvalued": "expensive",
                              "significantly overvalued": "expensive"}
#: The zones with their cut-offs, as the Buffett indicator's method note publishes them.
ZONES_TEXT = (f"z below {BUFFETT_CUTOFFS[0]:+g}: {BUFFETT_ZONES[0]}; {BUFFETT_CUTOFFS[0]:+g} to under "
              f"{BUFFETT_CUTOFFS[1]:+g}: {BUFFETT_ZONES[1]}; {BUFFETT_CUTOFFS[1]:+g} to {BUFFETT_CUTOFFS[2]:+g}: "
              f"{BUFFETT_ZONES[2]}; over {BUFFETT_CUTOFFS[2]:+g} to {BUFFETT_CUTOFFS[3]:+g}: {BUFFETT_ZONES[3]}; "
              f"above {BUFFETT_CUTOFFS[3]:+g}: {BUFFETT_ZONES[4]}")
#: The Buffett indicator's method note.
BUFFETT_METHOD = (
    "The corporate equities of US nonfinancial corporate business at market value (FRED's NCBEILQ027S, a quarterly "
    "level in $ millions, over 1,000) as a percentage of GDP (FRED's GDP, $ billions at an annual rate), in each "
    "quarter both series have: FRED's equities level lags GDP by a quarter or two, so the date is the latest quarter "
    "both have, and before 1951-Q4 the equities are annual. The trend is the least-squares line through ln(ratio) "
    "against time in years over the whole history, a constant growth a year; each quarter's residual is its ln(ratio) "
    "less the line's, and z is the latest quarter's residual over the residuals' standard deviation. The bands are the "
    "trend times e to the +-1 and +-2 standard deviations. Zones: " + ZONES_TEXT + ". The percentile is the share of "
    "the quarters at or below the latest.")

#: Shiller's CAPE.
CAPE_SERIES = "shiller:cape"
#: Shiller's CAPE's method note.
CAPE_METHOD = (
    "Robert Shiller's cyclically adjusted P/E of the S&P Composite, as his workbook gives it: the real price over the "
    "mean real earnings of the 120 months ending the month before, real meaning deflated by the CPI, monthly from "
    "1881. His price is the month's mean of daily closes, except in his latest month, which is a single day's close "
    "with an estimated CPI; in his latest months, whose earnings aren't out yet, the mean is of the window's months "
    "that have them. The percentile is the share of the months at or below the latest.")

#: The Treasury's maturities, shortest first (the ``ust:`` series), and their labels.
MATURITIES: Tuple[Tuple[str, str], ...] = (
    ("1m", "1 mo"), ("6w", "1.5 mo"), ("2m", "2 mo"), ("3m", "3 mo"), ("4m", "4 mo"), ("6m", "6 mo"), ("1y", "1 yr"),
    ("2y", "2 yr"), ("3y", "3 yr"), ("5y", "5 yr"), ("7y", "7 yr"), ("10y", "10 yr"), ("20y", "20 yr"),
    ("30y", "30 yr"))
#: Each maturity's label.
MATURITY_LABELS: Dict[str, str] = dict(MATURITIES)
#: The spreads, each the long maturity's yield less the short one's, in percentage points.
SPREADS: Tuple[Tuple[str, str], ...] = (("10y", "2y"), ("10y", "3m"))
#: The spreads' decimals: the yields have two, so the rounding only drops a difference's float noise.
SPREAD_DECIMALS = 6
#: The curve a year earlier is the latest day on or before this many days before the latest day.
YEAR_AGO_DAYS = 365
#: A spread's inversion episodes join its runs of days below 0 across gaps of at most this many of its own days (days
#: above 0 between two runs; a day the spread lacks is none of them).
EPISODE_GAP = 20
#: The latest sustained inversion is the latest episode with at least this many days below 0 (not its span).
EPISODE_MIN_DAYS = 20
#: The yield curve's method note.
CURVE_METHOD = (
    "Treasury.gov's daily par yield curve, each maturity's yield in percent as published: the latest day's curve "
    f"against the curve of the latest day on or before {YEAR_AGO_DAYS} days earlier, each with the maturities that "
    "have a yield that day (some bills are recent: the 1.5-month from 2025, the 4-month from 2022, the 2-month from "
    "2018). The spreads are the 10-year yield less the 2-year and less the 3-month, in percentage points, on each day "
    "both have; a spread below 0 is an inverted curve, and its last inverted day is the latest day it was below 0. Its "
    f"episodes are its runs of days below 0 joined across gaps of at most {EPISODE_GAP} of the spread's days, and the "
    f"latest sustained inversion is the latest episode with at least {EPISODE_MIN_DAYS} days below 0.")

#: Each section's sources as ``market_fetches`` names them, with their names on the page.
SECTION_SOURCES: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "buffett": (("fred:NCBEILQ027S", "FRED: NCBEILQ027S, corporate equities"), ("fred:GDP", "FRED: GDP")),
    "cape": (("shiller", "Robert Shiller: ie_data.xls"),),
    "yield_curve": (("treasury", "Treasury.gov: daily par yield curve rates"),),
}

Series = Dict[str, List[Tuple[str, float]]]


def decimal_year(day: str) -> float:
    """An ISO date as a decimal year, a month being a twelfth of a year: the year plus its months before the date's,
    and the days of its month before it as a fraction of that month. FRED dates a quarter by its first day, so its
    quarters are a quarter apart: "2026-04-01" is 2026.25."""
    d = date.fromisoformat(day)
    return d.year + (d.month - 1 + (d.day - 1) / calendar.monthrange(d.year, d.month)[1]) / 12


def buffett_zone(z: float) -> str:
    """The zone of the latest quarter's residual in standard deviations, by BUFFETT_CUTOFFS: -1 and +1 are fairly
    valued, -2 and +2 modestly under- and overvalued."""
    very_low, low, high, very_high = BUFFETT_CUTOFFS
    if z < very_low:
        return BUFFETT_ZONES[0]
    if z < low:
        return BUFFETT_ZONES[1]
    if z <= high:
        return BUFFETT_ZONES[2]
    if z <= very_high:
        return BUFFETT_ZONES[3]
    return BUFFETT_ZONES[4]


def is_inverted(spread: float) -> bool:
    """A spread below 0: the long yield under the short one."""
    return spread < 0


def _unavailable(section: str, method: str, reason: str, missing: bool) -> Dict[str, Any]:
    """A section without its figures: why, and, when its series aren't stored (*missing*), the command that collects
    them, at the end of the reason."""
    collect = f"{COLLECT_COMMAND} {COLLECT_FLAGS[section]}" if missing else None
    return {"available": False, "reason": f"{reason}: run {collect}" if collect else reason, "collect": collect,
            "method": method, "date": None}


def buffett_view(series: Series) -> Dict[str, Any]:
    """The Buffett indicator: corporate equities over GDP each quarter both FRED series have, in percent, against the
    least-squares trend of its log over the whole history. The latest quarter's ratio and date, the trend's value
    then and its growth a year (e^slope - 1), the residuals' standard deviation (sd, ln terms), the latest residual in
    standard deviations (z), its zone and pill, the ratio's percentile over the history (``window_stats``: None under
    its 36 values), and the chart's series: the ratio, the trend and the bands at the trend x e^(+-1 sd) and
    x e^(+-2 sd). Unavailable without both series, with fewer than MIN_TREND_QUARTERS quarters in common, or when the
    ratio never leaves its trend."""
    equities = dict(series.get(EQUITIES_SERIES, []))
    gdp = dict(series.get(GDP_SERIES, []))
    missing = [name for name, rows in (("NCBEILQ027S", equities), ("GDP", gdp)) if not rows]
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        return _unavailable("buffett", BUFFETT_METHOD, f"FRED's {' and '.join(missing)} {verb} not stored", True)
    # The quarters both series have: FRED's equities begin before GDP, lag it at the end and are annual at first.
    quarters = [(day, equities[day] / MILLIONS_PER_BILLION / gdp[day] * 100)
                for day in sorted(set(equities) & set(gdp)) if equities[day] > 0 and gdp[day] > 0]
    if len(quarters) < MIN_TREND_QUARTERS:
        return _unavailable("buffett", BUFFETT_METHOD, f"{len(quarters)} quarters both FRED series have: the trend "
                                                       f"needs {MIN_TREND_QUARTERS}", False)
    dates = [day for day, _ in quarters]
    ratios = [ratio for _, ratio in quarters]
    times = [decimal_year(day) for day in dates]
    logs = [math.log(ratio) for ratio in ratios]
    # Least squares on centred time; statistics.mean is exact, so a ratio that never moves leaves residuals of 0.
    time_mean, log_mean = statistics.mean(times), statistics.mean(logs)
    slope = (sum((t - time_mean) * (y - log_mean) for t, y in zip(times, logs))
             / sum((t - time_mean) ** 2 for t in times))
    fitted = [log_mean + slope * (t - time_mean) for t in times]
    residuals = [y - f for y, f in zip(logs, fitted)]
    sd = statistics.stdev(residuals)
    if sd == 0:
        return _unavailable("buffett", BUFFETT_METHOD, "the ratio never leaves its trend: no spread to read a zone "
                                                       "from", False)
    z = residuals[-1] / sd
    zone = buffett_zone(z)
    trend = [math.exp(f) for f in fitted]
    stats = window_stats(ratios, ratios[-1])
    return {
        "available": True, "reason": None, "collect": None, "method": BUFFETT_METHOD,
        "date": dates[-1], "ratio": ratios[-1], "trend": trend[-1], "growth": math.exp(slope) - 1, "sd": sd, "z": z,
        "zone": zone, "pill": ZONE_PILLS[zone], "percentile": stats["pct_rank"] if stats else None,
        "first_date": dates[0], "quarters": len(dates), "cutoffs": list(BUFFETT_CUTOFFS),
        "zones": list(BUFFETT_ZONES),
        "series": {"dates": dates, "ratio": ratios, "trend": trend,
                   "lower_2sd": [t * math.exp(-2 * sd) for t in trend],
                   "lower_1sd": [t * math.exp(-sd) for t in trend],
                   "upper_1sd": [t * math.exp(sd) for t in trend],
                   "upper_2sd": [t * math.exp(2 * sd) for t in trend]},
    }


def cape_view(series: Series) -> Dict[str, Any]:
    """Shiller's CAPE, monthly from its first value: the latest value and its month, and over the history its mean,
    median, min and max (each with its first month) and the latest value's percentile (``window_stats``; all None
    under its 36 values); the chart's series. Unavailable without the series."""
    rows = series.get(CAPE_SERIES, [])
    if not rows:
        return _unavailable("cape", CAPE_METHOD, "Shiller's CAPE is not stored", True)
    values = [value for _, value in rows]
    latest_date, latest = rows[-1]
    stats = window_stats(values, latest)
    out: Dict[str, Any] = {
        "available": True, "reason": None, "collect": None, "method": CAPE_METHOD, "date": latest_date,
        "value": latest, "first_date": rows[0][0], "months": len(rows), "mean": None, "median": None, "min": None,
        "min_date": None, "max": None, "max_date": None, "percentile": None,
        "series": {"dates": [day for day, _ in rows], "values": values},
    }
    if stats is not None:
        out.update(mean=statistics.fmean(values), median=stats["median"], min=stats["min"], max=stats["max"],
                   min_date=next(day for day, value in rows if value == stats["min"]),
                   max_date=next(day for day, value in rows if value == stats["max"]), percentile=stats["pct_rank"])
    return out


def _curve(stored: Dict[str, Dict[str, float]], day: str) -> List[Dict[str, Any]]:
    """The curve on *day*: each maturity with a yield that day, shortest first."""
    return [{"maturity": maturity, "label": label, "value": stored[maturity][day]}
            for maturity, label in MATURITIES if day in stored.get(maturity, {})]


def latest_episode(days: Sequence[str], inverted: Sequence[bool]) -> Optional[Dict[str, Any]]:
    """The latest sustained inversion of a spread whose *days* are *inverted* (below 0) or not: its runs of days below
    0 joined into episodes across gaps of at most EPISODE_GAP of its days, and of those with at least EPISODE_MIN_DAYS
    days below 0 the latest, as its first and last inverted day and its inverted days (not its span). None without
    one: blips after it make an episode of their own, too short to displace it, unless they join it."""
    episodes: List[List[int]] = []  # each episode's first and last inverted day (indexes) and its inverted days
    for i, below in enumerate(inverted):
        if not below:
            continue
        if episodes and i - episodes[-1][1] - 1 <= EPISODE_GAP:
            episodes[-1][1] = i
            episodes[-1][2] += 1
        else:
            episodes.append([i, i, 1])
    sustained = [episode for episode in episodes if episode[2] >= EPISODE_MIN_DAYS]
    if not sustained:
        return None
    first, last, count = sustained[-1]
    return {"first": days[first], "last": days[last], "days": count}


def _spread(stored: Dict[str, Dict[str, float]], long: str, short: str) -> Dict[str, Any]:
    """The *long* maturity's yield less the *short* one's on each day both have, oldest first, in percentage points:
    its latest value and day, whether it is inverted now, the share of its days inverted, its last inverted day and
    its latest sustained inversion (``latest_episode``); None without days (the last two: without one)."""
    longs, shorts = stored.get(long, {}), stored.get(short, {})
    days = sorted(set(longs) & set(shorts))
    values = [round(longs[day] - shorts[day], SPREAD_DECIMALS) for day in days]
    inverted = [is_inverted(value) for value in values]
    return {
        "key": f"{long}_{short}", "label": f"{MATURITY_LABELS[long]} - {MATURITY_LABELS[short]}", "long": long,
        "short": short, "dates": days, "values": values, "days": len(days),
        "first_date": days[0] if days else None, "latest_date": days[-1] if days else None,
        "latest_value": values[-1] if values else None, "inverted": inverted[-1] if inverted else None,
        "inverted_share": sum(inverted) / len(inverted) if inverted else None,
        "last_inverted_day": next((days[i] for i in range(len(days) - 1, -1, -1) if inverted[i]), None),
        "latest_episode": latest_episode(days, inverted),
    }


def yield_curve_view(series: Series) -> Dict[str, Any]:
    """The Treasury yield curve: the latest day any maturity has, its curve, and the curve of the latest day on or
    before YEAR_AGO_DAYS earlier (None without one); the SPREADS on each day both their yields have. Unavailable
    without any maturity's series."""
    stored = {maturity: dict(series[f"ust:{maturity}"]) for maturity, _ in MATURITIES if series.get(f"ust:{maturity}")}
    if not stored:
        return _unavailable("yield_curve", CURVE_METHOD, "Treasury.gov's yield curve is not stored", True)
    days = sorted({day for yields in stored.values() for day in yields})
    latest = days[-1]
    target = (date.fromisoformat(latest) - timedelta(days=YEAR_AGO_DAYS)).isoformat()
    on_or_before = bisect_right(days, target)
    year_ago = days[on_or_before - 1] if on_or_before else None
    return {
        "available": True, "reason": None, "collect": None, "method": CURVE_METHOD, "date": latest,
        "first_date": days[0], "curve": _curve(stored, latest),
        "year_ago": {"date": year_ago, "curve": _curve(stored, year_ago)} if year_ago else None,
        "spreads": [_spread(stored, long, short) for long, short in SPREADS],
    }


def _sources(sections: Dict[str, Dict[str, Any]], fetches: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Each section's latest date and its sources' latest fetches (url and fetched_at None when not fetched)."""
    return {section: {"latest": sections[section]["date"],
                      "fetches": [{"source": source, "name": name, "url": fetches.get(source, {}).get("url"),
                                   "fetched_at": fetches.get(source, {}).get("fetched_at")}
                                  for source, name in sources]}
            for section, sources in SECTION_SOURCES.items()}


def market_view(r: Reader) -> Dict[str, Any]:
    """The market page's data, as ``GET /api/market`` returns it: whether any section is available, the three
    sections ("buffett", "cape", "yield_curve") and each one's sources."""
    series = r.market_series([EQUITIES_SERIES, GDP_SERIES, CAPE_SERIES] + [f"ust:{m}" for m, _ in MATURITIES])
    sections = {"buffett": buffett_view(series), "cape": cape_view(series), "yield_curve": yield_curve_view(series)}
    return {"available": any(section["available"] for section in sections.values()), **sections,
            "sources": _sources(sections, r.market_fetches())}

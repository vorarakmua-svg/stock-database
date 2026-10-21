"""The valuation history (Milestone 1): each month's multiples, their trailing 10-year ranges, and a
historical fair value drawn against the price.

Multiples are taken on totals, with market cap = close x PF(d) x shares (``history_inputs``). Each
month reads only the fundamentals known by then, and each window only the months up to it, so the
line never looks ahead. The one exception is restatements: the stored quarters are the latest
restated figures. See docs/superpowers/specs/2026-10-21-valuation-history-design.md.
"""

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .history_inputs import STALE_DAYS, HistoryInputs, TTMPoint, price_factor_after

METRICS: Tuple[str, ...] = ("pe", "ps", "pb", "pfcf", "ev_ebitda", "pffo", "div_yield")
WINDOW_YEARS = 10
#: A metric's range needs this many monthly values in its window: three years.
MIN_MONTHS = 36
#: The historical value combines at least this many metrics.
MIN_FV_METRICS = 2
#: The labels of price / historical value: "far below" under 0.7 <= "below" < 0.9 <= "near" <= 1.1 < "above"
#: <= 1.3 < "far above". These cutoffs were approved with the plan (2026-10-21).
HISTORICAL_VALUE_CUTOFFS: Tuple[float, float, float, float] = (0.7, 0.9, 1.1, 1.3)

#: The total each multiple divides the market cap (or, for EV/EBITDA, the enterprise value) by.
_FUNDAMENTAL: Dict[str, str] = {"pe": "net_income", "ps": "revenue", "pb": "common_equity", "pfcf": "fcf",
                                "pffo": "ffo", "ev_ebitda": "ebitda"}
_LABEL: Dict[str, str] = {"net_income": "net income", "revenue": "revenue", "common_equity": "common equity",
                          "fcf": "free cash flow", "ffo": "FFO", "ebitda": "EBITDA"}
_GENERAL: Tuple[str, ...] = ("pe", "ps", "pb", "pfcf", "ev_ebitda")
#: The price multiples the sector rules don't suppress (``sector_metrics.SUPPRESSED_BY_SECTOR``). REITs
#: take P/FFO for P/E, as the multiples band does.
_FAIR_VALUE_METRICS: Dict[str, Tuple[str, ...]] = {
    "bank": ("pe", "ps", "pb"),
    "insurance": ("pe", "ps", "pb", "pfcf"),
    "reit": ("pffo", "ps", "pb", "ev_ebitda"),
}
#: The multiples the sector rules leave without a fundamental.
_NOT_MEANINGFUL: Dict[str, Tuple[str, ...]] = {"bank": ("pfcf", "ev_ebitda"), "insurance": ("ev_ebitda",),
                                               "reit": ("pfcf",)}
_SECTOR_NAMES: Dict[str, str] = {"bank": "banks", "insurance": "insurers", "reit": "REITs"}


@dataclass
class HistoryRow:
    """One month: the latest bar's close, the trailing year known then, and the multiples on them.

    The fair values are per share on the bars' basis, so they overlay the closes.
    """

    date: str
    close: float
    market_cap: Optional[float] = None
    enterprise_value: Optional[float] = None
    ttm_period_end: Optional[str] = None
    multiples: Dict[str, Optional[float]] = field(default_factory=dict)
    fair_value: Optional[float] = None
    fair_value_low: Optional[float] = None
    fair_value_high: Optional[float] = None
    fair_value_n: int = 0


@dataclass
class MetricRange:
    """A metric's trailing window at the latest month, or the historical value's row (metric "fair_value").

    The fair-value row's current, p25, p75 and the implied values are on the latest quote's price basis.
    """

    metric: str
    applicable: bool = False
    na_reason: Optional[str] = None
    as_of: Optional[str] = None
    window_start: Optional[str] = None
    n_months: int = 0
    current: Optional[float] = None
    pct_rank: Optional[float] = None
    min: Optional[float] = None
    p25: Optional[float] = None
    median: Optional[float] = None
    p75: Optional[float] = None
    max: Optional[float] = None
    implied_value: Optional[float] = None
    in_fair_value: bool = False
    n_metrics: Optional[int] = None


@dataclass
class HistoryResult:
    """The monthly series and the ranges at its latest month; *na_reason* when there's no history."""

    ticker: str
    rows: List[HistoryRow] = field(default_factory=list)
    ranges: List[MetricRange] = field(default_factory=list)
    na_reason: Optional[str] = None


def fair_value_metrics(sector: str) -> Tuple[str, ...]:
    """The multiples a sector's historical value combines."""
    return _FAIR_VALUE_METRICS.get(sector, _GENERAL)


def multiples_at(market_cap: float, close: float, point: TTMPoint, ttm_dps: float) -> Dict[str, Optional[float]]:
    """Every multiple on one day's market cap and trailing year. A zero or negative total gives None."""
    m: Dict[str, Optional[float]] = {k: None for k in METRICS}
    if market_cap > 0:
        for metric in ("pe", "ps", "pb", "pfcf", "pffo"):
            base = getattr(point, _FUNDAMENTAL[metric])
            if base is not None and base > 0:
                m[metric] = market_cap / base
        ev = market_cap + (point.net_debt or 0.0)
        if point.ebitda is not None and point.ebitda > 0 and ev > 0 and not point.debt_missing:
            m["ev_ebitda"] = ev / point.ebitda
    if ttm_dps and ttm_dps > 0 and close > 0:
        m["div_yield"] = ttm_dps / close
    return m


def window_stats(values: Sequence[Optional[float]], current: Optional[float]) -> Optional[Dict[str, Any]]:
    """min, p25, median, p75, max (inclusive quartiles), n, and the current value's percentile rank.

    The rank is the share of the window's values at or below the current value. The window needs
    MIN_MONTHS values, or there are no statistics.
    """
    data = sorted(v for v in values if v is not None)
    if len(data) < MIN_MONTHS:
        return None
    q1, q2, q3 = statistics.quantiles(data, n=4, method="inclusive")
    rank = sum(1 for v in data if v <= current) / len(data) if current is not None else None
    return {"n": len(data), "min": data[0], "p25": q1, "median": q2, "p75": q3, "max": data[-1], "pct_rank": rank}


def implied_price(metric: str, multiple: Optional[float], point: TTMPoint,
                  shares_bars: Optional[float]) -> Optional[float]:
    """The price per share a multiple implies on a trailing year: multiple x fundamental / shares.

    For EV/EBITDA it is (multiple x EBITDA - net debt) / shares. *shares_bars* is the count times PF,
    so the price is on the bars' basis. A zero or negative fundamental, or implied value, gives None.
    """
    if multiple is None or not shares_bars or shares_bars <= 0 or (metric == "ev_ebitda" and point.debt_missing):
        return None
    base = getattr(point, _FUNDAMENTAL[metric])
    if base is None or base <= 0:
        return None
    value = multiple * base - ((point.net_debt or 0.0) if metric == "ev_ebitda" else 0.0)
    return value / shares_bars if value > 0 else None


def historical_value_label(price: Optional[float], fair_value: Optional[float]) -> Optional[str]:
    """Where a price sits against the historical value, by HISTORICAL_VALUE_CUTOFFS."""
    if price is None or fair_value is None or price <= 0 or fair_value <= 0:
        return None
    ratio = price / fair_value
    far_below, below, above, far_above = HISTORICAL_VALUE_CUTOFFS
    if ratio < far_below:
        return "far below"
    if ratio < below:
        return "below"
    if ratio <= above:
        return "near"
    if ratio <= far_above:
        return "above"
    return "far above"


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # Feb 29 in a year without one
        return day.replace(year=day.year - years, day=28)


def _known_point(points: Sequence[TTMPoint], day: date) -> Optional[TTMPoint]:
    """The latest trailing year known on *day*."""
    for point in reversed(points):
        if point.known_on <= day:
            return point
    return None


def compute_history(inputs: HistoryInputs) -> HistoryResult:
    """The monthly series of multiples and historical values, and every metric's range at the latest month."""
    result = HistoryResult(ticker=inputs.ticker)
    if inputs.na_reason or not inputs.months:
        reason = inputs.na_reason or "no price history"
        result.na_reason = reason
        result.ranges = [MetricRange(metric=m, na_reason=reason) for m in METRICS + ("fair_value",)]
        return result
    sector = inputs.sector_class or "general"
    combined = fair_value_metrics(sector)
    off = set(_NOT_MEANINGFUL.get(sector, ())) | ({"pffo"} if sector != "reit" else set())
    points = sorted(inputs.points, key=lambda p: p.period_end)
    dividends = sorted((str(d)[:10], a) for d, a in inputs.dividends)
    last_bar = inputs.last_bar or inputs.months[-1][0]
    days = [date.fromisoformat(str(d)[:10]) for d, _ in inputs.months]

    # Each month's multiples on the trailing year known then, unless it has gone stale.
    rows: List[HistoryRow] = []
    used: List[Optional[TTMPoint]] = []
    shares_bars: List[Optional[float]] = []
    latest_gap: Optional[str] = None
    for (day_s, close), day in zip(inputs.months, days):
        row = HistoryRow(date=str(day_s)[:10], close=close, multiples={k: None for k in METRICS})
        point = _known_point(points, day)
        if point is not None and (day - point.known_on).days > STALE_DAYS:
            latest_gap = (f"the latest trailing year known ({point.period_end}) is "
                          f"{(day - point.known_on).days} days old")
            point = None
        elif point is None:
            latest_gap = "no trailing year known yet"
        else:
            latest_gap = None
        lo = (day - timedelta(days=365)).isoformat()
        dps = sum(a for d, a in dividends if lo < d <= row.date)
        count = None
        if point is not None and point.shares:
            count = point.shares * price_factor_after(row.date, inputs.price_factors, last_bar)
            row.market_cap = close * count
            row.ttm_period_end = point.period_end
            if point.net_debt is not None:
                row.enterprise_value = row.market_cap + point.net_debt
            row.multiples = multiples_at(row.market_cap, close, point, dps)
            for k in off:
                row.multiples[k] = None
        elif point is not None:
            latest_gap = f"no share count for the trailing year to {point.period_end}"
        if dps > 0 and close > 0:
            row.multiples["div_yield"] = dps / close
        rows.append(row)
        used.append(point if count else None)
        shares_bars.append(count)

    # The historical value: each combined metric's trailing-window quartiles applied to the month's year.
    series = {m: [r.multiples.get(m) for r in rows] for m in METRICS}
    left = 0
    latest_implied: Dict[str, Tuple[Optional[float], Optional[float], Optional[float]]] = {}
    latest_left = 0
    for i, row in enumerate(rows):
        start = _years_before(days[i], WINDOW_YEARS)
        while days[left] <= start:
            left += 1
        point = used[i]
        if i == len(rows) - 1:
            latest_left = left
        if point is None:
            continue
        mids: List[float] = []
        lows: List[float] = []
        highs: List[float] = []
        for metric in combined:
            stats = window_stats(series[metric][left:i + 1], None)
            if stats is None:
                continue
            mid = implied_price(metric, stats["median"], point, shares_bars[i])
            low = implied_price(metric, stats["p25"], point, shares_bars[i])
            high = implied_price(metric, stats["p75"], point, shares_bars[i])
            if i == len(rows) - 1:
                latest_implied[metric] = (mid, low, high)
            if mid is None:
                continue
            mids.append(mid)
            if low is not None:
                lows.append(low)
            if high is not None:
                highs.append(high)
        if len(mids) >= MIN_FV_METRICS:
            row.fair_value = statistics.median(mids)
            row.fair_value_low = statistics.median(lows) if lows else None
            row.fair_value_high = statistics.median(highs) if highs else None
            row.fair_value_n = len(mids)

    # The ranges at the latest month.
    latest = rows[-1]
    to_price = inputs.bars_to_price or 1.0
    window = range(latest_left, len(rows))
    for metric in METRICS:
        r = MetricRange(metric=metric, as_of=latest.date)
        result.ranges.append(r)
        if metric in off:
            r.na_reason = "REITs only" if metric == "pffo" else f"not meaningful for {_SECTOR_NAMES[sector]}"
            continue
        dated = [(rows[j].date, series[metric][j]) for j in window if series[metric][j] is not None]
        r.current = latest.multiples.get(metric)
        r.n_months = len(dated)
        r.window_start = dated[0][0] if dated else None
        stats = window_stats([v for _, v in dated], r.current)
        if stats is None:
            r.na_reason = (f"{len(dated)} months of values, fewer than {MIN_MONTHS}" if dated else "no values")
        else:
            r.applicable = True
            r.min, r.p25, r.median = stats["min"], stats["p25"], stats["median"]
            r.p75, r.max, r.pct_rank = stats["p75"], stats["max"], stats["pct_rank"]
        if r.current is None and r.na_reason is None:
            r.na_reason = latest_gap or _no_multiple_reason(metric, used[-1])
        implied = latest_implied.get(metric)
        if implied is not None and implied[0] is not None:
            r.implied_value = implied[0] * to_price
            r.in_fair_value = latest.fair_value is not None

    fv = MetricRange(metric="fair_value", as_of=latest.date)
    result.ranges.append(fv)
    with_value = [rows[j] for j in window if rows[j].fair_value is not None]
    fv.n_months = len(with_value)
    fv.window_start = with_value[0].date if with_value else None
    if latest.fair_value is not None:
        fv.applicable = True
        fv.current = latest.fair_value * to_price
        fv.p25 = latest.fair_value_low * to_price if latest.fair_value_low is not None else None
        fv.p75 = latest.fair_value_high * to_price if latest.fair_value_high is not None else None
        fv.n_metrics = latest.fair_value_n
    else:
        have = sum(1 for v in latest_implied.values() if v[0] is not None)
        fv.na_reason = latest_gap or (f"only {have} metric{'' if have == 1 else 's'} with {MIN_MONTHS} months "
                                      f"of values; {MIN_FV_METRICS} needed")
    # A month before the first market cap and before the latest window holds nothing the history uses.
    first_cap = next((i for i, r in enumerate(rows) if r.market_cap is not None), len(rows))
    result.rows = rows[min(first_cap, latest_left):]
    return result


def _no_multiple_reason(metric: str, point: Optional[TTMPoint]) -> str:
    if metric == "div_yield":
        return "no dividends in the trailing year"
    name = _FUNDAMENTAL[metric]
    value = getattr(point, name) if point is not None else None
    label = _LABEL[name]
    return f"no {label} in the trailing year" if value is None else f"{label} is zero or negative"

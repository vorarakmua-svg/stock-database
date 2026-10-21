"""HistoryInputs: what the valuation history reads for one ticker (Milestone 1).

Pure reads over the SQLite schema, like ``inputs.load_inputs``:
- the month-end closes;
- each trailing twelve months' fundamentals, with the day they became known;
- a share count per quarter, on the price bars' split basis.

Every multiple is then taken on totals, with market cap = close x PF(d) x shares. That holds across
splits and spin-offs, where dividing a price by a filed EPS doesn't, since filed counts sit on mixed
split bases. See docs/superpowers/specs/2026-10-21-valuation-history-design.md.
"""

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..parsers.calculated_metrics import CalculatedMetrics
from .inputs import (
    DECLARED_SPLIT_TOLERANCE,
    MAX_SPLIT_RATIO,
    SPLIT_JUMP_THRESHOLD,
    _dividend_onto,
    _drop_spin_off_dividends,
    _simple_ratio,
    _split_marks,
    _yahoo_dividend_factors,
    bars_to_price_factor,
)

#: A quarter's 10-Q is known this many days after the quarter ends (the deadline is 40 or 45 days).
QUARTER_LAG_DAYS = 45
#: A fiscal year's fourth quarter is known on its 10-K's first filing day, if the vintages have one within
#: Q4_FILING_WINDOW_DAYS of the year end (the stored 10-Ks: median 51 days, 99th percentile 62); else after
#: Q4_LAG_DAYS.
Q4_LAG_DAYS = 75
Q4_FILING_WINDOW_DAYS = 120
#: Fundamentals known for more than this many days at a date are stale there: a quarter or more was missed.
STALE_DAYS = 200
#: A share field must cover this share of the latest RECENT_QUARTERS quarters to be the ticker's: the
#: quarters the 10-year window reads. PRU files weighted counts only from 2016, 57% of all its quarters.
SHARE_FIELD_COVERAGE = 0.6
RECENT_QUARTERS = 40
#: A count from net income / diluted EPS needs EPS of at least this much: EPS is filed to the cent, so a
#: smaller one is up to 5% off or more (PRU's 2009 Q1: $14M over $0.01).
MIN_EPS_FOR_COUNT = 0.10
#: A quarter whose count moves more than SPIKE_MOVE and comes back within SPIKE_RETURN is a glitch.
SPIKE_MOVE = 0.15
SPIKE_RETURN = 0.05
#: Our latest count and Yahoo's (market cap / price) this far apart means another class or units (Q7).
YAHOO_SHARE_GAP = 1.25
#: The stored quarters are the latest restated figures, and later filings restate the quarters before a
#: split for up to three years, so a split's seam can sit up to three years and a filing's lag before it
#: (AAPL's 4:1 of 2020-08-31 shows at 2017-12-30, NFLX's 10:1 of 2025-11-17 at 2023-03-31): a seam matches
#: a declared split dated after its older quarter and no more than this many days after its newer one.
#: Unbounded, an older seam would take a split after the latest quarter (APH's 2026-09-03; BUG-174).
SEAM_RESTATEMENT_DAYS = 3 * 365 + 120
#: The listing's quarter, the first one ending on or after the first bar and within this many days of it (a
#: 14-week quarter), averages the private company's count with the public one: preferred stock converting,
#: shares sold (UBER's 2019 Q2: 1.11B against 1.70B after). An undeclared seam right after it is the
#: listing's, and the public count applies back to it, as to every bar.
LISTING_QUARTER_DAYS = 98
#: A quarter ending within this many days of a fiscal year's end is its fourth (the 52/53-week years).
YEAR_END_DAYS = 7

SHARE_FIELDS = ("weighted_avg_shares_basic", "shares_outstanding", "weighted_avg_shares_diluted")
NI_OVER_EPS = "net_income/eps_diluted"


@dataclass
class TTMPoint:
    """One trailing twelve months, ending on *period_end*, as known from *known_on*.

    The share count is the quarter's own, on the bars' split basis. The rest are totals, so splits
    don't touch them.
    """

    period_end: str
    known_on: date
    shares: Optional[float]
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    common_equity: Optional[float] = None
    fcf: Optional[float] = None
    ebitda: Optional[float] = None
    net_debt: Optional[float] = None
    ffo: Optional[float] = None
    #: The quarter lacks the long-term debt its company's 10-Ks report: no net debt, so no enterprise value.
    debt_missing: bool = False


@dataclass
class HistoryInputs:
    """Everything the valuation history needs for one ticker; *na_reason* says why it can't run."""

    ticker: str
    sector_class: str = "general"
    #: (day, close) for each month's last bar, on the bars' basis. The last one is the latest bar.
    months: List[Tuple[str, float]] = field(default_factory=list)
    #: Yahoo's price factors that aren't share splits (spin-offs, stock dividends), as (day, ratio).
    price_factors: List[Tuple[str, float]] = field(default_factory=list)
    last_bar: Optional[str] = None
    points: List[TTMPoint] = field(default_factory=list)
    #: Cash dividends as (ex-date, amount), on the bars' basis.
    dividends: List[Tuple[str, float]] = field(default_factory=list)
    share_field: Optional[str] = None
    splits_applied: List[Tuple[str, float]] = field(default_factory=list)
    #: Yahoo's count of every class (market cap / price), on the bars' basis.
    yahoo_shares: Optional[float] = None
    current_price: Optional[float] = None
    priced_on: Optional[str] = None
    #: Puts a bar's close onto the latest snapshot price's basis (``inputs.bars_to_price_factor``).
    bars_to_price: float = 1.0
    collected_on: Optional[date] = None
    na_reason: Optional[str] = None


def is_share_split(ratio: Optional[float]) -> bool:
    """Whether a ratio in Yahoo's split history is a share split (2:1, 3:2, 1:8...).

    The history also holds spin-off price factors and stock dividends (GE's 1.281 for GE HealthCare,
    1.04). Those move the price but not the filed share counts.
    """
    if ratio is None or ratio <= 0 or abs(ratio - 1.0) < 1e-9:
        return False
    return _simple_ratio(ratio)


def price_factor_after(day: str, events: Sequence[Tuple[str, float]], last_bar: str) -> float:
    """PF(d): the product of the factors in *events* that aren't share splits, dated in (*day*, *last_bar*].

    Yahoo divides every close before such a factor's day by it, so a bar's close times PF is the price
    it traded at, on the bars' split basis. A factor after the last bar isn't in the bars yet.
    """
    d, last = str(day)[:10], str(last_bar)[:10]
    return float(math.prod(r for when, r in events
                           if r and r > 0 and not is_share_split(r) and d < str(when)[:10] <= last))


def _day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def known_on(period_end: str, fiscal_quarter: Optional[int], first_annual_filed: Optional[str],
             collected_on: Optional[date]) -> date:
    """The day a quarter's trailing year became known.

    No quarter stores its filing date, so a lag rule stands in:
    - a fiscal fourth quarter is known on its 10-K's first filing day, if that is within
      Q4_FILING_WINDOW_DAYS of the year end, and otherwise after Q4_LAG_DAYS;
    - any other quarter is known after QUARTER_LAG_DAYS.

    The result is capped at *collected_on*: what was stored was known by the day it was collected.
    """
    end = date.fromisoformat(str(period_end)[:10])
    known = end + timedelta(days=QUARTER_LAG_DAYS)
    if fiscal_quarter == 4:
        known = end + timedelta(days=Q4_LAG_DAYS)
        filed = _day(first_annual_filed)
        if filed is not None and end < filed <= end + timedelta(days=Q4_FILING_WINDOW_DAYS):
            known = filed
    if collected_on is not None and known > collected_on:
        known = collected_on
    return known


def _share_count(row: Mapping[str, Any], name: str) -> Optional[float]:
    if name == NI_OVER_EPS:
        ni, eps = row.get("net_income"), row.get("eps_diluted")
        return abs(ni / eps) if ni is not None and eps and abs(eps) >= MIN_EPS_FOR_COUNT else None
    value = row.get(name)
    return float(value) if value is not None and value > 0 else None


def pick_share_field(rows: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """The ticker's one share field: the first covering at least SHARE_FIELD_COVERAGE of the latest
    RECENT_QUARTERS quarters (*rows* oldest first).

    The order is weighted basic, shares outstanding, weighted diluted, then net income / diluted
    EPS. Diluted counts flip in loss quarters, when anti-dilution leaves them basic (BX, PANW).
    """
    recent = list(rows)[-RECENT_QUARTERS:]
    if not recent:
        return None
    for name in SHARE_FIELDS + (NI_OVER_EPS,):
        have = sum(1 for r in recent if _share_count(r, name) is not None)
        if have / len(recent) >= SHARE_FIELD_COVERAGE - 1e-9:
            return name
    return None


def normalize_share_series(ends: Sequence[str], counts: Sequence[Optional[float]],
                           eps: Sequence[Optional[float]], splits: Sequence[Tuple[str, float]],
                           yahoo_count: Optional[float] = None,
                           revenue: Optional[Sequence[Optional[float]]] = None,
                           listed: Optional[str] = None,
                           ) -> Tuple[List[Optional[float]], List[Tuple[str, float]]]:
    """Every quarter's share count on the bars' split basis, today's, as Yahoo's closes are.

    Filings restate only the quarters around a split, so a stored series switches basis at a seam,
    not at the split itself. These are the rules of ``inputs._normalize_splits``, quarter by quarter,
    walking newest to oldest:
    - A count that moves more than SPIKE_MOVE and comes back within SPIKE_RETURN is a glitch, and is
      dropped.
    - A seam within DECLARED_SPLIT_TOLERANCE of a declared split, or run of splits, dated after the
      older quarter (and filed by the newer one), and nearer that run than 1.0, folds the run in.
      Each declared split explains one seam.
    - An undeclared seam of SPLIT_JUMP_THRESHOLD or more folds by its own ratio after the quarter of the
      listing on *listed* (LISTING_QUARTER_DAYS), or on a split's marks in EPS and *revenue*
      (SPLIT_EVIDENCE_SHARE). Otherwise it stays organic, as a merger or an exchange offer does.
    - A seam beyond MAX_SPLIT_RATIO is a change of units: the older quarters are dropped.

    A gap then carries the count before it. A split after the latest quarter, which no stored quarter
    reflects, applies only when it brings our latest count nearer *yahoo_count* (on the bars' basis).
    Returns the counts and the splits applied.
    """
    n = len(counts)
    raw: List[Optional[float]] = [c if c is not None and c > 0 else None for c in counts]
    for i in range(1, n - 1):
        a, b, c = raw[i - 1], raw[i], raw[i + 1]
        if a and b and c and abs(b / a - 1.0) > SPIKE_MOVE and abs(c / a - 1.0) <= SPIKE_RETURN:
            raw[i] = None
    out: List[Optional[float]] = [None] * n
    applied: List[Tuple[str, float]] = []
    valid = [i for i in range(n) if raw[i] is not None]
    if not valid:
        return out, applied
    unused = [(str(when)[:10], ratio) for when, ratio in splits if ratio and ratio > 0]
    factor = 1.0
    newest = raw[valid[-1]]
    out[valid[-1]] = newest
    first = 0
    for k in range(len(valid) - 2, -1, -1):
        i, j = valid[k], valid[k + 1]
        older, newer = raw[i], raw[j]
        assert older is not None and newer is not None
        ratio = newer / older
        if ratio > MAX_SPLIT_RATIO or ratio < 1.0 / MAX_SPLIT_RATIO:
            first = j  # a change of units: nothing older compares
            break
        horizon = (date.fromisoformat(str(ends[j])[:10]) + timedelta(days=SEAM_RESTATEMENT_DAYS)).isoformat()
        run = _seam_run(ratio, ends[i], [s for s in unused if s[0] <= horizon])
        run_ratio = math.prod(r for _, r in run) if run else 1.0
        if run and abs(math.log(ratio / run_ratio)) < abs(math.log(ratio)):
            unused = [s for s in unused if s not in run]
            factor *= run_ratio
            applied.extend(run)
        elif ratio >= SPLIT_JUMP_THRESHOLD or ratio <= 1.0 / SPLIT_JUMP_THRESHOLD:
            listing = (i == 0 and listed is not None and (date.fromisoformat(str(ends[0])[:10])
                       - timedelta(days=LISTING_QUARTER_DAYS)).isoformat() < listed[:10])
            if listing or _split_marks(ratio, eps[i], eps[j], revenue[i] if revenue else None,
                                       revenue[j] if revenue else None):
                factor *= ratio
        out[i] = older * factor
    carried: Optional[float] = None
    for i in range(first, n):
        if out[i] is None:
            out[i] = carried
        else:
            carried = out[i]
    later = [s for s in unused if s[0] > str(ends[-1])[:10]]
    latest = next((c for c in reversed(out) if c), None)
    if later and latest and yahoo_count:
        moved = math.prod(r for _, r in later)
        if abs(math.log(latest * moved / yahoo_count)) < abs(math.log(latest / yahoo_count)):
            out = [c * moved if c is not None else None for c in out]
            applied.extend(later)
    return out, sorted(applied)


def _seam_run(observed: float, after: str, splits: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """The run of consecutive declared splits dated after *after* whose ratio is nearest the seam's, within
    DECLARED_SPLIT_TOLERANCE, the earliest winning a tie: ``inputs._declared_run`` with ties going to the split
    nearest the seam, so a later split of the same ratio stays for the seam it made (or for after the latest
    quarter)."""
    later = sorted((when, ratio) for when, ratio in splits if ratio and ratio > 0 and when > after[:10])
    best: List[Tuple[str, float]] = []
    best_gap = math.log(DECLARED_SPLIT_TOLERANCE)
    for start in range(len(later)):
        run = 1.0
        for end in range(start, len(later)):
            run *= later[end][1]
            gap = abs(math.log(observed / run))
            if gap < best_gap or (gap == best_gap and not best):
                best, best_gap = later[start:end + 1], gap
    return best


def _rows(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    cur = conn.execute(sql, tuple(params))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _optional_rows(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Rows from a table a database from before it was kept lacks (vintages, collection runs)."""
    try:
        return _rows(conn, sql, params)
    except sqlite3.OperationalError:
        return []


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _near_year_end(end: str, year_ends: Sequence[date]) -> bool:
    day = _day(end)
    return day is not None and any(abs((day - y).days) <= YEAR_END_DAYS for y in year_ends)


def load_history_inputs(conn: sqlite3.Connection, ticker: str) -> HistoryInputs:
    """Read the history's inputs for *ticker*. Its ``na_reason`` is set when the history can't run."""
    x = HistoryInputs(ticker=ticker)
    company = _rows(conn, "SELECT sector_class FROM companies WHERE ticker = ?", (ticker,))
    if not company:
        x.na_reason = "no company record"
        return x
    x.sector_class = company[0]["sector_class"] or "general"
    ttm = _rows(conn, "SELECT * FROM financials_ttm WHERE ticker = ? AND period_end IS NOT NULL "
                "ORDER BY period_end", (ticker,))
    if not ttm:
        x.na_reason = "no trailing-twelve-month financials"
        return x
    splits = [(str(r["date"])[:10], float(r["ratio"])) for r in _rows(
        conn, "SELECT date, ratio FROM split_events WHERE ticker = ? AND ratio > 0 ORDER BY date", (ticker,))]
    x.price_factors = [(when, ratio) for when, ratio in splits if not is_share_split(ratio)]

    # Each month's last bar. A bar from yfinance's default history is dividend-adjusted (BUG-075): one
    # flagged so, or any bar of a database from before the flag, goes back to the market close, as
    # load_inputs does for the fiscal year ends.
    flagged = "dividend_adjusted" in _columns(conn, "price_bars")
    month_ends: Dict[str, Dict[str, Any]] = {}
    first_bar: Optional[str] = None
    for bar in _rows(conn, "SELECT date, close" + (", dividend_adjusted" if flagged else "") + " FROM price_bars "
                     "WHERE ticker = ? AND close IS NOT NULL ORDER BY date", (ticker,)):
        month_ends[str(bar["date"])[:7]] = bar
        first_bar = first_bar or str(bar["date"])[:10]
    if not month_ends or first_bar is None:
        x.na_reason = "no price history"
        return x
    # A quarter that ended before the first bar is from before the listing: its share count is the pre-IPO
    # base (UBER's 454M in 2019 Q1 against 1.7B after), and no price met it.
    ttm = [row for row in ttm if str(row["period_end"])[:10] >= first_bar]
    if not ttm:
        x.na_reason = "no trailing-twelve-month financials since the listing"
        return x
    factors: Optional[List[Tuple[str, float]]] = None
    for month in sorted(month_ends):
        bar = month_ends[month]
        day, close = str(bar["date"])[:10], float(bar["close"])
        if not flagged or bar.get("dividend_adjusted"):
            if factors is None:
                factors = _yahoo_dividend_factors(conn, ticker, flagged, splits)
            close /= math.prod(f for ex, f in factors if ex > day)
        x.months.append((day, close))
    x.last_bar = x.months[-1][0]

    # The latest snapshot with a price: a later run whose quote failed leaves a row without price, market cap
    # or count, and reading it would switch off the share guard and the later-split confirmation (BUG-176).
    snaps = _rows(conn, "SELECT * FROM market_snapshots WHERE ticker = ? AND current_price IS NOT NULL "
                  "ORDER BY collected_at DESC LIMIT 1", (ticker,))
    snap = snaps[0] if snaps else {}
    x.current_price = snap.get("current_price")
    x.priced_on = (str(snap["collected_at"])[:10]
                   if snap.get("current_price") is not None and snap.get("collected_at") else None)
    x.bars_to_price = bars_to_price_factor(splits, x.priced_on, x.last_bar)
    if snap.get("market_cap") and snap.get("current_price"):
        x.yahoo_shares = snap["market_cap"] / snap["current_price"] * x.bars_to_price
    elif snap.get("shares_outstanding"):
        x.yahoo_shares = snap["shares_outstanding"] * x.bars_to_price
    runs = _optional_rows(conn, "SELECT max(collected_at) AS c FROM collection_runs WHERE ticker = ?", (ticker,))
    x.collected_on = _day((runs[0]["c"] if runs else None) or snap.get("collected_at") or x.last_bar)

    served_column = "served_on" in _columns(conn, "dividend_events")
    payments = _rows(conn, "SELECT date, amount, " + ("served_on" if served_column else "NULL AS served_on")
                     + " FROM dividend_events WHERE ticker = ? AND amount > 0 ORDER BY date", (ticker,))
    served_on = {str(p["date"])[:10]: p["served_on"] for p in payments}
    cash = _drop_spin_off_dividends([(str(p["date"])[:10], float(p["amount"])) for p in payments], splits)
    x.dividends = [(day, amount * _dividend_onto(splits, x.last_bar, served_on.get(day))) for day, amount in cash]

    quarters = _rows(conn, "SELECT * FROM financials_quarterly WHERE ticker = ? AND period_end >= ? "
                     "ORDER BY period_end", (ticker, first_bar))
    x.share_field = pick_share_field(quarters)
    if x.share_field is None:
        x.na_reason = "no share counts in the quarterly financials"
        return x
    ends = [str(q["period_end"])[:10] for q in quarters]
    share_splits = [(when, ratio) for when, ratio in splits if is_share_split(ratio) and when <= x.last_bar]
    counts, x.splits_applied = normalize_share_series(
        ends, [_share_count(q, x.share_field) for q in quarters], [q.get("eps_diluted") for q in quarters],
        share_splits, x.yahoo_shares, revenue=[q.get("revenue") for q in quarters], listed=first_bar)
    shares_at = dict(zip(ends, counts))

    first_filed: Dict[int, str] = {}
    for r in _optional_rows(
            conn, "SELECT fiscal_year, MIN(filed_date) AS filed FROM financials_annual_vintages WHERE ticker = ? "
            "AND filed_date IS NOT NULL AND (form LIKE '10-K%' OR form LIKE '20-F%' OR form LIKE '40-F%') "
            "GROUP BY fiscal_year", (ticker,)):
        if r["fiscal_year"] is not None:
            first_filed[int(r["fiscal_year"])] = str(r["filed"])[:10]
    annual = _rows(conn, "SELECT period_end, long_term_debt FROM financials_annual WHERE ticker = ? "
                   "AND period_end IS NOT NULL", (ticker,))
    year_ends = [d for d in (_day(r["period_end"]) for r in annual) if d is not None]
    # A company whose 10-Ks report long-term debt but whose 10-Q lacks it (TMUS's $81B in 2026's Q1 and Q2)
    # would read a net debt short by all of it: unknown, then, rather than wrong.
    files_long_term_debt = any((r["long_term_debt"] or 0) > 0 for r in annual)
    by_end = {str(q["period_end"])[:10]: q for q in quarters}
    calc = CalculatedMetrics()
    for row in ttm:
        end = str(row["period_end"])[:10]
        quarter = by_end.get(end, {})
        fiscal_quarter = quarter.get("fiscal_quarter")
        if fiscal_quarter is None and _near_year_end(end, year_ends):
            fiscal_quarter = 4
        fiscal_year = row.get("fiscal_year") or quarter.get("fiscal_year")
        metrics = calc.calculate_all(row, sector=x.sector_class)
        equity = row.get("total_equity")
        debt_missing = files_long_term_debt and row.get("long_term_debt") is None
        x.points.append(TTMPoint(
            period_end=end,
            known_on=known_on(end, fiscal_quarter,
                              first_filed.get(int(fiscal_year)) if fiscal_year is not None else None,
                              x.collected_on),
            shares=shares_at.get(end),
            revenue=row.get("revenue"),
            net_income=row.get("net_income"),
            common_equity=equity - (row.get("preferred_equity") or 0.0) if equity is not None else None,
            # Operating cash flow alone isn't free cash flow: without capex there is none.
            fcf=metrics.get("free_cash_flow") if row.get("capex") is not None else None,
            ebitda=metrics.get("ebitda"),
            net_debt=None if debt_missing else metrics.get("net_debt"),
            ffo=metrics.get("ffo"),
            debt_missing=debt_missing,
        ))

    latest = next((c for c in reversed(counts) if c), None)
    if not x.yahoo_shares:
        x.na_reason = "no quote with a price to check the share count against"
    elif latest:
        gap = max(latest, x.yahoo_shares) / min(latest, x.yahoo_shares)
        if gap > YAHOO_SHARE_GAP:
            x.na_reason = (f"our share count ({latest / 1e6:,.0f}M) and Yahoo's ({x.yahoo_shares / 1e6:,.0f}M) "
                           f"differ {gap:.2f}x: another share class or units")
    return x

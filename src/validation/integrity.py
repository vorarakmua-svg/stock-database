"""Integrity checks over standardized financials and computed metrics.

Catches confidently-wrong values that per-period validation in ``quality.py``
cannot: magnitude outliers vs a field's own history, cash-flow statements that
don't reconcile to the change in balance-sheet cash, discrete quarters that
don't sum to the annual figure, metrics outside plausible bounds, and Yahoo
dividend histories missing payments that SEC filings declare. Every check is
FLAG-ONLY — it emits Findings and never mutates data.
"""

import statistics
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..mappings.canonical import CANONICAL_FIELDS, CASHFLOW, DURATION, INCOME, UNIT_USD
from .quality import HIGH, LOW, MEDIUM, Finding, _num

# Ignore sub-$1M figures (rounding/noise) across all checks.
_MATERIALITY = 1_000_000.0

# Event-driven / lumpy flows: financing transactions, buybacks, M&A, one-time charges.
# Too noisy for the magnitude-outlier and quarterly-sum consistency checks (they spike in a
# single year and legitimately don't reconcile quarter-to-annual), but fully captured.
_EVENT_DRIVEN_FLOWS = frozenset({
    "debt_issued", "debt_repaid", "share_repurchases", "acquisitions",
    "restructuring", "impairment",
})
# Discontinued-operations cash: lumpy/event-driven, and the aggregate is a cash-flow
# reconciliation residual. Captured, but excluded from the magnitude-outlier and
# quarterly-sum checks (like the event-driven flows and the cash residuals).
_DISCONTINUED_FIELDS = frozenset({
    "cash_from_discontinued_operations",
    "discontinued_operating_cash_flow",
    "discontinued_investing_cash_flow",
    "discontinued_financing_cash_flow",
})
# Volatile net residuals (cash-flow reconciliation inputs) plus the event-driven flows.
_OUTLIER_EXCLUDE = (
    _EVENT_DRIVEN_FLOWS | _DISCONTINUED_FIELDS
    | frozenset({"net_change_in_cash", "fx_effect_on_cash"})
)

# USD "level" fields (income/balance/cash-flow amounts); excludes per-share and
# share-count fields and the volatile residuals above. Candidates for magnitude-outlier.
_USD_FIELDS = tuple(
    f.key for f in CANONICAL_FIELDS
    if f.unit == UNIT_USD and f.key not in _OUTLIER_EXCLUDE
)

# Flow fields whose quarters should sum to the annual figure.
_FLOW_FIELDS = tuple(
    f.key for f in CANONICAL_FIELDS
    if f.unit == UNIT_USD and f.kind == DURATION and f.statement in (INCOME, CASHFLOW)
)

_OUTLIER_FACTOR = 100.0
_CASH_TOL = 0.01
_CASH_GROSS_TOL = 0.05
_QUARTERLY_TOL = 0.01
# Dividend completeness: an interval this many times the usual one between Yahoo payments
# means a payment is missing; a fiscal-year total under this share of SEC's declared
# dividends per share confirms it. Below 4 intervals there is no usual interval.
_DIVIDEND_GAP_FACTOR = 1.7
_DIVIDEND_SHORTFALL = 0.8
_DIVIDEND_MIN_INTERVALS = 4


def check_field_outliers(
    annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag a USD field that spikes >=100x BOTH adjacent years (a one-off anomaly).

    A transient spike-and-revert is the signature of a filing or mis-resolved-tag
    error; a genuine step-change (e.g. goodwill from an acquisition) jumps and
    PERSISTS, staying ~1x the next year, so it is correctly not flagged. The first
    and last points of a field's series -- including the latest year -- are never
    flagged: there is no later value to confirm a reversion, and a just-happened
    jump is indistinguishable from a real recent event.
    """
    scored = set(scored_years)
    findings: List[Finding] = []
    for key in _USD_FIELDS:
        points: List[Tuple[str, float, float]] = []  # (year, signed_value, magnitude)
        for year, period in annual.items():
            v = _num(period, key)
            if v is not None and v != 0:
                points.append((year, v, abs(v)))
        if len(points) < 3:
            continue
        points.sort(key=lambda p: p[0])  # chronological (4-digit year strings)
        for i in range(1, len(points) - 1):
            year, value, mag = points[i]
            if year not in scored or mag < _MATERIALITY:
                continue
            prev_mag = points[i - 1][2]
            next_mag = points[i + 1][2]
            if mag >= _OUTLIER_FACTOR * prev_mag and mag >= _OUTLIER_FACTOR * next_mag:
                findings.append(Finding(
                    HIGH, "magnitude_outlier",
                    f"'{key}' = {value:,.0f} spikes to {mag / prev_mag:.0f}x the prior "
                    f"year and {mag / next_mag:.0f}x the next; likely a one-off filing "
                    f"or tag error.",
                    year,
                ))
    return findings


def _exceeds_cash_tolerance(residual: float, denom: float, gross: float) -> bool:
    """True when a cash residual is material BOTH vs the net change and vs gross activity."""
    return abs(residual) > _CASH_TOL * denom and abs(residual) > _CASH_GROSS_TOL * gross


def check_cashflow_reconciliation(
    annual: Dict[str, Any], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag when the reported net change in cash != operating+investing+financing+FX.

    An internal-consistency check on the cash-flow statement itself: its reported
    net-change line equals the sum of its three sections plus the FX effect by
    construction, so the residual is ~0 for a correctly-tagged filing. (Comparing
    against the balance-sheet cash line is unsound — that line excludes restricted
    cash and the FX effect, producing systematic false positives.)
    """
    scored = set(scored_years)
    findings: List[Finding] = []
    for year in scored:
        period = annual.get(year)
        if not period:
            continue
        net_change = _num(period, "net_change_in_cash")
        ocf = _num(period, "operating_cash_flow")
        icf = _num(period, "investing_cash_flow")
        fcf = _num(period, "financing_cash_flow")
        if net_change is None or ocf is None or icf is None or fcf is None:
            continue
        fx = _num(period, "fx_effect_on_cash") or 0.0
        disc = _num(period, "cash_from_discontinued_operations") or 0.0
        expected = ocf + icf + fcf + fx
        denom = max(abs(net_change), abs(expected))
        if denom < _MATERIALITY:
            continue
        gross = abs(ocf) + abs(icf) + abs(fcf)
        # Two valid bases: the section subtotals may already include discontinued
        # operations (reconciles against `expected`), or be continuing-only (then the
        # separately-reported discontinued-ops cash line must be added -> `expected +
        # disc`). Flag only when NEITHER basis reconciles, so a filer like PRU that
        # reports continuing-only sections plus a separate discontinued line is not a
        # false positive. When `disc` is 0 the two bases are identical (behavior
        # unchanged for every filer without discontinued operations).
        residual = net_change - expected
        residual_disc = net_change - (expected + disc)
        denom_disc = max(abs(net_change), abs(expected + disc))
        if (_exceeds_cash_tolerance(residual, denom, gross)
                and _exceeds_cash_tolerance(residual_disc, denom_disc, gross)):
            findings.append(Finding(
                MEDIUM, "cashflow_imbalance",
                f"reported net change in cash ({net_change:,.0f}) != operating+"
                f"investing+financing+FX ({expected:,.0f}); residual {residual:,.0f}.",
                year,
            ))
    return findings


def check_quarterly_sums(
    annual: Dict[str, Dict[str, Any]],
    quarterly: Dict[str, Dict[str, Any]],
    scored_years: Iterable[str],
) -> List[Finding]:
    """Flag a flow field whose four discrete quarters don't sum to the annual figure.

    Validates the cumulative-ladder differencing. Only runs for a fiscal year that
    has all four discrete quarters and is in the scored window.
    """
    scored = set(scored_years)
    by_fy: Dict[int, Dict[int, Dict[str, Any]]] = {}
    for period in quarterly.values():
        fq = period.get("fiscal_quarter")
        fy = period.get("fiscal_year")
        if fq in (1, 2, 3, 4) and fy is not None:
            by_fy.setdefault(int(fy), {})[int(fq)] = period

    findings: List[Finding] = []
    for year in scored:
        if not str(year).isdigit():
            continue
        quarters = by_fy.get(int(year))
        ann = annual.get(year)
        if not quarters or set(quarters) != {1, 2, 3, 4} or not ann:
            continue
        mismatched: List[str] = []
        for key in _FLOW_FIELDS:
            if key in _EVENT_DRIVEN_FLOWS or key in _DISCONTINUED_FIELDS:
                continue
            ann_val = _num(ann, key)
            if ann_val is None or abs(ann_val) < _MATERIALITY:
                continue
            q_vals = [_num(quarters[q], key) for q in (1, 2, 3, 4)]
            if any(v is None for v in q_vals):
                continue
            sum_q = sum(v for v in q_vals if v is not None)
            if abs(sum_q - ann_val) / abs(ann_val) > _QUARTERLY_TOL:
                mismatched.append(key)
        if mismatched:
            preview = ", ".join(mismatched[:5])
            if len(mismatched) > 5:
                preview += ", ..."
            findings.append(Finding(
                MEDIUM, "quarterly_sum_mismatch",
                f"FY{year}: {len(mismatched)} flow field(s) whose four quarters don't "
                f"sum to the annual figure ({preview}).",
                year,
            ))
    return findings


def _out_of_bounds(metric: str, v: float) -> bool:
    if metric in ("gross_margin", "operating_margin", "ebitda_margin"):
        return v > 1.01
    if metric in ("net_margin", "fcf_margin"):
        return abs(v) > 2.0
    if metric in ("roe", "roa", "roic"):
        return abs(v) > 5.0
    if metric == "efficiency_ratio":
        return v <= 0 or v > 2.0
    if metric in ("loss_ratio", "combined_ratio"):
        return v < 0 or v > 3.0
    if metric in ("loan_to_deposit", "ffo_payout"):
        return v < 0 or v > 5.0
    if metric == "net_interest_margin":
        return v < 0 or v > 0.25
    return False


_BOUNDED_METRICS = (
    "gross_margin", "operating_margin", "ebitda_margin", "net_margin", "fcf_margin",
    "roe", "roa", "roic", "efficiency_ratio", "loss_ratio", "combined_ratio",
    "loan_to_deposit", "ffo_payout", "net_interest_margin",
)


_EQUITY_FLOOR = 0.05


def _weak_equity_base(period: Optional[Dict[str, Any]]) -> bool:
    """True when the equity denominator is too small/negative for roe/roic to be meaningful."""
    if not isinstance(period, dict):
        return False
    equity = _num(period, "total_equity")
    if equity is None:
        return False
    if equity <= 0:
        return True
    assets = _num(period, "total_assets")
    return assets is not None and abs(equity) < _EQUITY_FLOOR * abs(assets)


def check_ratio_bounds(
    historical: Dict[str, Dict[str, Any]],
    annual: Dict[str, Dict[str, Any]],
    scored_years: Iterable[str],
) -> List[Finding]:
    """Flag a computed metric that falls outside its mathematically-plausible range."""
    scored = set(scored_years)
    findings: List[Finding] = []
    for year in scored:
        metrics = historical.get(year)
        if not isinstance(metrics, dict):
            continue
        weak_equity = _weak_equity_base(annual.get(year))
        for metric in _BOUNDED_METRICS:
            if metric in ("roe", "roic") and weak_equity:
                continue
            v = metrics.get(metric)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and _out_of_bounds(metric, float(v)):
                findings.append(Finding(
                    LOW, "ratio_out_of_bounds",
                    f"'{metric}' = {float(v):.4f} is outside its plausible range.",
                    year,
                ))
    return findings

def check_dividend_completeness(
    annual: Dict[str, Dict[str, Any]],
    dividend_payments: Iterable[Dict[str, Any]],
    splits: Iterable[Dict[str, Any]],
    scored_years: Iterable[str],
) -> List[Finding]:
    """Flag a fiscal year whose Yahoo dividend payments miss what SEC filings declare.

    Two independent signals must agree: a gap in Yahoo's payment schedule that overlaps
    the year, and Yahoo's total for the year falling well short of the SEC-declared
    dividends per share. Either alone is noise: a large December declaration paid in
    January (PGR) shows a shortfall without a gap, and a real suspension shows a gap
    that SEC's figure confirms. The gap must also begin near the year: after years
    without a dividend, a first one declared before the year ends and paid after it
    is an initiation, not a missing payment (MU FY2021). Years with a later split are
    skipped, because Yahoo's amounts are split-adjusted and SEC's are as filed. (CB: no
    payment between 2025-06-13 and 2026-03-13, so FY2025 sums to 1.88 against 3.82.)
    """
    paid = sorted(
        (str(p["date"])[:10], float(p["amount"]))
        for p in dividend_payments
        if p.get("date") and isinstance(p.get("amount"), (int, float))
    )
    intervals = [
        (date.fromisoformat(later) - date.fromisoformat(earlier)).days
        for (earlier, _), (later, _) in zip(paid, paid[1:])
    ]
    if len(intervals) < _DIVIDEND_MIN_INTERVALS:
        return []
    usual = statistics.median(intervals)
    gaps = [
        (paid[i][0], paid[i + 1][0])
        for i, days in enumerate(intervals)
        if days > _DIVIDEND_GAP_FACTOR * usual
    ]
    split_dates = [str(s["date"])[:10] for s in splits if s.get("date")]
    findings: List[Finding] = []
    for year in sorted(set(scored_years)):
        period = annual.get(year) or {}
        declared = _num(period, "dividends_per_share")
        end = str(period.get("period_end") or "")[:10]
        if declared is None or declared <= 0 or not end:
            continue
        start_day = date.fromisoformat(end) - timedelta(days=365)
        start = start_day.isoformat()
        recent = (start_day - timedelta(days=_DIVIDEND_GAP_FACTOR * usual)).isoformat()
        if any(d > start for d in split_dates):
            continue
        gap = next(((a, b) for a, b in gaps if recent <= a < end and b > start), None)
        total = sum(amount for d, amount in paid if start < d <= end)
        if gap is not None and total < _DIVIDEND_SHORTFALL * declared:
            findings.append(Finding(
                MEDIUM, "dividend_payments_missing",
                f"Yahoo's dividend payments sum to {total:.2f}/share, but SEC filings declare "
                f"{declared:.2f}; there is no payment between {gap[0]} and {gap[1]} (usual "
                f"interval {usual:.0f} days), so Yahoo's dividend history is likely missing payments.",
                year,
            ))
    return findings

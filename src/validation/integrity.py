"""Integrity checks over standardized financials and computed metrics.

Catches confidently-wrong values that per-period validation in ``quality.py``
cannot: magnitude outliers vs a field's own history, cash-flow statements that
don't reconcile to the change in balance-sheet cash, discrete quarters that
don't sum to the annual figure, and metrics outside plausible bounds. Every
check is FLAG-ONLY — it emits Findings and never mutates data.
"""

import statistics
from typing import Any, Dict, Iterable, List

from ..mappings.canonical import CANONICAL_FIELDS, CASHFLOW, DURATION, INCOME, UNIT_USD
from .quality import HIGH, MEDIUM, Finding, _num

# Ignore sub-$1M figures (rounding/noise) across all checks.
_MATERIALITY = 1_000_000.0

# USD "level" fields (income/balance/cash-flow amounts); excludes per-share and
# share-count fields. Candidates for the magnitude-outlier check.
_USD_FIELDS = tuple(f.key for f in CANONICAL_FIELDS if f.unit == UNIT_USD)

# Flow fields whose quarters should sum to the annual figure.
_FLOW_FIELDS = tuple(
    f.key for f in CANONICAL_FIELDS
    if f.unit == UNIT_USD and f.kind == DURATION and f.statement in (INCOME, CASHFLOW)
)

_OUTLIER_FACTOR = 100.0
_CASH_TOL = 0.05
_QUARTERLY_TOL = 0.01


def check_field_outliers(
    annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag a USD field whose magnitude is >=100x its own across-year median.

    A value 100x above its field's median is almost certainly a mis-resolved tag
    or filing error (real year-over-year growth never approaches 100x). Uses all
    available years for the median but only flags periods in ``scored_years``.
    """
    scored = set(scored_years)
    findings: List[Finding] = []
    for key in _USD_FIELDS:
        points = []  # (year, signed_value, magnitude)
        for year, period in annual.items():
            v = _num(period, key)
            if v is not None and v != 0:
                points.append((year, v, abs(v)))
        mags = [m for _, _, m in points]
        if len(mags) < 3:
            continue
        median = statistics.median(mags)
        if median < _MATERIALITY:
            continue
        for year, value, mag in points:
            if year in scored and mag / median >= _OUTLIER_FACTOR:
                findings.append(Finding(
                    HIGH, "magnitude_outlier",
                    f"'{key}' = {value:,.0f} is {mag / median:.0f}x its median "
                    f"({median:,.0f}); likely a mis-resolved tag or filing error.",
                    year,
                ))
    return findings


def check_cashflow_reconciliation(
    annual: Dict[str, Dict[str, Any]], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag when change in balance-sheet cash != operating+investing+financing flows.

    The 5% tolerance absorbs the foreign-exchange-effect-on-cash line (not a
    canonical field) and minor restricted-cash reclassifications.
    """
    scored = set(scored_years)
    findings: List[Finding] = []
    years = sorted(annual.keys())
    for prev, curr in zip(years, years[1:]):
        if curr not in scored:
            continue
        cash_curr = _num(annual[curr], "cash_and_equivalents")
        cash_prev = _num(annual[prev], "cash_and_equivalents")
        ocf = _num(annual[curr], "operating_cash_flow")
        icf = _num(annual[curr], "investing_cash_flow")
        fcf = _num(annual[curr], "financing_cash_flow")
        if (cash_curr is None or cash_prev is None or ocf is None
                or icf is None or fcf is None):
            continue
        delta = cash_curr - cash_prev
        flow_sum = ocf + icf + fcf
        denom = max(abs(delta), abs(flow_sum))
        if denom < _MATERIALITY:
            continue
        residual = delta - flow_sum
        if abs(residual) / denom > _CASH_TOL:
            findings.append(Finding(
                MEDIUM, "cashflow_imbalance",
                f"change in cash ({delta:,.0f}) != operating+investing+financing "
                f"cash flow ({flow_sum:,.0f}); residual {residual:,.0f}.",
                curr,
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
        for key in _FLOW_FIELDS:
            ann_val = _num(ann, key)
            if ann_val is None or abs(ann_val) < _MATERIALITY:
                continue
            q_vals = [_num(quarters[q], key) for q in (1, 2, 3, 4)]
            if any(v is None for v in q_vals):
                continue
            sum_q = sum(v for v in q_vals if v is not None)
            if abs(sum_q - ann_val) / abs(ann_val) > _QUARTERLY_TOL:
                findings.append(Finding(
                    MEDIUM, "quarterly_sum_mismatch",
                    f"'{key}': four quarters sum to {sum_q:,.0f} but annual FY{year} "
                    f"is {ann_val:,.0f}.",
                    year,
                ))
    return findings

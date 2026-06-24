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
from .quality import HIGH, Finding, _num

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

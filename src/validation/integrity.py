"""Integrity checks over standardized financials and computed metrics.

Catches confidently-wrong values that per-period validation in ``quality.py``
cannot: magnitude outliers vs a field's own history, cash-flow statements that
don't reconcile to the change in balance-sheet cash, discrete quarters that
don't sum to the annual figure, and metrics outside plausible bounds. Every
check is FLAG-ONLY — it emits Findings and never mutates data.
"""

from typing import Any, Dict, Iterable, List, Tuple

from ..mappings.canonical import CANONICAL_FIELDS, CASHFLOW, DURATION, INCOME, UNIT_USD
from .quality import HIGH, LOW, MEDIUM, Finding, _num

# Ignore sub-$1M figures (rounding/noise) across all checks.
_MATERIALITY = 1_000_000.0

# Fields excluded from the magnitude-outlier check because they legitimately spike
# far more than the outlier factor in a single year. The check only makes sense for
# recurring, relatively stable fields (revenue, income, assets, equity, capex,
# dividends, D&A). These stay in the quarterly-sum check and stay fully captured.
_OUTLIER_EXCLUDE = frozenset({
    # Volatile net residuals (cash-flow reconciliation inputs).
    "net_change_in_cash", "fx_effect_on_cash",
    # Event-driven / lumpy flows: financing transactions, buybacks, M&A, one-time charges.
    "debt_issued", "debt_repaid", "share_repurchases", "acquisitions",
    "restructuring", "impairment",
})

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
        expected = ocf + icf + fcf + fx
        denom = max(abs(net_change), abs(expected))
        if denom < _MATERIALITY:
            continue
        residual = net_change - expected
        gross = abs(ocf) + abs(icf) + abs(fcf)
        # Flag only when the residual is material BOTH vs the net change and vs gross
        # cash activity -- so a small discontinued-ops/FX leftover on a company with
        # huge gross flows (e.g. an insurer) is not a false positive, while a
        # mis-resolved section (large vs gross) still is.
        if abs(residual) > _CASH_TOL * denom and abs(residual) > _CASH_GROSS_TOL * gross:
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


def check_ratio_bounds(
    historical: Dict[str, Dict[str, Any]], scored_years: Iterable[str]
) -> List[Finding]:
    """Flag a computed metric that falls outside its mathematically-plausible range."""
    scored = set(scored_years)
    findings: List[Finding] = []
    for year in scored:
        metrics = historical.get(year)
        if not isinstance(metrics, dict):
            continue
        for metric in _BOUNDED_METRICS:
            v = metrics.get(metric)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and _out_of_bounds(metric, float(v)):
                findings.append(Finding(
                    LOW, "ratio_out_of_bounds",
                    f"'{metric}' = {float(v):.4f} is outside its plausible range.",
                    year,
                ))
    return findings

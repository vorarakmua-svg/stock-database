"""Sector-aware financial ratios for banks, insurers, and REITs.

The generic ratio suite (``CalculatedMetrics``) assumes an operating company.
For banks/insurers/REITs many of those ratios are meaningless (a bank has no
inventory or invested capital; interest expense is a core cost, not a coverage
denominator). This module:

* computes the ratios that DO describe each sector, from canonical fields that
  already exist in the registry, and
* declares, per sector, which generic ratios to suppress (store ``None`` =
  "not applicable") so cross-sector screens don't compare on a broken metric.

Some ratios are documented proxies (see ``_BASIS``): the registry doesn't split
out real-estate-specific D&A, gains on property sales, or an earning-assets
line, so FFO/AFFO/combined-ratio/NIM use the closest available inputs.
"""

from typing import Any, Callable, Dict, Optional

from ..mappings.sectors import BANK, INSURANCE, REIT
from .metric_utils import field_value


def _f(financials: Dict[str, Any], key: str) -> Optional[float]:
    """Resolve a single canonical field (thin wrapper over field_value)."""
    return field_value(financials, [key])


def bank_metrics(financials: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Bank ratios: net interest margin (proxy), efficiency ratio, loan/deposit."""
    nii = _f(financials, "net_interest_income")
    noninterest_income = _f(financials, "noninterest_income")
    noninterest_expense = _f(financials, "noninterest_expense")
    total_assets = _f(financials, "total_assets")
    total_loans = _f(financials, "total_loans")
    total_deposits = _f(financials, "total_deposits")

    nim: Optional[float] = None
    if nii is not None and total_assets and total_assets > 0:
        nim = nii / total_assets

    revenue = (nii or 0.0) + (noninterest_income or 0.0)
    efficiency_ratio: Optional[float] = None
    if noninterest_expense is not None and revenue > 0:
        efficiency_ratio = noninterest_expense / revenue

    loan_to_deposit: Optional[float] = None
    if total_loans is not None and total_deposits and total_deposits > 0:
        loan_to_deposit = total_loans / total_deposits

    return {
        "net_interest_margin": nim,
        "efficiency_ratio": efficiency_ratio,
        "loan_to_deposit": loan_to_deposit,
    }


# Generic ratio keys to null per sector (must match keys emitted by
# CalculatedMetrics.calculate_all).
SUPPRESSED_BY_SECTOR: Dict[str, frozenset] = {
    BANK: frozenset({
        "ebitda", "ebit", "ebitda_margin", "debt_to_ebitda",
        "roic", "nopat", "invested_capital", "interest_coverage",
        "gross_margin", "operating_margin",
        "inventory_turnover", "days_inventory_outstanding",
        "receivables_turnover", "days_sales_outstanding",
        "asset_turnover", "working_capital",
        "net_debt", "total_debt",
        "free_cash_flow", "fcf_margin", "levered_fcf",
    }),
    INSURANCE: frozenset({
        "ebitda", "ebitda_margin", "debt_to_ebitda",
        "roic", "nopat", "invested_capital",
        "inventory_turnover", "days_inventory_outstanding",
        "gross_margin", "asset_turnover", "working_capital",
    }),
    REIT: frozenset({
        "roic", "nopat", "invested_capital",
        "inventory_turnover", "days_inventory_outstanding",
        "receivables_turnover", "days_sales_outstanding",
        "gross_margin", "asset_turnover",
        "free_cash_flow", "fcf_margin", "levered_fcf",
    }),
}

# Approximation provenance for proxy ratios; attached to metrics["_basis"] only
# when the corresponding metric was actually computed.
_BASIS: Dict[str, str] = {
    "net_interest_margin": "proxy: net_interest_income / total_assets (no earning-assets line)",
    "combined_ratio": "proxy: benefits_and_expenses / premiums_earned (no separate underwriting expense)",
    "ffo": "proxy: net_income + total D&A (not RE-specific; no gains-on-sale adjustment)",
    "affo": "proxy: ffo - total capex (not maintenance capex)",
}

# Registered in later tasks: INSURANCE -> insurer_metrics, REIT -> reit_metrics.
SECTOR_EXTRAS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Optional[float]]]] = {
    BANK: bank_metrics,
}


def apply_sector(
    metrics: Dict[str, Any], financials: Dict[str, Any], sector: Optional[str]
) -> Dict[str, Any]:
    """Merge sector ratios into ``metrics`` and null the suppressed generic ones.

    A ``None``/general/utility/energy ``sector`` is a no-op, so operating
    companies are unaffected. Mutates and returns ``metrics``.
    """
    if not sector:
        return metrics
    extras_fn = SECTOR_EXTRAS.get(sector)
    if extras_fn is None:
        return metrics

    metrics.update(extras_fn(financials))
    for key in SUPPRESSED_BY_SECTOR.get(sector, frozenset()):
        metrics[key] = None

    basis = metrics.setdefault("_basis", {})
    for key, note in _BASIS.items():
        if metrics.get(key) is not None:
            basis[key] = note
    return metrics

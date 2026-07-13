"""Valuation models: DCF, DDM, Graham, Lynch, historical multiples band.

Each model is a pure function ``ValuationInputs -> ValuationResult`` producing
a bear/base/bull per-share fair-value range plus the exact assumptions used.
A model that does not apply returns ``applicable=False`` with a user-facing
``na_reason`` — no number is better than a fake number.
"""

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .assumptions import (
    DISCOUNT_SPREAD,
    TERMINAL_GROWTH,
    derive_discount,
    derive_growth,
    growth_scenarios,
)
from .inputs import ValuationInputs

DCF_SECTORS = ("general", "utility", "energy")


@dataclass
class ValuationResult:
    """Outcome of one model for one ticker."""

    model: str
    applicable: bool
    na_reason: Optional[str] = None
    value_bear: Optional[float] = None
    value_base: Optional[float] = None
    value_bull: Optional[float] = None
    assumptions: Dict[str, Any] = field(default_factory=dict)
    basis_fiscal_year: Optional[int] = None


def _na(model: str, reason: str,
        basis_fy: Optional[int] = None) -> ValuationResult:
    return ValuationResult(model=model, applicable=False, na_reason=reason,
                           basis_fiscal_year=basis_fy)


def dcf_per_share(fcf0: float, shares: float, growth: float, discount: float,
                  terminal_growth: float = TERMINAL_GROWTH) -> float:
    """Two-stage 10-year DCF on equity free cash flow, per share.

    Years 1-5 grow at ``growth``; years 6-10 fade linearly to
    ``terminal_growth``; Gordon terminal value at year 10. ``discount`` must
    exceed ``terminal_growth`` (guaranteed by the DISCOUNT_FLOOR clamp).
    """
    value = 0.0
    fcf = fcf0
    for t in range(1, 11):
        rate = growth if t <= 5 else growth + (terminal_growth - growth) * (t - 5) / 5.0
        fcf *= 1.0 + rate
        value += fcf / (1.0 + discount) ** t
    terminal = fcf * (1.0 + terminal_growth) / (discount - terminal_growth)
    value += terminal / (1.0 + discount) ** 10
    return value / shares


def value_dcf(inputs: ValuationInputs) -> ValuationResult:
    """FCF DCF for operating companies (general/utility/energy)."""
    if inputs.sector_class not in DCF_SECTORS:
        return _na("dcf", f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if r.fcf is not None]
    if len(recs) < 4:
        return _na("dcf", "insufficient FCF history (need >= 4 fiscal years)")
    basis_fy = recs[-1].fiscal_year
    fcf_hist = [r.fcf for r in recs]
    basis = statistics.median([f for f in fcf_hist[-3:] if f is not None])
    if basis <= 0:
        return _na("dcf", "median 3-year FCF is not positive", basis_fy=basis_fy)
    shares = inputs.shares_outstanding
    if not shares or shares <= 0:
        return _na("dcf", "shares outstanding unavailable", basis_fy=basis_fy)

    growth, gmeta = derive_growth(fcf_hist, inputs.analyst_growth)
    discount, dmeta = derive_discount(inputs.risk_free_rate, inputs.beta)
    g_bear, g_base, g_bull = growth_scenarios(growth)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update(dmeta)
    assumptions.update({
        "fcf_basis": basis,
        "fcf_years": len(fcf_hist),
        "terminal_growth": TERMINAL_GROWTH,
        "shares_outstanding": shares,
    })
    return ValuationResult(
        model="dcf",
        applicable=True,
        value_bear=dcf_per_share(basis, shares, g_bear, discount + DISCOUNT_SPREAD),
        value_base=dcf_per_share(basis, shares, g_base, discount),
        value_bull=dcf_per_share(basis, shares, g_bull, discount - DISCOUNT_SPREAD),
        assumptions=assumptions,
        basis_fiscal_year=basis_fy,
    )

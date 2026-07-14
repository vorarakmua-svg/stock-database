"""Valuation models: DCF, DDM, Graham, Lynch, historical multiples band.

Each model is a pure function ``ValuationInputs -> ValuationResult`` producing
a bear/base/bull per-share fair-value range plus the exact assumptions used.
A model that does not apply returns ``applicable=False`` with a user-facing
``na_reason`` — no number is better than a fake number.
"""

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from .assumptions import (
    DDM_GROWTH_CAP,
    DISCOUNT_SPREAD,
    MARGIN_OF_SAFETY,
    OE_DISCOUNT_FLOOR,
    OE_HISTORY_WINDOW,
    OE_MIN_POSITIVE_YEARS,
    TERMINAL_GROWTH,
    derive_discount,
    derive_growth,
    growth_scenarios,
)
from .inputs import FYRecord, ValuationInputs

DCF_SECTORS = ("general", "utility", "energy")

#: A payer whose most recent dividend is older than this (15 months — one
#: missed annual payment plus a grace quarter) is treated as having stopped.
DIVIDEND_STALE_DAYS = 456


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
    shares_source = "market_snapshot"
    if not shares or shares <= 0:
        shares = None
        for r in reversed(recs):
            if r.shares is not None and r.shares > 0:
                shares = r.shares
                shares_source = "financials_annual"
                break
        if not shares:
            return _na("dcf", "shares outstanding unavailable", basis_fy=basis_fy)

    growth, gmeta = derive_growth(fcf_hist, inputs.analyst_growth,
                                  periods=[r.fiscal_year for r in recs])
    discount, dmeta = derive_discount(inputs.risk_free_rate, inputs.beta)
    g_bear, g_base, g_bull = growth_scenarios(growth)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update(dmeta)
    assumptions.update({
        "fcf_basis": basis,
        "fcf_years": len(fcf_hist),
        "history_truncated": inputs.history_truncated,
        "terminal_growth": TERMINAL_GROWTH,
        "shares_outstanding": shares,
        "shares_source": shares_source,
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


def ddm_per_share(ttm_dps: float, growth: float, discount: float,
                  terminal_growth: float = TERMINAL_GROWTH) -> float:
    """Multi-stage Gordon DDM: 5 years at ``growth``, then terminal growth."""
    value = 0.0
    d = ttm_dps
    for t in range(1, 6):
        d *= 1.0 + growth
        value += d / (1.0 + discount) ** t
    terminal = d * (1.0 + terminal_growth) / (discount - terminal_growth)
    value += terminal / (1.0 + discount) ** 5
    return value


def _annual_dividend_totals(dividends: List[Tuple[str, float]]) -> List[Tuple[int, float]]:
    totals: Dict[int, float] = {}
    for date_str, amount in dividends:
        year = int(date_str[:4])
        totals[year] = totals.get(year, 0.0) + amount
    return sorted(totals.items())


def value_ddm(inputs: ValuationInputs) -> ValuationResult:
    """Multi-stage dividend discount model — any steady payer, and the primary
    model for banks/insurers where FCF is meaningless."""
    if not inputs.dividends:
        return _na("ddm", "no dividend history")
    annual = _annual_dividend_totals(inputs.dividends)
    if len(annual) < 3:
        return _na("ddm", "insufficient dividend history (need >= 3 calendar years)")

    # Anchor the TTM window to "now" (the latest data collection), NOT to the
    # last dividend in the table — otherwise a company that suspended its
    # dividend years ago still shows a full DDM fair value today.
    anchor = inputs.as_of or date.today()
    last_paid = date.fromisoformat(inputs.dividends[-1][0][:10])
    if (anchor - last_paid).days > DIVIDEND_STALE_DAYS:
        return _na("ddm", "dividends discontinued (no payment in the last 15 months)")
    cutoff = (anchor - timedelta(days=365)).isoformat()
    ttm = sum(a for d, a in inputs.dividends if cutoff < d[:10] <= anchor.isoformat())
    if ttm <= 0:
        return _na("ddm", "no dividends in trailing 12 months")

    # CAGR over complete calendar years only (the anchor year is likely partial).
    complete_years = [(year, total) for year, total in annual if year < anchor.year]
    complete = [total for _, total in complete_years]
    growth, gmeta = derive_growth(complete, inputs.analyst_growth, cap=DDM_GROWTH_CAP,
                                  periods=[year for year, _ in complete_years])
    discount, dmeta = derive_discount(inputs.risk_free_rate, inputs.beta)
    g_bear, g_base, g_bull = growth_scenarios(growth, cap=DDM_GROWTH_CAP)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update(dmeta)
    assumptions.update({
        "ttm_dps": ttm,
        "ttm_anchor": anchor.isoformat(),
        "dividend_years": len(annual),
        "history_truncated": inputs.history_truncated,
        "terminal_growth": TERMINAL_GROWTH,
    })
    return ValuationResult(
        model="ddm",
        applicable=True,
        value_bear=ddm_per_share(ttm, g_bear, discount + DISCOUNT_SPREAD),
        value_base=ddm_per_share(ttm, g_base, discount),
        value_bull=ddm_per_share(ttm, g_bull, discount - DISCOUNT_SPREAD),
        assumptions=assumptions,
        basis_fiscal_year=anchor.year,
    )


LYNCH_PE_FLOOR = 5.0
LYNCH_PE_CAP = 25.0


def value_graham(inputs: ValuationInputs) -> ValuationResult:
    """Graham Number: sqrt(22.5 * EPS * BVPS). Bear/bull vary EPS over 3 FYs."""
    recs = [r for r in inputs.fy_records
            if r.eps() is not None and r.bvps() is not None]
    if not recs:
        return _na("graham", "EPS or book value unavailable")
    latest = recs[-1]
    eps = latest.eps()
    bvps = latest.bvps()
    assert eps is not None and bvps is not None  # narrowed by filter above
    if eps <= 0:
        return _na("graham", "EPS is not positive", basis_fy=latest.fiscal_year)
    if bvps <= 0:
        return _na("graham", "book value per share is not positive",
                   basis_fy=latest.fiscal_year)
    last3 = [e for e in (r.eps() for r in recs[-3:]) if e is not None and e > 0]
    eps_bear, eps_bull = min(last3), max(last3)
    assumptions: Dict[str, Any] = {
        "eps_base": eps, "eps_bear": eps_bear, "eps_bull": eps_bull,
        "bvps": bvps, "multiplier": 22.5,
        "history_truncated": inputs.history_truncated,
    }
    return ValuationResult(
        model="graham",
        applicable=True,
        value_bear=math.sqrt(22.5 * eps_bear * bvps),
        value_base=math.sqrt(22.5 * eps * bvps),
        value_bull=math.sqrt(22.5 * eps_bull * bvps),
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )


def _lynch_fair_pe(growth: float) -> float:
    return min(max(growth * 100.0, LYNCH_PE_FLOOR), LYNCH_PE_CAP)


def _split_adjusted(recs: List[FYRecord]) -> bool:
    """True iff any record used was restated onto the current share basis."""
    return any(r.split_factor != 1.0 for r in recs)


def value_lynch(inputs: ValuationInputs) -> ValuationResult:
    """Peter Lynch fair value: growth-rate-as-fair-P/E times latest EPS."""
    if inputs.sector_class not in DCF_SECTORS:
        return _na("lynch", f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if r.eps() is not None]
    if len(recs) < 4:
        return _na("lynch", "insufficient EPS history (need >= 4 fiscal years)")
    latest = recs[-1]
    eps = latest.eps()
    assert eps is not None
    if eps <= 0:
        return _na("lynch", "EPS is not positive", basis_fy=latest.fiscal_year)
    growth, gmeta = derive_growth([r.eps() for r in recs], inputs.analyst_growth,
                                  periods=[r.fiscal_year for r in recs])
    if gmeta["growth_source"] == "none":
        # Lynch IS the growth rate: with no CAGR and no analyst estimate the
        # P/E floor would publish 5 x EPS — a number with no information in it.
        return _na("lynch", "no usable earnings-growth history",
                   basis_fy=latest.fiscal_year)
    g_bear, g_base, g_bull = growth_scenarios(growth)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update({
        "eps_base": eps,
        "fair_pe_base": _lynch_fair_pe(g_base),
        "fair_pe_floor": LYNCH_PE_FLOOR,
        "fair_pe_cap": LYNCH_PE_CAP,
        "split_adjusted": _split_adjusted(recs),
        "history_truncated": inputs.history_truncated,
    })
    return ValuationResult(
        model="lynch",
        applicable=True,
        value_bear=_lynch_fair_pe(g_bear) * eps,
        value_base=_lynch_fair_pe(g_base) * eps,
        value_bull=_lynch_fair_pe(g_bull) * eps,
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )


def _multiple_basis(sector_class: str) -> Tuple[str, Callable[[FYRecord], Optional[float]]]:
    if sector_class in ("bank", "insurance"):
        return "pb", lambda r: r.bvps()
    if sector_class == "reit":
        return "pffo", lambda r: r.ffo_per_share
    return "pe", lambda r: r.eps()


def value_multiples(inputs: ValuationInputs) -> ValuationResult:
    """Price vs the company's own 5-year band of its sector-appropriate multiple."""
    kind, basis_fn = _multiple_basis(inputs.sector_class)
    recs = inputs.fy_records[-5:]
    multiples = []
    for r in recs:
        b = basis_fn(r)
        p = inputs.fy_end_prices.get(r.fiscal_year)
        if b is not None and b > 0 and p is not None and p > 0:
            multiples.append(p / b)
    if len(multiples) < 3:
        return _na("multiples",
                   "insufficient multiple history (need >= 3 fiscal years)")
    latest = inputs.fy_records[-1]
    latest_basis = basis_fn(latest)
    if latest_basis is None or latest_basis <= 0:
        return _na("multiples", "latest per-share basis is not positive",
                   basis_fy=latest.fiscal_year)
    band_low, band_mid, band_high = (min(multiples),
                                     statistics.median(multiples),
                                     max(multiples))
    assumptions: Dict[str, Any] = {
        "multiple_kind": kind,
        "band_low": band_low, "band_median": band_mid, "band_high": band_high,
        "n_years": len(multiples),
        "latest_basis": latest_basis,
        "split_adjusted": _split_adjusted(recs),
        "history_truncated": inputs.history_truncated,
    }
    return ValuationResult(
        model="multiples",
        applicable=True,
        value_bear=band_low * latest_basis,
        value_base=band_mid * latest_basis,
        value_bull=band_high * latest_basis,
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )


def owner_earnings(rec: FYRecord) -> Optional[float]:
    """Buffett's owner earnings: net income + non-cash charges - MAINTENANCE capex.

    Maintenance capex is the one figure he leaves to judgment; depreciation is the
    accounting estimate of the same thing (what it costs to stand still), so we use
    ``min(D&A, |capex|)`` — capped at what the company actually spent, since you
    cannot spend more maintaining assets than you spent in total. Capex above that
    is growth spending and is NOT subtracted: the whole point of the measure is to
    stop treating expansion as a cost of standing still.

    Stock-based compensation is deliberately NOT added back. It is a real cost of
    running the business, whatever its cash character.
    """
    if rec.net_income is None or rec.depreciation_amortization is None:
        return None
    da = rec.depreciation_amortization
    capex = abs(rec.capex) if rec.capex is not None else da
    maintenance = min(da, capex)
    return rec.net_income + da - maintenance


def value_owner_earnings(inputs: ValuationInputs) -> ValuationResult:
    """Owner earnings discounted at the Treasury rate, with a margin of safety.

    Deliberately unlike ``value_dcf``: no beta, no equity-risk premium, growth capex
    credited back, and a refusal to value a business whose earnings cannot be
    forecast. Its verdict is its own (see ``engine.owner_earnings_verdict``) and it is
    excluded from the cross-model median — averaging a Treasury-discounted value with
    CAPM-discounted ones would silently drag every verdict toward "cheap".
    """
    if inputs.sector_class not in DCF_SECTORS:
        return _na("owner_earnings",
                   f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if owner_earnings(r) is not None]
    if len(recs) < 4:
        return _na("owner_earnings",
                   "insufficient history (need >= 4 fiscal years)")
    basis_fy = recs[-1].fiscal_year

    window = recs[-OE_HISTORY_WINDOW:]
    if len(window) < OE_HISTORY_WINDOW:
        return _na("owner_earnings",
                   "insufficient history for the predictability test (need >= 10 fiscal years)",
                   basis_fy=basis_fy)
    oe_hist = [owner_earnings(r) for r in window]
    positive_years = sum(1 for v in oe_hist if v is not None and v > 0)
    if positive_years < OE_MIN_POSITIVE_YEARS:
        return _na("owner_earnings", "owner earnings too erratic to forecast",
                   basis_fy=basis_fy)

    basis = statistics.median([v for v in oe_hist[-3:] if v is not None])
    if basis <= 0:
        return _na("owner_earnings",
                   "median 3-year owner earnings is not positive", basis_fy=basis_fy)

    shares = inputs.shares_outstanding
    shares_source = "market_snapshot"
    if not shares or shares <= 0:
        shares = None
        for r in reversed(recs):
            if r.shares is not None and r.shares > 0:
                shares = r.shares
                shares_source = "financials_annual"
                break
        if not shares:
            return _na("owner_earnings", "shares outstanding unavailable",
                       basis_fy=basis_fy)

    growth, gmeta = derive_growth(
        oe_hist, inputs.analyst_growth,
        periods=[r.fiscal_year for r in window],
    )
    g_bear, g_base, g_bull = growth_scenarios(growth)

    # Buffett's discount rate: the long-term government rate, floored. No beta.
    rf = inputs.risk_free_rate
    rf_fallback = rf is None
    if rf is None:
        rf = OE_DISCOUNT_FLOOR
    discount = max(rf, OE_DISCOUNT_FLOOR)

    # The discount rate is held FIXED across scenarios: under this method it is an
    # observable market fact, not a risk knob. Only growth varies.
    latest = recs[-1]
    da = latest.depreciation_amortization or 0.0
    latest_capex = abs(latest.capex) if latest.capex is not None else da
    maintenance = min(da, latest_capex)

    value_base = dcf_per_share(basis, shares, g_base, discount)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update({
        "owner_earnings_basis": basis,
        "maintenance_capex": maintenance,
        "growth_capex_added_back": latest_capex - maintenance,
        "discount_base": discount,
        "discount_floor": OE_DISCOUNT_FLOOR,
        "risk_free_rate": rf,
        "beta_used": False,
        "sbc_added_back": False,
        "margin_of_safety": MARGIN_OF_SAFETY,
        "buy_below": value_base * (1.0 - MARGIN_OF_SAFETY),
        "positive_years": positive_years,
        "history_truncated": inputs.history_truncated,
        "terminal_growth": TERMINAL_GROWTH,
        "shares_outstanding": shares,
        "shares_source": shares_source,
    })
    if rf_fallback:
        assumptions["rf_fallback"] = True
    return ValuationResult(
        model="owner_earnings",
        applicable=True,
        value_bear=dcf_per_share(basis, shares, g_bear, discount),
        value_base=value_base,
        value_bull=dcf_per_share(basis, shares, g_bull, discount),
        assumptions=assumptions,
        basis_fiscal_year=basis_fy,
    )

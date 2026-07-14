"""Conservative mechanical assumption rules shared by the valuation models.

Growth = min(historical CAGR, analyst estimate), clamped to [0, cap] — biased
toward not overpaying. Discount = CAPM cost of equity with hard floor/cap.
Every derived number and every fallback is recorded in a meta dict that the
models fold into their stored ``assumptions`` JSON (the trust anchor).
"""

from typing import Any, Dict, Optional, Sequence, Tuple

GROWTH_CAP = 0.15
DDM_GROWTH_CAP = 0.10
TERMINAL_GROWTH = 0.025
ERP = 0.045
DISCOUNT_FLOOR = 0.08
DISCOUNT_CAP = 0.14
GROWTH_SPREAD = 0.03
DISCOUNT_SPREAD = 0.01
DEFAULT_BETA = 1.0
DEFAULT_RF = 0.045


def _cagr_span(n_values: int, periods: Optional[Sequence[int]]) -> Optional[int]:
    """Year count for the CAGR exponent: the true calendar span when the year
    labels are known, else the number of positions (which assumes no gaps)."""
    if periods is None:
        return n_values - 1
    if len(periods) != n_values:
        return None
    span = periods[-1] - periods[0]
    return span if span > 0 else None


def historical_cagr(
    values: Sequence[Optional[float]],
    periods: Optional[Sequence[int]] = None,
) -> Optional[float]:
    """CAGR from first to last value; None unless both endpoints are positive.

    ``periods`` is the year label of each value. When supplied, the exponent
    uses the true calendar span (``periods[-1] - periods[0]``) rather than the
    number of data POSITIONS — a series with gaps (AAPL's 1996-2011 dividend
    hiatus) otherwise reports a wildly overstated growth rate.
    """
    if len(values) < 2:
        return None
    first, last = values[0], values[-1]
    if first is None or last is None or first <= 0 or last <= 0:
        return None
    years = _cagr_span(len(values), periods)
    if not years or years <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def derive_growth(
    history: Sequence[Optional[float]],
    analyst_growth: Optional[float],
    *,
    cap: float = GROWTH_CAP,
    periods: Optional[Sequence[int]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """Base growth = min(hist CAGR, analyst), clamped to [0, cap].

    Callers enforce their own minimum-history applicability rules BEFORE
    calling; with no usable candidate this conservatively returns 0.0
    (flat projection) with ``growth_source: "none"`` — a caller that cannot
    honestly publish a number without a growth rate must check for that.
    ``periods`` (the year label of each history value) is passed through to
    ``historical_cagr`` so calendar gaps do not inflate the CAGR.
    """
    hist = list(history)
    cagr = historical_cagr(hist, periods=periods)
    candidates = [g for g in (cagr, analyst_growth) if g is not None]
    if cagr is not None and analyst_growth is not None:
        source = "min(hist,analyst)"
    elif cagr is not None:
        source = "hist_only"
    elif analyst_growth is not None:
        source = "analyst_only"
    else:
        source = "none"
    raw = min(candidates) if candidates else 0.0
    base = min(max(raw, 0.0), cap)
    meta: Dict[str, Any] = {
        "growth_base": base,
        "growth_raw": raw,
        "growth_source": source,
        "hist_cagr": cagr,
        "cagr_years": _cagr_span(len(hist), periods),
        "analyst_growth": analyst_growth,
        "growth_cap": cap,
    }
    return base, meta


def derive_discount(
    risk_free: Optional[float], beta: Optional[float]
) -> Tuple[float, Dict[str, Any]]:
    """CAPM cost of equity: rf + beta * ERP, clamped to [floor, cap]."""
    meta: Dict[str, Any] = {}
    rf = risk_free
    if rf is None:
        rf = DEFAULT_RF
        meta["rf_fallback"] = True
    b = beta
    if b is None:
        b = DEFAULT_BETA
        meta["beta_fallback"] = True
    raw = rf + b * ERP
    rate = min(max(raw, DISCOUNT_FLOOR), DISCOUNT_CAP)
    meta.update(
        {
            "risk_free_rate": rf,
            "beta": b,
            "erp": ERP,
            "discount_raw": raw,
            "discount_base": rate,
        }
    )
    return rate, meta


def growth_scenarios(
    base: float, *, cap: float = GROWTH_CAP
) -> Tuple[float, float, float]:
    """(bear, base, bull) growth: base -/+ GROWTH_SPREAD, clamped to [0, cap]."""
    bear = max(base - GROWTH_SPREAD, 0.0)
    bull = min(base + GROWTH_SPREAD, cap)
    return bear, base, bull

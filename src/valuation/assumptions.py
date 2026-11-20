"""Conservative mechanical assumption rules shared by the valuation models.

Growth = the historical CAGR, clamped to [0, cap] (Q2, Q5) — biased toward not
overpaying. Discount = CAPM cost of equity with hard floor/cap.
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

#: Owner-earnings ("Buffett mode") discount floor. Buffett describes discounting
#: at the long-term government rate, but has also said he would not mechanically
#: discount at absurdly low rates when yields collapse — so the Treasury yield is
#: used, floored here. Beta plays no part.
OE_DISCOUNT_FLOOR = 0.07

#: The safety lives in the PRICE, not in a padded discount rate: buy only at a
#: discount to intrinsic value.
MARGIN_OF_SAFETY = 0.30

#: Predictability gate — owner earnings must be positive in at least this many of
#: the last OE_HISTORY_WINDOW fiscal years, or the business is refused as
#: unforecastable rather than valued with false precision.
OE_MIN_POSITIVE_YEARS = 8
OE_HISTORY_WINDOW = 10


def _cagr_span(n_values: int, periods: Optional[Sequence[int]]) -> Optional[int]:
    """Year count for the CAGR exponent: the true calendar span when the year
    labels are known, else the number of positions (which assumes no gaps)."""
    if periods is None:
        return n_values - 1
    if len(periods) != n_values or n_values < 2:
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


#: Q5 (decided): a CAGR measured from the first profitable (positive) year needs at least this many fiscal years
#: from that year to the latest.
MIN_FYS_FROM_FIRST_POSITIVE = 4


def _cagr_from_first_positive(
    hist: Sequence[Optional[float]], periods: Optional[Sequence[int]]
) -> Tuple[Optional[float], Optional[int]]:
    """Q5's fallback: the CAGR from the first positive value to the latest, when at least
    MIN_FYS_FROM_FIRST_POSITIVE values remain from it (DELL's FCF from its first positive year). ``(cagr, start)``,
    the start index; ``(None, None)`` when there's no such run or the latest isn't positive."""
    for k, value in enumerate(hist):
        if value is not None and value > 0:
            if len(hist) - k < MIN_FYS_FROM_FIRST_POSITIVE:
                return None, None
            sub = list(hist[k:])
            cagr = historical_cagr(sub, periods=list(periods[k:]) if periods is not None else None)
            return (cagr, k) if cagr is not None else (None, None)
    return None, None


def derive_growth(
    history: Sequence[Optional[float]],
    *,
    cap: float = GROWTH_CAP,
    periods: Optional[Sequence[int]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """Base growth = the historical CAGR, clamped to [0, cap].

    Yahoo's ``earningsGrowth`` (one quarter's year-over-year change, seen from -85% to +1,369%) is no forecast, so
    it takes no part (Q2, decided). Where the CAGR can't be measured (the window starts on a loss), it is measured
    from the first positive year if at least MIN_FYS_FROM_FIRST_POSITIVE years remain (Q5, decided:
    ``growth_source: "hist_from_first_positive"``); otherwise growth is a flat 0.0 with ``growth_source: "none"``,
    which a caller that can't honestly publish a number without a growth rate (Lynch) must check for.
    Callers enforce their own minimum-history applicability rules BEFORE calling. ``periods`` (the year label of
    each history value) is passed through to ``historical_cagr`` so calendar gaps do not inflate the CAGR.
    """
    hist = list(history)
    cagr = historical_cagr(hist, periods=periods)
    source = "hist_only"
    cagr_years = _cagr_span(len(hist), periods)
    if cagr is None:
        cagr, start = _cagr_from_first_positive(hist, periods)
        if cagr is not None and start is not None:
            source = "hist_from_first_positive"
            cagr_years = _cagr_span(len(hist) - start, list(periods[start:]) if periods is not None else None)
        else:
            source = "none"
    raw = cagr if cagr is not None else 0.0
    base = min(max(raw, 0.0), cap)
    meta: Dict[str, Any] = {
        "growth_base": base,
        "growth_raw": raw,
        "growth_source": source,
        "hist_cagr": cagr,
        "cagr_years": cagr_years,
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

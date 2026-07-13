"""Conservative-rule assumption derivation: clamps, min-of, fallback flags."""
import pytest

from src.valuation.assumptions import (
    DEFAULT_BETA,
    DEFAULT_RF,
    derive_discount,
    derive_growth,
    growth_scenarios,
    historical_cagr,
)


def test_historical_cagr_basic():
    # 100 -> 121 over 2 periods = 10% CAGR
    assert historical_cagr([100.0, 105.0, 121.0]) == pytest.approx(0.10)


def test_historical_cagr_rejects_nonpositive_endpoints_and_short_series():
    assert historical_cagr([100.0]) is None
    assert historical_cagr([-5.0, 100.0]) is None
    assert historical_cagr([100.0, 0.0]) is None
    assert historical_cagr([100.0, None]) is None


def test_derive_growth_takes_min_of_hist_and_analyst():
    base, meta = derive_growth([100.0, 105.0, 121.0], 0.05)
    assert base == pytest.approx(0.05)
    assert meta["growth_source"] == "min(hist,analyst)"
    assert meta["hist_cagr"] == pytest.approx(0.10)
    assert meta["analyst_growth"] == 0.05


def test_derive_growth_clamps_to_cap_and_floor():
    base, _ = derive_growth([100.0, 400.0], None)  # 300% growth
    assert base == 0.15
    base, _ = derive_growth([100.0, 50.0], None)  # negative CAGR -> floor 0
    assert base == 0.0


def test_derive_growth_sources():
    _, meta = derive_growth([100.0, 110.0], None)
    assert meta["growth_source"] == "hist_only"
    _, meta = derive_growth([-1.0, 5.0], 0.07)  # cagr None -> analyst only
    assert meta["growth_source"] == "analyst_only"
    base, meta = derive_growth([-1.0, 5.0], None)
    assert base == 0.0
    assert meta["growth_source"] == "none"


def test_derive_discount_capm_and_clamps():
    rate, meta = derive_discount(0.043, 1.2)
    assert rate == pytest.approx(0.043 + 1.2 * 0.045)
    assert "beta_fallback" not in meta
    rate, _ = derive_discount(0.01, 0.2)  # raw 1.9% -> floor
    assert rate == 0.08
    rate, _ = derive_discount(0.06, 3.0)  # raw 19.5% -> cap
    assert rate == 0.14


def test_derive_discount_fallbacks_flagged():
    rate, meta = derive_discount(None, None)
    assert meta["beta_fallback"] is True
    assert meta["rf_fallback"] is True
    assert meta["beta"] == DEFAULT_BETA
    assert meta["risk_free_rate"] == DEFAULT_RF
    assert rate == pytest.approx(0.09)  # 0.045 + 1.0*0.045


def test_growth_scenarios_spread_and_clamps():
    assert growth_scenarios(0.08) == (pytest.approx(0.05), 0.08, pytest.approx(0.11))
    bear, base, bull = growth_scenarios(0.01)
    assert bear == 0.0
    bear, base, bull = growth_scenarios(0.14)
    assert bull == 0.15

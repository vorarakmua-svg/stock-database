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


def test_historical_cagr_uses_calendar_span_when_periods_supplied():
    # 4 values but a 1996 -> 2023 calendar span (a gap year series, AAPL dividends):
    # positions would say 3 years (~59%/yr); the true span is 27 years (~7%/yr).
    values = [1.0, 2.0, 4.0, 6.0]
    periods = [1996, 2021, 2022, 2023]
    assert historical_cagr(values) == pytest.approx(6.0 ** (1 / 3) - 1)
    assert historical_cagr(values, periods=periods) == pytest.approx(6.0 ** (1 / 27) - 1)


def test_historical_cagr_none_when_span_not_positive():
    assert historical_cagr([1.0, 2.0], periods=[2023, 2023]) is None
    assert historical_cagr([1.0, 2.0], periods=[2023, 2020]) is None


def test_derive_growth_threads_periods_and_records_cagr_years():
    base, meta = derive_growth([1.0, 6.0], periods=[1996, 2023])
    assert meta["cagr_years"] == 27
    assert meta["hist_cagr"] == pytest.approx(6.0 ** (1 / 27) - 1)
    assert base == pytest.approx(meta["hist_cagr"])
    _, meta_default = derive_growth([100.0, 105.0, 121.0])
    assert meta_default["cagr_years"] == 2
    assert meta_default["hist_cagr"] == pytest.approx(0.10)


def test_derive_growth_is_the_historical_cagr_with_no_analyst_term():
    # Q2 (decided): Yahoo's earningsGrowth is one quarter's year-over-year change, no forecast: growth is the
    # clamped historical CAGR alone, and the figure isn't recorded as a growth input.
    base, meta = derive_growth([100.0, 105.0, 121.0])
    assert base == pytest.approx(0.10)
    assert meta["growth_source"] == "hist_only"
    assert "analyst_growth" not in meta


def test_derive_growth_clamps_to_cap_and_floor():
    base, _ = derive_growth([100.0, 400.0])  # 300% growth
    assert base == 0.15
    base, _ = derive_growth([100.0, 50.0])  # negative CAGR -> floor 0
    assert base == 0.0


def test_derive_growth_measures_from_the_first_positive_year_when_four_remain():
    # Q5 (decided), option (c): a window starting on a loss is measured from its first positive year when at least
    # 4 fiscal years remain from it; DELL's FCF from its first positive year.
    base, meta = derive_growth([-1.0, 1.0, 1.1, 1.21, 1.331], periods=[2019, 2020, 2021, 2022, 2023])
    assert meta["growth_source"] == "hist_from_first_positive"
    assert meta["hist_cagr"] == pytest.approx(0.10)
    assert meta["cagr_years"] == 3
    assert base == pytest.approx(0.10)


def test_derive_growth_is_flat_with_fewer_than_four_years_from_the_first_positive():
    # Q5, option (b) behind (c): otherwise a flat 0% with source "none" (Lynch then N/A).
    base, meta = derive_growth([-1.0, -2.0, 1.0, 2.0, 3.0])
    assert base == 0.0
    assert meta["growth_source"] == "none"
    base, meta = derive_growth([1.0, 2.0, -1.0])  # the latest isn't positive: nothing to measure to
    assert (base, meta["growth_source"]) == (0.0, "none")


def test_derive_growth_sources():
    _, meta = derive_growth([100.0, 110.0])
    assert meta["growth_source"] == "hist_only"
    base, meta = derive_growth([-1.0, 5.0])
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

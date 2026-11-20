"""Engine: routing, cross-model medians, verdict rules."""
import pytest

from src.valuation.engine import (
    MEDIAN_MODELS,
    VERDICT_LABELS,
    intrinsic_summary,
    owner_earnings_verdict,
    run_valuations,
    upside_pct,
    verdict,
)
from src.valuation.inputs import ValuationInputs
from src.valuation.models import ValuationResult


def _inputs(**kwargs):
    defaults = dict(ticker="AAA", sector_class="general", fy_records=[],
                    shares_outstanding=100.0, beta=1.0, risk_free_rate=0.045,
                    analyst_growth=None, dividends=[], fy_end_prices={})
    defaults.update(kwargs)
    return ValuationInputs(**defaults)


def test_run_valuations_returns_all_five_models_in_order():
    results = run_valuations(_inputs())
    assert [r.model for r in results] == [
        "dcf", "ddm", "graham", "lynch", "multiples", "owner_earnings"]
    # Empty inputs: every model is N/A with a reason, none raises
    assert all(r.applicable is False and r.na_reason for r in results)


def test_run_valuations_bank_marks_dcf_lynch_sector_na():
    results = {r.model: r for r in run_valuations(_inputs(sector_class="bank"))}
    assert results["dcf"].na_reason == "not applicable to sector 'bank'"
    assert results["lynch"].na_reason == "not applicable to sector 'bank'"


def _res(model, bear, base, bull):
    return ValuationResult(model=model, applicable=True, value_bear=bear,
                           value_base=base, value_bull=bull)


def test_intrinsic_summary_medians():
    results = [
        _res("dcf", 80.0, 100.0, 120.0),
        _res("graham", 60.0, 90.0, 110.0),
        _res("multiples", 70.0, 110.0, 130.0),
        ValuationResult(model="ddm", applicable=False, na_reason="x"),
    ]
    s = intrinsic_summary(results)
    assert s["n_applicable"] == 3
    assert s["median_bear"] == 70.0
    assert s["median_base"] == 100.0
    assert s["median_bull"] == 120.0


def test_intrinsic_summary_leaves_an_na_bear_out_of_the_bear_median():
    # BUG-007: Graham's bear is N/A with a loss year in its window; the bear median is the other models'.
    results = [_res("dcf", 80.0, 100.0, 120.0), _res("graham", None, 90.0, 90.0)]
    s = intrinsic_summary(results)
    assert s["n_applicable"] == 2
    assert s["median_bear"] == 80.0
    assert s["median_base"] == 95.0


def test_intrinsic_summary_with_no_bear_at_all_has_no_bear_median():
    s = intrinsic_summary([_res("graham", None, 90.0, 90.0)])
    assert s["median_bear"] is None
    assert s["median_base"] == 90.0


def test_intrinsic_summary_empty():
    s = intrinsic_summary([ValuationResult(model="dcf", applicable=False)])
    assert s == {"n_applicable": 0, "median_bear": None,
                 "median_base": None, "median_bull": None}


def test_verdict_rules():
    assert verdict(70.0, 120.0, 50.0) == "cheap"
    assert verdict(70.0, 120.0, 90.0) == "fair"
    assert verdict(70.0, 120.0, 70.0) == "fair"   # boundary inclusive
    assert verdict(70.0, 120.0, 121.0) == "expensive"
    assert verdict(None, 120.0, 90.0) is None
    assert verdict(70.0, 120.0, None) is None
    assert verdict(70.0, 120.0, 0.0) is None


def test_a_price_above_every_bull_is_expensive_without_a_bear_median():
    # BUG-304 (decided): SNDK, valued by Graham alone with its bear N/A, at 1,238.03 over a bull of 410.47. Above
    # the bull median is expensive whatever the bear; below it, cheap and fair can't be told apart: no verdict. The
    # screener's SQL filters already read it so ("expensive" needs only median_bull).
    assert verdict(None, 410.47, 1238.03) == "expensive"
    assert verdict(None, 410.47, 300.0) is None
    assert verdict(None, 410.47, 410.47) is None
    assert verdict(None, None, 1238.03) is None


def test_upside_pct():
    assert upside_pct(120.0, 100.0) == pytest.approx(0.20)
    assert upside_pct(None, 100.0) is None
    assert upside_pct(120.0, 0.0) is None


def test_verdict_labels_complete():
    assert VERDICT_LABELS["cheap"] == "Looks cheap"
    assert VERDICT_LABELS[None] == "Not valued"


def test_run_valuations_returns_six_models_in_order():
    results = run_valuations(_inputs())
    assert [r.model for r in results] == [
        "dcf", "ddm", "graham", "lynch", "multiples", "owner_earnings"]


def test_median_models_excludes_owner_earnings():
    assert "owner_earnings" not in MEDIAN_MODELS
    assert set(MEDIAN_MODELS) == {"dcf", "ddm", "graham", "lynch", "multiples"}


def test_owner_earnings_never_moves_the_median():
    """The load-bearing carve-out: a Treasury-discounted value must not be averaged
    into a median of CAPM-discounted ones, or every existing verdict silently drifts
    toward 'cheap'. This is the invariant a future sixth model is most likely to break.
    """
    five = [
        _res("dcf", 80.0, 100.0, 120.0),
        _res("ddm", 60.0, 90.0, 110.0),
        _res("graham", 70.0, 110.0, 130.0),
        _res("lynch", 75.0, 105.0, 125.0),
        _res("multiples", 85.0, 115.0, 135.0),
    ]
    without = intrinsic_summary(five)
    # An owner-earnings row 10x higher than everything else must change nothing.
    with_oe = intrinsic_summary(five + [_res("owner_earnings", 800.0, 1000.0, 1200.0)])
    assert with_oe == without
    assert with_oe["n_applicable"] == 5


def test_owner_earnings_verdict_margin_of_safety():
    res = ValuationResult(
        model="owner_earnings", applicable=True,
        value_bear=80.0, value_base=100.0, value_bull=120.0,
        assumptions={"buy_below": 70.0},
    )
    assert owner_earnings_verdict(res, 60.0) == "cheap"       # below the MOS threshold
    assert owner_earnings_verdict(res, 70.0) == "fair"        # at the threshold
    assert owner_earnings_verdict(res, 100.0) == "fair"       # at intrinsic value
    assert owner_earnings_verdict(res, 101.0) == "expensive"  # above intrinsic value
    assert owner_earnings_verdict(res, None) is None
    assert owner_earnings_verdict(res, 0.0) is None
    assert owner_earnings_verdict(None, 60.0) is None
    na = ValuationResult(model="owner_earnings", applicable=False, na_reason="x")
    assert owner_earnings_verdict(na, 60.0) is None


def _graham_years(*years):
    from src.valuation.inputs import FYRecord
    return [FYRecord(fiscal_year=y, period_end=f"{y}-12-31", net_income=None, total_equity=2000.0, eps_diluted=2.0,
                     shares=100.0, fcf=None, ffo_per_share=None) for y in years]


def test_a_per_share_model_on_a_basis_more_than_two_fiscal_years_old_is_na():
    # Q8 (decided): a company whose usable annual reports stopped (IFRS, BUG-066; or no longer filing) was valued on
    # its last fiscal year beside today's price (Graham on FY2017 in 2026). More than two fiscal years old: N/A.
    from datetime import date
    res = {r.model: r for r in run_valuations(_inputs(fy_records=_graham_years(2021, 2022, 2023),
                                                      as_of=date(2026, 11, 3)))}
    assert res["graham"].applicable is False
    assert res["graham"].na_reason == "basis FY2023 is more than 2 fiscal years old"
    assert res["graham"].basis_fiscal_year == 2023


def test_a_basis_two_fiscal_years_old_still_values():
    from datetime import date
    res = {r.model: r for r in run_valuations(_inputs(fy_records=_graham_years(2022, 2023, 2024),
                                                      as_of=date(2026, 11, 3)))}
    assert res["graham"].applicable is True

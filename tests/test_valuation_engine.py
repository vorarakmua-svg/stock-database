"""Engine: routing, cross-model medians, verdict rules."""
import pytest

from src.valuation.engine import (
    VERDICT_LABELS,
    intrinsic_summary,
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
    assert [r.model for r in results] == ["dcf", "ddm", "graham", "lynch", "multiples"]
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


def test_upside_pct():
    assert upside_pct(120.0, 100.0) == pytest.approx(0.20)
    assert upside_pct(None, 100.0) is None
    assert upside_pct(120.0, 0.0) is None


def test_verdict_labels_complete():
    assert VERDICT_LABELS["cheap"] == "Looks cheap"
    assert VERDICT_LABELS[None] == "Not valued"

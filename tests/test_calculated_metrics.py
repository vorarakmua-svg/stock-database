"""Tests for CalculatedMetrics — pure financial math over plain dicts."""

import pytest

from src.parsers.calculated_metrics import CalculatedMetrics


@pytest.fixture
def calc():
    return CalculatedMetrics()


def test_calculate_all_core_metrics(calc, sample_financials):
    m = calc.calculate_all(
        financials=sample_financials,
        market_data={"market_cap": 5000.0},
        valuation={"ebitda": 240.0},
    )

    assert m["ebitda"] == pytest.approx(240.0)          # 200 operating + 40 D&A
    assert m["free_cash_flow"] == pytest.approx(200.0)  # 250 OCF - 50 CapEx
    assert m["net_debt"] == pytest.approx(280.0)        # (300+100) debt - 120 cash
    assert m["total_debt"] == pytest.approx(400.0)
    assert m["working_capital"] == pytest.approx(300.0)
    assert m["roe"] == pytest.approx(150.0 / 800.0)
    assert m["roa"] == pytest.approx(150.0 / 2000.0)
    assert m["interest_coverage"] == pytest.approx(10.0)  # 200 EBIT / 20 interest
    assert m["gross_margin"] == pytest.approx(0.4)
    assert m["net_margin"] == pytest.approx(0.15)
    assert m["enterprise_value"] == pytest.approx(5280.0)  # 5000 + 280 net debt
    assert m["ev_to_ebitda"] == pytest.approx(22.0)
    assert m["fcf_yield"] == pytest.approx(0.04)


def test_nopat_uses_effective_tax_rate(calc, sample_financials):
    # tax 50 / pretax 200 = 25% effective -> NOPAT = 200 * 0.75
    m = calc.calculate_all(sample_financials)
    assert m["nopat"] == pytest.approx(150.0)


def test_ebitda_fallback_from_net_income(calc):
    """No Operating Income -> EBITDA = NI + D&A + interest + taxes."""
    financials = {
        "Net Income": 100.0,
        "Depreciation and Amortization": 40.0,
        "Interest Expense": 20.0,
        "Income Tax Expense": 30.0,
    }
    assert calc._calculate_ebitda(financials) == pytest.approx(190.0)


def test_ebitda_yahoo_fallback(calc):
    """When SEC data can't produce EBITDA, fall back to Yahoo's value."""
    m = calc.calculate_all(financials={}, valuation={"ebitda": 999.0})
    assert m["ebitda"] == pytest.approx(999.0)
    assert m["ebitda_source"] == "yahoo"


def test_fcf_without_capex_returns_ocf(calc):
    assert calc._calculate_fcf({"Operating Cash Flow": 250.0}) == pytest.approx(250.0)


def test_net_debt_negative_when_net_cash(calc):
    financials = {"Long-Term Debt": 100.0, "Cash and Cash Equivalents": 500.0}
    assert calc._calculate_net_debt(financials) == pytest.approx(-400.0)


def test_interest_coverage_none_when_no_interest(calc):
    financials = {"Operating Income": 200.0, "Interest Expense": 0.0}
    assert calc._calculate_interest_coverage(financials) is None


def test_missing_inputs_return_none(calc):
    empty = {}
    assert calc._calculate_fcf(empty) is None
    assert calc._calculate_roe(empty) is None
    assert calc._calculate_total_debt(empty) is None


def test_calculate_historical(calc, sample_financials):
    hist = calc.calculate_historical({"2024": sample_financials, "2023": sample_financials})
    assert set(hist.keys()) == {"2024", "2023"}
    assert hist["2024"]["ebitda"] == pytest.approx(240.0)

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
        "net_income": 100.0,
        "depreciation_amortization": 40.0,
        "interest_expense": 20.0,
        "income_tax_expense": 30.0,
    }
    assert calc._calculate_ebitda(financials) == pytest.approx(190.0)


def test_ebitda_yahoo_fallback(calc):
    """When SEC data can't produce EBITDA, fall back to Yahoo's value."""
    m = calc.calculate_all(financials={}, valuation={"ebitda": 999.0})
    assert m["ebitda"] == pytest.approx(999.0)
    assert m["ebitda_source"] == "yahoo"


def test_fcf_without_capex_returns_ocf(calc):
    assert calc._calculate_fcf({"operating_cash_flow": 250.0}) == pytest.approx(250.0)


def test_net_debt_negative_when_net_cash(calc):
    financials = {"long_term_debt": 100.0, "cash_and_equivalents": 500.0}
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


def test_calculate_all_general_sector_is_unchanged():
    calc = CalculatedMetrics()
    financials = {"revenue": 1000.0, "net_income": 100.0, "total_assets": 2000.0,
                  "total_equity": 800.0, "operating_income": 150.0,
                  "inventory": 50.0, "cost_of_revenue": 600.0}
    base = calc.calculate_all(financials)            # sector=None
    same = calc.calculate_all(financials, sector="general")
    # No sector keys leak in, nothing is suppressed.
    assert "efficiency_ratio" not in base
    assert "ffo" not in same
    assert base["roic"] == same["roic"]
    assert same["inventory_turnover"] is not None


def test_calculate_all_bank_sector_suppresses_and_adds():
    calc = CalculatedMetrics()
    financials = {"net_interest_income": 50.0, "noninterest_income": 30.0,
                  "noninterest_expense": 48.0, "total_assets": 1000.0,
                  "total_loans": 600.0, "total_deposits": 900.0,
                  "inventory": 5.0, "cost_of_revenue": 10.0,
                  "net_income": 20.0, "total_equity": 120.0}
    m = calc.calculate_all(financials, sector="bank")
    assert m["efficiency_ratio"] == 48.0 / 80.0
    assert m["roic"] is None
    assert m["inventory_turnover"] is None
    assert m["roe"] == 20.0 / 120.0      # kept


def test_calculate_historical_threads_sector():
    calc = CalculatedMetrics()
    annual = {"2024": {"net_income": 100.0, "depreciation_amortization": 40.0,
                       "capex": 10.0, "revenue": 500.0, "total_assets": 2000.0,
                       "total_equity": 800.0}}
    hist = calc.calculate_historical(annual, sector="reit")
    assert hist["2024"]["ffo"] == 140.0
    assert hist["2024"]["roic"] is None

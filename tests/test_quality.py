"""Tests for the data-quality assessment."""

from src.validation.quality import assess_annual


def _balanced_year(**overrides):
    year = {
        "revenue": 1000.0,
        "cost_of_revenue": 600.0,
        "gross_profit": 400.0,
        "operating_income": 200.0,
        "net_income": 150.0,
        "total_assets": 2000.0,
        "total_liabilities": 1200.0,
        "total_equity": 800.0,
        "operating_cash_flow": 250.0,
        "capex": 50.0,
    }
    year.update(overrides)
    return year


def test_clean_company_scores_high():
    report = assess_annual({"2024": _balanced_year()})
    assert report.score == 100
    assert report.findings == []


def test_missing_required_field_flagged():
    year = _balanced_year()
    del year["net_income"]
    report = assess_annual({"2024": year})
    codes = {f.code for f in report.findings}
    assert "missing_field" in codes
    assert report.score < 100


def test_balance_sheet_imbalance_flagged():
    report = assess_annual({"2024": _balanced_year(total_equity=500.0)})  # 1200+500 != 2000
    codes = {f.code for f in report.findings}
    assert "balance_sheet_imbalance" in codes


def test_gross_profit_mismatch_flagged():
    report = assess_annual({"2024": _balanced_year(gross_profit=999.0)})
    codes = {f.code for f in report.findings}
    assert "gross_profit_mismatch" in codes


def test_negative_outflow_sign_flagged():
    report = assess_annual({"2024": _balanced_year(capex=-50.0)})
    codes = {f.code for f in report.findings}
    assert "unexpected_sign" in codes


def test_no_financials_scores_zero():
    report = assess_annual({})
    assert report.score == 0
    assert report.findings[0].code == "no_financials"


def test_revenue_discontinuity_is_info():
    annual = {
        "2023": _balanced_year(revenue=100.0),
        "2024": _balanced_year(revenue=1000.0),  # +900%
    }
    report = assess_annual(annual)
    codes = {f.code for f in report.findings}
    assert "revenue_discontinuity" in codes

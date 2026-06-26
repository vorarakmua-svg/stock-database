"""Tests for the data-quality assessment."""

from src.validation.quality import (
    HIGH,
    LOW,
    MEDIUM,
    Finding,
    assess_annual,
    score_for,
)


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


# ---------------- Sector-aware scoring ----------------

def _bank_year():
    return {
        "revenue": 80.0, "net_income": 20.0,
        "net_interest_income": 50.0, "noninterest_income": 30.0,
        "total_assets": 1000.0, "total_liabilities": 880.0, "total_equity": 120.0,
        "total_deposits": 900.0, "operating_cash_flow": 40.0,
    }


def test_bank_not_penalized_for_missing_operating_income():
    # No operating_income / gross_profit (banks don't report them).
    report = assess_annual({"2024": _bank_year()}, sector="bank")
    missing = {f.message for f in report.findings if f.code == "missing_field"}
    assert not any("operating_income" in m for m in missing)
    assert report.score == 100


def test_general_company_is_penalized_for_missing_operating_income():
    year = _bank_year()  # lacks operating_income
    report = assess_annual({"2024": year}, sector="general")
    missing = {f.message for f in report.findings if f.code == "missing_field"}
    assert any("operating_income" in m for m in missing)


def test_balance_check_skipped_when_liabilities_derived():
    year = _balanced_year(total_equity=500.0)  # 1200 + 500 != 2000 -> would flag
    year["_source_tags"] = {"total_liabilities": "derived"}
    report = assess_annual({"2024": year})
    codes = {f.code for f in report.findings}
    assert "balance_sheet_imbalance" not in codes


# ---------------- score_for unit tests ----------------

def test_score_for_no_findings_is_100():
    assert score_for([]) == 100


def test_score_for_sums_penalties_and_clamps():
    findings = [Finding(HIGH, "x", "m"), Finding(MEDIUM, "y", "m"), Finding(LOW, "z", "m")]
    assert score_for(findings) == 100 - 25 - 10 - 3  # 62
    # clamps at 0
    assert score_for([Finding(HIGH, "x", "m")] * 10) == 0


def test_energy_required_set_drops_operating_income():
    # An energy company reports everything required EXCEPT operating_income
    # (integrated oil majors don't file OperatingIncomeLoss).
    period = {"revenue": 100.0, "net_income": 10.0, "total_assets": 200.0,
              "total_liabilities": 120.0, "total_equity": 80.0, "operating_cash_flow": 20.0}
    annual = {"2024": period}
    energy = assess_annual(annual, sector="energy")
    assert not any(f.code == "missing_field" for f in energy.findings)
    assert energy.score == 100
    # The SAME data under the general sector still requires operating_income.
    general = assess_annual(annual, sector="general")
    assert any(f.code == "missing_field" and "operating_income" in f.message
               for f in general.findings)

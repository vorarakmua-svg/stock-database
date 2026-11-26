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


def test_operating_income_not_required():
    # No sector requires operating_income: diversified multinationals (JNJ, XOM)
    # report pretax income by geography rather than a tagged operating-income line.
    period = {"revenue": 100.0, "net_income": 10.0, "total_assets": 200.0,
              "total_liabilities": 120.0, "total_equity": 80.0, "operating_cash_flow": 20.0}
    annual = {"2024": period}
    for sector in ("general", "energy"):
        report = assess_annual(annual, sector=sector)
        assert not any(f.code == "missing_field" for f in report.findings)
        assert report.score == 100


def test_bank_not_required_to_report_noninterest_income():
    # Broker-dealers classified as banks (e.g. Schwab) report fee/trading/commission
    # components without a noninterest-income aggregate -> its absence is not a defect.
    period = {"revenue": 100.0, "net_income": 10.0, "net_interest_income": 40.0,
              "total_assets": 1000.0, "total_liabilities": 900.0, "total_equity": 100.0,
              "total_deposits": 500.0, "operating_cash_flow": 20.0}
    report = assess_annual({"2024": period}, sector="bank")
    assert not any(f.code == "missing_field" for f in report.findings)
    assert report.score == 100


def _scored(annual):
    from src.validation.quality import scored_years  # BUG-112's
    return scored_years(annual)


def _gev_shaped():
    # BUG-112: four years of statements and a fifth the earliest 10-K's equity statement opens with: its equity alone.
    annual = {str(y): _balanced_year() for y in (2022, 2023, 2024, 2025)}
    annual["2021"] = {"total_equity": 800.0, "period_end": "2021-12-31", "calendar_year": 2021,
                      "_source_tags": {"total_equity": "StockholdersEquity"}}
    return annual


def test_a_year_known_only_from_an_opening_balance_is_not_scored():
    annual = _gev_shaped()
    assert _scored(annual) == ["2025", "2024", "2023", "2022"]
    report = assess_annual(annual)
    assert report.score == 100 and report.findings == []


def test_every_opening_balance_year_before_the_first_reported_one_is_set_aside():
    annual = _gev_shaped()
    annual["2020"] = {"cash_and_equivalents": 50.0}
    del annual["2025"]
    assert _scored(annual) == ["2024", "2023", "2022"]


def test_a_later_year_with_balances_alone_stays_scored():
    # EXE's fresh-start FY2021: its flows lie in two stub periods the record doesn't hold, so its gap is the record's.
    annual = _gev_shaped()
    annual["2020"] = _balanced_year()
    assert _scored(annual) == ["2025", "2024", "2023", "2022", "2021"]
    assert {f.period for f in assess_annual(annual).findings} == {"2021"}


def test_a_year_with_any_flow_figure_is_scored():
    annual = _gev_shaped()
    annual["2021"]["weighted_avg_shares_diluted"] = 100.0
    assert "2021" in _scored(annual)
    assert assess_annual(annual).score == 100 - 5 * 10  # revenue, net income, assets, liabilities, CFO


def test_a_record_of_balances_alone_is_still_scored():
    annual = {"2024": {"total_assets": 2000.0}, "2023": {"total_assets": 1900.0}}
    assert _scored(annual) == ["2024", "2023"]
    assert assess_annual(annual).score < 100

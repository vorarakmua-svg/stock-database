"""Integrity checks: magnitude outliers, cash reconciliation, quarterly sums, ratio bounds."""

from src.validation.integrity import check_cashflow_reconciliation, check_field_outliers


def _yr(revenue, assets):
    return {"revenue": revenue, "total_assets": assets}


def test_outlier_fires_on_1000x_spike():
    annual = {
        "2021": _yr(1.0e9, 2.0e9),
        "2022": _yr(1.1e9, 2.1e9),
        "2023": _yr(1.2e9, 2.2e9),
        "2024": _yr(1.2e12, 2.3e9),  # revenue 1000x its own median
    }
    findings = check_field_outliers(annual, {"2024", "2023", "2022", "2021"})
    codes = [(f.code, f.period) for f in findings]
    assert ("magnitude_outlier", "2024") in codes
    assert all(f.severity == "high" for f in findings)


def test_outlier_silent_on_real_growth_and_small_series():
    # A real ~3x trend over 4 years: no field is 100x its own median.
    annual = {
        "2021": _yr(1.0e9, 2.0e9), "2022": _yr(1.5e9, 2.5e9),
        "2023": _yr(2.2e9, 3.0e9), "2024": _yr(3.0e9, 3.5e9),
    }
    assert check_field_outliers(annual, {"2021", "2022", "2023", "2024"}) == []
    # Fewer than 3 data points -> no median basis -> silent.
    assert check_field_outliers({"2024": _yr(1.0e9, 2.0e9)}, {"2024"}) == []


def test_outlier_only_flags_scored_years():
    annual = {
        "2019": _yr(1.0e9, 2.0e9), "2020": _yr(1.1e9, 2.1e9),
        "2021": _yr(1.2e9, 2.2e9), "2022": _yr(9.9e14, 2.3e9),  # spike, but out of window
    }
    # 2022 not in the scored set -> not flagged.
    assert check_field_outliers(annual, {"2021", "2020", "2019"}) == []


def _cf(cash, ocf, icf, fcf):
    return {"cash_and_equivalents": cash, "operating_cash_flow": ocf,
            "investing_cash_flow": icf, "financing_cash_flow": fcf}


def test_cashflow_imbalance_fires_when_flows_miss_delta_cash():
    annual = {
        "2023": _cf(1.00e9, 0, 0, 0),
        # delta_cash = +1.0e9, but flows sum to +0.5e9 -> 50% residual.
        "2024": _cf(2.00e9, 0.4e9, -0.1e9, 0.2e9),
    }
    findings = check_cashflow_reconciliation(annual, {"2024", "2023"})
    assert [(f.code, f.period, f.severity) for f in findings] == [
        ("cashflow_imbalance", "2024", "medium")]


def test_cashflow_reconcile_tolerates_small_gap():
    # delta_cash = +1.0e9; flows sum to +0.97e9 -> 3% residual, within 5%.
    annual = {"2023": _cf(1.0e9, 0, 0, 0), "2024": _cf(2.0e9, 0.9e9, -0.1e9, 0.17e9)}
    assert check_cashflow_reconciliation(annual, {"2024", "2023"}) == []


def test_cashflow_reconcile_silent_on_missing_fields():
    annual = {"2023": {"cash_and_equivalents": 1.0e9},
              "2024": {"cash_and_equivalents": 2.0e9}}  # no flow fields
    assert check_cashflow_reconciliation(annual, {"2024", "2023"}) == []

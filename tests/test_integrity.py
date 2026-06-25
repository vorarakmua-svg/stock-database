"""Integrity checks: magnitude outliers, cash reconciliation, quarterly sums, ratio bounds."""

from src.validation.integrity import (
    check_cashflow_reconciliation,
    check_field_outliers,
    check_quarterly_sums,
    check_ratio_bounds,
)


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


def _q(fy, fq, revenue):
    return {"fiscal_year": fy, "fiscal_quarter": fq, "revenue": revenue}


def test_quarterly_sum_mismatch_fires():
    annual = {"2024": {"revenue": 1.0e9}}
    quarterly = {  # quarters sum to 0.80e9, annual says 1.0e9 -> 20% off
        "2024-03-31": _q(2024, 1, 0.20e9), "2024-06-30": _q(2024, 2, 0.20e9),
        "2024-09-30": _q(2024, 3, 0.20e9), "2024-12-31": _q(2024, 4, 0.20e9),
    }
    findings = check_quarterly_sums(annual, quarterly, {"2024"})
    assert [(f.code, f.period, f.severity) for f in findings] == [
        ("quarterly_sum_mismatch", "2024", "medium")]


def test_quarterly_sum_exact_ladder_is_silent():
    annual = {"2024": {"revenue": 1.0e9}}
    quarterly = {
        "2024-03-31": _q(2024, 1, 0.25e9), "2024-06-30": _q(2024, 2, 0.25e9),
        "2024-09-30": _q(2024, 3, 0.25e9), "2024-12-31": _q(2024, 4, 0.25e9),
    }
    assert check_quarterly_sums(annual, quarterly, {"2024"}) == []


def test_quarterly_sum_silent_with_only_three_quarters():
    annual = {"2024": {"revenue": 1.0e9}}
    quarterly = {
        "2024-03-31": _q(2024, 1, 0.25e9), "2024-06-30": _q(2024, 2, 0.25e9),
        "2024-09-30": _q(2024, 3, 0.25e9),  # Q4 missing
    }
    assert check_quarterly_sums(annual, quarterly, {"2024"}) == []


def test_ratio_bounds_fire_on_impossible_values():
    historical = {"2024": {"gross_margin": 2.5, "efficiency_ratio": -0.3, "roe": 0.2}}
    findings = check_ratio_bounds(historical, {"2024"})
    metrics_flagged = {f.message.split("'")[1] for f in findings}
    assert "gross_margin" in metrics_flagged      # >1.01 impossible
    assert "efficiency_ratio" in metrics_flagged   # <=0 impossible
    assert "roe" not in metrics_flagged            # 20% is fine
    assert all(f.severity == "low" and f.code == "ratio_out_of_bounds" for f in findings)


def test_ratio_bounds_silent_on_strong_but_real_values():
    # Apple-like: 82% ROIC, 26% net margin, 45% gross margin — all plausible.
    historical = {"2024": {"roic": 0.82, "net_margin": 0.26, "gross_margin": 0.45}}
    assert check_ratio_bounds(historical, {"2024"}) == []


def test_ratio_bounds_skip_none_and_unscored_years():
    historical = {"2024": {"gross_margin": None}, "2019": {"gross_margin": 9.0}}
    assert check_ratio_bounds(historical, {"2024"}) == []  # None skipped, 2019 unscored


def test_magnitude_outlier_excludes_volatile_cashflow_residuals():
    # net_change_in_cash legitimately swings >100x; it must NOT be flagged
    # (excluded from the outlier candidate set).
    annual = {
        "2021": {"net_change_in_cash": 5.0e7},
        "2022": {"net_change_in_cash": 5.0e7},
        "2023": {"net_change_in_cash": 5.0e7},
        "2024": {"net_change_in_cash": 2.0e10},  # 400x swing
    }
    assert check_field_outliers(annual, {"2021", "2022", "2023", "2024"}) == []

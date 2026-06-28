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
    # Revenue spikes in 2022 and reverts in 2023 -> a one-off anomaly (interior year).
    annual = {
        "2021": _yr(1.0e9, 2.0e9),
        "2022": _yr(1.2e12, 2.1e9),
        "2023": _yr(1.1e9, 2.2e9),
        "2024": _yr(1.2e9, 2.3e9),
    }
    findings = check_field_outliers(annual, {"2021", "2022", "2023", "2024"})
    codes = [(f.code, f.period) for f in findings]
    assert ("magnitude_outlier", "2022") in codes
    assert all(f.severity == "high" for f in findings)


def test_outlier_silent_on_persistent_step_change():
    # Goodwill stays tiny for years, then jumps on an acquisition and PERSISTS.
    # With many tiny pre-merger years the all-history median is tiny, so the OLD
    # median check flagged every post-merger year (the Realty Income / VEREIT false
    # positive). Spike-and-revert must NOT flag a jump that persists (~1x the next).
    tiny = {"goodwill": 1.5e7}
    big = {"goodwill": 3.7e9}
    annual = {
        "2017": tiny, "2018": tiny, "2019": tiny, "2020": tiny, "2021": tiny,
        "2022": big, "2023": big, "2024": big,
    }
    assert check_field_outliers(annual, {"2022", "2023", "2024"}) == []


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


def test_cashflow_imbalance_fires_when_sections_dont_match_net_change():
    # sections+FX = 0.5e9 but the reported net change is 1.0e9 -> 50% residual.
    annual = {"2024": {"net_change_in_cash": 1.0e9, "operating_cash_flow": 0.4e9,
                       "investing_cash_flow": -0.1e9, "financing_cash_flow": 0.2e9}}
    findings = check_cashflow_reconciliation(annual, {"2024"})
    assert [(f.code, f.period, f.severity) for f in findings] == [
        ("cashflow_imbalance", "2024", "medium")]


def test_cashflow_reconcile_silent_when_consistent_including_fx():
    # net_change == OCF+ICF+FCF+FX exactly, with a large non-zero FX -> silent.
    annual = {"2024": {"net_change_in_cash": -42.0e6, "operating_cash_flow": 30.0e6,
                       "investing_cash_flow": -20.0e6, "financing_cash_flow": -12.0e6,
                       "fx_effect_on_cash": -40.0e6}}  # 30 - 20 - 12 - 40 = -42
    assert check_cashflow_reconciliation(annual, {"2024"}) == []


def test_cashflow_reconcile_skips_when_net_change_or_section_absent():
    # net_change absent -> skip
    a1 = {"2024": {"operating_cash_flow": 1.0e9, "investing_cash_flow": 0.0,
                   "financing_cash_flow": 0.0}}
    assert check_cashflow_reconciliation(a1, {"2024"}) == []
    # a section absent -> skip
    a2 = {"2024": {"net_change_in_cash": 1.0e9, "operating_cash_flow": 1.0e9,
                   "financing_cash_flow": 0.0}}
    assert check_cashflow_reconciliation(a2, {"2024"}) == []


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
    findings = check_ratio_bounds(historical, {}, {"2024"})
    metrics_flagged = {f.message.split("'")[1] for f in findings}
    assert "gross_margin" in metrics_flagged      # >1.01 impossible
    assert "efficiency_ratio" in metrics_flagged   # <=0 impossible
    assert "roe" not in metrics_flagged            # 20% is fine
    assert all(f.severity == "low" and f.code == "ratio_out_of_bounds" for f in findings)


def test_ratio_bounds_silent_on_strong_but_real_values():
    # Apple-like: 82% ROIC, 26% net margin, 45% gross margin — all plausible.
    historical = {"2024": {"roic": 0.82, "net_margin": 0.26, "gross_margin": 0.45}}
    assert check_ratio_bounds(historical, {}, {"2024"}) == []


def test_ratio_bounds_skip_none_and_unscored_years():
    historical = {"2024": {"gross_margin": None}, "2019": {"gross_margin": 9.0}}
    assert check_ratio_bounds(historical, {}, {"2024"}) == []  # None skipped, 2019 unscored


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


def test_outlier_excludes_event_driven_lumpy_flows():
    # debt_issued spikes 1000x in an interior year and reverts (a real one-off bond
    # issuance) -> excluded, not flagged. revenue spikes the same way -> still flagged,
    # proving the exclusion is field-scoped, not global.
    annual = {
        "2021": {"debt_issued": 1.0e7, "revenue": 1.0e9},
        "2022": {"debt_issued": 1.0e10, "revenue": 1.2e12},  # both spike here
        "2023": {"debt_issued": 1.0e7, "revenue": 1.1e9},    # both revert
        "2024": {"debt_issued": 1.2e7, "revenue": 1.2e9},
    }
    findings = check_field_outliers(annual, {"2021", "2022", "2023", "2024"})
    fields = [f.message.split("'")[1] for f in findings]
    assert "revenue" in fields              # recurring field still flagged
    assert "debt_issued" not in fields      # event-driven flow excluded


def test_cashflow_imbalance_silent_when_residual_immaterial_to_gross():
    # Insurer/discontinued-ops shape: net change != OCF+ICF+FCF, but the residual is
    # tiny vs the GROSS cash activity (huge OCF/ICF that nearly cancel) -> not flagged.
    annual = {"2024": {"net_change_in_cash": -571.0e6, "operating_cash_flow": 17000.0e6,
                       "investing_cash_flow": -17000.0e6, "financing_cash_flow": 26.0e6}}
    # sections sum to 26e6; residual = -597e6; gross = 34026e6; |residual|/gross = 1.8% < 5%
    assert check_cashflow_reconciliation(annual, {"2024"}) == []


def test_quarterly_sum_aggregates_to_one_finding_per_year():
    # Two flow fields both mismatch in 2024 -> exactly ONE finding for the year.
    annual = {"2024": {"revenue": 1.0e9, "net_income": 1.0e9}}

    def _q2(fq, rev, ni):
        return {"fiscal_year": 2024, "fiscal_quarter": fq, "revenue": rev, "net_income": ni}

    quarterly = {  # each field's quarters sum to 0.8e9 vs annual 1.0e9 (20% off)
        "2024-03-31": _q2(1, 0.20e9, 0.20e9), "2024-06-30": _q2(2, 0.20e9, 0.20e9),
        "2024-09-30": _q2(3, 0.20e9, 0.20e9), "2024-12-31": _q2(4, 0.20e9, 0.20e9),
    }
    findings = check_quarterly_sums(annual, quarterly, {"2024"})
    assert len(findings) == 1
    assert findings[0].code == "quarterly_sum_mismatch"
    assert findings[0].period == "2024"
    assert "revenue" in findings[0].message and "net_income" in findings[0].message


def test_quarterly_sum_skips_event_driven_flows():
    # A lumpy event-driven flow (debt_repaid) whose quarters don't sum to annual is NOT
    # flagged; a core field (revenue) mismatch in the same year IS -- skip is field-scoped.
    annual = {"2024": {"debt_repaid": 1.0e9, "revenue": 1.0e9}}

    def _q(fq, dr, rev):
        return {"fiscal_year": 2024, "fiscal_quarter": fq, "debt_repaid": dr, "revenue": rev}

    quarterly = {  # each field's four quarters sum to 0.8e9 vs annual 1.0e9 (20% off)
        "2024-03-31": _q(1, 0.20e9, 0.20e9), "2024-06-30": _q(2, 0.20e9, 0.20e9),
        "2024-09-30": _q(3, 0.20e9, 0.20e9), "2024-12-31": _q(4, 0.20e9, 0.20e9),
    }
    findings = check_quarterly_sums(annual, quarterly, {"2024"})
    assert len(findings) == 1
    assert "revenue" in findings[0].message
    assert "debt_repaid" not in findings[0].message


def test_ratio_bounds_suppresses_roe_on_weak_equity():
    # roe = 14.5 is out of bounds, but equity is ~1% of assets (buyback-depleted) -> the
    # extreme value is a denominator artifact, not a data error -> no finding.
    historical = {"2024": {"roe": 14.5}}
    weak = {"2024": {"total_equity": 1.0e9, "total_assets": 100.0e9}}
    assert check_ratio_bounds(historical, weak, {"2024"}) == []
    # the same roe on a NORMAL equity base is a genuine error -> still flagged.
    normal = {"2024": {"total_equity": 50.0e9, "total_assets": 100.0e9}}
    findings = check_ratio_bounds(historical, normal, {"2024"})
    assert [(f.code, f.message[:5]) for f in findings] == [("ratio_out_of_bounds", "'roe'")]


def test_cashflow_reconcile_silent_with_continuing_sections_plus_discontinued():
    # PRU-shaped: OCF/ICF/FCF are continuing-only; a separate discontinued line and FX
    # make up the difference to the reported net change. Adding the discontinued line
    # reconciles, so no finding.
    annual = {"2021": {
        "net_change_in_cash": -921.0e6,
        "operating_cash_flow": 9812.0e6,
        "investing_cash_flow": -5342.0e6,
        "financing_cash_flow": -3011.0e6,
        "fx_effect_on_cash": -309.0e6,
        "cash_from_discontinued_operations": -2071.0e6,
    }}  # 9812 - 5342 - 3011 - 309 - 2071 = -921
    assert check_cashflow_reconciliation(annual, {"2021"}) == []


def test_cashflow_reconcile_silent_total_basis_no_discontinued():
    # Sections already include everything; no discontinued field -> still silent (regression).
    annual = {"2024": {"net_change_in_cash": -42.0e6, "operating_cash_flow": 30.0e6,
                       "investing_cash_flow": -20.0e6, "financing_cash_flow": -12.0e6,
                       "fx_effect_on_cash": -40.0e6}}
    assert check_cashflow_reconciliation(annual, {"2024"}) == []


def test_cashflow_imbalance_fires_when_neither_basis_reconciles():
    # A genuine break: neither expected nor expected+disc matches the net change.
    annual = {"2024": {"net_change_in_cash": 5.0e9, "operating_cash_flow": 0.4e9,
                       "investing_cash_flow": -0.1e9, "financing_cash_flow": 0.2e9,
                       "cash_from_discontinued_operations": 0.1e9}}
    findings = check_cashflow_reconciliation(annual, {"2024"})
    assert [(f.code, f.period) for f in findings] == [("cashflow_imbalance", "2024")]


def test_outlier_skips_discontinued_ops_fields():
    # cash_from_discontinued_operations spikes 100x and reverts, but is excluded.
    annual = {
        "2021": {"cash_from_discontinued_operations": 1.0e7, "revenue": 1.0e9},
        "2022": {"cash_from_discontinued_operations": 1.0e12, "revenue": 1.1e9},
        "2023": {"cash_from_discontinued_operations": 1.1e7, "revenue": 1.2e9},
    }
    findings = check_field_outliers(annual, {"2021", "2022", "2023"})
    assert all("discontinued" not in f.message for f in findings)
    assert findings == []  # revenue is a smooth trend; nothing else flags


def test_quarterly_sum_skips_discontinued_ops_fields():
    annual = {"2024": {"cash_from_discontinued_operations": 1.0e9, "revenue": 1.0e9}}

    def _qd(fq, disc, rev):
        return {"fiscal_year": 2024, "fiscal_quarter": fq,
                "cash_from_discontinued_operations": disc, "revenue": rev}

    quarterly = {  # each field's quarters sum to 0.8e9 vs annual 1.0e9 (20% off)
        "2024-03-31": _qd(1, 0.20e9, 0.20e9), "2024-06-30": _qd(2, 0.20e9, 0.20e9),
        "2024-09-30": _qd(3, 0.20e9, 0.20e9), "2024-12-31": _qd(4, 0.20e9, 0.20e9),
    }
    findings = check_quarterly_sums(annual, quarterly, {"2024"})
    assert len(findings) == 1
    assert "revenue" in findings[0].message
    assert "discontinued" not in findings[0].message

"""Sector-aware ratio functions and the suppression/orchestration layer."""

from src.parsers.sector_metrics import apply_sector, bank_metrics


def test_bank_metrics_compute_from_canonical_fields():
    f = {
        "net_interest_income": 50.0, "noninterest_income": 30.0,
        "noninterest_expense": 48.0, "total_assets": 1000.0,
        "total_loans": 600.0, "total_deposits": 900.0,
    }
    m = bank_metrics(f)
    assert m["efficiency_ratio"] == 48.0 / 80.0           # exact
    assert m["loan_to_deposit"] == 600.0 / 900.0          # exact
    assert m["net_interest_margin"] == 50.0 / 1000.0      # proxy


def test_bank_metrics_missing_inputs_are_none():
    assert bank_metrics({})["efficiency_ratio"] is None
    assert bank_metrics({})["loan_to_deposit"] is None


def test_apply_sector_bank_adds_and_suppresses():
    metrics = {
        "roic": 0.20, "inventory_turnover": 5.0, "ebitda": 100.0,
        "interest_coverage": 8.0, "net_debt": 10.0,
        "roe": 0.15, "net_margin": 0.10,
    }
    f = {
        "net_interest_income": 50.0, "noninterest_income": 30.0,
        "noninterest_expense": 48.0, "total_assets": 1000.0,
        "total_loans": 600.0, "total_deposits": 900.0,
    }
    apply_sector(metrics, f, "bank")
    # generic ratios that don't apply to a bank are nulled
    assert metrics["roic"] is None
    assert metrics["inventory_turnover"] is None
    assert metrics["ebitda"] is None
    assert metrics["interest_coverage"] is None
    assert metrics["net_debt"] is None
    # universally meaningful ones are kept
    assert metrics["roe"] == 0.15
    assert metrics["net_margin"] == 0.10
    # bank ratios are added
    assert metrics["efficiency_ratio"] == 48.0 / 80.0
    # proxy basis recorded
    assert "net_interest_margin" in metrics["_basis"]


def test_apply_sector_none_is_noop():
    metrics = {"roic": 0.2, "inventory_turnover": 5.0}
    apply_sector(metrics, {}, None)
    assert metrics == {"roic": 0.2, "inventory_turnover": 5.0}

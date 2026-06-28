"""Tests for derived-field accounting identities."""

from src.parsers.derived_fields import apply_derivations


def test_total_liabilities_derived_from_assets_minus_equity():
    period = {"total_assets": 1000.0, "total_equity": 400.0}
    derived = apply_derivations(period)
    assert period["total_liabilities"] == 600.0
    assert "total_liabilities" in derived
    assert period["_source_tags"]["total_liabilities"] == "derived"


def test_derivation_does_not_overwrite_reported():
    period = {"total_assets": 1000.0, "total_equity": 400.0,
              "total_liabilities": 590.0,  # reported (e.g. a small rounding diff)
              "_source_tags": {"total_liabilities": "Liabilities"}}
    apply_derivations(period)
    assert period["total_liabilities"] == 590.0  # untouched
    assert period["_source_tags"]["total_liabilities"] == "Liabilities"


def test_gross_profit_derived():
    period = {"revenue": 1000.0, "cost_of_revenue": 600.0}
    apply_derivations(period)
    assert period["gross_profit"] == 400.0
    assert period["_source_tags"]["gross_profit"] == "derived"


def test_bank_revenue_derived_from_components():
    # No top-line revenue tag, but bank components present.
    period = {"net_interest_income": 50.0, "noninterest_income": 30.0}
    apply_derivations(period)
    assert period["revenue"] == 80.0
    assert period["_source_tags"]["revenue"] == "derived"


def test_no_derivation_when_inputs_missing():
    period = {"total_assets": 1000.0}  # no equity
    derived = apply_derivations(period)
    assert "total_liabilities" not in period
    assert derived == []


def test_discontinued_ops_cash_derived_from_components():
    period = {
        "discontinued_operating_cash_flow": -1500.0,
        "discontinued_investing_cash_flow": -500.0,
        "discontinued_financing_cash_flow": -71.0,
    }
    derived = apply_derivations(period)
    assert period["cash_from_discontinued_operations"] == -2071.0
    assert "cash_from_discontinued_operations" in derived
    assert period["_source_tags"]["cash_from_discontinued_operations"] == "derived"


def test_discontinued_ops_cash_not_overwritten_when_reported():
    period = {
        "cash_from_discontinued_operations": -2071.0,
        "_source_tags": {"cash_from_discontinued_operations":
                         "NetCashProvidedByUsedInDiscontinuedOperations"},
        "discontinued_operating_cash_flow": -1500.0,
    }
    apply_derivations(period)
    assert period["cash_from_discontinued_operations"] == -2071.0  # untouched
    assert period["_source_tags"]["cash_from_discontinued_operations"] == \
        "NetCashProvidedByUsedInDiscontinuedOperations"


def test_discontinued_ops_cash_sums_only_present_components():
    period = {"discontinued_operating_cash_flow": -1500.0}  # only one component
    apply_derivations(period)
    assert period["cash_from_discontinued_operations"] == -1500.0


def test_discontinued_ops_cash_absent_when_no_components():
    period = {"revenue": 100.0}
    derived = apply_derivations(period)
    assert "cash_from_discontinued_operations" not in period
    assert "cash_from_discontinued_operations" not in derived

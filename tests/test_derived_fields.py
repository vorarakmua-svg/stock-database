"""Tests for derived-field accounting identities."""

import pytest

from src.parsers.derived_fields import apply_derivations


def test_total_liabilities_derived_from_assets_minus_equity():
    period = {"total_assets": 1000.0, "total_equity": 400.0}
    derived = apply_derivations(period)
    assert period["total_liabilities"] == 600.0
    assert "total_liabilities" in derived
    assert period["_source_tags"]["total_liabilities"] == "derived"


def test_derived_liabilities_leave_out_the_noncontrolling_interest():
    # BUG-100: total equity is the parent's (`StockholdersEquity`) whenever that is filed, so assets less it held the
    # non-controlling interest too: INTC's FY2025 liabilities read $97.15B, where its filed figures give $85.07B.
    period = {"total_assets": 211.43e9, "total_equity": 114.28e9, "minority_interest": 12.08e9,
              "_source_tags": {"total_assets": "Assets", "total_equity": "StockholdersEquity",
                               "minority_interest": "MinorityInterest"}}
    apply_derivations(period)
    assert period["total_liabilities"] == pytest.approx(85.07e9)


def test_derived_liabilities_beside_equity_that_includes_the_noncontrolling_interest():
    # VZ's FY2012 equity is the tag that includes the non-controlling interest (its 45% of Verizon Wireless), so
    # assets less it is already the liabilities.
    tags = {"total_equity": "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"}
    period = {"total_assets": 225.22e9, "total_equity": 85.53e9, "minority_interest": 52.38e9, "_source_tags": tags}
    apply_derivations(period)
    assert period["total_liabilities"] == pytest.approx(225.22e9 - 85.53e9)


def test_derived_liabilities_take_total_equity_as_the_parents_when_its_tag_is_unknown():
    # `total_equity` is the parent's by definition (its first tag; quality's identity adds the interest to it).
    period = {"total_assets": 1000.0, "total_equity": 400.0, "minority_interest": 50.0}
    apply_derivations(period)
    assert period["total_liabilities"] == 550.0


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

"""Tests for the canonical registry and the Pydantic period validator."""

from src.mappings.canonical import CANONICAL_BY_KEY, CANONICAL_FIELDS
from src.models.canonical import validate_period


def test_registry_keys_unique_and_have_tags():
    keys = [f.key for f in CANONICAL_FIELDS]
    assert len(keys) == len(set(keys)), "canonical keys must be unique"
    assert all(f.tags for f in CANONICAL_FIELDS), "every field needs candidate tags"


def test_validate_period_coerces_and_preserves_provenance():
    raw = {
        "fiscal_year": 2024,
        "revenue": "1000",          # string -> coerced to float
        "net_income": 150.0,
        "_source_tags": {"revenue": "Revenues"},
    }
    clean, errors = validate_period(raw)
    assert errors == []
    assert clean["revenue"] == 1000.0
    assert isinstance(clean["revenue"], float)
    assert clean["_source_tags"] == {"revenue": "Revenues"}


def test_validate_period_drops_none_values():
    clean, errors = validate_period({"fiscal_year": 2024, "revenue": 100.0})
    assert errors == []
    # Optional fields left unset are not emitted.
    assert "net_income" not in clean


def test_validate_period_reports_bad_type():
    raw = {"fiscal_year": 2024, "revenue": "not-a-number"}
    clean, errors = validate_period(raw)
    assert errors  # validation failed
    assert clean == raw  # original returned so data isn't lost


def test_revenue_field_lists_apple_tag():
    # Apple reports under the ASC 606 contract tag; it must be a revenue candidate.
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in CANONICAL_BY_KEY["revenue"].tags


def test_discontinued_ops_cash_fields_registered():
    from src.mappings.canonical import CANONICAL_BY_KEY
    expected = {
        "cash_from_discontinued_operations": "NetCashProvidedByUsedInDiscontinuedOperations",
        "discontinued_operating_cash_flow":
            "CashProvidedByUsedInOperatingActivitiesDiscontinuedOperations",
        "discontinued_investing_cash_flow":
            "CashProvidedByUsedInInvestingActivitiesDiscontinuedOperations",
        "discontinued_financing_cash_flow":
            "CashProvidedByUsedInFinancingActivitiesDiscontinuedOperations",
    }
    for key, primary_tag in expected.items():
        assert key in CANONICAL_BY_KEY, key
        field = CANONICAL_BY_KEY[key]
        assert field.tags[0] == primary_tag
        assert field.statement == "cashflow"
        assert field.kind == "duration"

"""Tests for unmapped-tag detection (taxonomy evolution / firm variability)."""

from datetime import datetime

from src.mappings.canonical import ALL_MAPPED_TAGS
from src.parsers.unmapped import detect_unmapped
from tests.conftest import usd

_YEAR = datetime.now().year


def _facts(**tags):
    return {"facts": {"us-gaap": {t: {"label": t, "units": {"USD": e}}
                                  for t, e in tags.items()}}}


def test_all_mapped_tags_contains_known_excludes_invented():
    assert "Revenues" in ALL_MAPPED_TAGS
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in ALL_MAPPED_TAGS
    assert "NewRevenueConceptZZZ" not in ALL_MAPPED_TAGS


def test_future_tag_is_captured():
    # A hypothetical new taxonomy tag with a material, recent value.
    facts = _facts(
        Revenues=[usd(1_000, end=f"{_YEAR}-09-30", form="10-K", filed=f"{_YEAR}-11-01")],
        NewRevenueConceptZZZ=[usd(5_000_000_000, end=f"{_YEAR}-09-30",
                                  form="10-K", filed=f"{_YEAR}-11-01")],
    )
    result = detect_unmapped(facts)
    tags = {r["tag"] for r in result}
    assert "NewRevenueConceptZZZ" in tags     # unmapped -> captured
    assert "Revenues" not in tags             # mapped -> skipped
    rec = next(r for r in result if r["tag"] == "NewRevenueConceptZZZ")
    assert rec["value"] == 5_000_000_000
    assert rec["period_end"] == f"{_YEAR}-09-30"


def test_subthreshold_and_old_facts_excluded():
    facts = _facts(
        TinyTagZZZ=[usd(100, end=f"{_YEAR}-09-30", form="10-K", filed=f"{_YEAR}-11-01")],
        StaleTagZZZ=[usd(9_000_000_000, end="2005-09-30", form="10-K", filed="2005-11-01")],
    )
    result = detect_unmapped(facts)
    tags = {r["tag"] for r in result}
    assert "TinyTagZZZ" not in tags    # below materiality floor
    assert "StaleTagZZZ" not in tags   # older than the lookback window


def test_keeps_most_recent_fact_per_tag():
    facts = _facts(BigTagZZZ=[
        usd(2_000_000_000, end=f"{_YEAR - 1}-09-30", form="10-K", filed=f"{_YEAR - 1}-11-01"),
        usd(3_000_000_000, end=f"{_YEAR}-09-30", form="10-K", filed=f"{_YEAR}-11-01"),
    ])
    rec = next(r for r in detect_unmapped(facts) if r["tag"] == "BigTagZZZ")
    assert rec["value"] == 3_000_000_000  # latest period kept

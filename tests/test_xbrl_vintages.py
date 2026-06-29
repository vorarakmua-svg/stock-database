"""Tests for point-in-time annual vintage extraction (one view per filing)."""

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


def _facts(entries):
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}


def test_two_filings_yield_two_vintages_with_own_values():
    # FY2022 reported as 100 in the original 10-K, restated to 110 in the next year's
    # 10-K (different accn + filed). Both vintages are kept with their own value.
    e1 = usd(100, "2022-01-01", "2022-12-31", fy=2022, filed="2023-02-15")
    e1["accn"] = "0000-22-A"
    e2 = usd(110, "2022-01-01", "2022-12-31", fy=2023, filed="2024-02-15")
    e2["accn"] = "0000-23-B"
    facts = _facts([e1, e2])
    # give the two entries distinct accns (conftest usd defaults accn=filed)
    v = XBRLParser().extract_annual_vintages(facts)
    assert set(v.keys()) == {"2022"}
    by_accn = v["2022"]
    assert len(by_accn) == 2
    vals = sorted(p["revenue"] for p in by_accn.values())
    assert vals == [100, 110]
    # each vintage carries its own filed_date and accn
    for accn, p in by_accn.items():
        assert p["accn"] == accn
        assert p["filed_date"] in ("2023-02-15", "2024-02-15")
        assert p["fiscal_year"] == 2022


def test_within_filing_higher_priority_tag_wins():
    # One filing (one accn) tags revenue under both Revenues (priority 0) and the
    # contract-revenue tag (priority 1); the priority-0 tag wins for that vintage.
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            usd(200, "2023-01-01", "2023-12-31", fy=2023, filed="2024-02-15")]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            usd(999, "2023-01-01", "2023-12-31", fy=2023, filed="2024-02-15")]}},
    }}}
    v = XBRLParser().extract_annual_vintages(facts)
    (period,) = v["2023"].values()
    assert period["revenue"] == 200
    assert period["_source_tags"]["revenue"] == "Revenues"


def test_vintage_uses_date_rule_and_calendar():
    # Early-January 52/53-week year-end -> fiscal_year is end-year - 1; calendar from frame.
    facts = _facts([
        usd(50, "2022-01-03", "2023-01-01", fy=2022, filed="2023-02-15", frame="CY2022"),
    ])
    (period,) = XBRLParser().extract_annual_vintages(facts)["2022"].values()
    assert period["fiscal_year"] == 2022
    assert period["calendar_year"] == 2022


def test_years_back_trims_to_recent():
    facts = _facts([
        usd(1, "2020-01-01", "2020-12-31", fy=2020, filed="2021-02-15"),
        usd(2, "2021-01-01", "2021-12-31", fy=2021, filed="2022-02-15"),
        usd(3, "2022-01-01", "2022-12-31", fy=2022, filed="2023-02-15"),
    ])
    v = XBRLParser().extract_annual_vintages(facts, years_back=2)
    assert set(v.keys()) == {"2022", "2021"}


def test_empty_facts_returns_empty():
    assert XBRLParser().extract_annual_vintages({}) == {}

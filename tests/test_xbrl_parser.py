"""Tests for XBRLParser — in particular the fiscal-year grouping regression (A1)."""

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


def test_annual_buckets_by_period_not_filing_fy(sample_company_facts):
    """Comparative years inside one 10-K must land in their own year.

    Every ``Revenues``/``Assets`` fact in the FY2024 filing carries ``fy=2024``;
    correct extraction keys by the period (end/frame), so each year gets its own
    value rather than the filing-year value.
    """
    parser = XBRLParser()
    annual = parser.extract_annual_financials(sample_company_facts, years_back=10)

    assert set(annual.keys()) == {"2024", "2023", "2022"}
    assert annual["2024"]["revenue"] == 400
    assert annual["2023"]["revenue"] == 300
    assert annual["2022"]["revenue"] == 200
    # Instant (balance-sheet) concept buckets by end-date year too.
    assert annual["2024"]["total_assets"] == 70
    assert annual["2023"]["total_assets"] == 60
    assert annual["2022"]["total_assets"] == 50
    # Provenance records which XBRL tag each canonical value came from.
    assert annual["2024"]["_source_tags"]["revenue"] == "Revenues"


def test_annual_excludes_quarterly_spans(sample_company_facts):
    """A quarter-length span reported in a 10-K must not pollute annual revenue."""
    parser = XBRLParser()
    annual = parser.extract_annual_financials(sample_company_facts, years_back=10)
    # 120 was the Q4-only figure; the full-year value is 400.
    assert annual["2024"]["revenue"] == 400
    for year in annual.values():
        assert year["revenue"] != 120


def test_annual_years_back_limit(sample_company_facts):
    parser = XBRLParser()
    annual = parser.extract_annual_financials(sample_company_facts, years_back=2)
    assert set(annual.keys()) == {"2024", "2023"}


def test_most_recently_filed_value_wins():
    """A restated value filed later supersedes the original."""
    facts = {
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            usd(60, end="2023-09-30", fy=2023, filed="2023-11-03"),
                            # Restated a year later
                            usd(62, end="2023-09-30", fy=2024, filed="2024-11-01"),
                        ]
                    }
                }
            }
        }
    }
    parser = XBRLParser()
    annual = parser.extract_annual_financials(facts, years_back=5)
    assert annual["2023"]["total_assets"] == 62


def test_quarterly_buckets_and_excludes_full_year(sample_company_facts):
    parser = XBRLParser()
    quarterly = parser.extract_quarterly_financials(sample_company_facts, quarters_back=10)

    # Two quarter-length NetIncomeLoss facts; the full-year span is excluded.
    assert "2024-06-29" in quarterly
    assert "2024-09-28" in quarterly
    assert quarterly["2024-06-29"]["net_income"] == 25
    assert quarterly["2024-09-28"]["net_income"] == 30
    for period in quarterly.values():
        assert period.get("net_income") != 400


def test_empty_facts_returns_empty():
    parser = XBRLParser()
    assert parser.extract_annual_financials({}) == {}
    assert parser.extract_quarterly_financials({}) == {}


def test_period_year_prefers_frame_then_end():
    parser = XBRLParser()
    assert parser._period_year({"frame": "CY2021", "end": "2021-12-31"}) == 2021
    assert parser._period_year({"frame": "CY2020Q4I", "end": "2020-12-31"}) == 2020
    assert parser._period_year({"end": "2019-06-30"}) == 2019
    assert parser._period_year({}) is None

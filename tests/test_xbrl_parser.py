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


def test_quarterly_ladder_differencing_and_q4():
    """Discrete quarters come from differencing the cumulative (YTD) ladder, and
    Q4 = annual - 9-month YTD."""
    # One fiscal year (start 2023-10-01): YTD ladder at 3/6/9/12 months + the 10-K
    # full-year point that yields Q4.
    s = "2023-10-01"
    ladder = [
        usd(100, s, "2023-12-30", fy=2024, fp="Q1", form="10-Q", filed="2024-02-01"),
        usd(250, s, "2024-03-30", fy=2024, fp="Q2", form="10-Q", filed="2024-05-01"),
        usd(430, s, "2024-06-29", fy=2024, fp="Q3", form="10-Q", filed="2024-08-01"),
        usd(600, s, "2024-09-28", fy=2024, fp="FY", form="10-K", filed="2024-11-01"),
    ]
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": ladder}}}}}
    q = XBRLParser().extract_quarterly_financials(facts)

    assert q["2023-12-30"]["net_income"] == 100          # Q1 (base)
    assert q["2024-03-30"]["net_income"] == 150          # 250 - 100
    assert q["2024-06-29"]["net_income"] == 180          # 430 - 250
    assert q["2024-09-28"]["net_income"] == 170          # Q4 = 600 - 430
    # Fiscal quarter inferred from cumulative span.
    assert q["2024-09-28"]["fiscal_quarter"] == 4
    # The full-year cumulative (600) is never emitted as a discrete quarter.
    assert all(p.get("net_income") != 600 for p in q.values())
    # Discrete quarters sum to the annual full-year value.
    assert sum(q[e]["net_income"] for e in q) == 600


def test_quarterly_cashflow_from_ytd_only_ladder():
    """Cash flow is YTD-only in 10-Qs (no standalone 3-month facts); differencing
    must still recover discrete quarters and Q4."""
    s = "2023-10-01"
    ladder = [
        usd(30, s, "2023-12-30", fy=2024, fp="Q1", form="10-Q", filed="2024-02-01"),
        usd(54, s, "2024-03-30", fy=2024, fp="Q2", form="10-Q", filed="2024-05-01"),
        usd(82, s, "2024-06-29", fy=2024, fp="Q3", form="10-Q", filed="2024-08-01"),
        usd(110, s, "2024-09-28", fy=2024, fp="FY", form="10-K", filed="2024-11-01"),
    ]
    facts = {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ladder}}}}}
    q = XBRLParser().extract_quarterly_financials(facts)
    assert q["2023-12-30"]["operating_cash_flow"] == 30
    assert q["2024-03-30"]["operating_cash_flow"] == 24   # 54 - 30
    assert q["2024-06-29"]["operating_cash_flow"] == 28   # 82 - 54
    assert q["2024-09-28"]["operating_cash_flow"] == 28   # Q4 = 110 - 82


def test_quarterly_no_cap_returns_all_and_limit_works():
    s1, s2 = "2022-10-01", "2023-10-01"
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        usd(10, s1, "2022-12-31", fy=2023, form="10-Q", filed="2023-02-01"),
        usd(25, s1, "2023-03-31", fy=2023, form="10-Q", filed="2023-05-01"),
        usd(10, s2, "2023-12-30", fy=2024, form="10-Q", filed="2024-02-01"),
        usd(25, s2, "2024-03-30", fy=2024, form="10-Q", filed="2024-05-01"),
    ]}}}}}
    parser = XBRLParser()
    assert len(parser.extract_quarterly_financials(facts)) == 4          # all
    assert len(parser.extract_quarterly_financials(facts, quarters_back=2)) == 2


def test_empty_facts_returns_empty():
    parser = XBRLParser()
    assert parser.extract_annual_financials({}) == {}
    assert parser.extract_quarterly_financials({}) == {}


def test_period_year_uses_end_then_frame():
    parser = XBRLParser()
    # End year is primary and consistent across statements...
    assert parser._period_year({"end": "2019-06-30"}) == 2019
    # ...even when SEC's calendar-year frame disagrees (non-Dec fiscal year).
    assert parser._period_year({"frame": "CY2025", "end": "2026-01-31"}) == 2026
    # Frame is only a fallback when end is absent.
    assert parser._period_year({"frame": "CY2020Q4I"}) == 2020
    assert parser._period_year({}) is None

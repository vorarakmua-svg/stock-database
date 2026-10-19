"""Tests for XBRLParser — in particular the fiscal-year grouping regression (A1)."""

import pytest

from src.parsers.xbrl_parser import XBRLParser, explain_no_annual_financials
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


def _dividend_facts(declared, cash_paid):
    """Dividends per share under its two tags (USD/shares facts)."""
    us_gaap = {}
    if declared:
        us_gaap["CommonStockDividendsPerShareDeclared"] = {"units": {"USD/shares": declared}}
    if cash_paid:
        us_gaap["CommonStockDividendsPerShareCashPaid"] = {"units": {"USD/shares": cash_paid}}
    return {"facts": {"us-gaap": us_gaap}}


def test_a_duration_field_ignores_point_in_time_facts():
    # BAC tags each quarterly declaration as a point-in-time fact under the preferred tag, while the
    # fallback tag covers the year: the last declaration (0.24) became FY2023's dividends per share,
    # against 0.92 paid in the year.
    declared = [usd(v, end=e, fy=2023, filed="2024-02-20")
                for v, e in [(0.22, "2023-02-01"), (0.22, "2023-04-26"), (0.24, "2023-07-19"), (0.24, "2023-10-18")]]
    cash_paid = [usd(0.92, start="2023-01-01", end="2023-12-31", fy=2023, filed="2024-02-20")]
    annual = XBRLParser().extract_annual_financials(_dividend_facts(declared, cash_paid))
    assert annual["2023"]["dividends_per_share"] == 0.92
    assert annual["2023"]["_source_tags"]["dividends_per_share"] == "CommonStockDividendsPerShareCashPaid"


def test_a_duration_field_with_only_point_in_time_facts_stays_empty():
    # DIS reports each semiannual payment as a point-in-time fact. No fact covers the year, so there is
    # no annual value (one payment, half the year's dividends, used to stand in for it).
    cash_paid = [usd(0.5, end="2025-01-16", fy=2025, filed="2025-11-20"),
                 usd(0.5, end="2025-07-23", fy=2025, filed="2025-11-20")]
    annual = XBRLParser().extract_annual_financials(_dividend_facts([], cash_paid))
    assert "dividends_per_share" not in annual.get("2025", {})


def _fy2024(val):
    return usd(val, start="2024-01-01", end="2024-12-31", fy=2024, filed="2025-02-27")


def test_the_combined_basic_and_diluted_concepts_fill_both_fields():
    # A filer without dilutive securities may tag one EPS and one share count for both (SCCO for
    # FY2011-18, SPG, O, NOW, PANW, MO, TSLA, CRWD): those years had no EPS or share counts at all.
    annual = XBRLParser().extract_annual_financials({"facts": {"us-gaap": {
        "EarningsPerShareBasicAndDiluted": {"units": {"USD/shares": [_fy2024(1.00)]}},
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted": {"units": {"shares": [_fy2024(773.1e6)]}},
    }}})
    year = annual["2024"]
    assert (year["eps_basic"], year["eps_diluted"]) == (1.00, 1.00)
    assert (year["weighted_avg_shares_basic"], year["weighted_avg_shares_diluted"]) == (773.1e6, 773.1e6)


def test_the_separate_basic_and_diluted_concepts_win_over_the_combined_one():
    annual = XBRLParser().extract_annual_financials({"facts": {"us-gaap": {
        "EarningsPerShareBasic": {"units": {"USD/shares": [_fy2024(1.02)]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [_fy2024(1.00)]}},
        "EarningsPerShareBasicAndDiluted": {"units": {"USD/shares": [_fy2024(1.01)]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [_fy2024(780e6)]}},
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted": {"units": {"shares": [_fy2024(773e6)]}},
    }}})
    year = annual["2024"]
    assert (year["eps_basic"], year["eps_diluted"]) == (1.02, 1.00)
    assert (year["weighted_avg_shares_basic"], year["weighted_avg_shares_diluted"]) == (773e6, 780e6)


def _facts_from(taxonomy, form, tag="Revenues"):
    return {"facts": {taxonomy: {tag: {"units": {"USD": [usd(1.0e9, start="2025-01-01", end="2025-12-31",
                                                             fy=2025, form=form, filed="2026-03-01")]}}}}}


@pytest.mark.parametrize("facts, reason", [
    (None, "SEC has no XBRL financial statements for it"),
    (_facts_from("ifrs-full", "20-F"), "it reports in IFRS (20-F), which isn't mapped"),
    (_facts_from("us-gaap", "6-K"), "it has filed no annual report (20-F) yet"),
    (_facts_from("us-gaap", "10-Q"), "it has filed no annual report (10-K) yet"),
])
def test_an_empty_sec_record_says_why(facts, reason):
    # TSM (IFRS) and XOM's new holding company (10-Qs only) came back with no annual financials and no word on why.
    assert explain_no_annual_financials(facts) == reason


def test_a_10k_filer_whose_facts_cover_no_full_year_gets_the_last_reason():
    # Only a quarter-length fact from a 10-K: the parser finds no fiscal year, and the reason says so.
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        usd(2.5e8, start="2025-10-01", end="2025-12-31", fy=2025, form="10-K", filed="2026-02-20")]}}}}}
    assert XBRLParser().extract_annual_financials(facts) == {}
    assert explain_no_annual_financials(facts) == "its annual-report facts give no full fiscal year of a mapped concept"


def _fy_to_march(val, form, unit="USD"):
    entry = usd(val, start="2024-04-01", end="2025-03-31", fy=2025, form=form, filed="2025-05-28")
    return {"units": {unit: [entry]}}


@pytest.mark.parametrize("form", ["20-F", "20-F/A", "40-F"])
def test_a_foreign_issuers_annual_report_is_read(form):
    # ARM files its US-GAAP accounts on 20-F (fiscal year to March), but only 10-K facts were read: no financials.
    year = XBRLParser().extract_annual_financials({"facts": {"us-gaap": {
        "Revenues": _fy_to_march(4.0e9, form),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _fy_to_march(1.06e9, form, unit="shares"),
    }}})["2025"]
    assert (year["revenue"], year["weighted_avg_shares_diluted"]) == (4.0e9, 1.06e9)


def test_a_filer_reporting_in_another_currency_gets_no_record_and_a_reason():
    # Alibaba-style: US GAAP on 20-F, in renminbi. Only the share counts would resolve, a record of nothing else;
    # an empty one lets the run say why.
    facts = {"facts": {"us-gaap": {
        "Revenues": _fy_to_march(9.96e11, "20-F", unit="CNY"),
        "WeightedAverageNumberOfDilutedSharesOutstanding": _fy_to_march(2.4e10, "20-F", unit="shares"),
    }}}
    parser = XBRLParser()
    assert parser.extract_annual_financials(facts) == {}
    assert parser.extract_annual_vintages(facts) == {}
    assert explain_no_annual_financials(facts) == "it reports in CNY (20-F), which isn't converted to dollars"


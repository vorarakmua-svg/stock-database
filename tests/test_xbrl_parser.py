"""Tests for XBRLParser — in particular the fiscal-year grouping regression (A1)."""

import pytest

from src.parsers.xbrl_parser import XBRLParser, explain_no_annual_financials, explain_stale_annual_financials
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


def _fiscal_years(unit, values, form="20-F"):
    """Full-year facts in *unit* for FY2023-25 (calendar years), each filed the March after."""
    return {unit: [usd(v, start=f"{fy}-01-01", end=f"{fy}-12-31", fy=fy, form=form, filed=f"{fy + 1}-03-20")
                   for fy, v in values.items()]}


def test_a_filer_reporting_in_another_currency_with_dollar_convenience_figures_gets_no_record():
    # Code review 6 (I1): a non-USD 20-F filer also tags a US$ convenience translation of the year it reports, so any
    # USD fact kept the record, at each year's convenience rate (TSM: 4,624 TWD facts beside 1,010 USD ones).
    cny = {2023: 9.0e11, 2024: 9.4e11, 2025: 9.96e11}
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {**_fiscal_years("CNY", cny), **_fiscal_years("USD", {2025: 1.37e11})}},
        "NetIncomeLoss": {"units": {**_fiscal_years("CNY", {fy: v / 8 for fy, v in cny.items()}),
                                    **_fiscal_years("USD", {2025: 1.7e10})}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": _fiscal_years(
            "shares", {2023: 2.5e10, 2024: 2.45e10, 2025: 2.4e10})},
    }}}
    parser = XBRLParser()
    assert parser.extract_annual_financials(facts) == {}
    assert parser.extract_annual_vintages(facts) == {}
    assert explain_no_annual_financials(facts) == "it reports in CNY (20-F), which isn't converted to dollars"


def test_a_filer_reporting_in_another_currency_keeps_no_share_only_quarters():
    # BUG-071: a CAD 10-Q filer's quarters held nothing but share counts while its annual record was dropped.
    q = [usd(v, start=s, end=e, fy=2025, fp=fp, form="10-Q", filed=f) for v, s, e, fp, f in [
        (1.0e9, "2025-01-01", "2025-03-31", "Q1", "2025-05-01"),
        (2.1e9, "2025-01-01", "2025-06-30", "Q2", "2025-08-01")]]
    shares = [usd(5.0e8, end=e, fy=2025, fp=fp, form="10-Q", filed=f) for e, fp, f in [
        ("2025-03-31", "Q1", "2025-05-01"), ("2025-06-30", "Q2", "2025-08-01")]]
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"CAD": q}},
                                   "CommonStockSharesOutstanding": {"units": {"shares": shares}}}}}
    assert XBRLParser().extract_quarterly_financials(facts) == {}


def _filed_years(tag, unit, by_fy, instant=False):
    """One tag's facts: {fy: [(filed, value), ...]}, each a calendar fiscal year (or its year-end instant)."""
    return {tag: {"units": {unit: [usd(v, start=None if instant else f"{fy}-01-01", end=f"{fy}-12-31", fy=fy,
                                       filed=filed) for fy, filings in by_fy.items() for filed, v in filings]}}}


def _annual(*tags):
    facts = {"facts": {"us-gaap": {k: v for tag in tags for k, v in tag.items()}}}
    return XBRLParser().extract_annual_financials(facts)


def test_a_later_filings_scale_typo_keeps_the_figure_earlier_filings_report():
    # BUG-061: STX's FY2024 10-K tagged FY2022's dividend per share as 2,770,000; the 10-Ks of 2022 and 2023 said 2.77.
    annual = _annual(_filed_years("CommonStockDividendsPerShareDeclared", "USD/shares", {
        2021: [("2021-08-06", 2.68)],
        2022: [("2022-08-05", 2.77), ("2023-08-04", 2.77), ("2024-08-02", 2770000.0)],
        2023: [("2023-08-04", 2.80), ("2024-08-02", 2.80)]}))
    assert annual["2022"]["dividends_per_share"] == 2.77
    assert [(r["field"], r["kind"], r["dropped"]) for r in annual["2022"]["_repairs"]] == [
        ("dividends_per_share", "scale x10^6", 2770000.0)]


def test_a_later_filing_that_corrects_a_scale_typo_wins():
    # BRK-B's 10-Ks of 2011 and 2012 agreed on 1.6 trillion weighted shares for FY2010; the 2013 one corrected it.
    annual = _annual(_filed_years("WeightedAverageNumberOfSharesOutstandingBasic", "shares", {
        2009: [("2010-02-26", 1.55e6)],
        2010: [("2011-02-28", 1.636e12), ("2012-02-24", 1.636e12), ("2013-03-01", 1.636e6)],
        2011: [("2012-02-24", 1.65e6)]}),
        _filed_years("Revenues", "USD", {2009: [("2010-02-26", 1.12e11)], 2010: [("2011-02-28", 1.36e11)],
                                         2011: [("2012-02-24", 1.43e11)]}))
    assert annual["2010"]["weighted_avg_shares_basic"] == 1.636e6
    assert "_repairs" not in annual["2010"]


def test_a_later_filings_zero_keeps_the_reported_total():
    # BAC's 10-K filed 2011-02-25 tagged FY2008's total assets as 0; three earlier filings say $1,817.9B.
    annual = _annual(_filed_years("Assets", "USD", {
        2007: [("2009-02-27", 1.716e12)],
        2008: [("2009-02-27", 1.818e12), ("2009-08-07", 1.818e12), ("2010-02-26", 1.818e12), ("2011-02-25", 0.0)],
        2009: [("2010-02-26", 2.224e12)]}, instant=True))
    assert annual["2008"]["total_assets"] == 1.818e12



def test_a_later_zero_for_a_line_item_is_kept():
    # T-Mobile's 2014 10-K reports T-Mobile USA's FY2012 after the reverse merger: no long-term debt repaid, where
    # MetroPCS's own 10-K had $25M. Only a total can't be zero.
    annual = _annual(_filed_years("RepaymentsOfLongTermDebt", "USD", {
        2010: [("2011-03-01", 1.6e7)], 2011: [("2012-02-29", 2.429e7)],
        2012: [("2013-03-01", 2.539e7), ("2015-02-19", 0.0)],
        2013: [("2015-02-19", 9.0e6)], 2014: [("2015-02-19", 1.0e9)]}))
    assert (annual["2012"]["debt_repaid"], "_repairs" in annual["2012"]) == (0.0, False)


@pytest.mark.parametrize("fy2022, kept", [(1.549e9, 1.546e9), (-2.0e8, -1.546e9)])
def test_a_sign_flip_goes_back_only_when_the_neighbouring_years_agree(fy2022, kept):
    # DIS's 2023 10-K tagged FY2021's interest expense negative beside two filings and the years around it.
    annual = _annual(_filed_years("InterestExpense", "USD", {
        2020: [("2021-11-23", 1.491e9)],
        2021: [("2021-11-23", 1.546e9), ("2022-11-29", 1.546e9), ("2023-11-21", -1.546e9)],
        2022: [("2023-11-21", fy2022)]}))
    assert annual["2021"]["interest_expense"] == kept


def test_flipped_cash_flow_sections_go_back_when_the_year_then_adds_up():
    # AMD's 2024 10-K tagged FY2021's investing and financing cash flows positive: the year no longer summed to its
    # $0.94B change in cash.
    def year(tag, by_filing):
        return _filed_years(tag, "USD", {2021: list(zip(("2022-02-03", "2023-02-27", "2024-01-31"), by_filing))})

    annual = _annual(year("NetCashProvidedByUsedInOperatingActivities", (3.521e9,) * 3),
                     year("NetCashProvidedByUsedInInvestingActivities", (-0.686e9, -0.686e9, 0.686e9)),
                     year("NetCashProvidedByUsedInFinancingActivities", (-1.895e9, -1.895e9, 1.895e9)),
                     year("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecrease"
                          "IncludingExchangeRateEffect", (0.940e9,) * 3))
    assert (annual["2021"]["investing_cash_flow"], annual["2021"]["financing_cash_flow"]) == (-0.686e9, -1.895e9)


def test_a_ten_for_one_restatement_is_left_alone():
    # NVDA's 2025 10-K restated FY2023's 2.487B shares for its 10:1 split: a split, not a typo, even where the years
    # before it, counting fewer shares, would side with the earlier filings.
    annual = _annual(_filed_years("WeightedAverageNumberOfSharesOutstandingBasic", "shares", {
        2021: [("2022-03-18", 2.40e9)], 2022: [("2023-02-24", 2.45e9)],
        2023: [("2023-02-24", 2.487e9), ("2024-02-21", 2.487e9), ("2025-02-26", 2.487e10)]}),
        _filed_years("Revenues", "USD", {2023: [("2024-02-21", 2.7e10)]}))
    assert (annual["2023"]["weighted_avg_shares_basic"], "_repairs" in annual["2023"]) == (2.487e10, False)


def test_earlier_filings_that_disagree_leave_the_latest_figure():
    # TJX's filings for FY2012 operating income went -16.6M, +16.6M, -16.6M: no figure is agreed on.
    annual = _annual(_filed_years("OperatingIncomeLoss", "USD", {
        2011: [("2012-03-27", 1.03e7)],
        2012: [("2012-03-27", -1.66e7), ("2013-04-02", 1.66e7), ("2014-04-01", -1.66e7)],
        2013: [("2014-04-01", 3.442e9)], 2014: [("2015-03-31", 3.68e9)]}))
    assert annual["2012"]["operating_income"] == -1.66e7


def test_a_later_filing_that_misscales_two_years_has_both_put_back():
    # Each year's error stands beside the other's: the two years on each side outvote it.
    annual = _annual(_filed_years("CommonStockDividendsPerShareDeclared", "USD/shares", {
        2020: [("2020-08-04", 2.58)], 2021: [("2021-08-06", 2.66)],
        2022: [("2022-08-05", 2.77), ("2024-08-02", 2.77e6)],
        2023: [("2023-08-04", 2.80), ("2024-08-02", 2.80e6)],
        2024: [("2024-08-02", 2.80)], 2025: [("2025-08-01", 2.88)]}))
    assert (annual["2022"]["dividends_per_share"], annual["2023"]["dividends_per_share"]) == (2.77, 2.80)


def test_a_fourth_quarter_comes_from_the_restored_year():
    # AMD's Q4 FY2021 is the year less nine months; with the 2024 10-K's flipped year its investing cash flow read
    # +$0.99B.
    start, filings = "2020-12-27", ("2022-02-03", "2023-02-27", "2024-01-31")

    def section(tag, ytd, year):
        rungs = [usd(v, start, end, fy=2021, fp=fp, form="10-Q", filed=f)
                 for v, end, fp, f in zip(ytd, ("2021-03-27", "2021-06-26", "2021-09-25"), ("Q1", "Q2", "Q3"),
                                          ("2021-05-05", "2021-08-04", "2021-11-03"))]
        return {tag: {"units": {"USD": rungs + [usd(v, start, "2021-12-25", fy=2021, filed=f)
                                                for v, f in zip(year, filings)]}}}

    facts = {"facts": {"us-gaap": {
        **section("NetCashProvidedByUsedInOperatingActivities", (0.995e9, 1.947e9, 2.618e9), (3.521e9,) * 3),
        **section("NetCashProvidedByUsedInInvestingActivities", (-0.1e9, -0.2e9, -0.3e9),
                  (-0.686e9, -0.686e9, 0.686e9)),
        **section("NetCashProvidedByUsedInFinancingActivities", (-0.3e9, -0.6e9, -1.2e9),
                  (-1.895e9, -1.895e9, 1.895e9)),
        **section("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncluding"
                  "ExchangeRateEffect", (0.595e9, 1.147e9, 1.118e9), (0.940e9,) * 3)}}}
    q4 = XBRLParser().extract_quarterly_financials(facts)["2021-12-25"]
    assert (round(q4["investing_cash_flow"]), round(q4["financing_cash_flow"])) == (-386000000, -695000000)


def test_a_year_end_balance_comes_from_the_restored_year():
    # BAC's 2011 10-K put total assets at 2008-12-31 at 0, which the quarterly checkpoints read too.
    annual = _filed_years("Assets", "USD", {
        2007: [("2009-02-27", 1.716e12)],
        2008: [("2009-02-27", 1.818e12), ("2010-02-26", 1.818e12), ("2011-02-25", 0.0)],
        2009: [("2010-02-26", 2.224e12)]}, instant=True)
    quarterly = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": annual}})
    assert quarterly["2008-12-31"]["total_assets"] == 1.818e12


def _switcher_facts(later_form="20-F"):
    """US-GAAP 20-Fs through FY2019, then IFRS reports from FY2020, each also restating the two years before."""
    def fy(val, year, filed, form):
        return {"val": val, "start": f"{year}-01-01", "end": f"{year}-12-31", "fy": year, "fp": "FY", "form": form,
                "filed": filed, "accn": filed}

    gaap = [fy(1.0e10 + 1.0e9 * (y - 2015), y, f"{y + 1}-04-15", "20-F") for y in range(2015, 2020)]
    ifrs = [fy(4.5e11 + 1.0e10 * (y - 2018), y, f"{r + 1}-04-15", later_form)
            for r in range(2020, 2026) for y in (r - 2, r - 1, r)]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": gaap}}},
                      "ifrs-full": {"Revenue": {"units": {"TWD": ifrs}}}}}


def test_a_record_that_stops_where_ifrs_reports_begin_is_explained():
    # Code review 6 (I3): with US-GAAP 20-Fs to FY2019 and IFRS ones after, TSM's FY2015-19 were kept and valued
    # beside today's price, with nothing saying the figures were years old.
    assert explain_stale_annual_financials(_switcher_facts(), "2019-12-31") == (
        "later annual reports are in IFRS (20-F), which isn't mapped")


@pytest.mark.parametrize("facts, latest_end", [
    (_switcher_facts(), "2025-12-31"),            # nothing in IFRS after the record's last year
    (_switcher_facts(later_form="6-K"), "2019-12-31"),  # later IFRS facts, but no annual report
])
def test_ifrs_facts_that_are_no_later_annual_report_say_nothing(facts, latest_end):
    assert explain_stale_annual_financials(facts, latest_end) is None


def test_a_quarters_weighted_share_count_is_its_own_average():
    # BUG-074: the ladder differenced year-to-date averages as if they were sums, so AAPL's Q2-Q4 FY2025 diluted counts
    # read about -0.05B. A quarter's average is the year-to-date share-days less the previous rung's, over its days.
    start = "2024-09-29"
    rungs = [("2024-12-28", 15.150e9, "Q1", "10-Q"), ("2025-03-29", 15.100e9, "Q2", "10-Q"),
             ("2025-06-28", 15.050e9, "Q3", "10-Q"), ("2025-09-27", 15.000e9, "FY", "10-K")]
    entries = [{"val": v, "start": start, "end": end, "fy": 2025, "fp": fp, "form": form, "filed": end, "accn": end}
               for end, v, fp, form in rungs]
    quarterly = XBRLParser().extract_quarterly_financials(
        {"facts": {"us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": entries}}}}})
    counts = [quarterly[end]["weighted_avg_shares_diluted"] for end, *_ in rungs]
    # (15.100 x 181 - 15.150 x 90) / 91, and so on for Q3 and Q4
    assert counts == [15.150e9, pytest.approx(15.0505e9, rel=1e-5), pytest.approx(14.9505e9, rel=1e-5),
                      pytest.approx(14.8505e9, rel=1e-5)]


def test_a_quarters_own_3_month_share_count_is_used_where_filed():
    # A 10-Q tags the quarter's own weighted-average count; the year-to-date rungs can disagree with it (GE's restated
    # nine months beside an unrestated half year read -14.0B for Q3 2020), so it wins; Q4, which 10-Ks don't tag, is
    # still the year's share-days less the nine months'.
    start = "2024-09-29"
    ytd = [("2024-12-28", 15.150e9, "Q1", "10-Q"), ("2025-03-29", 15.100e9, "Q2", "10-Q"),
           ("2025-06-28", 15.050e9, "Q3", "10-Q"), ("2025-09-27", 15.000e9, "FY", "10-K")]
    entries = [{"val": v, "start": start, "end": end, "fy": 2025, "fp": fp, "form": form, "filed": end, "accn": end}
               for end, v, fp, form in ytd]
    entries.append({"val": 15.060e9, "start": "2024-12-29", "end": "2025-03-29", "fy": 2025, "fp": "Q2",
                    "form": "10-Q", "filed": "2025-03-29", "accn": "2025-03-29"})
    quarterly = XBRLParser().extract_quarterly_financials(
        {"facts": {"us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": entries}}}}})
    assert [quarterly[end]["weighted_avg_shares_diluted"] for end, *_ in ytd] == [
        15.150e9, 15.060e9, pytest.approx(14.9505e9, rel=1e-5), pytest.approx(14.8505e9, rel=1e-5)]


def test_a_quarters_own_share_count_fills_a_gap_in_the_year_to_date_rungs():
    # A filer that tagged Q2's 3-month count but no half-year one: the year-to-date ladder skips Q2, its own figure
    # doesn't.
    start = "2024-09-29"

    def entry(val, begin, end, fp, form):
        return {"val": val, "start": begin, "end": end, "fy": 2025, "fp": fp, "form": form, "filed": end, "accn": end}

    shares = [entry(15.150e9, start, "2024-12-28", "Q1", "10-Q"),
              entry(15.060e9, "2024-12-29", "2025-03-29", "Q2", "10-Q"),
              entry(15.050e9, start, "2025-06-28", "Q3", "10-Q"), entry(15.000e9, start, "2025-09-27", "FY", "10-K")]
    income = [entry(v, start, end, fp, form) for v, end, fp, form in (
        (3.0e10, "2024-12-28", "Q1", "10-Q"), (5.5e10, "2025-03-29", "Q2", "10-Q"),
        (8.0e10, "2025-06-28", "Q3", "10-Q"), (1.1e11, "2025-09-27", "FY", "10-K"))]
    quarterly = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": shares}},
        "NetIncomeLoss": {"units": {"USD": income}}}}})
    assert quarterly["2025-03-29"]["weighted_avg_shares_diluted"] == 15.060e9


def _currency_switcher(before, after, switch_fy):
    """A 20-F filer reporting in *before* through FY{switch_fy - 1} and in *after* from FY{switch_fy}: each annual
    report gives its year and the two before it (its balance sheet, two) in its own currency, and one in a currency
    other than dollars also tags a US$ convenience translation of the year it reports."""
    rate = {"USD": 1.0, "RUB": 75.0, "TWD": 31.0}
    tags = {"Revenues": 1.0e10, "NetIncomeLoss": 1.0e9, "Assets": 3.0e10}
    facts = {}
    for tag, size in tags.items():
        instant = tag == "Assets"
        units = {}
        for report_fy in range(2019, 2026):
            currency = before if report_fy < switch_fy else after
            filed = f"{report_fy + 1}-04-15"
            for fy in range(report_fy - (1 if instant else 2), report_fy + 1):
                value = size * 1.08 ** (fy - 2019) * rate[currency]
                units.setdefault(currency, []).append(
                    usd(value, start=None if instant else f"{fy}-01-01", end=f"{fy}-12-31", fy=report_fy, form="20-F",
                        filed=filed))
            if currency != "USD":  # the convenience translation, at that year's rate
                units.setdefault("USD", []).append(usd(
                    size * 1.08 ** (report_fy - 2019) * rate[currency] / (rate[currency] * 0.93),
                    start=None if instant else f"{report_fy}-01-01", end=f"{report_fy}-12-31", fy=report_fy,
                    form="20-F", filed=filed))
        facts[tag] = {"units": units}
    return {"facts": {"us-gaap": facts}}


def test_a_filer_that_switched_to_dollars_keeps_its_dollar_filings():
    # BUG-077 (code review 7, I3): the reporting currency was read over the whole history (the Yandex-to-Nebius path:
    # RUB through FY2022, dollars since), so the filer got no record at all and the run said it reports in RUB.
    facts = _currency_switcher("RUB", "USD", 2023)
    parser = XBRLParser()
    annual = parser.extract_annual_financials(facts)
    assert sorted(annual) == ["2021", "2022", "2023", "2024", "2025"]  # the dollar 20-Fs and their comparatives
    assert annual["2022"]["revenue"] == pytest.approx(1.0e10 * 1.08 ** 3)  # restated in dollars, not converted
    assert {accn for year in parser.extract_annual_vintages(facts).values() for accn in year} == {
        "2024-04-15", "2025-04-15", "2026-04-15"}


def test_a_filer_that_switched_from_dollars_keeps_its_dollar_years_and_says_why_they_stop():
    # The other way round, the later convenience translations made a record of their own.
    facts = _currency_switcher("USD", "TWD", 2023)
    annual = XBRLParser().extract_annual_financials(facts)
    assert sorted(annual) == ["2017", "2018", "2019", "2020", "2021", "2022"]  # the dollar 20-Fs to FY2022
    assert annual["2022"]["revenue"] == pytest.approx(1.0e10 * 1.08 ** 3)
    assert explain_stale_annual_financials(facts, annual["2022"]["period_end"]) == (
        "later annual reports are in TWD (20-F), which isn't converted to dollars")


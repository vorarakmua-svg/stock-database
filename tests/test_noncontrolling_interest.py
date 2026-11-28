"""minority_interest where a filer tags its non-controlling interests in parts, or not at all (BUG-063): the equity
including them less the parent's, as each annual report gives the two, where its balance sheet closes with them."""

import pytest

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd

_K10 = dict(fy=2024, fp="FY", form="10-K", filed="2025-02-28")
_SEI = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"


def _filing(end="2024-12-31", filing=_K10, **tagged):
    return {tag: [usd(val, None, end, **filing)] for tag, val in tagged.items()}


def _facts(*filings):
    us_gaap = {}
    for tagged in filings:
        for tag, entries in tagged.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].extend(entries)
    return {"facts": {"us-gaap": us_gaap}}


def _bx_2024(**more):
    # BX's 10-K for 2024: its non-controlling interests in two parts (6.155B + 4.326B), its redeemable ones (0.801B)
    # between liabilities and equity, its equity with and without them.
    tagged = dict(Assets=43.469875e9, Liabilities=23.974860e9, StockholdersEquity=8.212321e9,
                  NonredeemableNoncontrollingInterest=6.154943e9, MinorityInterestInOperatingPartnerships=4.326352e9,
                  RedeemableNoncontrollingInterestEquityCarryingAmount=0.801399e9)
    tagged[_SEI] = 18.693616e9
    tagged.update(more)
    return _filing(**tagged)


def _annual(*filings):
    return XBRLParser().extract_annual_financials(_facts(*filings))


def test_the_parts_a_filer_tags_are_its_equity_with_them_less_the_parents():
    year = _annual(_bx_2024())["2024"]
    assert year["minority_interest"] == pytest.approx(10.481295e9)
    assert year["total_equity"] == 8.212321e9


def test_a_filer_that_tags_minority_interest_keeps_its_figure():
    assert _annual(_bx_2024(MinorityInterest=10.0e9))["2024"]["minority_interest"] == 10.0e9


def test_a_filing_that_tags_minority_interest_on_a_date_and_one_that_doesnt():
    # MAS's 10-K for 2025 tags no MinorityInterest at 2025-12-31 (its equity -$185M, with the interests $76M), its
    # 10-Qs tag $261M: the 10-K's view reads the difference, each 10-Q its own figure. Of two filings' figures for a
    # date, the later one stands, as for any restated figure.
    k25 = dict(fy=2025, fp="FY", form="10-K", filed="2026-02-10")
    q26 = dict(fy=2026, fp="Q1", form="10-Q", filed="2026-04-22")
    sheet = dict(end="2025-12-31", Assets=5.201e9, Liabilities=5.125e9, StockholdersEquity=-0.185e9, **{_SEI: 0.076e9})
    facts = _facts(_filing(**sheet, filing=k25), _filing(**sheet, MinorityInterest=0.261e9, filing=q26))
    assert XBRLParser().extract_annual_financials(facts)["2025"]["minority_interest"] == pytest.approx(0.261e9)
    annual = _annual(_filing(MinorityInterest=10.0e9),
                     _filing(Assets=43.0e9, Liabilities=24.0e9, StockholdersEquity=8.0e9, **{_SEI: 19.0e9}, filing=k25))
    assert annual["2024"]["minority_interest"] == pytest.approx(11.0e9)


def test_no_minority_interest_where_the_two_equities_agree():
    year = _annual(_filing(Assets=10.0e9, Liabilities=6.0e9, StockholdersEquity=4.0e9, **{_SEI: 4.0e9}))["2024"]
    assert year.get("minority_interest") is None


def test_the_two_equities_of_different_filings_make_no_figure():
    # Each figure is one filing's: a 10-K's equity and a later 10-K/A's equity with the interests aren't paired.
    amended = dict(fy=2024, fp="FY", form="10-K/A", filed="2025-06-30")
    annual = _annual(_filing(Assets=43.0e9, Liabilities=24.0e9, StockholdersEquity=8.0e9),
                     _filing(Assets=43.0e9, Liabilities=24.0e9, **{_SEI: 19.0e9}, filing=amended))
    assert annual["2024"].get("minority_interest") is None


@pytest.mark.parametrize("tagged", [
    # Agilent's 10-K for FY2025 tags -$226M as its equity with the interests beside its $6.741B equity: the balance
    # sheet (assets 12.727B, liabilities 5.986B) doesn't close with it, so it is no figure of the interests.
    dict(Assets=12.727e9, Liabilities=5.986e9, StockholdersEquity=6.741e9, **{_SEI: -0.226e9}),
    # A balance sheet that tags no liabilities proves nothing (NKE's FY2016 tags -$11.94B beside its equity).
    dict(Assets=21.396e9, StockholdersEquity=12.258e9, **{_SEI: 0.318e9}),
    # JPM's FY2010 10-K: its 2009 equity restated ($160.845B) beside the equity with the interests as first filed
    # ($165.365B); its balance sheet closes with the restated one alone, within 1% with the other.
    dict(Assets=2119.673e9, Liabilities=1958.828e9, StockholdersEquity=160.845e9, **{_SEI: 165.365e9}),
    # Nor one beyond the 1% the figures may miss its total by.
    dict(Assets=43.469875e9, Liabilities=23.5e9, StockholdersEquity=8.212321e9, **{_SEI: 18.693616e9}),
])
def test_an_equity_with_the_interests_the_balance_sheet_doesnt_close_with_makes_no_figure(tagged):
    assert _annual(_filing(**tagged))["2024"].get("minority_interest") is None


def test_the_balance_sheets_total_and_temporary_equity_prove_the_interests():
    # The total of liabilities and equity stands for total assets, and temporary equity's total for its parts.
    year = _annual(_filing(LiabilitiesAndStockholdersEquity=43.469875e9, Liabilities=23.974860e9,
                           StockholdersEquity=8.212321e9, **{_SEI: 18.693616e9},
                           TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests=0.801399e9,
                           TemporaryEquityCarryingAmountAttributableToParent=3.0e9))["2024"]
    assert year["minority_interest"] == pytest.approx(10.481295e9)


def test_a_10qs_equities_make_no_figure_and_its_year_end_reads_the_10ks():
    # Teledyne's 10-Q for 2016-04-03 tags its parent equity as -$0.38B beside $1.43B with the interests, and its balance
    # sheet closes with the latter: a 10-Q's equities make no figure. A quarter on the year end reads the 10-K's.
    q10 = dict(fy=2016, fp="Q1", form="10-Q", filed="2016-05-06")
    facts = _facts(_filing(end="2016-04-03", filing=q10, Assets=3.0e9, Liabilities=1.573e9,
                           StockholdersEquity=-0.381e9, **{_SEI: 1.427e9}), _bx_2024())
    quarters = XBRLParser().extract_quarterly_financials(facts)
    assert quarters["2016-04-03"].get("minority_interest") is None
    assert quarters["2024-12-31"]["minority_interest"] == pytest.approx(10.481295e9)


def test_each_vintage_reads_its_filings_figure():
    k25 = dict(fy=2025, fp="FY", form="10-K", filed="2026-02-27")
    facts = _facts(_bx_2024(), _filing(Assets=43.469875e9, Liabilities=23.974860e9, StockholdersEquity=8.0e9,
                                       RedeemableNoncontrollingInterestEquityCarryingAmount=0.801399e9,
                                       **{_SEI: 18.693616e9}, filing=k25))
    views = XBRLParser().extract_annual_vintages(facts)["2024"]
    assert sorted(round(view["minority_interest"] / 1e6) for view in views.values()) == [10481, 10694]

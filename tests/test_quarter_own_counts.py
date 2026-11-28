"""A quarter's own 3-month share count fills only a quarter the filings hold (BUG-096)."""

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd

_START = "2010-10-31"


def _shares(val, end, form, filed, start=_START):
    return usd(val, start, end, fy=2011, fp="Q", form=form, filed=filed)


def _adi_facts():
    """ADI's FY2011 (October year): its 10-Qs' year-to-date ladders, the first quarter's own counts, and the FY2012
    10-K, which re-dates the first quarter's counts to 2011-02-04 beside the 10-Q's 2011-01-29."""
    counts = [_shares(308_848_000, "2011-01-29", "10-Q", "2011-02-15"),
              _shares(308_848_000, "2011-02-04", "10-K", "2012-11-27"),
              _shares(309_233_000, "2011-04-30", "10-Q", "2011-05-17"),
              _shares(309_619_000, "2011-04-30", "10-Q", "2011-05-17", start="2011-01-30"),
              _shares(309_070_000, "2011-07-30", "10-Q", "2011-08-16"),
              _shares(308_744_000, "2011-07-30", "10-Q", "2011-08-16", start="2011-05-01"),
              _shares(308_237_000, "2011-10-29", "10-K", "2011-11-22")]
    income = [usd(222_106_000.0, _START, "2011-01-29", form="10-Q", filed="2011-02-15"),
              usd(463_932_000.0, _START, "2011-04-30", form="10-Q", filed="2011-05-17"),
              usd(683_867_000.0, _START, "2011-07-30", form="10-Q", filed="2011-08-16"),
              usd(867_394_000.0, _START, "2011-10-29", form="10-K", filed="2011-11-22")]
    assets = [usd(v, None, end, form=form, filed=filed) for v, end, form, filed in (
        (4_596_905_000.0, "2011-01-29", "10-Q", "2011-02-15"), (5_160_399_000.0, "2011-04-30", "10-Q", "2011-05-17"),
        (5_251_381_000.0, "2011-07-30", "10-Q", "2011-08-16"), (5_277_635_000.0, "2011-10-29", "10-K", "2011-11-22"))]
    return {"facts": {"us-gaap": {
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": counts}},
        "NetIncomeLoss": {"units": {"USD": income}},
        "Assets": {"units": {"USD": assets}}}}}


def test_a_10k_re_dating_a_first_quarters_count_makes_no_quarter():
    # BUG-096: ADI's FY2012 10-K re-dates fiscal Q1 2011 to end 2011-02-04, beside its 10-Q's 2011-01-29: the own count
    # made a quarter of share counts alone, a second first quarter of FY2011.
    quarterly = XBRLParser().extract_quarterly_financials(_adi_facts())
    assert "2011-02-04" not in quarterly
    assert sorted(quarterly) == ["2011-01-29", "2011-04-30", "2011-07-30", "2011-10-29"]


def test_the_quarters_the_filings_hold_keep_their_own_counts():
    quarterly = XBRLParser().extract_quarterly_financials(_adi_facts())
    assert [quarterly[end]["weighted_avg_shares_diluted"] for end in ("2011-01-29", "2011-04-30", "2011-07-30")] == [
        308_848_000, 309_619_000, 308_744_000]
    assert [quarterly[end]["fiscal_quarter"] for end in sorted(quarterly)] == [1, 2, 3, 4]
    assert quarterly["2011-01-29"]["_source_tags"]["weighted_avg_shares_diluted"] == \
        "WeightedAverageNumberOfDilutedSharesOutstanding"


def test_a_quarter_the_filings_hold_takes_its_own_count_off_the_ladders_step():
    # A balance sheet at 2011-02-04 makes it a quarter the filings hold: its own count fills it, as a count fills any,
    # and dates it (the earliest quarters of many filers have no other figure that does: their 3-month counts are
    # comparatives beside a year-to-date rung more than a quarter on).
    facts = _adi_facts()
    facts["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(
        usd(4_600_000_000.0, None, "2011-02-04", form="10-K", filed="2012-11-27"))
    quarterly = XBRLParser().extract_quarterly_financials(facts)
    quarter = quarterly["2011-02-04"]
    assert (quarter["weighted_avg_shares_diluted"], quarter["fiscal_year"], quarter["fiscal_quarter"]) == (
        308_848_000, 2011, 1)

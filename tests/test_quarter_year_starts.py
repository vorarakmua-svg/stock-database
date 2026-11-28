"""A year a 10-K closes opens the next one; a 10-Q's trailing twelve months don't (BUG-146)."""

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd

OCF = "NetCashProvidedByUsedInOperatingActivities"


def _quarters(*ocf):
    """Calendar-year filer: its FY2024 10-K, 10-Q rungs of 2025, and *ocf* entries; a balance sheet at each quarter's
    end, so every quarter is one the filings hold."""
    rungs = [usd(400.0, "2024-01-01", "2024-12-31", fy=2024, fp="FY", form="10-K", filed="2025-02-01"),
             usd(90.0, "2025-01-01", "2025-03-31", fy=2025, fp="Q1", form="10-Q", filed="2025-05-01"),
             usd(200.0, "2025-01-01", "2025-06-30", fy=2025, fp="Q2", form="10-Q", filed="2025-08-01"),
             *ocf]
    assets = [usd(900.0, None, end, form="10-Q", filed=filed) for end, filed in (
        ("2025-03-31", "2025-05-01"), ("2025-06-30", "2025-08-01"), ("2025-09-30", "2025-11-01"))]
    q = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {
        OCF: {"units": {"USD": rungs}}, "Assets": {"units": {"USD": assets}}}}})
    return {end: (p.get("operating_cash_flow"), p.get("fiscal_year"), p.get("fiscal_quarter")) for end, p in q.items()}


def test_a_10qs_trailing_twelve_months_open_no_year():
    # BUG-146 (code review 13, M4): a 10-Q's trailing twelve months to 2025-06-30 (AMZN tags such spans) read as a year,
    # so the quarter after them, a lone three months no ladder reaches, was FY2026's first quarter.
    ttm = usd(380.0, "2024-07-01", "2025-06-30", fy=2025, fp="Q2", form="10-Q", filed="2025-08-01")
    lone = usd(120.0, "2025-07-01", "2025-09-30", fy=2025, fp="Q3", form="10-Q", filed="2025-11-01")
    quarters = _quarters(ttm, lone)
    assert quarters["2025-09-30"] == (None, None, None)
    assert quarters["2025-03-31"] == (90.0, 2025, 1)
    assert quarters["2025-06-30"] == (110.0, 2025, 2)


def test_a_year_a_10k_closes_still_opens_the_next_ones_first_quarter():
    # BUG-124's rule stands for a year a 10-K closes: the lone first quarter after FY2024 is FY2025's.
    q = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {
        OCF: {"units": {"USD": [
            usd(400.0, "2024-01-01", "2024-12-31", fy=2024, fp="FY", form="10-K", filed="2025-02-01"),
            usd(90.0, "2025-01-01", "2025-03-31", fy=2025, fp="Q1", form="10-Q", filed="2025-05-01")]}},
        "Assets": {"units": {"USD": [usd(900.0, None, "2025-03-31", form="10-Q", filed="2025-05-01")]}}}}})
    assert (q["2025-03-31"]["operating_cash_flow"], q["2025-03-31"]["fiscal_year"]) == (90.0, 2025)


def test_a_10qs_span_of_a_year_another_fields_10k_reports_is_a_year():
    # BXP's 10-Q tags its 2010 dividends a share for the year (0.50, its quarterly payment) and no 10-K tags them for
    # the year, but its 10-K reports 2010 in other fields: the year is one a 10-K closes, and Q1 2011's lone figure
    # stays FY2011's first quarter.
    dps = [usd(0.5, "2010-01-01", "2010-12-31", fy=2011, fp="Q3", form="10-Q", filed="2011-11-08"),
           usd(0.5, "2011-01-01", "2011-03-31", fy=2011, fp="Q2", form="10-Q", filed="2011-08-08")]
    q = XBRLParser().extract_quarterly_financials({"facts": {"us-gaap": {
        "CommonStockDividendsPerShareCashPaid": {"units": {"USD/shares": dps}},
        "Revenues": {"units": {"USD": [
            usd(1500.0, "2010-01-01", "2010-12-31", fy=2010, fp="FY", form="10-K", filed="2011-02-25")]}},
        "Assets": {"units": {"USD": [usd(900.0, None, "2011-03-31", form="10-Q", filed="2011-05-09")]}}}}})
    assert (q["2011-03-31"]["dividends_per_share"], q["2011-03-31"]["fiscal_year"]) == (0.5, 2011)

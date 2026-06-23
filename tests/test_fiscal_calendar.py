"""Tests for SEC-frame-based calendar-year alignment across fiscal year-ends."""

import pytest

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


@pytest.fixture
def parser():
    return XBRLParser()


# ---------------- frame parsing & fallback ----------------

def test_parse_frame(parser):
    assert parser._parse_frame("CY2025") == (2025, None)
    assert parser._parse_frame("CY2024Q4I") == (2024, 4)
    assert parser._parse_frame("CY2024Q1") == (2024, 1)
    assert parser._parse_frame("") == (None, None)
    assert parser._parse_frame("garbage") == (None, None)


def test_calendar_year_from_end_fallback(parser):
    # Jan-May -> prior calendar year; Jun-Dec -> end year.
    assert parser._calendar_year_from_end("2026-01-31") == 2025  # Walmart-style
    assert parser._calendar_year_from_end("2025-06-30") == 2025  # Microsoft-style
    assert parser._calendar_year_from_end("2024-09-28") == 2024  # Apple-style
    assert parser._calendar_year_from_end("2025-12-31") == 2025  # Dec filer
    assert parser._calendar_year_from_end(None) is None


def _facts_annual(entries):
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": entries}}}}}


# ---------------- parser integration ----------------

def test_january_filer_calendar_year_from_frame(parser):
    # Walmart-style: fiscal year ends Jan 2026 but macro year is 2025; SEC frame=CY2025.
    facts = _facts_annual([
        usd(648000, "2025-02-01", "2026-01-31", fy=2026, filed="2026-03-15", frame="CY2025"),
    ])
    annual = parser.extract_annual_financials(facts, years_back=1)
    period = annual["2026"]
    assert period["fiscal_year"] == 2026          # company's own label (end-year bucket)
    assert period["calendar_year"] == 2025        # SEC-aligned macro year
    assert period["frame"] == "CY2025"


def test_calendar_year_falls_back_without_frame(parser):
    facts = _facts_annual([
        usd(100, "2025-02-01", "2026-01-31", fy=2026, filed="2026-03-15"),  # no frame
    ])
    period = parser.extract_annual_financials(facts, years_back=1)["2026"]
    assert period["calendar_year"] == 2025  # via end-month fallback


def test_june_and_sep_filers_align_to_same_calendar_year(parser):
    msft = parser.extract_annual_financials(_facts_annual([
        usd(1, "2024-07-01", "2025-06-30", fy=2025, filed="2025-07-30", frame="CY2025"),
    ]), years_back=1)["2025"]
    aapl = parser.extract_annual_financials(_facts_annual([
        usd(1, "2024-09-29", "2025-09-27", fy=2025, filed="2025-11-01", frame="CY2025"),
    ]), years_back=1)["2025"]
    assert msft["calendar_year"] == aapl["calendar_year"] == 2025


def test_quarterly_calendar_quarter(parser):
    # A cumulative ladder (Q1 then H1) sharing the fiscal-year start.
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        usd(25, "2025-02-01", "2025-05-01", fy=2026, fp="Q1", form="10-Q",
            filed="2025-05-20", frame="CY2025Q1"),
        usd(55, "2025-02-01", "2025-07-31", fy=2026, fp="Q2", form="10-Q",
            filed="2025-08-20"),
    ]}}}}}
    q = parser.extract_quarterly_financials(facts)
    period = q["2025-05-01"]                 # discrete Q1
    assert period["net_income"] == 25
    assert period["calendar_year"] == 2025
    assert period["calendar_quarter"] == 1   # from frame CY2025Q1
    assert q["2025-07-31"]["net_income"] == 30  # 55 (H1) - 25 (Q1)

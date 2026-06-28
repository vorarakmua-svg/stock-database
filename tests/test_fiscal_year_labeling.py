"""Tests for authoritative fiscal-year derivation (52/53-week Dec/Jan-boundary fix)."""

import pytest

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


@pytest.fixture
def parser():
    return XBRLParser()


# ---------------- _fiscal_year_from_end ----------------

def test_fiscal_year_from_end_early_january_is_prior_year(parser):
    # A 52/53-week December filer whose year-end drifted into early January
    # belongs to the prior fiscal year.
    assert parser._fiscal_year_from_end("2023-01-01") == 2022
    assert parser._fiscal_year_from_end("2021-01-03") == 2020
    assert parser._fiscal_year_from_end("2016-01-07") == 2015


def test_fiscal_year_from_end_keeps_end_year_otherwise(parser):
    assert parser._fiscal_year_from_end("2023-12-31") == 2023   # December filer
    assert parser._fiscal_year_from_end("2026-01-31") == 2026   # Jan-31 retailer (day > 7)
    assert parser._fiscal_year_from_end("2024-09-28") == 2024   # September filer
    assert parser._fiscal_year_from_end(None) is None


# ---------------- annual integration ----------------

def test_annual_dec_jan_boundary_no_collision(parser):
    # JNJ-like: FY2022 ends 2023-01-01, FY2023 ends 2023-12-31 -> both calendar 2023.
    # end.year bucketing would collide both into "2023" and drop FY2022; the fy map
    # keeps them distinct.
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        usd(78000, "2022-01-03", "2023-01-01", fy=2022, filed="2023-02-16"),
        usd(85000, "2023-01-02", "2023-12-31", fy=2023, filed="2024-02-16", frame="CY2023"),
        # FY2022 carried as a comparative in the FY2023 10-K (inflated fy).
        usd(78000, "2022-01-03", "2023-01-01", fy=2023, filed="2024-02-16"),
    ]}}}}}
    annual = parser.extract_annual_financials(facts, years_back=10)

    assert {"2022", "2023"} <= set(annual.keys())
    assert annual["2022"]["revenue"] == 78000
    assert annual["2022"]["fiscal_year"] == 2022
    assert annual["2022"]["period_end"] == "2023-01-01"
    assert annual["2023"]["revenue"] == 85000
    assert annual["2023"]["fiscal_year"] == 2023
    assert annual["2023"]["period_end"] == "2023-12-31"


def test_annual_january_retailer_unchanged(parser):
    # WMT-like Jan-31 filer: declared FY2026 (fy=2026), macro/calendar 2025.
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        usd(648000, "2025-02-01", "2026-01-31", fy=2026, filed="2026-03-15", frame="CY2025"),
    ]}}}}}
    period = parser.extract_annual_financials(facts, years_back=1)["2026"]
    assert period["fiscal_year"] == 2026
    assert period["calendar_year"] == 2025


# ---------------- quarterly integration ----------------

def _ni_ladder():
    # JNJ-like: FY2022 (start 2022-01-03, ends 2023-01-01) and FY2023 (start
    # 2023-01-02, ends 2023-12-31). Each is a YTD ladder Q1/H1/9M + 10-K full year.
    return [
        usd(100, "2022-01-03", "2022-04-03", fy=2022, fp="Q1", form="10-Q", filed="2022-04-20"),
        usd(210, "2022-01-03", "2022-07-03", fy=2022, fp="Q2", form="10-Q", filed="2022-07-20"),
        usd(320, "2022-01-03", "2022-10-02", fy=2022, fp="Q3", form="10-Q", filed="2022-10-20"),
        usd(440, "2022-01-03", "2023-01-01", fy=2022, fp="FY", form="10-K", filed="2023-02-16"),
        usd(120, "2023-01-02", "2023-04-02", fy=2023, fp="Q1", form="10-Q", filed="2023-04-20"),
        usd(240, "2023-01-02", "2023-07-02", fy=2023, fp="Q2", form="10-Q", filed="2023-07-20"),
        usd(370, "2023-01-02", "2023-10-01", fy=2023, fp="Q3", form="10-Q", filed="2023-10-20"),
        usd(500, "2023-01-02", "2023-12-31", fy=2023, fp="FY", form="10-K", filed="2024-02-16"),
    ]


def test_quarterly_dec_jan_boundary_fiscal_year(parser):
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": _ni_ladder()}}}}}
    q = parser.extract_quarterly_financials(facts)

    # The early-January year-end quarter rolls into FY2022, not FY2023.
    assert q["2023-01-01"]["fiscal_year"] == 2022
    assert q["2023-01-01"]["fiscal_quarter"] == 4
    assert q["2023-01-01"]["net_income"] == 120          # 440 - 320

    # FY2023's own quarters are labeled 2023, distinct from FY2022's.
    assert q["2023-04-02"]["fiscal_year"] == 2023
    assert q["2022-04-03"]["fiscal_year"] == 2022

    # No fiscal-year bucket holds two of the same fiscal quarter.
    from collections import Counter
    buckets = Counter((p["fiscal_year"], p["fiscal_quarter"]) for p in q.values()
                      if p.get("fiscal_year") and p.get("fiscal_quarter"))
    assert all(n == 1 for n in buckets.values())

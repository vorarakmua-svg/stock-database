"""Tests for authoritative fiscal-year derivation (52/53-week Dec/Jan-boundary fix)."""

import pytest

from src.parsers.xbrl_parser import XBRLParser
from tests.conftest import usd


@pytest.fixture
def parser():
    return XBRLParser()


# ---------------- _fiscal_year_fallback ----------------

def test_fiscal_year_fallback_early_january_is_prior_year(parser):
    # A 52/53-week December filer whose year-end drifted into early January
    # belongs to the prior fiscal year.
    assert parser._fiscal_year_fallback("2023-01-01") == 2022
    assert parser._fiscal_year_fallback("2021-01-03") == 2020
    assert parser._fiscal_year_fallback("2016-01-07") == 2015


def test_fiscal_year_fallback_keeps_end_year_otherwise(parser):
    assert parser._fiscal_year_fallback("2023-12-31") == 2023   # December filer
    assert parser._fiscal_year_fallback("2026-01-31") == 2026   # Jan-31 retailer (day > 7)
    assert parser._fiscal_year_fallback("2024-09-28") == 2024   # September filer
    assert parser._fiscal_year_fallback(None) is None


# ---------------- _build_fiscal_year_map ----------------

def test_build_fiscal_year_map_uses_original_filing_fy(parser):
    # End 2023-01-01 is reported by the original FY2022 10-K (fy=2022) and carried as
    # a comparative in later 10-Ks with inflated fy. Earliest-filed fp==FY wins.
    us_gaap = {
        "Revenues": {"units": {"USD": [
            usd(78000, "2022-01-03", "2023-01-01", fy=2022, filed="2023-02-16"),
            usd(78000, "2022-01-03", "2023-01-01", fy=2023, filed="2024-02-16"),
            usd(78000, "2022-01-03", "2023-01-01", fy=2024, filed="2025-02-13"),
            usd(85000, "2023-01-02", "2023-12-31", fy=2023, filed="2024-02-16"),
            # A quarter-length fp=Q1 fact must be ignored by the FY map.
            usd(20000, "2023-01-02", "2023-04-02", fy=2023, fp="Q1", form="10-Q",
                filed="2023-04-20"),
        ]}},
    }
    fy_map = parser._build_fiscal_year_map(us_gaap, {"10-K", "10-K/A"})
    assert fy_map == {"2023-01-01": 2022, "2023-12-31": 2023}

"""The sector a collection stores when the SEC's companyfacts request fails (BUG-309), and the one its parse reads
when the submissions request fails (BUG-373)."""
import sqlite3

import pytest

from src.models.stock_data import StockData
from tests.conftest import usd
from tests.test_fetcher_concurrency import _sec_only_fetcher


def _blk(tmp_path, monkeypatch, stored):
    fetcher = _sec_only_fetcher(tmp_path, monkeypatch, {
        "cik": "0002012383", "ticker": "BLK", "source": "sec_edgar",
        "company_facts_error": "503 Server Error",
        "submissions": {"name": "BlackRock, Inc.", "sic": "6211", "filings": []}})
    if stored:
        fetcher.sqlite_store.export([StockData(ticker="BLK", cik="0002012383", company_name="BlackRock, Inc.",
                                               sector_class=stored)])
    stock = fetcher.fetch_ticker("BLK", include_yahoo=False)
    fetcher.sqlite_store.export([stock])
    kept = sqlite3.connect(str(fetcher.sqlite_store.db_path)).execute(
        "SELECT sector_class FROM companies WHERE ticker = 'BLK'").fetchone()[0]
    return stock, kept


def test_a_failed_companyfacts_request_keeps_a_stored_general_company_general(tmp_path, monkeypatch):
    # BUG-309 (code review 25): Q9 makes a finance SIC without deposits general, which needs the facts. When their
    # request fails, the SIC alone says bank; the last good collection's sector stands, with a warning.
    stock, kept = _blk(tmp_path, monkeypatch, "general")
    assert (stock.sector_class, kept) == ("general", "general")
    assert any("sector 'general' taken from the stored record" in w for w in stock.warnings)


def test_with_nothing_stored_the_sic_decides(tmp_path, monkeypatch):
    stock, kept = _blk(tmp_path, monkeypatch, None)
    assert (stock.sector_class, kept) == ("bank", "bank")


def test_with_the_facts_they_decide_over_the_stored_sector(tmp_path, monkeypatch):
    # Facts that tag deposits make a finance SIC a bank whatever an earlier collection stored.
    facts = {"facts": {"us-gaap": {"Deposits": {"units": {"USD": [
        {"val": 1.0e11, "end": "2025-12-31", "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-20",
         "accn": "0000000000-26-000001"}]}}}}}
    fetcher = _sec_only_fetcher(tmp_path, monkeypatch, {
        "cik": "0000019617", "ticker": "JPM", "source": "sec_edgar", "company_facts": facts,
        "submissions": {"name": "JPMorgan Chase & Co.", "sic": "6021", "filings": []}})
    fetcher.sqlite_store.export([StockData(ticker="JPM", cik="0000019617", company_name="JPMorgan Chase & Co.",
                                           sector_class="general")])
    stock = fetcher.fetch_ticker("JPM", include_yahoo=False)
    assert stock.sector_class == "bank"


def _bac_without_submissions(tmp_path, monkeypatch, stored):
    """BAC collected with its companyfacts (a 10-K and a 10-Q with its securities book) but no submissions (a failed
    request), its companies row holding *stored* as the last good collection's sector."""
    us_gaap = {}
    for form, fp, filed, end, book in (("10-K", "FY", "2026-02-12", "2025-12-31", 925.6e9),
                                       ("10-Q", "Q1", "2026-05-05", "2026-03-31", 930.0e9)):
        filing = dict(fy=int(filed[:4]), fp=fp, form=form, filed=filed)
        for tag, val in {"LiabilitiesAndStockholdersEquity": 3.3e12, "MarketableSecurities": book,
                         "Deposits": 2.0e12}.items():
            us_gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(usd(val, None, end, **filing))
    fetcher = _sec_only_fetcher(tmp_path, monkeypatch, {
        "cik": "0000070858", "ticker": "BAC", "source": "sec_edgar", "company_facts": {"facts": {"us-gaap": us_gaap}},
        "submissions_error": "503 Server Error"})
    if stored:
        fetcher.sqlite_store.export([StockData(ticker="BAC", cik="0000070858", company_name="BANK OF AMERICA CORP",
                                               sector_class=stored)])
    return fetcher.fetch_ticker("BAC", include_yahoo=False)


def test_a_failed_submissions_request_leaves_a_stored_banks_book_unread(tmp_path, monkeypatch):
    # BUG-373: Q20's bank rule reads the run's sector class, which the submissions give; without them it was None
    # until after the parse, so the run read BAC's whole securities book ($925.6B) as short-term investments. The
    # stored sector (the fallback that already followed the parse) now decides the parse too.
    stock = _bac_without_submissions(tmp_path, monkeypatch, "bank")
    assert stock.sector_class == "bank"
    assert any("sector 'bank' taken from the stored record (the submissions data is unavailable)" in w
               for w in stock.warnings)
    assert stock.financials_annual["2025"].get("short_term_investments") is None
    assert all(period.get("short_term_investments") is None for period in stock.financials_quarterly.values())
    assert all(row.get("short_term_investments") is None for row in stock.financials_annual_known)
    assert stock.financials_annual_vintages["2025"]["2026-02-12"].get("short_term_investments") is None


@pytest.mark.parametrize("stored", ["general", None])
def test_without_submissions_a_stored_general_company_or_none_reads_the_book(tmp_path, monkeypatch, stored):
    stock = _bac_without_submissions(tmp_path, monkeypatch, stored)
    assert stock.sector_class == stored
    assert stock.financials_annual["2025"]["short_term_investments"] == 925.6e9
    assert stock.financials_quarterly["2026-03-31"]["short_term_investments"] == 930.0e9

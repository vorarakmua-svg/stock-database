"""The sector a collection stores when the SEC's companyfacts request fails (BUG-309)."""
import sqlite3

from src.models.stock_data import StockData
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

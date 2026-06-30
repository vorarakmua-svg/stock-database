"""Tests for point-in-time metrics (ratios computed on as-of-date financials)."""

import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.query.pit_metrics import PointInTimeMetrics


def _build_db(tmp_path, vintages, ticker="PRU", sector_class=None):
    """Write a StockData (vintages + optional sector_class) and return the db path.

    vintages: {fy_str: {accn: period_dict}} as StockData.financials_annual_vintages.
    """
    s = StockData(ticker=ticker, cik="1", company_name="Co")
    if sector_class is not None:
        s.sector_class = sector_class
    s.financials_annual_vintages = vintages
    db = tmp_path / "stock.db"
    SQLiteStore(db_path=str(db)).export([s])
    return str(db)


_ONE_VINTAGE = {
    "2019": {"a": {"fiscal_year": 2019, "accn": "a", "filed_date": "2020-02-15",
                   "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                   "net_income": 100.0, "total_equity": 1000.0, "revenue": 1000.0}},
}


def test_sector_auto_read_from_companies(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE, sector_class="bank")
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm._sector("PRU") == "bank"


def test_sector_none_when_absent_or_unknown(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE)  # no sector_class set
    with PointInTimeMetrics.from_path(db) as pm:
        assert pm._sector("PRU") is None       # column is NULL
        assert pm._sector("ZZZZ") is None       # no such company row


def test_from_path_builds_reader_and_closes(tmp_path):
    db = _build_db(tmp_path, _ONE_VINTAGE)
    pm = PointInTimeMetrics.from_path(db)
    assert pm.reader is not None
    pm.close()
    # After close, the reader's connection is unusable.
    with pytest.raises(sqlite3.ProgrammingError):
        pm.reader.conn.execute("SELECT 1")

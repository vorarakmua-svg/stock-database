"""Tests for the point-in-time as-of-date query API (no look-ahead)."""

import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData
from src.query.asof import AsOfReader


def _build_db(tmp_path, vintages):
    """Write a StockData carrying the given annual vintages and return the db path.

    vintages: {fy_str: {accn: period_dict}} exactly as StockData.financials_annual_vintages.
    """
    s = StockData(ticker="PRU", cik="1", company_name="Prudential")
    s.financials_annual_vintages = vintages
    db = tmp_path / "stock.db"
    SQLiteStore(db_path=str(db)).export([s])
    return str(db)


def test_reader_connection_is_read_only(tmp_path):
    db = _build_db(tmp_path, {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    reader = AsOfReader(db)
    with pytest.raises(sqlite3.OperationalError):
        reader._conn.execute(
            "INSERT INTO financials_annual_vintages (ticker, fiscal_year, accn) "
            "VALUES ('X', 1, 'y')"
        )
    reader.close()


def test_reader_context_manager_closes(tmp_path):
    db = _build_db(tmp_path, {
        "2019": {"a-orig": {"fiscal_year": 2019, "accn": "a-orig",
                            "filed_date": "2020-02-15", "period_end": "2019-12-31",
                            "form": "10-K", "calendar_year": 2019, "net_income": 100.0}},
    })
    with AsOfReader(db) as reader:
        assert reader._conn is not None
    # After exit, using the closed connection raises.
    with pytest.raises(sqlite3.ProgrammingError):
        reader._conn.execute("SELECT 1")

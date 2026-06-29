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


def _pru_two_vintages(tmp_path):
    return _build_db(tmp_path, {
        "2019": {
            "a-orig": {"fiscal_year": 2019, "accn": "a-orig", "filed_date": "2020-02-15",
                       "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                       "net_income": 100.0, "revenue": 1000.0},
            "b-restate": {"fiscal_year": 2019, "accn": "b-restate", "filed_date": "2021-02-15",
                          "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                          "net_income": 90.0, "revenue": 1000.0},
        },
    })


def test_not_yet_filed_returns_none(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # Before the original filing, FY2019 was not yet known.
        assert r.as_of_annual("PRU", 2019, "2020-02-14") is None


def test_boundary_is_inclusive(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # On the exact filing date, the original is visible.
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row is not None
        assert row["net_income"] == 100.0
        assert row["accn"] == "a-orig"


def test_restatement_switch(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        # Between the two filings: still the original.
        assert r.as_of_annual("PRU", 2019, "2020-06-30")["net_income"] == 100.0
        # On/after the restatement: the restated value.
        assert r.as_of_annual("PRU", 2019, "2021-02-15")["net_income"] == 90.0
        assert r.as_of_annual("PRU", 2019, "2025-01-01")["net_income"] == 90.0


def test_row_carries_provenance_metadata(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-06-30")
        assert row["fiscal_year"] == 2019
        assert row["accn"] == "a-orig"
        assert row["filed_date"] == "2020-02-15"
        assert row["period_end"] == "2019-12-31"
        assert row["form"] == "10-K"


def test_accepts_date_object(tmp_path):
    from datetime import date
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_annual("PRU", 2019, date(2020, 6, 30))["net_income"] == 100.0


def test_same_day_tie_break_prefers_higher_accn(tmp_path):
    # Two filings on the same date: deterministic accn DESC tie-break.
    db = _build_db(tmp_path, {
        "2019": {
            "0001-10K": {"fiscal_year": 2019, "accn": "0001-10K", "filed_date": "2020-02-15",
                         "period_end": "2019-12-31", "form": "10-K", "calendar_year": 2019,
                         "net_income": 100.0},
            "0002-10KA": {"fiscal_year": 2019, "accn": "0002-10KA", "filed_date": "2020-02-15",
                          "period_end": "2019-12-31", "form": "10-K/A", "calendar_year": 2019,
                          "net_income": 105.0},
        },
    })
    with AsOfReader(db) as r:
        row = r.as_of_annual("PRU", 2019, "2020-02-15")
        assert row["accn"] == "0002-10KA"
        assert row["net_income"] == 105.0


def test_unknown_ticker_or_year_returns_none(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_annual("ZZZZ", 2019, "2025-01-01") is None
        assert r.as_of_annual("PRU", 1990, "2025-01-01") is None


def test_as_of_value_delegates(tmp_path):
    db = _pru_two_vintages(tmp_path)
    with AsOfReader(db) as r:
        assert r.as_of_value("PRU", 2019, "net_income", "2020-06-30") == 100.0
        assert r.as_of_value("PRU", 2019, "net_income", "2021-02-15") == 90.0
        # Not yet filed → None passthrough.
        assert r.as_of_value("PRU", 2019, "net_income", "2019-01-01") is None
        # Missing field → None.
        assert r.as_of_value("PRU", 2019, "no_such_field", "2025-01-01") is None

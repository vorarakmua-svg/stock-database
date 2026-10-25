"""``SQLiteStore.write_batch`` (code review 19, I-1): a batch's stocks in one transaction, each in its own savepoint.

A stock whose write fails is rolled back alone, logged with its ticker and the error, and recorded on its StockData
("SQLite write failed: ..."); the others are committed. The schema, its migration, the write lock or the commit
failing, or SQLite rolling the whole transaction back, fails the whole export: none of the batch is stored, and the
result says why. Ctrl+C mid-batch commits none of it. ``export`` still returns the database's path, now when it stored
any of the batch.
"""
import logging
import sqlite3
from datetime import datetime

import pytest

import src.exporters.sqlite_store as sqlite_store
from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData

COLLECTED = datetime(2026, 10, 23, 9, 30)
#: The tables each stock of these tests has a row in.
TABLES = ("companies", "market_snapshots", "collection_runs", "price_bars")


def _stock(ticker, employees=100, volume=1.0):
    """A company with a companies row, a snapshot, a collection run and a daily bar. An *employees* (its first row) or
    a bar *volume* (among its last) that SQLite can't bind, a dict, fails its write early or late."""
    stock = StockData(ticker=ticker, cik="0000000001", company_name=f"{ticker} Inc", collected_at=COLLECTED)
    stock.company_info = {"sector": "Industrials", "full_time_employees": employees}
    stock.market_data = {"current_price": 10.0}
    stock.price_bars = [{"date": "2026-10-23", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": volume}]
    stock.data_sources = ["yahoo_finance"]
    return stock


def _tickers(db, table):
    conn = sqlite3.connect(db)
    try:
        return [row[0] for row in conn.execute(f"SELECT DISTINCT ticker FROM {table} ORDER BY ticker")]
    finally:
        conn.close()


def test_a_stock_that_cant_be_bound_is_rolled_back_alone_and_the_others_are_committed(tmp_path, caplog):
    # The review's probe (one_bad_ticker): one value SQLite couldn't bind rolled back all four stocks.
    db = tmp_path / "stock.db"
    stocks = [_stock("GOODA"), _stock("GOODB"), _stock("BADC", employees={"oops": 1}), _stock("GOODD")]
    result = SQLiteStore(db).write_batch(stocks)
    assert (result.path, result.stored, list(result.failed), result.error) == (
        db, ["GOODA", "GOODB", "GOODD"], ["BADC"], None)
    assert "Error binding parameter" in result.failed["BADC"]
    for table in TABLES:
        assert _tickers(db, table) == ["GOODA", "GOODB", "GOODD"]
    assert len(stocks[2].errors) == 1 and stocks[2].errors[0].startswith("SQLite write failed: Error binding parameter")
    assert [s.errors for s in stocks if s.ticker != "BADC"] == [[], [], []]
    assert [r for r in caplog.records
            if r.levelno == logging.ERROR and "BADC" in r.getMessage() and "Error binding parameter" in r.getMessage()]


def test_a_stock_that_fails_late_leaves_none_of_its_rows(tmp_path):
    # Its companies row, snapshot and collection run were written before its bar failed: its savepoint takes them back.
    db = tmp_path / "stock.db"
    result = SQLiteStore(db).write_batch([_stock("AAA"), _stock("BBB", volume={"x": 1}), _stock("CCC")])
    assert (result.stored, list(result.failed)) == (["AAA", "CCC"], ["BBB"])
    for table in TABLES:
        assert _tickers(db, table) == ["AAA", "CCC"]


def test_a_failed_recollection_keeps_what_the_last_one_stored(tmp_path):
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    assert store.write_batch([_stock("AAA")]).stored == ["AAA"]
    later = _stock("AAA", volume={"x": 1})
    later.collected_at = datetime(2026, 10, 24, 9, 30)
    later.company_info["sector"] = "Energy"
    assert list(store.write_batch([later]).failed) == ["AAA"]
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT sector FROM companies").fetchall() == [("Industrials",)]
        assert conn.execute("SELECT collected_at FROM collection_runs").fetchall() == [(COLLECTED.isoformat(),)]
    finally:
        conn.close()


def test_export_returns_the_path_when_it_stored_any(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    assert store.export([_stock("AAA"), _stock("BBB", employees={"x": 1})]) == tmp_path / "stock.db"
    assert store.export([_stock("CCC", employees={"x": 1})]) is None
    assert store.export([]) is None
    assert _tickers(tmp_path / "stock.db", "companies") == ["AAA"]


@pytest.fixture
def quick_busy_timeout(monkeypatch):
    """SQLite's wait for a lock cut from 5 s to 0.2 s, so a locked database fails fast."""
    connect = sqlite3.connect
    monkeypatch.setattr(sqlite_store.sqlite3, "connect", lambda *a, **k: connect(*a, **{**k, "timeout": 0.2}))


def test_a_locked_database_fails_the_whole_export_and_says_so(tmp_path, caplog, quick_busy_timeout):
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    store.write_batch([_stock("OLD")])
    holder = sqlite3.connect(db)
    holder.execute("BEGIN IMMEDIATE")  # another writer
    stocks = [_stock("AAA"), _stock("BBB")]
    try:
        result = store.write_batch(stocks)
    finally:
        holder.rollback()
        holder.close()
    assert (result.error, result.stored, result.failed) == (
        "database is locked", [], {"AAA": "database is locked", "BBB": "database is locked"})
    assert [s.errors for s in stocks] == [["SQLite write failed: database is locked"]] * 2
    assert _tickers(db, "companies") == ["OLD"]
    assert "Error writing SQLite store: database is locked" in caplog.text and "AAA, BBB" in caplog.text
    assert store.export([_stock("CCC")]) == db  # the lock gone, the next batch is stored


def test_a_migration_that_fails_fails_the_whole_export(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "stock.db")

    def locked(conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_migrate", locked)
    result = store.write_batch([_stock("AAA")])
    assert (result.error, result.stored, list(result.failed)) == ("database is locked", [], ["AAA"])
    assert _tickers(tmp_path / "stock.db", "companies") == []


class _CommitFails:
    """A connection whose commit fails, as a full disk's or an I/O error's would."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self):
        raise sqlite3.OperationalError("disk I/O error")


def test_a_commit_that_fails_stores_none_of_the_batch(tmp_path, monkeypatch):
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    connect = store._connect
    monkeypatch.setattr(store, "_connect", lambda: _CommitFails(connect()))
    stocks = [_stock("AAA"), _stock("BBB", employees={"x": 1})]
    result = store.write_batch(stocks)
    assert (result.error, result.stored, list(result.failed)) == ("disk I/O error", [], ["AAA", "BBB"])
    assert stocks[0].errors == ["SQLite write failed: disk I/O error"]
    assert len(stocks[1].errors) == 1 and "Error binding parameter" in result.failed["BBB"]  # its own failure kept
    assert _tickers(db, "companies") == []


def test_a_failure_that_rolls_the_whole_transaction_back_stores_none_of_the_batch(tmp_path, monkeypatch):
    # SQLite rolls a whole transaction back on some errors (a full disk, an I/O error), the stocks written before in
    # the batch with it: none may be reported stored, and none written after it.
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    write = store._write_stock

    def failing(conn, stock):
        if stock.ticker == "CCC":
            conn.rollback()
            raise sqlite3.OperationalError("database or disk is full")
        write(conn, stock)

    monkeypatch.setattr(store, "_write_stock", failing)
    result = store.write_batch([_stock("AAA"), _stock("BBB"), _stock("CCC"), _stock("DDD")])
    assert (result.error, result.stored, list(result.failed)) == (
        "database or disk is full", [], ["AAA", "BBB", "CCC", "DDD"])
    assert _tickers(db, "companies") == []


def test_ctrl_c_mid_batch_commits_none_of_it(tmp_path, monkeypatch):
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    write = store._write_stock

    def interrupted(conn, stock):
        if stock.ticker == "BBB":
            raise KeyboardInterrupt
        write(conn, stock)

    monkeypatch.setattr(store, "_write_stock", interrupted)
    stocks = [_stock("AAA"), _stock("BBB"), _stock("CCC")]
    with pytest.raises(KeyboardInterrupt):
        store.write_batch(stocks)
    assert _tickers(db, "companies") == []
    assert [s.errors for s in stocks] == [[], [], []]

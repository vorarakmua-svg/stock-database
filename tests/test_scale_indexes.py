"""The two indexes the scale pre-flight added (build task M5-4; docs/superpowers/specs/2026-10-25-universe-automation-
design.md, "Scale"; qa/evidence/scale-m5-4-plans.txt):
- insider_trades (ticker, code, date): the screener's insider fields seek a company's trades of one code in the year
  (its open-market purchases, and for the net buying its sales too) instead of reading every trade of its year;
- unmapped_facts (tag, ticker, label): covers the quality page's top unmapped tags (``Reader.unmapped_top``), which
  then reads the index instead of a table row per fact.

Both are created with the schema (``CREATE INDEX IF NOT EXISTS``), so a new database has them and an older one gains
them at its next write; the web app, which opens the database read-only, plans with them once they exist.
"""
import sqlite3
from datetime import datetime

import pytest

from src.exporters.sqlite_store import SQLiteStore, read_only_uri
from src.models.stock_data import StockData
from src.webapp.repository import Reader
from src.webapp.screener import ScreenSpec, build_screen_query

#: The indexes by name: their table and columns.
INDEXES = {
    "idx_insider_trades_code": ("insider_trades", ["ticker", "code", "date"]),
    "idx_unmapped_tag_cover": ("unmapped_facts", ["tag", "ticker", "label"]),
}


def _indexes(db):
    """Each index made by a CREATE INDEX: its table and columns."""
    conn = sqlite3.connect(db)
    try:
        return {name: (table, [r[2] for r in conn.execute(f"PRAGMA index_info({name})")])
                for name, table in conn.execute(
                    "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
    finally:
        conn.close()


def _plan(conn, sql, params=()):
    return [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]


@pytest.fixture
def db(tmp_path):
    """A database with the schema, a company with two Form 4 trades (a purchase and a sale) and two unmapped facts."""
    path = tmp_path / "stock.db"
    store = SQLiteStore(path)
    stock = StockData(ticker="AAA", cik="0000000001", company_name="AAA Corp", sector_class="general",
                      collected_at=datetime(2026, 10, 1))
    stock.unmapped_facts = [{"tag": "SomeTag", "label": "Some label", "unit": "USD", "value": 1.0},
                            {"tag": "OtherTag", "label": "Other label", "unit": "USD", "value": 2.0}]
    stock.add_source("sec_edgar")
    store.export([stock])
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO insider_filings (accession, owner_cik, ticker, form, filed_date, collected_at) "
                 "VALUES ('0000000001-26-000001', '0000000009', 'AAA', '4', '2026-10-02', '2026-10-03')")
    conn.executemany("INSERT INTO insider_trades (accession, seq, ticker, kind, date, code, shares, price) "
                     "VALUES ('0000000001-26-000001', ?, 'AAA', 'nonderivative', '2026-09-30', ?, 100, 50.0)",
                     [(0, "P"), (1, "S")])
    conn.commit()
    conn.close()
    return path


def test_a_new_database_has_both_indexes_and_a_later_write_keeps_them(tmp_path):
    store = SQLiteStore(tmp_path / "new.db")
    for _ in range(2):  # the second, as every later write: the indexes exist, and the schema leaves them as they are
        conn = store._connect()
        store._create_schema(conn)
        conn.close()
        found = _indexes(tmp_path / "new.db")
        assert {name: found.get(name) for name in INDEXES} == INDEXES


def _a_collection(store):
    store.export([StockData(ticker="ZZZ", cik="0000000002", company_name="ZZZ Inc.")])


def _a_refresh_log(store):
    store.record_refresh_step("quotes", "2026-10-25T01:00:00", "2026-10-25T01:01:00", True, {"stored": 1})


def _a_market_fetch(store):
    store.export_market_series("fred:GDP", {"fred:GDP": [("2026-04-01", 31000.0)]}, None, "2026-10-25T01:00:00")


@pytest.mark.parametrize("write", [_a_collection, _a_refresh_log, _a_market_fetch])
def test_an_older_database_gains_both_at_its_next_write_with_its_rows(db, write):
    """A database from before them (the two indexes dropped) gains both at a write, whichever write it is, every row
    in them."""
    conn = sqlite3.connect(db)
    for name in INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.commit()
    conn.close()
    assert not set(INDEXES) & set(_indexes(db))
    write(SQLiteStore(db))
    found = _indexes(db)
    assert {name: found.get(name) for name in INDEXES} == INDEXES
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM insider_trades INDEXED BY idx_insider_trades_code "
                            "WHERE ticker = 'AAA' AND code IN ('P', 'S')").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM unmapped_facts INDEXED BY idx_unmapped_tag_cover "
                            "WHERE tag > ''").fetchone()[0] == 2
    finally:
        conn.close()


def test_the_screeners_insider_subqueries_seek_a_companys_trades_by_code(db):
    """The default screen's plan: each insider subquery (the purchases' ``t``, the cluster's ``t1`` and ``t2``, and
    the net buying's ``t``) seeks the company's trades of a code in its date range on the new index."""
    conn = sqlite3.connect(read_only_uri(db), uri=True)
    sql, params = build_screen_query(ScreenSpec())
    plan = _plan(conn, sql, params)
    conn.close()
    searches = [line for line in plan if line.startswith(("SEARCH t ", "SEARCH t1 ", "SEARCH t2 "))]
    assert len(searches) == 5
    assert all("USING INDEX idx_insider_trades_code (ticker=? AND code=? AND date>" in line for line in searches)
    assert "SEARCH t2 USING INDEX idx_insider_trades_code (ticker=? AND code=? AND date>? AND date<?)" in plan


class _Recording:
    """A Reader connection that records each statement it runs, with its parameters."""

    def __init__(self, conn):
        self.conn, self.calls = conn, []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self.conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def test_the_ins_tabs_trades_keep_the_date_ordered_index(db):
    """The INS tab reads a company's trades newest first: it stays on idx_insider_trades_ticker (ticker, date), whose
    order the new index (ticker, code, date) doesn't give."""
    r = Reader(db)
    try:
        recording = _Recording(r._conn)
        r._conn = recording
        assert len(r.sec_insider_trades("AAA")) == 2
        (sql, params), = recording.calls
        plan = _plan(recording.conn, sql, params)
    finally:
        r.close()
    assert "SEARCH t USING INDEX idx_insider_trades_ticker (ticker=?)" in plan
    assert "USE TEMP B-TREE FOR RIGHT PART OF ORDER BY" in plan


def test_the_quality_pages_top_unmapped_tags_read_the_covering_index(db):
    r = Reader(db)
    try:
        recording = _Recording(r._conn)
        r._conn = recording
        top = r.unmapped_top(limit=50)
        (sql, params), = recording.calls
        plan = _plan(recording.conn, sql, params)
    finally:
        r.close()
    assert sorted((row["tag"], row["n_companies"], row["example_label"]) for row in top) == [
        ("OtherTag", 1, "Other label"), ("SomeTag", 1, "Some label")]
    assert "SCAN unmapped_facts USING COVERING INDEX idx_unmapped_tag_cover" in plan

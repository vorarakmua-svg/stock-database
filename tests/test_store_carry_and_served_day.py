"""The store's writers on what a run didn't get, and the day a run's payments were served.

BUG-054: a priced collection without a beta carries the last one, as a quote refresh does. BUG-055: an empty string is
no value, to SQLite and the JSON as to the CSV. BUG-098: two first runs at once both add a missing column, under the
write lock, looking again once they hold it. BUG-131 / BUG-307: a run without bars dates its payments no earlier than
the day before New York's, nor before the latest split Yahoo lists. BUG-145: a refresh that can't compute the EV family
carries only a full collection's."""
import json
import sqlite3
from datetime import datetime

import pytest

from src.exporters.csv_exporter import CSVExporter
from src.exporters.json_exporter import JSONExporter
from src.exporters.sqlite_store import SQLiteStore, _served_day
from src.fetchers.quote_refresh import refresh_quote
from src.models.stock_data import StockData
from src.valuation.inputs import load_inputs
from tests.test_quote_refresh_payments import FACTOR, SPLIT, _collect, _Handler, _quote, _yahoo


def _priced(ticker="ZZZ", at="2026-06-01T10:00:00", **market):
    stock = StockData(ticker=ticker, cik="000", company_name=f"{ticker} Inc.",
                      collected_at=datetime.fromisoformat(at))
    stock.market_data = {"current_price": 100.0, **market}
    return stock


def _latest(db, columns):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT {columns} FROM market_snapshots WHERE ticker = 'ZZZ' "
                            "ORDER BY collected_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()


# ---- BUG-054 -----------------------------------------------------------------------------------------------------

def test_a_priced_collection_without_a_beta_carries_the_last_one(tmp_path):
    db = tmp_path / "stock.db"
    SQLiteStore(db).export([_priced(beta=1.3)])
    SQLiteStore(db).export([_priced(at="2026-06-02T10:00:00")])
    assert _latest(db, "collected_at, beta") == ("2026-06-02T10:00:00", 1.3)


def test_a_collections_own_beta_replaces_the_last_one(tmp_path):
    db = tmp_path / "stock.db"
    SQLiteStore(db).export([_priced(beta=1.3)])
    SQLiteStore(db).export([_priced(at="2026-06-02T10:00:00", beta=0.9)])
    assert _latest(db, "beta") == (0.9,)


def test_a_collection_carries_the_last_known_beta_past_a_snapshot_without_one(tmp_path):
    # INTU's and KEYS's latest collections stored none: the next one carries the one before.
    db = tmp_path / "stock.db"
    SQLiteStore(db).export([_priced(beta=1.3)])
    _with_refresh_row(db, "2026-06-02T10:00:00")
    SQLiteStore(db).export([_priced(at="2026-06-03T10:00:00")])
    assert _latest(db, "collected_at, beta") == ("2026-06-03T10:00:00", 1.3)


# ---- BUG-055 -----------------------------------------------------------------------------------------------------

def test_an_empty_company_field_keeps_the_stored_one_in_all_three_stores(tmp_path):
    db = tmp_path / "stock.db"
    first, second = _priced(), _priced(at="2026-06-02T10:00:00")
    first.company_info = {"website": "https://zzz.example", "industry": "Widgets"}
    second.company_info = {"website": "", "industry": "Gadgets"}
    second.company_name = ""
    for run in (first, second):
        SQLiteStore(db).export([run])
        JSONExporter(tmp_path).export([run])
        CSVExporter(tmp_path).export([run])
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT website, industry, company_name FROM companies").fetchone() == (
        "https://zzz.example", "Gadgets", "ZZZ Inc.")
    conn.close()
    stored = json.loads((tmp_path / "ZZZ.json").read_text(encoding="utf-8"))
    assert (stored["company_info"]["website"], stored["company_info"]["industry"]) == ("https://zzz.example",
                                                                                        "Gadgets")
    assert stored["company_name"] == "ZZZ Inc."
    csv_text = (tmp_path / "summary.csv").read_text(encoding="utf-8-sig")
    assert "https://zzz.example" in csv_text and "Gadgets" in csv_text


# ---- BUG-098 -----------------------------------------------------------------------------------------------------

def _pre_warnings_db(tmp_path):
    """A database from before `collection_runs.warnings` (the user's copy lacked it, BUG-098)."""
    db = tmp_path / "stock.db"
    SQLiteStore(db).export([_priced(ticker="OLD")])
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE collection_runs DROP COLUMN warnings")
    conn.commit()
    conn.close()
    return db


class _Watched:
    """A connection that records each ALTER (whether a write transaction held it), and can make another run add the
    column `warnings` just after this one read collection_runs' columns."""

    def __init__(self, conn, db, race):
        self._conn, self._db, self._race = conn, db, race
        self._last, self.raced, self.immediate, self.alters = "", False, False, []

    def execute(self, sql, *args):
        if self._race and not self.raced and self._last.startswith("PRAGMA table_info(collection_runs)"):
            self.raced = True
            other = sqlite3.connect(self._db)
            other.execute('ALTER TABLE collection_runs ADD COLUMN "warnings" TEXT')
            other.commit()
            other.close()
        if sql == "BEGIN IMMEDIATE":
            self.immediate = True
        if sql.startswith("ALTER TABLE collection_runs"):
            self.alters.append((sql, self._conn.in_transaction and self.immediate))
        self._last = sql
        result = self._conn.execute(sql, *args)
        if not self._conn.in_transaction:
            self.immediate = False
        return result

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _watch(monkeypatch, db, race):
    real_connect, conns = SQLiteStore._connect, []

    def connect(self):
        conns.append(_Watched(real_connect(self), db, race))
        return conns[-1]

    monkeypatch.setattr(SQLiteStore, "_connect", connect)
    return conns


def test_the_loser_of_two_first_runs_finds_the_column_the_winner_added(tmp_path, monkeypatch):
    # BUG-098: the loser read the columns, the winner added `warnings`, and the loser's ALTER failed "duplicate column
    # name", its whole batch with it.
    db = _pre_warnings_db(tmp_path)
    conns = _watch(monkeypatch, db, race=True)
    result = SQLiteStore(db).write_batch([_priced()])
    assert any(c.raced for c in conns)
    assert result.stored == ["ZZZ"], result


def test_a_missing_column_is_added_under_the_write_lock(tmp_path, monkeypatch):
    db = _pre_warnings_db(tmp_path)
    conns = _watch(monkeypatch, db, race=False)
    assert SQLiteStore(db).write_batch([_priced()]).stored == ["ZZZ"]
    alters = [a for c in conns for a in c.alters]
    assert alters and all(locked for _, locked in alters), alters


def test_a_column_added_outside_a_transaction_is_committed(tmp_path):
    db = _pre_warnings_db(tmp_path)
    conn = sqlite3.connect(db)
    SQLiteStore._ensure_columns(conn, "collection_runs", {"warnings": "TEXT"})
    assert not conn.in_transaction
    other = sqlite3.connect(db)
    assert "warnings" in {row[1] for row in other.execute("PRAGMA table_info(collection_runs)")}
    other.close()
    conn.close()


# ---- BUG-131, BUG-307 --------------------------------------------------------------------------------------------

def test_a_run_east_of_new_york_without_bars_dates_its_payments_the_day_before_new_yorks():
    # BUG-131: a UTC+12 collector at 00:30 on the 13th is New York's 08:30 on the 12th, before the open on a split
    # dated the 12th, which Yahoo hasn't applied: the payments are dated the 11th, not the 12th.
    assert _served_day(StockData(ticker="ZZZ"), "2026-06-13T00:30:00+12:00") == "2026-06-11"


def test_a_run_without_bars_dates_its_payments_no_earlier_than_the_day_before_new_yorks():
    # BUG-307: the latest split Yahoo lists can be years old; the payments are served on the run's own day.
    stock = StockData(ticker="ZZZ", splits=[{"date": "2021-06-01", "ratio": 2.0}])
    assert _served_day(stock, "2024-09-20T08:51:00-04:00") == "2024-09-19"


def test_a_split_yahoo_lists_on_new_yorks_day_dates_the_payments():
    stock = StockData(ticker="ZZZ", splits=[{"date": "2026-06-12", "ratio": 10.0}])
    assert _served_day(stock, "2026-06-12T10:30:00-04:00") == "2026-06-12"


def test_a_runs_last_bar_still_dates_its_payments():
    stock = StockData(ticker="ZZZ", price_bars=[{"date": "2026-06-10", "close": 1.0}])
    assert _served_day(stock, "2026-06-13T00:30:00+12:00") == "2026-06-10"


def test_a_refresh_after_a_stored_split_yahoo_no_longer_lists_keeps_its_payments_on_their_basis(tmp_path):
    # BUG-307: the collection stored the 2024-08-01 factor; the refresh's list holds the 2021 split alone. Its payments,
    # on Yahoo's basis of the day it served them, were dated 2021-06-01 and divided again by the stored factor.
    db = _collect(tmp_path / "stock.db", "2024-09-01", factor=True)
    quote = _quote("2024-09-20", factor=True)
    quote["splits"] = [SPLIT]
    result = refresh_quote(_Handler(quote), SQLiteStore(db), "ZZZ", "2024-09-20T08:51:00-04:00")
    assert result.saved
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    inputs = load_inputs(conn, "ZZZ")
    conn.close()
    served = dict(inputs.dividends)
    yahoo = {p["date"]: p["amount"] for p in _yahoo("2024-09-20", True)["payments"]}
    assert served["2024-06-14"] == pytest.approx(yahoo["2024-06-14"]) == pytest.approx(1.0 / FACTOR["ratio"])


def test_a_refresh_on_a_splits_day_dates_its_payments_by_the_split_it_lists(tmp_path):
    # A split the collection stored that morning, which the refresh lists again (no factor since the collection): its
    # payments are on the split's basis, served on its day, not the day before New York's.
    db = tmp_path / "stock.db"
    run = _priced(at="2026-06-12T10:30:00-04:00", market_cap=1e9)
    run.splits = [{"date": "2026-06-12", "ratio": 10.0}]
    SQLiteStore(db).export([run])
    quote = {"market_data": {"current_price": 10.0, "market_cap": 1e9},
             "splits": [{"date": "2026-06-12", "ratio": 10.0}],
             "dividend_history": {"dividend_payments": [{"date": "2026-03-01", "amount": 0.1}]}}
    result = refresh_quote(_Handler(quote), SQLiteStore(db), "ZZZ", "2026-06-12T14:00:00-04:00")
    assert result.outcome == "stored"
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT served_on FROM dividend_events WHERE ticker = 'ZZZ'").fetchall() == [("2026-06-12",)]
    conn.close()


# ---- BUG-145 -----------------------------------------------------------------------------------------------------

def _with_refresh_row(db, at, **values):
    """A snapshot a quote refresh wrote (no collection run beside it), as one before BUG-126 did: Yahoo's EV."""
    conn = sqlite3.connect(db)
    columns = ["ticker", "collected_at", "current_price", *values]
    conn.execute(f"INSERT INTO market_snapshots ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                 ["ZZZ", at, 101.0, *values.values()])
    conn.commit()
    conn.close()


def test_a_refresh_without_a_stored_year_carries_no_refreshs_yahoo_ev(tmp_path):
    db = tmp_path / "stock.db"
    SQLiteStore(db).export([_priced(market_cap=1e9)])  # no stored year: no EV family of ours
    _with_refresh_row(db, "2026-06-02T10:00:00", enterprise_value=5010.0, ev_to_ebitda=50.1)
    SQLiteStore(db).upsert_quote("ZZZ", {"market_data": {"current_price": 102.0, "market_cap": 1.1e9}},
                                 "2026-06-03T10:00:00")
    assert _latest(db, "collected_at, enterprise_value, ev_to_ebitda") == ("2026-06-03T10:00:00", None, None)


def test_a_quote_without_a_market_cap_carries_the_last_collections_ev(tmp_path):
    db = tmp_path / "stock.db"
    SQLiteStore(db).export([_priced(market_cap=1e9)])
    conn = sqlite3.connect(db)
    conn.execute("UPDATE market_snapshots SET enterprise_value = 4000.0, ev_to_ebitda = 40.0")
    conn.commit()
    conn.close()
    _with_refresh_row(db, "2026-06-02T10:00:00", enterprise_value=5010.0, ev_to_ebitda=50.1)
    SQLiteStore(db).upsert_quote("ZZZ", {"market_data": {"current_price": 102.0}}, "2026-06-03T10:00:00")
    assert _latest(db, "enterprise_value, ev_to_ebitda") == (4000.0, 40.0)

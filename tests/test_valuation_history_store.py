"""The valuation history in SQLite (Milestone 1): its two tables, the per-ticker replace, the engine hook.

docs/superpowers/specs/2026-10-21-valuation-history-design.md
"""
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation import engine
from src.valuation.backfill import main as backfill_main
from src.valuation.engine import MEDIAN_MODELS, compute_and_store
from src.valuation.history import HistoryResult, HistoryRow, MetricRange
from tests.test_valuation_history_inputs import _db

HISTORY_COLUMNS = ["ticker", "date", "close", "market_cap", "enterprise_value", "ttm_period_end", "pe", "ps", "pb",
                   "pfcf", "ev_ebitda", "pffo", "div_yield", "fair_value", "fair_value_n"]
RANGE_COLUMNS = ["ticker", "metric", "applicable", "na_reason", "as_of", "window_start", "n_months", "current_value",
                 "pct_rank", "min", "p25", "median", "p75", "max", "implied_value", "in_fair_value", "n_metrics",
                 "computed_at"]


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_the_history_tables_are_created(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "s.db")
    conn = store._connect()
    store._create_schema(conn)
    assert _columns(conn, "valuation_history") == HISTORY_COLUMNS
    assert _columns(conn, "valuation_ranges") == RANGE_COLUMNS


def _result(ticker, n):
    rows = [HistoryRow(date=f"2025-{m:02d}-28", close=10.0 + m, market_cap=1e9, ttm_period_end="2024-12-31",
                       multiples={"pe": 15.0, "ps": 2.0, "pb": None, "pfcf": None, "ev_ebitda": 9.0, "pffo": None,
                                  "div_yield": 0.02},
                       fair_value=11.0 if m > 1 else None, fair_value_n=2 if m > 1 else 0) for m in range(1, n + 1)]
    ranges = [MetricRange(metric="pe", applicable=True, as_of=rows[-1].date, window_start=rows[0].date, n_months=n,
                          current=15.0, pct_rank=0.5, min=10.0, p25=12.0, median=15.0, p75=18.0, max=20.0,
                          implied_value=11.5, in_fair_value=True),
              MetricRange(metric="fair_value", applicable=True, as_of=rows[-1].date, current=11.0, p25=9.0,
                          p75=13.0, n_metrics=2),
              MetricRange(metric="pffo", na_reason="REITs only", as_of=rows[-1].date)]
    return HistoryResult(ticker=ticker, rows=rows, ranges=ranges)


def test_an_export_replaces_one_tickers_rows(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "s.db")
    store.export_valuation_history("AAA", _result("AAA", 3), "2026-10-21T00:00:00+00:00")
    store.export_valuation_history("BBB", _result("BBB", 3), "2026-10-21T00:00:00+00:00")
    store.export_valuation_history("AAA", _result("AAA", 2), "2026-10-22T00:00:00+00:00")
    conn = sqlite3.connect(tmp_path / "s.db")

    def count(table, ticker):
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ticker = ?", (ticker,)).fetchone()[0]

    assert (count("valuation_history", "AAA"), count("valuation_history", "BBB")) == (2, 3)
    assert (count("valuation_ranges", "AAA"), count("valuation_ranges", "BBB")) == (3, 3)
    assert conn.execute("SELECT close, pe, pb, div_yield, fair_value, fair_value_n FROM valuation_history "
                        "WHERE ticker = 'AAA' AND date = '2025-02-28'").fetchone() == (12.0, 15.0, None, 0.02, 11.0, 2)
    assert conn.execute("SELECT applicable, current_value, pct_rank, median, implied_value, in_fair_value, "
                        "computed_at FROM valuation_ranges WHERE ticker = 'AAA' AND metric = 'pe'"
                        ).fetchone() == (1, 15.0, 0.5, 15.0, 11.5, 1, "2026-10-22T00:00:00+00:00")
    assert conn.execute("SELECT applicable, na_reason FROM valuation_ranges WHERE ticker = 'AAA' "
                        "AND metric = 'pffo'").fetchone() == (0, "REITs only")


def test_an_old_database_gains_the_tables(tmp_path):
    db = tmp_path / "old.db"
    store = SQLiteStore(db_path=db)
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("DROP TABLE valuation_history")
    conn.execute("DROP TABLE valuation_ranges")
    conn.commit()
    conn.close()
    store.export_valuation_history("AAA", _result("AAA", 2), "2026-10-21T00:00:00+00:00")
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM valuation_history").fetchone()[0] == 2


def _history_db(tmp_path, **kw):
    _db(tmp_path, **kw).close()  # tmp_path / "hist.db": quarters, trailing years, bars, a 2:1 split
    return tmp_path / "hist.db"


def test_compute_and_store_writes_the_history(tmp_path):
    db = _history_db(tmp_path)
    assert compute_and_store(db) == 1
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT date, market_cap, pe FROM valuation_history WHERE ticker = 'AAA' "
                        "ORDER BY date").fetchall()
    assert [r[0] for r in rows] == ["2025-01-31", "2025-02-28", "2025-12-31", "2026-02-24"]
    assert rows[0][1] is None  # no trailing year known yet
    assert rows[1][1] == pytest.approx(12.5 * 1.1 * 200e6)  # before the 1.1 spin-off factor
    assert rows[-1][1] == pytest.approx(21.0 * 200e6)
    applicable, reason, current = conn.execute(
        "SELECT applicable, na_reason, current_value FROM valuation_ranges WHERE ticker = 'AAA' AND metric = 'pe'"
    ).fetchone()
    assert applicable == 0 and "3 months" in reason
    assert current == pytest.approx(21.0 * 200e6 / 4.4e8)
    assert conn.execute("SELECT COUNT(*) FROM valuations WHERE ticker = 'AAA'").fetchone()[0] == 6


def test_a_ticker_without_a_history_gets_its_reasons(tmp_path):
    db = _history_db(tmp_path, ttm=False)
    compute_and_store(db)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM valuation_history").fetchone()[0] == 0
    reasons = {r[0] for r in conn.execute("SELECT na_reason FROM valuation_ranges WHERE ticker = 'AAA'")}
    assert reasons == {"no trailing-twelve-month financials"}


def test_a_history_failure_never_blocks_the_valuations(tmp_path, monkeypatch, caplog):
    db = _history_db(tmp_path)

    def broken(_inputs):
        raise RuntimeError("history broke")

    monkeypatch.setattr(engine, "compute_history", broken)
    assert compute_and_store(db) == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM valuations").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM valuation_history").fetchone()[0] == 0
    assert "history broke" in caplog.text


def test_the_models_and_the_median_are_unchanged(tmp_path):
    assert MEDIAN_MODELS == ("dcf", "ddm", "graham", "lynch", "multiples")
    db = _history_db(tmp_path)
    compute_and_store(db)
    models = sorted(r[0] for r in sqlite3.connect(db).execute("SELECT model FROM valuations"))
    assert models == ["dcf", "ddm", "graham", "lynch", "multiples", "owner_earnings"]


def test_the_backfill_writes_the_history(tmp_path):
    db = _history_db(tmp_path)
    assert backfill_main(["--db", str(db)]) == 0
    assert sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM valuation_ranges WHERE ticker = 'AAA'").fetchone()[0] == 8  # 7 metrics + the value

"""Tests for the Task-1 store migration: widened snapshots + 5 new tables.

Covers:
- lossless upgrade of a pre-existing (old 13-column) market_snapshots DB
- export writes for analyst_snapshots/dividend_events/holders/insider_transactions/officers
- idempotency of the ``_ensure_columns`` migration helper
"""

import sqlite3
from datetime import datetime

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData

# The ORIGINAL 13-column _SNAPSHOT_COLUMNS list, hardcoded here so this test still
# proves the upgrade path even after the source list grows.
_OLD_SNAPSHOT_COLUMNS = [
    "current_price", "market_cap", "beta", "pe_trailing", "pe_forward",
    "eps_trailing", "price_to_book", "dividend_yield",
    "enterprise_value", "ev_to_ebitda", "ev_to_fcf", "fcf_yield",
    "risk_free_rate",
]


def _stub_stock(ticker="ZZZ"):
    return StockData(ticker=ticker, cik="000", company_name=f"{ticker} Inc.")


def _full_stock(ticker="AAA"):
    s = StockData(ticker=ticker, cik="000", company_name=f"{ticker} Inc.")
    s.company_info = {
        "sector": "Technology",
        "description": "A company that makes things.",
        "address": "1 Infinite Loop",
        "city": "Cupertino",
        "state": "CA",
        "officers": [
            {"name": "Jane Doe", "title": "CEO", "age": 50, "total_pay": 1000000},
            {"name": None, "title": "Ghost", "age": None, "total_pay": None},  # skip: no name
        ],
    }
    s.market_data = {"current_price": 100.0, "previous_close": 99.0}
    s.valuation = {"pe_trailing": 20.0, "peg_ratio": 1.5}
    s.shareholders = {
        "shares_outstanding": 1e9,
        "institutional_holders": [
            {"Holder": "Vanguard", "Shares": 1000.0, "Date Reported": "2024-01-01",
             "pctHeld": 0.05, "Value": 50000.0},
            {"Holder": None, "Shares": 10.0},  # skip: no holder name
        ],
        "mutualfund_holders": [
            {"Holder": "Fidelity Fund", "Shares": 500.0, "Date Reported": "2024-01-01",
             "pctHeld": 0.02, "Value": 20000.0},
        ],
        "insider_transactions": [
            {"Insider": "John Smith", "Position": "CFO", "Start Date": "2024-02-01",
             "Shares": 100.0, "Value": 5000.0, "Text": "Sale", "Ownership": "D"},
            {"Insider": None, "Start Date": "2024-02-01", "Text": "Sale"},  # skip: no insider
        ],
    }
    s.analyst_estimates = {
        "target_price_low": 90.0, "target_price_mean": 120.0, "target_price_median": 118.0,
        "target_price_high": 150.0, "recommendation": "buy", "recommendation_mean": 2.0,
        "number_of_analysts": 10, "earnings_date": "2024-05-01", "forward_eps": 5.0,
        "forward_pe": 18.0, "earnings_growth": 0.1, "revenue_growth": 0.08,
        "upside_potential": 0.2,
    }
    s.dividend_history = {
        "dividend_payments": [
            {"date": "2024-01-15", "amount": 0.5},
            {"date": "2024-04-15", "amount": 0.55},
        ],
    }
    s.add_source("yahoo_finance")
    return s


def test_old_schema_upgrades_losslessly(tmp_path):
    """A DB created under the OLD 13-column schema must upgrade in place, keeping data."""
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    cols_ddl = ", ".join(f'"{c}" REAL' for c in _OLD_SNAPSHOT_COLUMNS)
    conn.execute(
        f"""
        CREATE TABLE market_snapshots (
            ticker TEXT NOT NULL, collected_at TEXT NOT NULL,
            {cols_ddl},
            PRIMARY KEY (ticker, collected_at)
        )
        """
    )
    conn.execute(
        "INSERT INTO market_snapshots (ticker, collected_at, current_price, market_cap) "
        "VALUES ('OLD', '2020-01-01T00:00:00', 42.0, 1000.0)"
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    store.export([_stub_stock("ZZZ")])

    conn = sqlite3.connect(db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(market_snapshots)")}
        for c in ("previous_close", "shares_outstanding", "peg_ratio", "ma_50"):
            assert c in cols, f"missing widened column {c}"
        assert "ex_dividend_date" in cols  # the TEXT column

        row = conn.execute(
            "SELECT current_price, market_cap FROM market_snapshots WHERE ticker='OLD'"
        ).fetchone()
        assert row == (42.0, 1000.0)  # old row untouched

        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("analyst_snapshots", "dividend_events", "holders",
                  "insider_transactions", "officers"):
            assert t in tables

        company_cols = {row[1] for row in conn.execute("PRAGMA table_info(companies)")}
        for c in ("description", "address", "hq_city", "hq_state"):
            assert c in company_cols
    finally:
        conn.close()


def test_export_writes_new_tables(tmp_path):
    db = tmp_path / "stock.db"
    SQLiteStore(db).export([_full_stock("AAA")])

    conn = sqlite3.connect(db)
    try:
        # companies gains description/address/hq_city/hq_state
        row = conn.execute(
            "SELECT description, address, hq_city, hq_state FROM companies WHERE ticker='AAA'"
        ).fetchone()
        assert row == ("A company that makes things.", "1 Infinite Loop", "Cupertino", "CA")

        # holders: institutional + mutualfund, malformed record without a holder skipped
        holders = conn.execute(
            "SELECT holder_type, holder, shares, date_reported, pct_held, value "
            "FROM holders WHERE ticker='AAA' ORDER BY holder_type, holder"
        ).fetchall()
        assert holders == [
            ("institutional", "Vanguard", 1000.0, "2024-01-01", 0.05, 50000.0),
            ("mutualfund", "Fidelity Fund", 500.0, "2024-01-01", 0.02, 20000.0),
        ]

        # insider_transactions: malformed record without an insider name skipped
        insiders = conn.execute(
            "SELECT insider, start_date, text, position, shares, value, ownership "
            "FROM insider_transactions WHERE ticker='AAA'"
        ).fetchall()
        assert insiders == [
            ("John Smith", "2024-02-01", "Sale", "CFO", 100.0, 5000.0, "D"),
        ]

        # officers: replace-per-run, malformed record without a name skipped
        officers = conn.execute(
            "SELECT name, title, age, total_pay FROM officers WHERE ticker='AAA'"
        ).fetchall()
        assert officers == [("Jane Doe", "CEO", 50, 1000000)]

        # analyst_snapshots: one row per collection
        analyst = conn.execute(
            "SELECT target_price_mean, recommendation, number_of_analysts, upside_potential "
            "FROM analyst_snapshots WHERE ticker='AAA'"
        ).fetchone()
        assert analyst == (120.0, "buy", 10, 0.2)

        # dividend_events: all payments upserted
        divs = conn.execute(
            "SELECT date, amount FROM dividend_events WHERE ticker='AAA' ORDER BY date"
        ).fetchall()
        assert divs == [("2024-01-15", 0.5), ("2024-04-15", 0.55)]

        # widened market_snapshots columns populated from market_data/valuation/shareholders
        snap = conn.execute(
            "SELECT previous_close, peg_ratio, shares_outstanding "
            "FROM market_snapshots WHERE ticker='AAA'"
        ).fetchone()
        assert snap == (99.0, 1.5, 1e9)
    finally:
        conn.close()


def test_officers_replace_per_run(tmp_path):
    """Officers table is replace-per-run: a second export with a different roster overwrites."""
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    store.export([_full_stock("AAA")])

    s2 = _full_stock("AAA")
    s2.company_info["officers"] = [
        {"name": "New CEO", "title": "CEO", "age": 45, "total_pay": 2000000},
    ]
    store.export([s2])

    conn = sqlite3.connect(db)
    try:
        officers = conn.execute(
            "SELECT name FROM officers WHERE ticker='AAA'"
        ).fetchall()
        assert officers == [("New CEO",)]
    finally:
        conn.close()


def test_holders_and_officers_wipe_guarded_on_empty_fetch(tmp_path):
    """A transient yfinance sub-fetch failure yielding empty holders/officers
    lists must NOT wipe previously-good rows; only a fresh non-empty list
    replaces them."""
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    store.export([_full_stock("AAA")])

    conn = sqlite3.connect(db)
    try:
        holders_before = conn.execute(
            "SELECT holder_type, holder FROM holders WHERE ticker='AAA' ORDER BY holder"
        ).fetchall()
        officers_before = conn.execute(
            "SELECT name FROM officers WHERE ticker='AAA'"
        ).fetchall()
    finally:
        conn.close()
    assert holders_before  # sanity: rows exist from the first export
    assert officers_before

    # Second export: holders/officers sections come back EMPTY (simulating a
    # transient sub-fetch failure) -> previous rows must survive untouched.
    s2 = _full_stock("AAA")
    s2.shareholders["institutional_holders"] = []
    s2.shareholders["mutualfund_holders"] = []
    s2.company_info["officers"] = []
    store.export([s2])

    conn = sqlite3.connect(db)
    try:
        holders_after_empty = conn.execute(
            "SELECT holder_type, holder FROM holders WHERE ticker='AAA' ORDER BY holder"
        ).fetchall()
        officers_after_empty = conn.execute(
            "SELECT name FROM officers WHERE ticker='AAA'"
        ).fetchall()
    finally:
        conn.close()
    assert holders_after_empty == holders_before
    assert officers_after_empty == officers_before

    # Third export: a NEW non-empty list -> old rows ARE replaced by the new list.
    s3 = _full_stock("AAA")
    s3.shareholders["institutional_holders"] = [
        {"Holder": "NewCo Capital", "Shares": 42.0, "Date Reported": "2024-06-01",
         "pctHeld": 0.01, "Value": 1000.0},
    ]
    s3.shareholders["mutualfund_holders"] = []
    s3.company_info["officers"] = [
        {"name": "Fresh Officer", "title": "COO", "age": 40, "total_pay": 500000},
    ]
    store.export([s3])

    conn = sqlite3.connect(db)
    try:
        holders_final = conn.execute(
            "SELECT holder_type, holder FROM holders WHERE ticker='AAA' ORDER BY holder"
        ).fetchall()
        officers_final = conn.execute(
            "SELECT name FROM officers WHERE ticker='AAA'"
        ).fetchall()
    finally:
        conn.close()
    assert holders_final == [("institutional", "NewCo Capital")]
    assert officers_final == [("Fresh Officer",)]


def test_insider_transactions_upsert_idempotent(tmp_path):
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    store.export([_full_stock("AAA")])
    store.export([_full_stock("AAA")])  # re-export must not duplicate

    conn = sqlite3.connect(db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM insider_transactions WHERE ticker='AAA'"
        ).fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_export_writes_price_bars_earnings_history_and_splits(tmp_path):
    """Task 2: 30 synthetic bars + 4 earnings rows + 1 split land in their own tables."""
    db = tmp_path / "stock.db"
    s = _full_stock("BBB")
    s.price_bars = [
        {"date": f"2024-01-{d:02d}", "open": float(d), "high": float(d) + 1,
         "low": float(d) - 1, "close": float(d) + 0.5, "volume": float(1000 + d)}
        for d in range(1, 31)
    ]
    s.earnings_history = [
        {"quarter": "2023-03-31", "eps_estimate": 1.0, "eps_actual": 1.05, "surprise_pct": 5.0},
        {"quarter": "2023-06-30", "eps_estimate": 1.1, "eps_actual": 1.08, "surprise_pct": -1.8},
        {"quarter": "2023-09-30", "eps_estimate": 1.2, "eps_actual": 1.25, "surprise_pct": 4.2},
        {"quarter": "2023-12-31", "eps_estimate": 1.3, "eps_actual": 1.28, "surprise_pct": -1.5},
    ]
    s.splits = [{"date": "2024-06-10", "ratio": 2.0}]

    SQLiteStore(db).export([s])

    conn = sqlite3.connect(db)
    try:
        bars = conn.execute(
            "SELECT date, open, high, low, close, volume FROM price_bars "
            "WHERE ticker='BBB' ORDER BY date"
        ).fetchall()
        assert len(bars) == 30
        assert bars[0] == ("2024-01-01", 1.0, 2.0, 0.0, 1.5, 1001.0)
        assert bars[-1] == ("2024-01-30", 30.0, 31.0, 29.0, 30.5, 1030.0)

        earnings = conn.execute(
            "SELECT quarter, eps_estimate, eps_actual, surprise_pct FROM earnings_history "
            "WHERE ticker='BBB' ORDER BY quarter"
        ).fetchall()
        assert earnings == [
            ("2023-03-31", 1.0, 1.05, 5.0),
            ("2023-06-30", 1.1, 1.08, -1.8),
            ("2023-09-30", 1.2, 1.25, 4.2),
            ("2023-12-31", 1.3, 1.28, -1.5),
        ]

        splits = conn.execute(
            "SELECT date, ratio FROM split_events WHERE ticker='BBB'"
        ).fetchall()
        assert splits == [("2024-06-10", 2.0)]
    finally:
        conn.close()


def test_export_price_bars_upsert_idempotent(tmp_path):
    db = tmp_path / "stock.db"
    s = _full_stock("BBB")
    s.price_bars = [{"date": "2024-01-01", "open": 1.0, "high": 2.0, "low": 0.5,
                      "close": 1.5, "volume": 100.0}]
    s.earnings_history = [
        {"quarter": "2023-12-31", "eps_estimate": 1.0, "eps_actual": 1.1, "surprise_pct": 10.0},
    ]
    s.splits = [{"date": "2024-06-10", "ratio": 2.0}]
    store = SQLiteStore(db)
    store.export([s])
    store.export([s])  # re-export must not duplicate

    conn = sqlite3.connect(db)
    try:
        for table in ("price_bars", "earnings_history", "split_events"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ticker='BBB'").fetchone()[0]
            assert n == 1, f"{table} duplicated rows on re-export"
    finally:
        conn.close()


def test_export_benchmark_bars_writes_bars_without_companies_row(tmp_path):
    """export_benchmark_bars writes bars only — never creates a companies row."""
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    bars = [
        {"date": "2024-01-02", "open": 4700.0, "high": 4720.0, "low": 4690.0,
         "close": 4710.0, "volume": 3_000_000.0},
        {"date": "2024-01-03", "open": 4710.0, "high": 4730.0, "low": 4700.0,
         "close": 4715.0, "volume": 2_800_000.0},
    ]
    store.export_benchmark_bars("^GSPC", bars)

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT date, close FROM price_bars WHERE ticker='^GSPC' ORDER BY date"
        ).fetchall()
        assert rows == [("2024-01-02", 4710.0), ("2024-01-03", 4715.0)]

        companies = conn.execute(
            "SELECT COUNT(*) FROM companies WHERE ticker='^GSPC'"
        ).fetchone()[0]
        assert companies == 0
    finally:
        conn.close()


def test_export_benchmark_bars_empty_is_noop(tmp_path):
    """An empty bars list must not raise and must not touch the DB file at all."""
    db = tmp_path / "stock.db"
    store = SQLiteStore(db)
    store.export_benchmark_bars("^GSPC", [])
    assert not db.exists()


def test_ensure_columns_is_idempotent(tmp_path):
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.commit()

    SQLiteStore._ensure_columns(conn, "t", {"a": "TEXT", "b": "REAL"})
    SQLiteStore._ensure_columns(conn, "t", {"a": "TEXT", "b": "REAL"})  # no error, no dup

    cols = [row[1] for row in conn.execute("PRAGMA table_info(t)")]
    assert cols.count("b") == 1
    assert cols.count("a") == 1
    conn.close()


def _pre_flag_db(tmp_path):
    """A database from before the dividend-adjusted flag: KO's and PEP's bars, Yahoo's adjusted prices."""
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE price_bars (ticker TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL, "
                 "close REAL, volume REAL, PRIMARY KEY (ticker, date))")
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES (?, ?, ?)",
                     [("KO", "2021-12-31", 51.546), ("PEP", "2021-12-31", 154.0)])
    conn.commit()
    conn.close()
    return db


def test_bars_stored_before_market_closes_are_flagged_dividend_adjusted(tmp_path):
    # BUG-075: every bar stored before the fetch asked for market prices holds Yahoo's dividend-adjusted one. The
    # migration flags them once; a re-collected ticker's bars are market prices again.
    db = _pre_flag_db(tmp_path)
    ko = _full_stock("KO")
    ko.price_bars = [{"date": "2021-12-31", "open": 59.0, "high": 59.3, "low": 58.9, "close": 59.21, "volume": 1.0e7}]
    SQLiteStore(db).export([ko])
    SQLiteStore(db).export([_stub_stock("ZZZ")])  # a later run's migration doesn't flag KO's again

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT ticker, close, dividend_adjusted FROM price_bars ORDER BY ticker").fetchall()
    finally:
        conn.close()
    assert rows == [("KO", 59.21, None), ("PEP", 154.0, 1)]


@pytest.mark.parametrize("failure", [sqlite3.OperationalError("database is locked"), KeyboardInterrupt()])
def test_a_first_export_that_fails_keeps_the_flags(tmp_path, monkeypatch, failure):
    # BUG-083 (code review 8): the migration's ALTER TABLE committed at once while its UPDATE waited on the run's
    # writes, so a first run that failed while writing (logged and swallowed, or Ctrl+C) kept the column and lost every
    # flag: the bars read as market closes for good, and BUG-075's prices came back.
    db = _pre_flag_db(tmp_path)

    def failing_write(self, conn, stock):
        raise failure

    monkeypatch.setattr(SQLiteStore, "_write_stock", failing_write)
    try:
        SQLiteStore(db).export([_full_stock("KO")])
    except KeyboardInterrupt:
        pass
    monkeypatch.undo()
    SQLiteStore(db).export([_stub_stock("ZZZ")])

    conn = sqlite3.connect(db)
    try:
        flags = conn.execute("SELECT ticker, dividend_adjusted FROM price_bars ORDER BY ticker").fetchall()
    finally:
        conn.close()
    assert flags == [("KO", 1), ("PEP", 1)]


def test_a_migration_interrupted_while_flagging_is_undone_whole(tmp_path, monkeypatch):
    # BUG-083: the column and the flags arrive together or not at all. Interrupted as the flags are written (Ctrl+C,
    # a killed process), the copy keeps no column, and the next run migrates again.
    db = _pre_flag_db(tmp_path)
    real_connect = SQLiteStore._connect

    class InterruptedWhileFlagging:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.startswith("UPDATE price_bars SET dividend_adjusted"):
                raise KeyboardInterrupt
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(SQLiteStore, "_connect", lambda self: InterruptedWhileFlagging(real_connect(self)))
    with pytest.raises(KeyboardInterrupt):
        SQLiteStore(db).export([_stub_stock("ZZZ")])
    monkeypatch.undo()
    conn = sqlite3.connect(db)
    try:
        assert "dividend_adjusted" not in {row[1] for row in conn.execute("PRAGMA table_info(price_bars)")}
    finally:
        conn.close()

    SQLiteStore(db).export([_stub_stock("ZZZ")])
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT ticker, dividend_adjusted FROM price_bars ORDER BY ticker").fetchall() == [
            ("KO", 1), ("PEP", 1)]
    finally:
        conn.close()


def _served_days(db, ticker):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT date, served_on FROM dividend_events WHERE ticker = ? ORDER BY date",
                            (ticker,)).fetchall()
    finally:
        conn.close()


def test_dividends_keep_the_day_yahoo_served_them(tmp_path):
    # BUG-103: a payment's amount is Yahoo's, split-adjusted as of the run that fetched it, so that run's day on Yahoo's
    # calendar, its last bar (BUG-117), goes with it; a later run whose dividend request failed leaves the stored
    # payments on their day.
    db = tmp_path / "stock.db"
    failed = {"error": "chart request failed"}
    for day, last_bar, history in ((datetime(2026, 6, 1, 9), "2026-05-29", None),
                                   (datetime(2026, 10, 1, 9), "2026-09-30", failed)):
        stock = _full_stock("AAA")
        stock.collected_at, stock.price_bars = day, [{"date": last_bar, "close": 100.0}]
        if history is not None:
            stock.dividend_history = history
        SQLiteStore(db).export([stock])
    assert _served_days(db, "AAA") == [("2024-01-15", "2026-05-29"), ("2024-04-15", "2026-05-29")]
    stock = _full_stock("AAA")
    stock.collected_at, stock.price_bars = datetime(2026, 10, 8, 9), [{"date": "2026-10-07", "close": 100.0}]
    SQLiteStore(db).export([stock])
    assert _served_days(db, "AAA") == [("2024-01-15", "2026-10-07"), ("2024-04-15", "2026-10-07")]


def _served_after_one_run(tmp_path, collected_at, bars=(), splits=()):
    db = tmp_path / "stock.db"
    stock = _full_stock("AAA")
    stock.collected_at = collected_at
    stock.price_bars = [{"date": day, "close": 100.0} for day in bars]
    stock.splits = [{"date": day, "ratio": 10.0} for day in splits]
    SQLiteStore(db).export([stock])
    return {served for _, served in _served_days(db, "AAA")}


def test_a_run_before_new_yorks_open_dates_its_payments_by_its_last_bar(tmp_path):
    # BUG-117 (code review 11): the collector's date runs ahead of New York's east of it. A run at 08:00 local (+0700)
    # on a split's day saw New York's evening before, no split yet, but dated its pre-split payments on the split's
    # day, so they read as split; after a later run whose dividend request failed, DDM read ten times its value.
    served = _served_after_one_run(tmp_path, datetime(2026, 6, 12, 8), bars=("2026-06-10", "2026-06-11"))
    assert served == {"2026-06-11"}


def test_a_split_yahoo_lists_dates_the_payments_it_adjusted(tmp_path):
    # A split Yahoo lists is in the payments it serves, though the split's first bar is still to come.
    served = _served_after_one_run(tmp_path, datetime(2026, 6, 12, 8), bars=("2026-06-11",), splits=("2026-06-12",))
    assert served == {"2026-06-12"}


def test_a_run_without_bars_or_splits_dates_its_payments_the_day_before_its_own(tmp_path):
    # Yahoo then lists no split, and any it hasn't applied is dated on New York's day or later: never before the
    # collector's day less one.
    assert _served_after_one_run(tmp_path, datetime(2026, 6, 12, 8)) == {"2026-06-11"}


def _pre_served_day_db(tmp_path):
    """A database from before `dividend_events.served_on`: KO's payments beside bars to 2026-08-03; NVDA's beside bars
    to 2024-06-07 and a split of 2024-06-10 a run stored while its bar request failed; XYZ's with neither."""
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dividend_events (ticker TEXT NOT NULL, date TEXT NOT NULL, amount REAL, "
                 "PRIMARY KEY (ticker, date))")
    conn.execute("CREATE TABLE price_bars (ticker TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL, "
                 "close REAL, volume REAL, dividend_adjusted INTEGER, PRIMARY KEY (ticker, date))")
    conn.execute("CREATE TABLE split_events (ticker TEXT NOT NULL, date TEXT NOT NULL, ratio REAL, "
                 "PRIMARY KEY (ticker, date))")
    conn.executemany("INSERT INTO dividend_events (ticker, date, amount) VALUES (?, ?, ?)",
                     [("KO", "2026-06-13", 0.51), ("NVDA", "2024-06-05", 0.01), ("XYZ", "2025-03-01", 1.0)])
    conn.executemany("INSERT INTO price_bars (ticker, date, close) VALUES (?, ?, ?)",
                     [("KO", "2026-07-31", 70.0), ("KO", "2026-08-03", 70.5), ("NVDA", "2024-06-07", 1208.88)])
    conn.execute("INSERT INTO split_events (ticker, date, ratio) VALUES ('NVDA', '2024-06-10', 10.0)")
    conn.commit()
    conn.close()
    return db


def test_dividends_stored_before_the_served_day_take_their_series_latest_basis(tmp_path):
    # Until BUG-103's fix, a run stored no split without the dividends from the same chart request, so a payment stored
    # before the column is on the basis of everything its ticker's series show: the migration dates it by its last bar
    # or its latest split, whichever is later, the rule it was read by until now. A ticker with neither keeps no day.
    db = _pre_served_day_db(tmp_path)
    SQLiteStore(db).export([_stub_stock("ZZZ")])
    assert [_served_days(db, t) for t in ("KO", "NVDA", "XYZ")] == [
        [("2026-06-13", "2026-08-03")], [("2024-06-05", "2024-06-10")], [("2025-03-01", None)]]


def test_a_migration_interrupted_while_dating_dividends_is_undone_whole(tmp_path, monkeypatch):
    # As BUG-083's flags: the column and the days arrive together or not at all, committed before the run writes.
    db = _pre_served_day_db(tmp_path)
    real_connect = SQLiteStore._connect

    class InterruptedWhileDating:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.startswith("UPDATE dividend_events SET served_on"):
                raise KeyboardInterrupt
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(SQLiteStore, "_connect", lambda self: InterruptedWhileDating(real_connect(self)))
    with pytest.raises(KeyboardInterrupt):
        SQLiteStore(db).export([_stub_stock("ZZZ")])
    monkeypatch.undo()
    conn = sqlite3.connect(db)
    try:
        assert "served_on" not in {row[1] for row in conn.execute("PRAGMA table_info(dividend_events)")}
    finally:
        conn.close()
    SQLiteStore(db).export([_stub_stock("ZZZ")])
    assert _served_days(db, "KO") == [("2026-06-13", "2026-08-03")]

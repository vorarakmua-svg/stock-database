"""Tests for the Task-1 store migration: widened snapshots + 5 new tables.

Covers:
- lossless upgrade of a pre-existing (old 13-column) market_snapshots DB
- export writes for analyst_snapshots/dividend_events/holders/insider_transactions/officers
- idempotency of the ``_ensure_columns`` migration helper
"""

import sqlite3

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

"""Tests for the SQLite store: schema, upserts, and cross-company screening."""

import sqlite3

from src.exporters.sqlite_store import SQLiteStore
from src.models.stock_data import StockData


def _company(ticker, revenue, roic):
    s = StockData(ticker=ticker, cik="000", company_name=f"{ticker} Inc.")
    s.company_info = {"sector": "Technology"}
    s.financials_annual = {"2024": {"fiscal_year": 2024, "revenue": revenue,
                                    "net_income": revenue * 0.1, "total_assets": revenue * 2,
                                    "total_liabilities": revenue, "total_equity": revenue,
                                    "operating_cash_flow": revenue * 0.2}}
    s.calculated_metrics = {"historical": {"2024": {"roic": roic, "net_margin": 0.1}}}
    s.add_source("sec_edgar")
    return s


def test_export_creates_schema_and_rows(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    store.export([_company("AAA", 1000.0, 0.20)])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1
        row = conn.execute(
            "SELECT revenue FROM financials_annual WHERE ticker='AAA' AND fiscal_year=2024"
        ).fetchone()
        assert row[0] == 1000.0
        roic = conn.execute(
            "SELECT roic FROM metrics_annual WHERE ticker='AAA' AND fiscal_year=2024"
        ).fetchone()[0]
        assert roic == 0.20
    finally:
        conn.close()


def test_upsert_is_idempotent(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    store.export([_company("AAA", 1000.0, 0.20)])
    # Re-export with an updated value; row count stays 1, value updates.
    store.export([_company("AAA", 1111.0, 0.25)])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM financials_annual WHERE ticker='AAA'"
        ).fetchone()[0] == 1
        rev = conn.execute(
            "SELECT revenue FROM financials_annual WHERE ticker='AAA'"
        ).fetchone()[0]
        assert rev == 1111.0
    finally:
        conn.close()


def test_cross_company_screen(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    store.export([
        _company("AAA", 1000.0, 0.20),
        _company("BBB", 2000.0, 0.05),
        _company("CCC", 3000.0, 0.18),
    ])

    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        # Screen: companies with ROIC > 15% — comparable only because data is canonical.
        rows = conn.execute(
            "SELECT ticker FROM metrics_annual WHERE roic > 0.15 ORDER BY ticker"
        ).fetchall()
        assert [r[0] for r in rows] == ["AAA", "CCC"]
    finally:
        conn.close()


def test_empty_export_returns_none(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    assert store.export([]) is None


def _company_with_calendar(ticker, fiscal_year, calendar_year):
    s = StockData(ticker=ticker, cik="000", company_name=f"{ticker} Inc.")
    s.financials_annual = {str(fiscal_year): {
        "fiscal_year": fiscal_year, "calendar_year": calendar_year,
        "period_end": f"{fiscal_year}-01-31", "revenue": 100.0, "net_income": 10.0,
        "total_assets": 200.0, "total_equity": 100.0,
    }}
    s.add_source("sec_edgar")
    return s


def test_calendar_year_screen_aligns_fiscal_calendars(tmp_path):
    store = SQLiteStore(tmp_path / "stock.db")
    # A January filer (fiscal 2026) and a December filer (fiscal 2025) both belong to
    # macro calendar year 2025 and must be returned together.
    store.export([
        _company_with_calendar("JANCO", 2026, 2025),
        _company_with_calendar("DECCO", 2025, 2025),
        _company_with_calendar("OLDCO", 2024, 2024),
    ])
    conn = sqlite3.connect(tmp_path / "stock.db")
    try:
        rows = conn.execute(
            "SELECT ticker FROM financials_annual WHERE calendar_year=2025 ORDER BY ticker"
        ).fetchall()
        assert [r[0] for r in rows] == ["DECCO", "JANCO"]
    finally:
        conn.close()


def test_migrate_adds_missing_columns(tmp_path):
    db = tmp_path / "stock.db"
    # Simulate an old DB: a companies table without the newer sector_class column.
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE companies (ticker TEXT PRIMARY KEY, cik TEXT)")
    conn.commit()
    conn.close()

    SQLiteStore(db).export([_company("AAA", 1000.0, 0.20)])

    conn = sqlite3.connect(db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(companies)")}
        assert "sector_class" in cols  # migration added it
        # And a new canonical column exists on the financials table.
        fcols = {row[1] for row in conn.execute("PRAGMA table_info(financials_annual)")}
        assert "net_interest_income" in fcols
        assert "calendar_year" in fcols  # calendar alignment column migrated in
    finally:
        conn.close()

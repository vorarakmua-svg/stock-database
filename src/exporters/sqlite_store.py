"""SQLite store for standardized stock data.

Because financials are standardized to canonical fields before they reach here,
every company maps onto the same columns — so the DB supports real cross-company
screening (e.g. ``WHERE roic > 0.15 AND ev_to_ebitda < 12``).

Tables:
- ``companies``            one row per ticker (latest profile)
- ``financials_annual``    canonical annual line items, one row per (ticker, fy)
- ``financials_quarterly`` canonical quarterly line items, one row per (ticker, period_end)
- ``metrics_annual``       calculated ratios per (ticker, fy)
- ``market_snapshots``     point-in-time market/valuation data per (ticker, collected_at)
- ``collection_runs``      provenance/quality of each collection

All writes are idempotent upserts on the natural keys.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..mappings.canonical import CANONICAL_FIELDS
from ..models.stock_data import StockData

# Canonical line-item columns shared by the annual/quarterly financial tables.
_CANONICAL_COLUMNS = [f.key for f in CANONICAL_FIELDS]

# Calculated ratios stored per fiscal year (from CalculatedMetrics historical).
_METRIC_COLUMNS = [
    "ebitda", "ebit", "nopat", "free_cash_flow", "fcf_margin", "levered_fcf",
    "net_debt", "total_debt", "working_capital", "invested_capital",
    "roic", "roa", "roe", "interest_coverage", "debt_to_ebitda",
    "asset_turnover", "inventory_turnover", "receivables_turnover",
    "gross_margin", "operating_margin", "net_margin", "ebitda_margin",
]

# Point-in-time market/valuation columns for the snapshot table.
_SNAPSHOT_COLUMNS = [
    "current_price", "market_cap", "beta", "pe_trailing", "pe_forward",
    "eps_trailing", "price_to_book", "dividend_yield",
    "enterprise_value", "ev_to_ebitda", "ev_to_fcf", "fcf_yield",
    "risk_free_rate",
]


def _cols_ddl(columns: Sequence[str], col_type: str = "REAL") -> str:
    return ", ".join(f'"{c}" {col_type}' for c in columns)


class SQLiteStore:
    """Persist StockData into a queryable SQLite database."""

    def __init__(self, db_path: Path, logger: Optional[logging.Logger] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)

    # ---- schema -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS companies (
                ticker TEXT PRIMARY KEY,
                cik TEXT,
                company_name TEXT,
                sector_class TEXT,
                sector TEXT,
                industry TEXT,
                country TEXT,
                employees INTEGER,
                website TEXT,
                fiscal_year_end TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS financials_annual (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                calendar_year INTEGER,
                period_end TEXT,
                filed_date TEXT,
                form TEXT,
                quality_score INTEGER,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, fiscal_year)
            );

            CREATE TABLE IF NOT EXISTS financials_quarterly (
                ticker TEXT NOT NULL,
                period_end TEXT NOT NULL,
                fiscal_year INTEGER,
                fiscal_period TEXT,
                fiscal_quarter INTEGER,
                calendar_year INTEGER,
                calendar_quarter INTEGER,
                filed_date TEXT,
                form TEXT,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, period_end)
            );

            CREATE TABLE IF NOT EXISTS financials_ttm (
                ticker TEXT NOT NULL,
                period_end TEXT NOT NULL,
                fiscal_year INTEGER,
                calendar_year INTEGER,
                calendar_quarter INTEGER,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, period_end)
            );

            CREATE INDEX IF NOT EXISTS idx_fa_calendar_year
                ON financials_annual (calendar_year);
            CREATE INDEX IF NOT EXISTS idx_fq_calendar
                ON financials_quarterly (calendar_year, calendar_quarter);
            CREATE INDEX IF NOT EXISTS idx_ttm_calendar
                ON financials_ttm (ticker, calendar_year, calendar_quarter);

            CREATE TABLE IF NOT EXISTS metrics_annual (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                {_cols_ddl(_METRIC_COLUMNS)},
                PRIMARY KEY (ticker, fiscal_year)
            );

            CREATE TABLE IF NOT EXISTS market_snapshots (
                ticker TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                {_cols_ddl(_SNAPSHOT_COLUMNS)},
                PRIMARY KEY (ticker, collected_at)
            );

            CREATE TABLE IF NOT EXISTS collection_runs (
                ticker TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                data_sources TEXT,
                warning_count INTEGER,
                error_count INTEGER,
                quality_score INTEGER,
                PRIMARY KEY (ticker, collected_at)
            );

            -- Material us-gaap facts under tags not yet in the canonical registry.
            -- The tag is a row VALUE (not a column), so new taxonomy tags never
            -- require a schema change and material data is never silently lost.
            CREATE TABLE IF NOT EXISTS unmapped_facts (
                ticker TEXT NOT NULL,
                tag TEXT NOT NULL,
                label TEXT,
                unit TEXT,
                period_end TEXT,
                value REAL,
                form TEXT,
                collected_at TEXT,
                PRIMARY KEY (ticker, tag)
            );

            CREATE INDEX IF NOT EXISTS idx_unmapped_tag ON unmapped_facts (tag);
            """
        )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add any columns missing from an existing DB (registry grows over time).

        ``CREATE TABLE IF NOT EXISTS`` won't add new columns to a pre-existing table,
        so we reconcile each table against its current expected column set.
        """
        expected = {
            "companies": [("sector_class", "TEXT"), ("fiscal_year_end", "TEXT")],
            "financials_annual": [("calendar_year", "INTEGER")]
            + [(c, "REAL") for c in _CANONICAL_COLUMNS],
            "financials_quarterly": [("calendar_year", "INTEGER"),
                                     ("calendar_quarter", "INTEGER"),
                                     ("fiscal_quarter", "INTEGER")]
            + [(c, "REAL") for c in _CANONICAL_COLUMNS],
            "financials_ttm": [("fiscal_year", "INTEGER"), ("calendar_year", "INTEGER"),
                               ("calendar_quarter", "INTEGER")]
            + [(c, "REAL") for c in _CANONICAL_COLUMNS],
            "metrics_annual": [(c, "REAL") for c in _METRIC_COLUMNS],
            "market_snapshots": [(c, "REAL") for c in _SNAPSHOT_COLUMNS],
        }
        for table, columns in expected.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, col_type in columns:
                if name not in existing:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {col_type}')

    # ---- upsert helper ----------------------------------------------------

    @staticmethod
    def _upsert(conn: sqlite3.Connection, table: str, keys: Sequence[str],
                row: Dict[str, Any]) -> None:
        """Insert or update ``row`` in ``table`` keyed on ``keys``."""
        columns = list(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        col_list = ", ".join(f'"{c}"' for c in columns)
        updates = ", ".join(f'"{c}"=excluded."{c}"' for c in columns if c not in keys)
        conflict = ", ".join(f'"{k}"' for k in keys)
        sql = (
            f'INSERT INTO {table} ({col_list}) VALUES ({placeholders}) '
            f'ON CONFLICT ({conflict}) DO UPDATE SET {updates}'
        )
        conn.execute(sql, [row[c] for c in columns])

    @staticmethod
    def _canonical_values(period: Dict[str, Any]) -> Dict[str, Any]:
        """Pull only canonical numeric columns from a period dict."""
        return {c: period.get(c) for c in _CANONICAL_COLUMNS if c in period}

    # ---- public API -------------------------------------------------------

    def export(self, data: List[StockData]) -> Optional[Path]:
        """Write a batch of StockData rows; returns the DB path."""
        if not data:
            return None
        try:
            conn = self._connect()
            try:
                self._create_schema(conn)
                self._migrate(conn)
                for stock in data:
                    self._write_stock(conn, stock)
                conn.commit()
            finally:
                conn.close()
            self.logger.info(f"Wrote {len(data)} tickers to {self.db_path}")
            return self.db_path
        except Exception as e:
            self.logger.error(f"Error writing SQLite store: {e}")
            return None

    def _write_stock(self, conn: sqlite3.Connection, stock: StockData) -> None:
        collected_at = (
            stock.collected_at.isoformat()
            if hasattr(stock.collected_at, "isoformat")
            else str(stock.collected_at)
        )
        quality_score = (stock.data_quality or {}).get("score")
        info = stock.company_info or {}

        # companies
        self._upsert(conn, "companies", ["ticker"], {
            "ticker": stock.ticker,
            "cik": stock.cik,
            "company_name": stock.company_name,
            "sector_class": stock.sector_class,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "employees": info.get("full_time_employees"),
            "website": info.get("website"),
            "fiscal_year_end": (stock.sec_submissions or {}).get("fiscal_year_end"),
            "updated_at": collected_at,
        })

        # financials_annual + metrics_annual
        historical = (stock.calculated_metrics or {}).get("historical", {})
        for year, period in (stock.financials_annual or {}).items():
            row = {
                "ticker": stock.ticker,
                "fiscal_year": int(year) if str(year).isdigit() else None,
                "calendar_year": period.get("calendar_year"),
                "period_end": period.get("period_end"),
                "filed_date": period.get("filed_date"),
                "form": period.get("form"),
                "quality_score": quality_score,
            }
            row.update(self._canonical_values(period))
            self._upsert(conn, "financials_annual", ["ticker", "fiscal_year"], row)

            year_metrics = historical.get(year) or {}
            if year_metrics and "error" not in year_metrics:
                mrow = {"ticker": stock.ticker,
                        "fiscal_year": row["fiscal_year"]}
                mrow.update({c: year_metrics.get(c) for c in _METRIC_COLUMNS})
                self._upsert(conn, "metrics_annual", ["ticker", "fiscal_year"], mrow)

        # financials_quarterly
        for period_end, period in (stock.financials_quarterly or {}).items():
            row = {
                "ticker": stock.ticker,
                "period_end": period_end,
                "fiscal_year": period.get("fiscal_year"),
                "fiscal_period": period.get("fiscal_period"),
                "fiscal_quarter": period.get("fiscal_quarter"),
                "calendar_year": period.get("calendar_year"),
                "calendar_quarter": period.get("calendar_quarter"),
                "filed_date": period.get("filed_date"),
                "form": period.get("form"),
            }
            row.update(self._canonical_values(period))
            self._upsert(conn, "financials_quarterly", ["ticker", "period_end"], row)

        # financials_ttm (trailing-twelve-month series)
        for period_end, period in (stock.financials_ttm or {}).items():
            row = {
                "ticker": stock.ticker,
                "period_end": period_end,
                "fiscal_year": period.get("fiscal_year"),
                "calendar_year": period.get("calendar_year"),
                "calendar_quarter": period.get("calendar_quarter"),
            }
            row.update(self._canonical_values(period))
            self._upsert(conn, "financials_ttm", ["ticker", "period_end"], row)

        # market_snapshots (point-in-time)
        md = stock.market_data or {}
        val = stock.valuation or {}
        cm = stock.calculated_metrics or {}
        rf = stock.risk_free_rate or {}
        snapshot = {
            "ticker": stock.ticker,
            "collected_at": collected_at,
            "current_price": md.get("current_price"),
            "market_cap": md.get("market_cap"),
            "beta": md.get("beta"),
            "pe_trailing": val.get("pe_trailing"),
            "pe_forward": val.get("pe_forward"),
            "eps_trailing": val.get("eps_trailing"),
            "price_to_book": val.get("price_to_book"),
            "dividend_yield": val.get("dividend_yield"),
            "enterprise_value": cm.get("enterprise_value"),
            "ev_to_ebitda": cm.get("ev_to_ebitda"),
            "ev_to_fcf": cm.get("ev_to_fcf"),
            "fcf_yield": cm.get("fcf_yield"),
            "risk_free_rate": rf.get("risk_free_rate"),
        }
        # Only record a snapshot if we have any market data.
        if any(snapshot.get(c) is not None for c in _SNAPSHOT_COLUMNS):
            self._upsert(conn, "market_snapshots", ["ticker", "collected_at"], snapshot)

        # collection_runs
        self._upsert(conn, "collection_runs", ["ticker", "collected_at"], {
            "ticker": stock.ticker,
            "collected_at": collected_at,
            "data_sources": ", ".join(stock.data_sources),
            "warning_count": len(stock.warnings),
            "error_count": len(stock.errors),
            "quality_score": quality_score,
        })

        # unmapped_facts (tags not yet in the canonical registry; tag is a row value)
        for fact in (stock.unmapped_facts or []):
            self._upsert(conn, "unmapped_facts", ["ticker", "tag"], {
                "ticker": stock.ticker,
                "tag": fact.get("tag"),
                "label": fact.get("label"),
                "unit": fact.get("unit"),
                "period_end": fact.get("period_end"),
                "value": fact.get("value"),
                "form": fact.get("form"),
                "collected_at": collected_at,
            })

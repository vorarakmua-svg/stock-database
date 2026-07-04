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
    # Sector-aware ratios (NULL where not applicable to the company's sector).
    "net_interest_margin", "efficiency_ratio", "loan_to_deposit",
    "loss_ratio", "combined_ratio",
    "ffo", "affo", "ffo_per_share", "ffo_payout",
]

# Point-in-time market/valuation columns for the snapshot table.
_SNAPSHOT_COLUMNS = [
    "current_price", "market_cap", "beta", "pe_trailing", "pe_forward",
    "eps_trailing", "price_to_book", "dividend_yield",
    "enterprise_value", "ev_to_ebitda", "ev_to_fcf", "fcf_yield",
    "risk_free_rate",
    "previous_close", "open", "day_high", "day_low", "volume", "avg_volume",
    "avg_volume_10d", "fifty_two_week_high", "fifty_two_week_low", "ma_50", "ma_200",
    "post_market_price", "pre_market_price",
    "peg_ratio", "price_to_sales", "eps_forward",
    "dividend_rate", "payout_ratio",
    "debt_to_equity", "current_ratio", "quick_ratio",
    "shares_outstanding", "float_shares", "shares_short", "shares_short_prior_month",
    "short_ratio", "short_percent_of_float", "insider_percent", "institutional_percent",
]

# Snapshot columns that are text, not numeric (kept separate so _cols_ddl can type them).
_SNAPSHOT_TEXT_COLUMNS = ["ex_dividend_date"]

# One row per collection: analyst price targets / recommendations (from analyst_estimates).
_ANALYST_COLUMNS = [
    "target_price_low", "target_price_mean", "target_price_median", "target_price_high",
    "recommendation", "recommendation_mean", "number_of_analysts",
    "earnings_date", "forward_eps", "forward_pe",
    "earnings_growth", "revenue_growth", "upside_potential",
]

# Daily OHLCV bars, one row per (ticker, date). Also used for the ^GSPC benchmark
# (see export_benchmark_bars), which writes into this same table.
_PRICE_BAR_COLUMNS = ["open", "high", "low", "close", "volume"]

# Earnings-surprise history, one row per (ticker, quarter).
_EARNINGS_HISTORY_COLUMNS = ["eps_estimate", "eps_actual", "surprise_pct"]


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
                description TEXT,
                address TEXT,
                hq_city TEXT,
                hq_state TEXT,
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

            CREATE TABLE IF NOT EXISTS financials_annual_vintages (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                accn TEXT NOT NULL,
                filed_date TEXT,
                period_end TEXT,
                form TEXT,
                calendar_year INTEGER,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, fiscal_year, accn)
            );

            CREATE INDEX IF NOT EXISTS idx_fa_calendar_year
                ON financials_annual (calendar_year);
            CREATE INDEX IF NOT EXISTS idx_fq_calendar
                ON financials_quarterly (calendar_year, calendar_quarter);
            CREATE INDEX IF NOT EXISTS idx_ttm_calendar
                ON financials_ttm (ticker, calendar_year, calendar_quarter);
            CREATE INDEX IF NOT EXISTS idx_fav_asof
                ON financials_annual_vintages (ticker, fiscal_year, filed_date);

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
                {_cols_ddl(_SNAPSHOT_TEXT_COLUMNS, "TEXT")},
                PRIMARY KEY (ticker, collected_at)
            );

            CREATE TABLE IF NOT EXISTS analyst_snapshots (
                ticker TEXT NOT NULL, collected_at TEXT NOT NULL,
                target_price_low REAL, target_price_mean REAL, target_price_median REAL,
                target_price_high REAL,
                recommendation TEXT, recommendation_mean REAL, number_of_analysts INTEGER,
                earnings_date TEXT, forward_eps REAL, forward_pe REAL,
                earnings_growth REAL, revenue_growth REAL, upside_potential REAL,
                PRIMARY KEY (ticker, collected_at)
            );

            CREATE TABLE IF NOT EXISTS dividend_events (
                ticker TEXT NOT NULL, date TEXT NOT NULL, amount REAL,
                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS holders (
                ticker TEXT NOT NULL, holder_type TEXT NOT NULL, holder TEXT NOT NULL,
                shares REAL, date_reported TEXT, pct_held REAL, value REAL, collected_at TEXT,
                PRIMARY KEY (ticker, holder_type, holder)
            );

            CREATE TABLE IF NOT EXISTS insider_transactions (
                ticker TEXT NOT NULL, insider TEXT NOT NULL, start_date TEXT NOT NULL,
                text TEXT NOT NULL,
                position TEXT, shares REAL, value REAL, ownership TEXT, collected_at TEXT,
                PRIMARY KEY (ticker, insider, start_date, text)
            );

            CREATE TABLE IF NOT EXISTS officers (
                ticker TEXT NOT NULL, name TEXT NOT NULL, title TEXT, age INTEGER,
                total_pay REAL,
                PRIMARY KEY (ticker, name)
            );

            CREATE TABLE IF NOT EXISTS price_bars (
                ticker TEXT NOT NULL, date TEXT NOT NULL,
                {_cols_ddl(_PRICE_BAR_COLUMNS)},
                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS earnings_history (
                ticker TEXT NOT NULL, quarter TEXT NOT NULL,
                {_cols_ddl(_EARNINGS_HISTORY_COLUMNS)},
                PRIMARY KEY (ticker, quarter)
            );

            CREATE TABLE IF NOT EXISTS split_events (
                ticker TEXT NOT NULL, date TEXT NOT NULL, ratio REAL,
                PRIMARY KEY (ticker, date)
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

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
        """Add any of ``columns`` (name -> SQL type) missing from ``table``.

        Uses ``PRAGMA table_info`` to inspect the live schema and
        ``ALTER TABLE ... ADD COLUMN`` for anything not already present. Never drops
        or rewrites existing columns, so it is safe to run repeatedly (idempotent)
        and against long-lived production databases created under an older schema.
        ``table`` must be a module-level constant, never user input.
        """
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, col_type in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {col_type}')

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add any columns missing from an existing DB (registry grows over time).

        ``CREATE TABLE IF NOT EXISTS`` won't add new columns to a pre-existing table,
        so we reconcile each table against its current expected column set.
        """
        expected: Dict[str, Dict[str, str]] = {
            "companies": {
                "sector_class": "TEXT", "fiscal_year_end": "TEXT",
                "description": "TEXT", "address": "TEXT",
                "hq_city": "TEXT", "hq_state": "TEXT",
            },
            "financials_annual": {"calendar_year": "INTEGER",
                                   **{c: "REAL" for c in _CANONICAL_COLUMNS}},
            "financials_quarterly": {"calendar_year": "INTEGER",
                                      "calendar_quarter": "INTEGER",
                                      "fiscal_quarter": "INTEGER",
                                      **{c: "REAL" for c in _CANONICAL_COLUMNS}},
            "financials_ttm": {"fiscal_year": "INTEGER", "calendar_year": "INTEGER",
                               "calendar_quarter": "INTEGER",
                               **{c: "REAL" for c in _CANONICAL_COLUMNS}},
            "financials_annual_vintages": {"calendar_year": "INTEGER",
                                            **{c: "REAL" for c in _CANONICAL_COLUMNS}},
            "metrics_annual": {c: "REAL" for c in _METRIC_COLUMNS},
            "market_snapshots": {**{c: "REAL" for c in _SNAPSHOT_COLUMNS},
                                 **{c: "TEXT" for c in _SNAPSHOT_TEXT_COLUMNS}},
        }
        for table, columns in expected.items():
            self._ensure_columns(conn, table, columns)

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

    @staticmethod
    def _first(rec: Dict[str, Any], *keys: str) -> Any:
        """Return the first present, non-None value among ``keys``.

        yfinance's DataFrame column names vary across versions/locales (e.g.
        ``"Holder"`` vs ``"holder"``, ``"Date Reported"`` vs ``"dateReported"``), so
        record normalization tries each candidate key defensively rather than
        assuming one literal spelling.
        """
        for key in keys:
            if key in rec and rec[key] is not None:
                return rec[key]
        return None

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
            "description": info.get("description") or info.get("longBusinessSummary"),
            "address": info.get("address") or info.get("address1"),
            "hq_city": info.get("hq_city") or info.get("city"),
            "hq_state": info.get("hq_state") or info.get("state"),
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

        # financials_annual_vintages (point-in-time: one row per filing/accession)
        for fy, by_accn in (stock.financials_annual_vintages or {}).items():
            for accn, period in by_accn.items():
                vrow = {
                    "ticker": stock.ticker,
                    "fiscal_year": int(fy) if str(fy).isdigit() else None,
                    "accn": accn,
                    "filed_date": period.get("filed_date"),
                    "period_end": period.get("period_end"),
                    "form": period.get("form"),
                    "calendar_year": period.get("calendar_year"),
                }
                vrow.update(self._canonical_values(period))
                self._upsert(conn, "financials_annual_vintages",
                             ["ticker", "fiscal_year", "accn"], vrow)

        # market_snapshots (point-in-time)
        md = stock.market_data or {}
        val = stock.valuation or {}
        shareholders = stock.shareholders or {}
        cm = stock.calculated_metrics or {}
        rf = stock.risk_free_rate or {}
        # Existing pattern: merge the source dicts and pick columns by name — every
        # new snapshot column added to _SNAPSHOT_COLUMNS/_SNAPSHOT_TEXT_COLUMNS is
        # picked up automatically as long as its key matches across market_data/
        # valuation/shareholders.
        merged: Dict[str, Any] = {**md, **val, **shareholders}
        snapshot: Dict[str, Any] = {
            "ticker": stock.ticker,
            "collected_at": collected_at,
        }
        for col in _SNAPSHOT_COLUMNS:
            snapshot[col] = merged.get(col)
        for col in _SNAPSHOT_TEXT_COLUMNS:
            snapshot[col] = merged.get(col)
        # These are computed downstream (calculated_metrics / risk_free_rate), not
        # present verbatim on market_data/valuation/shareholders.
        snapshot["enterprise_value"] = cm.get("enterprise_value")
        snapshot["ev_to_ebitda"] = cm.get("ev_to_ebitda")
        snapshot["ev_to_fcf"] = cm.get("ev_to_fcf")
        snapshot["fcf_yield"] = cm.get("fcf_yield")
        snapshot["risk_free_rate"] = rf.get("risk_free_rate")
        # Only record a snapshot if we have any market data.
        if any(snapshot.get(c) is not None for c in _SNAPSHOT_COLUMNS + _SNAPSHOT_TEXT_COLUMNS):
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

        # holders (replace-per-run: institutional + mutualfund)
        self._write_holders(conn, stock.ticker, collected_at, shareholders)

        # insider_transactions (upsert on 4-part natural key)
        self._write_insider_transactions(conn, stock.ticker, collected_at, shareholders)

        # officers (replace-per-run)
        self._write_officers(conn, stock.ticker, info)

        # analyst_snapshots (one row per collection)
        self._write_analyst_snapshot(conn, stock.ticker, collected_at, stock.analyst_estimates or {})

        # dividend_events (upsert all known payments)
        self._write_dividend_events(conn, stock.ticker, stock.dividend_history or {})

        # price_bars / earnings_history / split_events (upsert all known records)
        self._write_price_bars(conn, stock.ticker, stock.price_bars or [])
        self._write_earnings_history(conn, stock.ticker, stock.earnings_history or [])
        self._write_split_events(conn, stock.ticker, stock.splits or [])

    def _write_holders(self, conn: sqlite3.Connection, ticker: str, collected_at: str,
                        shareholders: Dict[str, Any]) -> None:
        """Replace-per-run: institutional + mutualfund holders from yfinance DataFrames.

        yfinance's column names vary by version, so every field is looked up
        defensively via ``_first``; records with no holder name are skipped.
        """
        conn.execute("DELETE FROM holders WHERE ticker = ?", (ticker,))
        for holder_type, key in (("institutional", "institutional_holders"),
                                  ("mutualfund", "mutualfund_holders")):
            for rec in shareholders.get(key) or []:
                holder = self._first(rec, "Holder", "holder")
                if not holder:
                    continue
                self._upsert(conn, "holders", ["ticker", "holder_type", "holder"], {
                    "ticker": ticker,
                    "holder_type": holder_type,
                    "holder": holder,
                    "shares": self._first(rec, "Shares", "shares"),
                    "date_reported": self._first(rec, "Date Reported", "dateReported",
                                                  "date_reported"),
                    "pct_held": self._first(rec, "pctHeld", "% Out", "pct_held"),
                    "value": self._first(rec, "Value", "value"),
                    "collected_at": collected_at,
                })

    def _write_insider_transactions(self, conn: sqlite3.Connection, ticker: str,
                                     collected_at: str, shareholders: Dict[str, Any]) -> None:
        """Upsert insider Form-4-derived transactions on the (ticker, insider, start_date, text) key.

        Records missing an insider name or start date are skipped (they can't form
        a valid natural key); missing text is coerced to ``""`` since it's part of
        the primary key but not always populated by yfinance.
        """
        for rec in shareholders.get("insider_transactions") or []:
            insider = self._first(rec, "Insider", "insider")
            start_date = self._first(rec, "Start Date", "startDate", "start_date")
            if not insider or not start_date:
                continue
            text = self._first(rec, "Text", "text") or ""
            self._upsert(conn, "insider_transactions",
                         ["ticker", "insider", "start_date", "text"], {
                "ticker": ticker,
                "insider": insider,
                "start_date": start_date,
                "text": text,
                "position": self._first(rec, "Position", "position"),
                "shares": self._first(rec, "Shares", "shares"),
                "value": self._first(rec, "Value", "value"),
                "ownership": self._first(rec, "Ownership", "ownership"),
                "collected_at": collected_at,
            })

    def _write_officers(self, conn: sqlite3.Connection, ticker: str,
                         info: Dict[str, Any]) -> None:
        """Replace-per-run: company officers (roster changes over time, not additive)."""
        conn.execute("DELETE FROM officers WHERE ticker = ?", (ticker,))
        for officer in info.get("officers") or []:
            name = officer.get("name")
            if not name:
                continue
            self._upsert(conn, "officers", ["ticker", "name"], {
                "ticker": ticker,
                "name": name,
                "title": officer.get("title"),
                "age": officer.get("age"),
                "total_pay": officer.get("total_pay"),
            })

    def _write_analyst_snapshot(self, conn: sqlite3.Connection, ticker: str, collected_at: str,
                                 analyst: Dict[str, Any]) -> None:
        """One row per collection run, only when at least one analyst field is known."""
        if not analyst:
            return
        row: Dict[str, Any] = {"ticker": ticker, "collected_at": collected_at}
        row.update({c: analyst.get(c) for c in _ANALYST_COLUMNS})
        if any(row.get(c) is not None for c in _ANALYST_COLUMNS):
            self._upsert(conn, "analyst_snapshots", ["ticker", "collected_at"], row)

    def _write_dividend_events(self, conn: sqlite3.Connection, ticker: str,
                                dividend_history: Dict[str, Any]) -> None:
        """Upsert every known dividend payment (append-only in practice; history rarely changes)."""
        for rec in dividend_history.get("dividend_payments") or []:
            date = rec.get("date")
            if not date:
                continue
            self._upsert(conn, "dividend_events", ["ticker", "date"], {
                "ticker": ticker,
                "date": date,
                "amount": rec.get("amount"),
            })

    def _write_price_bars(self, conn: sqlite3.Connection, ticker: str,
                           bars: List[Dict[str, Any]]) -> None:
        """Upsert daily OHLCV bars on (ticker, date). Shared by per-stock export and
        ``export_benchmark_bars`` (e.g. ^GSPC), which uses ``ticker`` as the index symbol."""
        for bar in bars:
            date = bar.get("date")
            if not date:
                continue
            row = {"ticker": ticker, "date": date}
            row.update({c: bar.get(c) for c in _PRICE_BAR_COLUMNS})
            self._upsert(conn, "price_bars", ["ticker", "date"], row)

    def _write_earnings_history(self, conn: sqlite3.Connection, ticker: str,
                                 earnings_history: List[Dict[str, Any]]) -> None:
        """Upsert earnings-surprise records (estimate vs. actual EPS) on (ticker, quarter)."""
        for rec in earnings_history:
            quarter = rec.get("quarter")
            if not quarter:
                continue
            row = {"ticker": ticker, "quarter": quarter}
            row.update({c: rec.get(c) for c in _EARNINGS_HISTORY_COLUMNS})
            self._upsert(conn, "earnings_history", ["ticker", "quarter"], row)

    def _write_split_events(self, conn: sqlite3.Connection, ticker: str,
                             splits: List[Dict[str, Any]]) -> None:
        """Upsert stock split events on (ticker, date)."""
        for rec in splits:
            date = rec.get("date")
            if not date:
                continue
            self._upsert(conn, "split_events", ["ticker", "date"], {
                "ticker": ticker,
                "date": date,
                "ratio": rec.get("ratio"),
            })

    def upsert_quote(self, ticker: str, quote: Dict[str, Any], collected_at: str) -> None:
        """Write ONE ``market_snapshots`` row + ONE ``analyst_snapshots`` row from
        a quote-only fetch (``YahooHandler.fetch_quote``) — nothing else.

        ``market_snapshots``' primary key is ``(ticker, collected_at)``, so
        this INSERTS a new row rather than overwriting the snapshot from the
        last full collection — a quote refresh never clobbers full-collection
        history.

        ``fetch_quote``'s ``market_data`` omits ``ma_50``/``ma_200`` (no
        history call is made for a quote-only refresh). Rather than let the
        DES page's moving averages go blank after every quote refresh, this
        carries the PREVIOUS snapshot's ``ma_50``/``ma_200``/``beta`` forward
        into the new row whenever the fresh quote didn't return them (a
        one-row read-only ``SELECT`` against ``market_snapshots``, no writer
        contention — this method is only ever called from the single-writer
        job worker).
        """
        try:
            conn = self._connect()
            try:
                self._create_schema(conn)
                self._migrate(conn)

                md = quote.get("market_data") or {}
                val = quote.get("valuation") or {}
                shareholders = quote.get("shareholders") or {}
                merged: Dict[str, Any] = {**md, **val, **shareholders}

                snapshot: Dict[str, Any] = {"ticker": ticker, "collected_at": collected_at}
                for col in _SNAPSHOT_COLUMNS:
                    snapshot[col] = merged.get(col)
                for col in _SNAPSHOT_TEXT_COLUMNS:
                    snapshot[col] = merged.get(col)

                # Carry forward ma_50/ma_200/beta from the latest prior snapshot
                # when this quote-only fetch didn't return them.
                prev = self._latest_snapshot_row(conn, ticker)
                if prev is not None:
                    for col in ("ma_50", "ma_200", "beta"):
                        if snapshot.get(col) is None:
                            snapshot[col] = prev.get(col)

                if any(snapshot.get(c) is not None
                       for c in _SNAPSHOT_COLUMNS + _SNAPSHOT_TEXT_COLUMNS):
                    self._upsert(conn, "market_snapshots", ["ticker", "collected_at"], snapshot)

                self._write_analyst_snapshot(
                    conn, ticker, collected_at, quote.get("analyst_estimates") or {}
                )

                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error writing quote for {ticker}: {e}")

    @staticmethod
    def _latest_snapshot_row(conn: sqlite3.Connection, ticker: str) -> Optional[Dict[str, Any]]:
        """Newest ``market_snapshots`` row for *ticker* by ``collected_at``, or ``None``."""
        cur = conn.execute(
            "SELECT * FROM market_snapshots WHERE ticker = ? ORDER BY collected_at DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [d[0] for d in cur.description]
        return dict(zip(columns, row))

    def export_benchmark_bars(self, symbol: str, bars: List[Dict[str, Any]]) -> None:
        """Write benchmark index OHLCV bars (e.g. ^GSPC) into ``price_bars``.

        Collected once per run rather than once per ticker, so market-relative
        metrics (beta, relative strength) can be computed against a common index.
        Writes bars only — unlike ``export``, this never creates/updates a
        ``companies`` row, since a benchmark index is not a company.
        """
        if not bars:
            return
        try:
            conn = self._connect()
            try:
                self._create_schema(conn)
                self._migrate(conn)
                self._write_price_bars(conn, symbol, bars)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error writing benchmark bars for {symbol}: {e}")

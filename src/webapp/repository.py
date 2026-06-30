"""Read-only data-access layer for the stock-database web app.

Returns plain ``dict``s so callers stay thin and mypy-light. The connection is
opened ``mode=ro``; the Reader never mutates data. Column names that are
interpolated into SQL are validated against the schema constant lists imported
from the SQLite store — this is the SQL-injection guard for series endpoints.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..exporters.sqlite_store import _CANONICAL_COLUMNS, _METRIC_COLUMNS, _SNAPSHOT_COLUMNS

# O(1) whitelist sets — used for column-name validation before interpolation.
_METRIC_COL_SET: frozenset[str] = frozenset(_METRIC_COLUMNS)
_CANONICAL_COL_SET: frozenset[str] = frozenset(_CANONICAL_COLUMNS)
_SNAPSHOT_COL_SET: frozenset[str] = frozenset(_SNAPSHOT_COLUMNS)  # reserved for future snapshot-field filter


class Reader:
    """Read-only DAL over the SQLite stock database."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        self._conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Reader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying read-only connection."""
        return self._conn

    # ---- Companies --------------------------------------------------------

    def list_companies(
        self,
        sector_class: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """All companies, optionally filtered by sector_class and/or search term."""
        where: List[str] = []
        params: List[Any] = []
        if sector_class is not None:
            where.append("sector_class = ?")
            params.append(sector_class)
        if search is not None:
            where.append("(ticker LIKE ? OR company_name LIKE ?)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern])
        sql = "SELECT * FROM companies"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ticker LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def count_companies(
        self,
        sector_class: Optional[str] = None,
        search: Optional[str] = None,
    ) -> int:
        """Count of companies matching the optional filters."""
        where: List[str] = []
        params: List[Any] = []
        if sector_class is not None:
            where.append("sector_class = ?")
            params.append(sector_class)
        if search is not None:
            where.append("(ticker LIKE ? OR company_name LIKE ?)")
            pattern = f"%{search}%"
            params.extend([pattern, pattern])
        sql = "SELECT COUNT(*) FROM companies"
        if where:
            sql += " WHERE " + " AND ".join(where)
        cur = self._conn.execute(sql, params)
        return int(cur.fetchone()[0])

    def get_company(self, ticker: str) -> Optional[Dict[str, Any]]:
        """One companies row by primary key, or ``None`` if not found."""
        cur = self._conn.execute(
            "SELECT * FROM companies WHERE ticker = ?", (ticker,)
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def search_companies(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Autocomplete: ticker/company_name prefix search returning ``{ticker, company_name, sector_class}``."""
        pattern = f"%{query}%"
        cur = self._conn.execute(
            "SELECT ticker, company_name, sector_class FROM companies "
            "WHERE ticker LIKE ? OR company_name LIKE ? "
            "ORDER BY ticker LIMIT ?",
            (pattern, pattern, limit),
        )
        return [dict(row) for row in cur.fetchall()]

    def distinct_sectors(self) -> List[str]:
        """Distinct non-null ``sector_class`` values, sorted."""
        cur = self._conn.execute(
            "SELECT DISTINCT sector_class FROM companies "
            "WHERE sector_class IS NOT NULL ORDER BY sector_class"
        )
        return [row[0] for row in cur.fetchall()]

    # ---- Single-company deep dive -----------------------------------------

    def company_overview(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Compound overview for one company, or ``None`` if the ticker is unknown.

        Returns ``{"company": ..., "latest_snapshot": ..., "latest_annual": ..., "latest_metrics": ...}``.
        Any sub-key may be ``None`` if that table has no rows for the ticker.
        """
        company = self.get_company(ticker)
        if company is None:
            return None

        snap_row = self._conn.execute(
            "SELECT * FROM market_snapshots WHERE ticker = ? "
            "ORDER BY collected_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()

        ann_row = self._conn.execute(
            "SELECT * FROM financials_annual WHERE ticker = ? "
            "ORDER BY fiscal_year DESC LIMIT 1",
            (ticker,),
        ).fetchone()

        met_row = self._conn.execute(
            "SELECT * FROM metrics_annual WHERE ticker = ? "
            "ORDER BY fiscal_year DESC LIMIT 1",
            (ticker,),
        ).fetchone()

        return {
            "company": company,
            "latest_snapshot": dict(snap_row) if snap_row is not None else None,
            "latest_annual": dict(ann_row) if ann_row is not None else None,
            "latest_metrics": dict(met_row) if met_row is not None else None,
        }

    def annual_financials(
        self, ticker: str, years_back: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """``financials_annual`` rows newest fiscal_year first, optionally trimmed to N years."""
        sql = (
            "SELECT * FROM financials_annual WHERE ticker = ? "
            "ORDER BY fiscal_year DESC"
        )
        params: List[Any] = [ticker]
        if years_back is not None:
            sql += " LIMIT ?"
            params.append(years_back)
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def quarterly_financials(
        self, ticker: str, quarters_back: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """``financials_quarterly`` rows newest period_end first, optionally trimmed."""
        sql = (
            "SELECT * FROM financials_quarterly WHERE ticker = ? "
            "ORDER BY period_end DESC"
        )
        params: List[Any] = [ticker]
        if quarters_back is not None:
            sql += " LIMIT ?"
            params.append(quarters_back)
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def ttm_financials(self, ticker: str) -> List[Dict[str, Any]]:
        """``financials_ttm`` rows newest period_end first."""
        cur = self._conn.execute(
            "SELECT * FROM financials_ttm WHERE ticker = ? ORDER BY period_end DESC",
            (ticker,),
        )
        return [dict(row) for row in cur.fetchall()]

    def annual_metrics(
        self, ticker: str, years_back: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """``metrics_annual`` rows newest fiscal_year first, optionally trimmed to N years."""
        sql = (
            "SELECT * FROM metrics_annual WHERE ticker = ? "
            "ORDER BY fiscal_year DESC"
        )
        params: List[Any] = [ticker]
        if years_back is not None:
            sql += " LIMIT ?"
            params.append(years_back)
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def metric_series(
        self, ticker: str, metric: str
    ) -> List[Dict[str, Any]]:
        """``[{"fiscal_year": int, "value": float|None}]`` ascending, for one ``metric`` column.

        Raises ``ValueError`` if ``metric`` is not in the whitelisted ``_METRIC_COLUMNS``
        (the column name is interpolated into SQL — this is the injection guard).
        """
        if metric not in _METRIC_COL_SET:
            raise ValueError(
                f"Unknown metric column {metric!r}. "
                f"Must be one of the whitelisted _METRIC_COLUMNS."
            )
        cur = self._conn.execute(
            f'SELECT fiscal_year, "{metric}" AS value '
            f"FROM metrics_annual WHERE ticker = ? ORDER BY fiscal_year ASC",
            (ticker,),
        )
        return [{"fiscal_year": row["fiscal_year"], "value": row["value"]}
                for row in cur.fetchall()]

    def financial_series(
        self, ticker: str, field: str
    ) -> List[Dict[str, Any]]:
        """``[{"fiscal_year": int, "value": float|None}]`` ascending, for one ``field`` column.

        Raises ``ValueError`` if ``field`` is not in the whitelisted ``_CANONICAL_COLUMNS``
        (the column name is interpolated into SQL — this is the injection guard).
        """
        if field not in _CANONICAL_COL_SET:
            raise ValueError(
                f"Unknown canonical column {field!r}. "
                f"Must be one of the whitelisted _CANONICAL_COLUMNS."
            )
        cur = self._conn.execute(
            f'SELECT fiscal_year, "{field}" AS value '
            f"FROM financials_annual WHERE ticker = ? ORDER BY fiscal_year ASC",
            (ticker,),
        )
        return [{"fiscal_year": row["fiscal_year"], "value": row["value"]}
                for row in cur.fetchall()]

    def latest_snapshot(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Newest ``market_snapshots`` row by ``collected_at``, or ``None``."""
        cur = self._conn.execute(
            "SELECT * FROM market_snapshots WHERE ticker = ? "
            "ORDER BY collected_at DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def snapshot_history(self, ticker: str) -> List[Dict[str, Any]]:
        """All ``market_snapshots`` rows ascending by ``collected_at``."""
        cur = self._conn.execute(
            "SELECT * FROM market_snapshots WHERE ticker = ? "
            "ORDER BY collected_at ASC",
            (ticker,),
        )
        return [dict(row) for row in cur.fetchall()]

"""Read-only data-access layer for the stock-database web app.

Returns plain ``dict``s so callers stay thin and mypy-light. The connection is
opened ``mode=ro``; the Reader never mutates data. Column names that are
interpolated into SQL are validated against the schema constant lists imported
from the SQLite store — this is the SQL-injection guard for series endpoints.
"""
from __future__ import annotations

import sqlite3
import statistics
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Union

from ..exporters.sqlite_store import _CANONICAL_COLUMNS, _METRIC_COLUMNS, _SNAPSHOT_COLUMNS
from .screener import SCREEN_COLUMNS, ScreenSpec, build_count_query, build_screen_query

# O(1) whitelist sets — used for column-name validation before interpolation.
_METRIC_COL_SET: FrozenSet[str] = frozenset(_METRIC_COLUMNS)
_CANONICAL_COL_SET: FrozenSet[str] = frozenset(_CANONICAL_COLUMNS)
_SNAPSHOT_COL_SET: FrozenSet[str] = frozenset(_SNAPSHOT_COLUMNS)  # reserved for future snapshot-field filter


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

    def vintages(
        self, ticker: str, fiscal_year: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """All ``financials_annual_vintages`` rows for a ticker.

        Optionally filtered to ``fiscal_year``. Ordered by ``fiscal_year`` DESC
        then ``filed_date`` ASC so the earliest filing of the newest year comes
        first within each year.
        """
        params: List[Any] = [ticker]
        sql = (
            "SELECT * FROM financials_annual_vintages WHERE ticker = ?"
        )
        if fiscal_year is not None:
            sql += " AND fiscal_year = ?"
            params.append(fiscal_year)
        sql += " ORDER BY fiscal_year DESC, filed_date ASC"
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    # ---- Screener & cross-company analytics --------------------------------

    def screen(self, spec: ScreenSpec) -> Dict[str, Any]:
        """Run a screener query. Returns ``{columns, items, total}``.

        ``spec`` is validated inside ``build_screen_query``; raises
        ``ValueError`` if any field/sort/op is not whitelisted.
        """
        sql, params = build_screen_query(spec)
        count_sql, count_params = build_count_query(spec)

        cur = self._conn.execute(sql, params)
        items = [dict(row) for row in cur.fetchall()]

        count_cur = self._conn.execute(count_sql, count_params)
        total = int(count_cur.fetchone()[0])

        return {"columns": SCREEN_COLUMNS, "items": items, "total": total}

    def compare(
        self,
        tickers: Sequence[str],
        metrics: Sequence[str],
    ) -> Dict[str, Any]:
        """Side-by-side comparison of tickers for the given metrics.

        Returns ``{"tickers": [...], "rows": [{"metric": m, "values": {ticker: v}}]}``.
        Raises ``ValueError`` if any metric is not in ``_METRIC_COLUMNS``.
        """
        for m in metrics:
            if m not in _METRIC_COL_SET:
                raise ValueError(
                    f"Unknown metric column {m!r}. "
                    "Must be one of the whitelisted _METRIC_COLUMNS."
                )

        # Fetch latest metrics_annual row for each ticker
        latest_rows: Dict[str, Optional[Dict[str, Any]]] = {}
        for ticker in tickers:
            cur = self._conn.execute(
                "SELECT * FROM metrics_annual WHERE ticker = ? "
                "ORDER BY fiscal_year DESC LIMIT 1",
                (ticker,),
            )
            row = cur.fetchone()
            latest_rows[ticker] = dict(row) if row is not None else None

        rows_list: List[Dict[str, Any]] = []
        for m in metrics:
            values: Dict[str, Any] = {}
            for ticker in tickers:
                rd = latest_rows.get(ticker)
                values[ticker] = rd.get(m) if rd is not None else None
            rows_list.append({"metric": m, "values": values})

        return {"tickers": list(tickers), "rows": rows_list}

    def sector_medians(
        self,
        sector_class: str,
        fiscal_year: Optional[int] = None,
    ) -> Dict[str, float]:
        """Per-column medians across the sector's companies.

        Uses each company's latest ``metrics_annual`` row (or the given year).
        Columns with no non-null values are omitted from the result.
        Median is computed in Python (SQLite has no MEDIAN aggregate).
        """
        tickers_cur = self._conn.execute(
            "SELECT ticker FROM companies WHERE sector_class = ?",
            (sector_class,),
        )
        tickers = [row[0] for row in tickers_cur.fetchall()]

        metric_rows = self._fetch_latest_metrics(tickers, fiscal_year)
        if not metric_rows:
            return {}

        result: Dict[str, float] = {}
        for col in _METRIC_COLUMNS:
            values = [r[col] for r in metric_rows if r.get(col) is not None]
            if values:
                result[col] = statistics.median(values)
        return result

    def sector_aggregates(
        self,
        fiscal_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Per-sector summary with company count and headline metric medians.

        Returns a list sorted by ``sector_class``.  Each entry has:
        ``{"sector_class", "n", "median_quality", "roic", "roe",
           "net_margin", "debt_to_ebitda"}``.
        """
        HEADLINE = ["roic", "roe", "net_margin", "debt_to_ebitda"]

        sectors_cur = self._conn.execute(
            "SELECT DISTINCT sector_class FROM companies "
            "WHERE sector_class IS NOT NULL ORDER BY sector_class"
        )
        sectors = [row[0] for row in sectors_cur.fetchall()]

        result: List[Dict[str, Any]] = []
        for sector in sectors:
            n_cur = self._conn.execute(
                "SELECT COUNT(*) FROM companies WHERE sector_class = ?",
                (sector,),
            )
            n = int(n_cur.fetchone()[0])

            tickers_cur = self._conn.execute(
                "SELECT ticker FROM companies WHERE sector_class = ?",
                (sector,),
            )
            tickers = [row[0] for row in tickers_cur.fetchall()]

            metric_rows = self._fetch_latest_metrics(tickers, fiscal_year)

            # Collect quality_score values for median_quality
            quality_scores: List[float] = []
            for ticker in tickers:
                if fiscal_year is not None:
                    qs_cur = self._conn.execute(
                        "SELECT quality_score FROM financials_annual "
                        "WHERE ticker = ? AND fiscal_year = ?",
                        (ticker, fiscal_year),
                    )
                else:
                    qs_cur = self._conn.execute(
                        "SELECT quality_score FROM financials_annual "
                        "WHERE ticker = ? ORDER BY fiscal_year DESC LIMIT 1",
                        (ticker,),
                    )
                qs_row = qs_cur.fetchone()
                if qs_row is not None and qs_row[0] is not None:
                    quality_scores.append(float(qs_row[0]))

            agg: Dict[str, Any] = {
                "sector_class": sector,
                "n": n,
                "median_quality": (
                    statistics.median(quality_scores) if quality_scores else None
                ),
            }
            for m in HEADLINE:
                vals = [r[m] for r in metric_rows if r.get(m) is not None]
                agg[m] = statistics.median(vals) if vals else None

            result.append(agg)

        return result

    def peer_comparison(
        self,
        ticker: str,
        metrics: Sequence[str],
    ) -> Dict[str, Any]:
        """Company vs sector peer medians for the given metrics.

        Returns
        ``{"sector_class", "n_peers", "company": {m: v}, "sector_median": {m: v}}``.
        Raises ``ValueError`` if any metric is not in ``_METRIC_COLUMNS`` or if
        the ticker is unknown.
        """
        for m in metrics:
            if m not in _METRIC_COL_SET:
                raise ValueError(
                    f"Unknown metric column {m!r}. "
                    "Must be one of the whitelisted _METRIC_COLUMNS."
                )

        company = self.get_company(ticker)
        if company is None:
            raise ValueError(f"Unknown ticker {ticker!r}.")
        sector_class: Optional[str] = company.get("sector_class")

        # Ticker's own latest metrics
        own_cur = self._conn.execute(
            "SELECT * FROM metrics_annual WHERE ticker = ? "
            "ORDER BY fiscal_year DESC LIMIT 1",
            (ticker,),
        )
        own_row = own_cur.fetchone()
        own_dict = dict(own_row) if own_row is not None else {}
        company_vals: Dict[str, Any] = {m: own_dict.get(m) for m in metrics}

        # All peers in the sector (including the ticker itself)
        if sector_class is not None:
            peers_cur = self._conn.execute(
                "SELECT ticker FROM companies WHERE sector_class = ?",
                (sector_class,),
            )
            peer_tickers = [row[0] for row in peers_cur.fetchall()]
        else:
            peer_tickers = [ticker]

        peer_rows = self._fetch_latest_metrics(peer_tickers, fiscal_year=None)

        sector_median: Dict[str, Any] = {}
        for m in metrics:
            vals = [r[m] for r in peer_rows if r.get(m) is not None]
            sector_median[m] = statistics.median(vals) if vals else None

        return {
            "sector_class": sector_class,
            "n_peers": len(peer_tickers),
            "company": company_vals,
            "sector_median": sector_median,
        }

    # ---- Quality & coverage ------------------------------------------------

    def collection_runs(
        self,
        ticker: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Rows from ``collection_runs``, newest first.

        Optionally filtered to one *ticker*.
        """
        where: List[str] = []
        params: List[Any] = []
        if ticker is not None:
            where.append("ticker = ?")
            params.append(ticker)
        sql = "SELECT * FROM collection_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY collected_at DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def latest_collection_runs(self) -> List[Dict[str, Any]]:
        """Most-recent ``collection_runs`` row per ticker, ordered by ticker.

        Uses a two-step CTE: first finds MAX(collected_at) per ticker, then
        picks MAX(rowid) among any tied rows so exactly one row per ticker is
        always returned even if two runs share the same collected_at timestamp.
        """
        sql = """
            WITH max_ts AS (
                SELECT ticker, MAX(collected_at) AS mx
                FROM collection_runs
                GROUP BY ticker
            ),
            max_rid AS (
                SELECT cr.ticker, MAX(cr.rowid) AS mr
                FROM collection_runs AS cr
                JOIN max_ts ON cr.ticker = max_ts.ticker
                           AND cr.collected_at = max_ts.mx
                GROUP BY cr.ticker
            )
            SELECT cr.ticker, cr.collected_at, cr.data_sources,
                   cr.warning_count, cr.error_count, cr.quality_score
            FROM collection_runs AS cr
            JOIN max_rid ON cr.rowid = max_rid.mr
            ORDER BY cr.ticker
        """
        cur = self._conn.execute(sql)
        return [dict(row) for row in cur.fetchall()]

    def coverage_by_sector(self) -> List[Dict[str, Any]]:
        """Per-``sector_class`` summary: n_companies and median quality_score.

        Quality scores come from the latest ``collection_runs`` row per ticker.
        Median is computed in Python (SQLite has no MEDIAN aggregate).
        """
        sectors_cur = self._conn.execute(
            "SELECT DISTINCT sector_class FROM companies "
            "WHERE sector_class IS NOT NULL ORDER BY sector_class"
        )
        sectors = [row[0] for row in sectors_cur.fetchall()]

        latest_runs = self.latest_collection_runs()
        qs_by_ticker: Dict[str, Optional[float]] = {
            r["ticker"]: r["quality_score"] for r in latest_runs
        }

        result: List[Dict[str, Any]] = []
        for sector in sectors:
            tickers_cur = self._conn.execute(
                "SELECT ticker FROM companies WHERE sector_class = ?",
                (sector,),
            )
            tickers = [row[0] for row in tickers_cur.fetchall()]
            n = len(tickers)
            scores: List[float] = []
            for t in tickers:
                qs = qs_by_ticker.get(t)
                if qs is not None:
                    scores.append(float(qs))
            result.append({
                "sector_class": sector,
                "n_companies": n,
                "median_quality": statistics.median(scores) if scores else None,
            })
        return result

    def field_fill_rates(
        self, sector_class: Optional[str] = None
    ) -> Dict[str, float]:
        """Fraction of in-scope companies whose LATEST ``financials_annual`` row
        has a non-null value for each canonical field.

        Denominator = number of in-scope companies that have *any*
        ``financials_annual`` row.  Returns ``{field: fraction}`` (0.0–1.0)
        sorted by field name for stable output.

        Single-pass: one query fetches every company's latest annual row, then
        Python counts non-null values per field — avoids N_tickers × N_fields
        round-trips.
        """
        where: List[str] = []
        params: List[Any] = []
        if sector_class is not None:
            where.append("c.sector_class = ?")
            params.append(sector_class)

        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        latest_sql = f"""
            SELECT fa.*
            FROM financials_annual AS fa
            JOIN (
                SELECT fa2.ticker, MAX(fa2.fiscal_year) AS mfy
                FROM financials_annual AS fa2
                JOIN companies AS c ON fa2.ticker = c.ticker
                {where_clause}
                GROUP BY fa2.ticker
            ) AS lv ON fa.ticker = lv.ticker AND fa.fiscal_year = lv.mfy
        """
        rows = [dict(r) for r in self._conn.execute(latest_sql, params).fetchall()]
        n_total = len(rows)
        if n_total == 0:
            return {f: 0.0 for f in sorted(_CANONICAL_COLUMNS)}
        return {
            f: sum(1 for row in rows if row.get(f) is not None) / n_total
            for f in sorted(_CANONICAL_COLUMNS)
        }

    def unmapped_facts(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Rows from ``unmapped_facts``, most-recent ``collected_at`` first."""
        cur = self._conn.execute(
            "SELECT * FROM unmapped_facts ORDER BY collected_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def unmapped_top(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Top unmapped tags by company count.

        Returns ``[{"tag", "n_companies", "example_label"}]`` ordered by
        ``n_companies`` DESC.
        """
        cur = self._conn.execute(
            "SELECT tag, COUNT(DISTINCT ticker) AS n_companies, "
            "       MAX(label) AS example_label "
            "FROM unmapped_facts "
            "GROUP BY tag "
            "ORDER BY n_companies DESC "
            "LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def data_freshness(self) -> Dict[str, Any]:
        """High-level freshness summary.

        Returns ``{"latest_company_update", "n_tickers", "table_counts"}``.
        ``table_counts`` covers the main operational tables.
        """
        latest_cur = self._conn.execute(
            "SELECT MAX(updated_at) FROM companies"
        )
        latest_update = latest_cur.fetchone()[0]

        n_cur = self._conn.execute("SELECT COUNT(*) FROM companies")
        n_tickers = int(n_cur.fetchone()[0])

        tables = [
            "companies",
            "financials_annual",
            "financials_quarterly",
            "metrics_annual",
            "market_snapshots",
            "collection_runs",
            "unmapped_facts",
        ]
        table_counts: Dict[str, int] = {}
        for tbl in tables:
            cnt_cur = self._conn.execute(f"SELECT COUNT(*) FROM {tbl}")  # noqa: S608
            table_counts[tbl] = int(cnt_cur.fetchone()[0])

        return {
            "latest_company_update": latest_update,
            "n_tickers": n_tickers,
            "table_counts": table_counts,
        }

    # ---- Private helpers ---------------------------------------------------

    def _fetch_latest_metrics(
        self,
        tickers: List[str],
        fiscal_year: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Return the latest ``metrics_annual`` row for each ticker.

        If ``fiscal_year`` is provided, fetch that specific year; otherwise
        fetch the most-recent year.  Tickers with no row are skipped.
        """
        rows: List[Dict[str, Any]] = []
        for ticker in tickers:
            if fiscal_year is not None:
                cur = self._conn.execute(
                    "SELECT * FROM metrics_annual WHERE ticker = ? AND fiscal_year = ?",
                    (ticker, fiscal_year),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM metrics_annual WHERE ticker = ? "
                    "ORDER BY fiscal_year DESC LIMIT 1",
                    (ticker,),
                )
            row = cur.fetchone()
            if row is not None:
                rows.append(dict(row))
        return rows

"""Point-in-time as-of-date queries over the vintaged annual store.

A read-only consumer of the ``financials_annual_vintages`` table (see
``exporters/sqlite_store.py``). For a given date ``D``, each figure of an annual period
resolves to the latest filing made on or before ``D`` that reports it — so backtests read
fundamentals with no look-ahead bias. The connection is opened ``mode=ro``; the reader
never mutates data.

A vintage with a NULL ``filed_date`` (unknown filing date) is deliberately invisible to
every as-of query — ``NULL <= D`` is never true — which keeps resolution on the safe side
of no-look-ahead (omit rather than leak unknown-timed data).
"""

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..exporters.sqlite_store import _CANONICAL_COLUMNS, read_only_uri

# Accepts an ISO ``YYYY-MM-DD`` string or a date/datetime (normalized before querying).
AsOfDate = Union[str, date]


class AsOfReader:
    """Resolve vintaged annual data as it was known on a given date."""

    def __init__(self, db_path: Union[str, Path],
                 logger: Optional[logging.Logger] = None) -> None:
        self.db_path = Path(db_path)
        self.logger = logger or logging.getLogger(__name__)
        self._conn = sqlite3.connect(read_only_uri(self.db_path), uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AsOfReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying read-only connection (for sibling readers over the same store)."""
        return self._conn

    @staticmethod
    def _norm_date(as_of_date: AsOfDate) -> str:
        """Normalize a date/datetime or ISO string to ``YYYY-MM-DD`` for comparison."""
        if isinstance(as_of_date, datetime):
            return as_of_date.date().isoformat()
        if isinstance(as_of_date, date):
            return as_of_date.isoformat()
        return str(as_of_date)

    def as_of_annual(
        self, ticker: str, fiscal_year: int, as_of_date: AsOfDate
    ) -> Optional[Dict[str, Any]]:
        """The annual period for ``(ticker, fiscal_year)`` as known on ``as_of_date``.

        Each figure is the one last filed on or before ``as_of_date``. A 10-K repeats only
        part of each earlier year (the income and cash-flow statements of two years back,
        the balance sheet of one, the equity statement's opening balances), so a figure the
        latest filing leaves out keeps the value an earlier filing gave it (BUG-110).
        Returns a plain dict (all canonical line items + provenance metadata), or ``None``
        if the year had not been filed yet as of that date. The provenance (``accn``,
        ``filed_date``, ``form``, ``period_end``) is the latest filing's, and
        ``carried_from`` names the filing of each figure last filed earlier:
        ``{field: {"accn", "filed_date", "form"}}``.
        """
        cutoff = self._norm_date(as_of_date)
        rows = self._conn.execute(
            "SELECT * FROM financials_annual_vintages "
            "WHERE ticker = ? AND fiscal_year = ? AND filed_date <= ? "
            "ORDER BY filed_date DESC, accn DESC",
            (ticker, int(fiscal_year), cutoff),
        ).fetchall()
        if not rows:
            return None
        period = dict(rows[0])
        figures = [key for key in _CANONICAL_COLUMNS if key in period]
        carried: Dict[str, Dict[str, Any]] = {}
        for row in rows[1:]:  # newest first, so each gap takes the latest figure filed
            for key in figures:
                if period[key] is None and row[key] is not None:
                    period[key] = row[key]
                    carried[key] = {"accn": row["accn"], "filed_date": row["filed_date"], "form": row["form"]}
        period["carried_from"] = carried
        return period

    def as_of_value(
        self, ticker: str, fiscal_year: int, field: str, as_of_date: AsOfDate
    ) -> Any:
        """A single canonical field's value as known on ``as_of_date`` (or ``None``)."""
        row = self.as_of_annual(ticker, fiscal_year, as_of_date)
        return row.get(field) if row is not None else None

    def history_as_of(
        self, ticker: str, as_of_date: AsOfDate, years_back: Optional[int] = None
    ) -> Dict[int, Dict[str, Any]]:
        """Every fiscal year known as of ``as_of_date``, each resolved to its latest
        vintage filed <= that date. Keyed by fiscal_year, newest first; ``years_back``
        trims to the most recent N years.
        """
        cutoff = self._norm_date(as_of_date)
        cur = self._conn.execute(
            "SELECT DISTINCT fiscal_year FROM financials_annual_vintages "
            "WHERE ticker = ? AND filed_date <= ? "
            "ORDER BY fiscal_year DESC",
            (ticker, cutoff),
        )
        years = [row["fiscal_year"] for row in cur.fetchall()]
        if years_back:
            years = years[:years_back]
        result: Dict[int, Dict[str, Any]] = {}
        for fy in years:
            period = self.as_of_annual(ticker, fy, cutoff)
            if period is not None:
                result[int(fy)] = period
        return result

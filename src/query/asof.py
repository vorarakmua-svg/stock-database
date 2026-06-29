"""Point-in-time as-of-date queries over the vintaged annual store.

A read-only consumer of the ``financials_annual_vintages`` table (see
``exporters/sqlite_store.py``). For a given date ``D``, each annual period resolves to
the latest filing made on or before ``D`` — so backtests read fundamentals with no
look-ahead bias. The connection is opened ``mode=ro``; the reader never mutates data.
"""

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

# Accepts an ISO ``YYYY-MM-DD`` string or a date/datetime (normalized before querying).
AsOfDate = Union[str, "date"]


class AsOfReader:
    """Resolve vintaged annual data as it was known on a given date."""

    def __init__(self, db_path: Union[str, Path],
                 logger: Optional[logging.Logger] = None) -> None:
        self.db_path = Path(db_path)
        self.logger = logger or logging.getLogger(__name__)
        self._conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AsOfReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

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

        Returns the latest vintage filed on or before ``as_of_date`` as a plain dict
        (all canonical line items + provenance metadata), or ``None`` if the year had
        not been filed yet as of that date.
        """
        cutoff = self._norm_date(as_of_date)
        cur = self._conn.execute(
            "SELECT * FROM financials_annual_vintages "
            "WHERE ticker = ? AND fiscal_year = ? AND filed_date <= ? "
            "ORDER BY filed_date DESC, accn DESC LIMIT 1",
            (ticker, int(fiscal_year), cutoff),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def as_of_value(
        self, ticker: str, fiscal_year: int, field: str, as_of_date: AsOfDate
    ) -> Any:
        """A single canonical field's value as known on ``as_of_date`` (or ``None``)."""
        row = self.as_of_annual(ticker, fiscal_year, as_of_date)
        return row.get(field) if row is not None else None

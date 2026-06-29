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
from typing import Any, Optional, Union

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

"""Point-in-time as-of-date queries over the annual record as it was known on each filing date.

A read-only consumer of the ``financials_annual_known`` table (see ``exporters/sqlite_store.py``): the collection
replays the annual record (``build_annual_record``) on the facts filed by each filing date and stores a year's row
each day it changed, so the record as known on any date ``D`` is each year's latest row on or before ``D`` (see
``parsers/annual_snapshots.py``). Reading it is a lookup, with no selection rule of its own, so backtests read the
fundamentals the record held then, with no look-ahead. The connection is opened ``mode=ro``; the reader never
mutates data.

A database collected before the snapshots, or a ticker not re-collected since, has none: every as-of query reads
nothing for it (no fallback to another rule), and :meth:`AsOfReader.has_snapshots` tells it apart from a year not
yet filed.
"""

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..exporters.sqlite_store import _CANONICAL_COLUMNS, read_only_uri

# Accepts an ISO ``YYYY-MM-DD`` string or a date/datetime (normalized before querying).
AsOfDate = Union[str, date]

#: A period's provenance, as the vintages gave it: its latest contributing filing's.
_PROVENANCE = ("ticker", "fiscal_year", "accn", "filed_date", "period_end", "form", "calendar_year")
#: The figures a ``carried_from`` entry may name.
_FIGURES = frozenset(_CANONICAL_COLUMNS)


class AsOfReader:
    """Read the annual record as it was known on a given date."""

    def __init__(self, db_path: Union[str, Path],
                 logger: Optional[logging.Logger] = None) -> None:
        self.db_path = Path(db_path)
        self.logger = logger or logging.getLogger(__name__)
        self._conn = sqlite3.connect(read_only_uri(self.db_path), uri=True)
        self._conn.row_factory = sqlite3.Row
        self._built = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'financials_annual_known'"
        ).fetchone() is not None

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

    def has_snapshots(self, ticker: str) -> bool:
        """Whether *ticker*'s as-of data is built: it has rows in ``financials_annual_known``. False for a database
        or a ticker collected before the snapshots."""
        if not self._built:
            return False
        return self._conn.execute(
            "SELECT 1 FROM financials_annual_known WHERE ticker = ? LIMIT 1", (ticker,)
        ).fetchone() is not None

    def as_of_annual(
        self, ticker: str, fiscal_year: int, as_of_date: AsOfDate
    ) -> Optional[Dict[str, Any]]:
        """The annual period for ``(ticker, fiscal_year)`` as known on ``as_of_date``: the year's row with the
        latest ``known_from`` on or before it.

        Returns a plain dict (all canonical line items + provenance metadata), or ``None`` if the year wasn't in the
        record as of that date: not filed yet, left the record that day (a ``present`` 0 row), or no snapshots built
        for the ticker. The provenance (``accn``, ``filed_date``, ``form``) is the year's latest contributing
        filing's; ``period_end`` and ``calendar_year`` are the row's, and ``carried_from`` names the filing of each
        figure read from an earlier one: ``{field: {"accn", "filed_date", "form"}}``.
        """
        if not self._built:
            return None
        row = self._conn.execute(
            "SELECT * FROM financials_annual_known "
            "WHERE ticker = ? AND fiscal_year = ? AND known_from <= ? "
            "ORDER BY known_from DESC LIMIT 1",
            (ticker, int(fiscal_year), self._norm_date(as_of_date)),
        ).fetchone()
        if row is None or not row["present"]:
            return None
        return _period(row)

    def as_of_value(
        self, ticker: str, fiscal_year: int, field: str, as_of_date: AsOfDate
    ) -> Any:
        """A single canonical field's value as known on ``as_of_date`` (or ``None``)."""
        row = self.as_of_annual(ticker, fiscal_year, as_of_date)
        return row.get(field) if row is not None else None

    def history_as_of(
        self, ticker: str, as_of_date: AsOfDate, years_back: Optional[int] = None
    ) -> Dict[int, Dict[str, Any]]:
        """Every fiscal year in the record as of ``as_of_date``, each read as :meth:`as_of_annual` reads it. Keyed
        by fiscal_year, newest first; ``years_back`` trims to the most recent N years the record held then (a year
        that had left it takes no place). ``years_back`` None, 0 or negative trims nothing.
        """
        if not self._built:
            return {}
        cutoff = self._norm_date(as_of_date)
        years = [row["fiscal_year"] for row in self._conn.execute(
            "SELECT DISTINCT fiscal_year FROM financials_annual_known "
            "WHERE ticker = ? AND known_from <= ? ORDER BY fiscal_year DESC",
            (ticker, cutoff),
        )]
        result: Dict[int, Dict[str, Any]] = {}
        for fy in years:
            if years_back is not None and years_back > 0 and len(result) >= years_back:
                break
            period = self.as_of_annual(ticker, fy, cutoff)
            if period is not None:
                result[int(fy)] = period
        return result


def _period(row: sqlite3.Row) -> Dict[str, Any]:
    """A stored snapshot row as the as-of period: its provenance, the canonical figures and ``carried_from`` (each
    figure whose filing, in the row's ``source_filings``, isn't the row's latest one)."""
    period: Dict[str, Any] = {key: row[key] for key in _PROVENANCE}
    period.update({key: row[key] for key in _CANONICAL_COLUMNS})
    filings = json.loads(row["source_filings"]) if row["source_filings"] else {}
    period["carried_from"] = {
        key: {"accn": filing.get("accn"), "filed_date": filing.get("filed"), "form": filing.get("form")}
        for key, filing in sorted(filings.items())
        if filing and key in _FIGURES and period[key] is not None and filing.get("accn") != row["accn"]}
    return period

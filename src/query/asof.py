"""Point-in-time as-of-date queries over the vintaged annual store.

A read-only consumer of the ``financials_annual_vintages`` table (see
``exporters/sqlite_store.py``). For a given date ``D``, each figure of an annual period
resolves to the latest filing made on or before ``D`` that reports it, past a later
filing's narrower concept (see :meth:`AsOfReader.as_of_annual`) — so backtests read
fundamentals with no look-ahead bias. The connection is opened ``mode=ro``; the reader
never mutates data.

A vintage with a NULL ``filed_date`` (unknown filing date) is deliberately invisible to
every as-of query — ``NULL <= D`` is never true — which keeps resolution on the safe side
of no-look-ahead (omit rather than leak unknown-timed data).
"""

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..exporters.sqlite_store import _CANONICAL_COLUMNS, read_only_uri
from ..mappings.canonical import CANONICAL_FIELDS

# Accepts an ISO ``YYYY-MM-DD`` string or a date/datetime (normalized before querying).
AsOfDate = Union[str, date]

#: Each field's tags in the order the parser prefers them; a tag outside it ("derived", "rescaled x1000") ranks last.
_TAG_RANKS: Dict[str, Dict[str, int]] = {f.key: {tag: i for i, tag in enumerate(f.tags)} for f in CANONICAL_FIELDS}
#: A later filing's figure under a lesser-ranked tag than the one the as-of view holds, and under this share of it,
#: is a narrower concept's part, not the year restated (BUG-271).
_NARROWER = 0.5
#: The debt a balance sheet splits one way: read together, from one filing's view of the date (BUG-271).
_DEBT = ("long_term_debt", "short_term_debt", "commercial_paper")
#: The debt figures whose narrower concept sets a filing's view of the debt aside.
_DEBT_LINES = ("long_term_debt", "short_term_debt")


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

        A later filing's figure under a narrower concept doesn't replace an earlier one's
        (BUG-271): one under a tag the field ranks below the held figure's, and under half
        of it, is a part of the figure, not the year restated (EXC's FY2024 10-K tags only a
        $0.198B note as FY2023's long-term debt, beside $41.279B). A restatement under the
        same tag, a better-ranked one, or a lesser one from half the figure up still
        replaces it. The debt is read as one filing's view of it, its long-term and
        short-term debt and paper together (its lines split the debt one way: a total that
        holds the maturities, borrowings combined with them, paper apart): the latest
        filing's, unless its long-term or short-term debt is such a narrower figure. A
        vintage stored without its tags is never set aside.

        Returns a plain dict (all canonical line items + provenance metadata), or ``None``
        if the year had not been filed yet as of that date. The provenance (``accn``,
        ``filed_date``, ``form``, ``period_end``) is the latest filing's, and
        ``carried_from`` names the filing of each figure read from an earlier one:
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
        period.pop("source_tags", None)
        figures = [key for key in _CANONICAL_COLUMNS if key in period]
        filings = [dict(row) for row in reversed(rows)]  # in filing order
        tags = [_tags(f.pop("source_tags", None)) for f in filings]
        chosen = {key: _held(filings, tags, key) for key in figures if key not in _DEBT}
        day = _debt_view(filings, tags)
        for key in _DEBT:
            if key in period:
                chosen[key] = _latest(filings, key, day)
        latest = len(filings) - 1
        carried: Dict[str, Dict[str, Any]] = {}
        for key, i in chosen.items():
            period[key] = None if i is None else filings[i][key]
            if i is not None and i != latest:
                row = filings[i]
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
        """Every fiscal year known as of ``as_of_date``, each resolved as
        :meth:`as_of_annual` resolves it. Keyed by fiscal_year, newest first; ``years_back``
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


def _tags(stored: Optional[str]) -> Optional[Dict[str, str]]:
    """A vintage's ``source_tags``: ``{field: tag}``, or None for a vintage stored without them."""
    return json.loads(stored) if stored else None


def _narrower(key: str, filing: Dict[str, Any], tags: Optional[Dict[str, str]],
              held: Dict[str, Any], held_tags: Optional[Dict[str, str]]) -> bool:
    """Whether *filing*'s figure for *key* is a narrower concept's part of the one *held* (BUG-271): under a tag the
    field ranks below the held one's, and under half of it. Unknowable for a vintage stored without its tags."""
    if tags is None or held_tags is None:
        return False
    ranks = _TAG_RANKS.get(key, {})
    last = len(ranks)
    rank, held_rank = ranks.get(tags.get(key, ""), last), ranks.get(held_tags.get(key, ""), last)
    return rank > held_rank and abs(filing[key]) < _NARROWER * abs(held[key])


def _held(filings: List[Dict[str, Any]], tags: List[Optional[Dict[str, str]]], key: str) -> Optional[int]:
    """The filing (an index into *filings*, in filing order) whose figure for *key* the as-of view reads: the latest
    that reports it, past each later one's narrower figure."""
    held: Optional[int] = None
    for i, filing in enumerate(filings):
        if filing[key] is None:
            continue
        if held is not None and _narrower(key, filing, tags[i], filings[held], tags[held]):
            continue
        held = i
    return held


def _latest(filings: List[Dict[str, Any]], key: str, day: Optional[int]) -> Optional[int]:
    """The latest filing at or before *day* (an index, in filing order) that reports *key*."""
    for i in range(-1 if day is None else day, -1, -1):
        if filings[i][key] is not None:
            return i
    return None


def _narrower_than_view(filings: List[Dict[str, Any]], tags: List[Optional[Dict[str, str]]], i: int, day: int,
                        key: str) -> bool:
    """Whether filing *i*'s figure for *key* is narrower than the one the view as of filing *day* holds."""
    held = _latest(filings, key, day)
    return filings[i][key] is not None and held is not None and _narrower(key, filings[i], tags[i], filings[held],
                                                                           tags[held])


def _debt_view(filings: List[Dict[str, Any]], tags: List[Optional[Dict[str, str]]]) -> Optional[int]:
    """The filing whose view of the date's debt the as-of view reads: the latest that reports any of it, past each
    later one whose long-term or short-term debt is narrower than the view held then."""
    day: Optional[int] = None
    for i, filing in enumerate(filings):
        if all(filing[key] is None for key in _DEBT):
            continue
        if day is not None and any(_narrower_than_view(filings, tags, i, day, key) for key in _DEBT_LINES):
            continue
        day = i
    return day

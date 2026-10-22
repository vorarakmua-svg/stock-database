"""A guru's quarters (Milestone 3): the positions of each period of report, from the 13F filings that stand, and the
changes from the quarter before (docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

**The filings a quarter stands on.** For each filer and period, of its filings that pass the checksum, ordered by
(filed, accession): the base is the latest original 13F-HR or RESTATEMENT amendment (a 13F-HR/A without an amendment
type counts as one), and each NEW HOLDINGS amendment filed after the base adds its rows; an amendment filed before the
base is superseded by it. An amendment's row that repeats one of the base's (the same CUSIP, put/call, shares and
value), each base row matching one, is a duplicate and left out: First Eagle's Q2 2026 13F-HR/A
(0001325447-26-000033), labelled NEW HOLDINGS, is a complete report, its 616 rows the base's 614 and Exxon Mobil's
two, so the quarter is $60.75B, not twice that; Berkshire's Q1 2025 one adds 202 Lennar class B shares to the
152,572 its base reports, a row of its own. A filer whose original and restatements all fail the checksum (or that
has none) leaves the quarter incomplete, and its rows are left out. The guru's quarter is the union of its filers'
(Pershing Square Inc. and Pershing Square Capital Management are one guru).

**Positions** are keyed (security, put/call): the security is the CUSIP's ticker in ``cusip_map`` when it has one,
else the CUSIP, as one security can take two CUSIPs within the window (Honeywell's 438516106 and 438516205 across
its 2026 change, Exxon Mobil's 30231G102 and ExxonMobil Holdings' 30233Q108, a filer's 200021014 for Allstate's
020002101). Shares and principal amounts are under None, options under 'Put' or 'Call', apart from the shares. Rows
of one key are summed (shares, value), as one CUSIP may take several rows (other managers, discretion), and the
position keeps the CUSIPs it covers and the first row's issuer, class and share type. Each position's weight is its
value over the quarter's total.

**Changes** compare a quarter with the one before it only (``previous_period``): per key of either, new, sold out,
added, reduced or unchanged by the shares, the shares before times the share splits of the security's ticker dated
after the earlier period's end and on or before the later one (``split_factor``). A security without a ticker has no
split data (``split_events`` covers our universe), so its shares aren't adjusted.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from ..valuation.inputs import _simple_ratio

#: A position's key: its security (the CUSIP's ticker, else the CUSIP) and put/call (None for shares and principal).
Key = Tuple[Optional[str], Optional[str]]
#: The amendment type whose rows add to the base's.
NEW_HOLDINGS = "NEW HOLDINGS"
ACTIONS = ("new", "added", "reduced", "sold_out", "unchanged")


@dataclass(frozen=True)
class Filing:
    """A stored 13F filing (``guru_filings``), as the quarter's rules read it."""

    accession: str
    filer_cik: str
    form: Optional[str]
    period: Optional[str]
    filed: Optional[str]
    amendment_type: Optional[str]
    checksum_ok: bool

    @property
    def adds(self) -> bool:
        """A NEW HOLDINGS amendment: its rows add to the base's. Every other filing can be the base."""
        return (self.amendment_type or "").upper() == NEW_HOLDINGS


@dataclass(frozen=True)
class Entry:
    """A stored entry of a filing's information table (``guru_holdings``)."""

    accession: str
    row: int
    cusip: Optional[str]
    issuer: Optional[str]
    title_of_class: Optional[str]
    value_usd: Optional[float]
    shares: Optional[float]
    share_type: Optional[str]
    put_call: Optional[str]


@dataclass
class Position:
    """A security held at a period's end: its ticker (None without one), the CUSIPs its rows gave, the first row's
    issuer, class and share type, the summed shares and value in dollars, and its weight in the quarter."""

    security: Optional[str]
    ticker: Optional[str]
    put_call: Optional[str]
    issuer: Optional[str]
    title_of_class: Optional[str]
    share_type: Optional[str]
    cusips: Tuple[str, ...] = ()
    shares: float = 0.0
    value_usd: float = 0.0
    weight: Optional[float] = None

    @property
    def key(self) -> Key:
        return (self.security, self.put_call)


@dataclass
class Quarter:
    """A guru's quarter: its positions by key, in the order the filings give them, their total value, whether every
    filer's report stands, and the filings it stands on in (filed, accession) order."""

    period: str
    positions: Dict[Key, Position]
    total_value: float
    complete: bool
    accessions: Tuple[str, ...]


@dataclass
class Change:
    """A key's change from the quarter before: its action, the shares and values before (as filed) and after, the
    split factor between, and the change in shares against the split-adjusted shares before, as a fraction (0.25 for
    a quarter more; None for a new or sold-out position, or one from no shares)."""

    security: Optional[str]
    ticker: Optional[str]
    put_call: Optional[str]
    cusips: Tuple[str, ...]
    issuer: Optional[str]
    title_of_class: Optional[str]
    share_type: Optional[str]
    action: str
    shares_before: Optional[float]
    shares_after: Optional[float]
    value_before: Optional[float]
    value_after: Optional[float]
    split_factor: float
    change_pct: Optional[float]

    @property
    def shares_before_adjusted(self) -> Optional[float]:
        """The shares before on the later quarter's split basis."""
        return None if self.shares_before is None else self.shares_before * self.split_factor


@dataclass
class GuruQuarters:
    """A guru's quarters by period, oldest first, and each one's changes from the quarter before (None without it)."""

    guru_cik: str
    quarters: Dict[str, Quarter] = field(default_factory=dict)
    changes: Dict[str, Optional[List[Change]]] = field(default_factory=dict)


def previous_period(period: str) -> str:
    """The period of report before *period*: the last day of the calendar quarter before its own."""
    day = date.fromisoformat(period[:10])
    return (date(day.year, (day.month - 1) // 3 * 3 + 1, 1) - timedelta(days=1)).isoformat()


def _order(filing: Filing) -> Tuple[str, str]:
    return (filing.filed or "", filing.accession)


def _repeats(entry: Entry) -> Tuple[Optional[str], Optional[str], Optional[float], Optional[float]]:
    """What makes a NEW HOLDINGS amendment's row the same as one of its base's: CUSIP, put/call, shares and value."""
    return (entry.cusip, entry.put_call, entry.shares, entry.value_usd)


def standing_filings(filings: Iterable[Filing]) -> Optional[List[Filing]]:
    """The filings one filer's report for a period stands on, in (filed, accession) order: the latest original or
    restatement that passes the checksum, then each NEW HOLDINGS amendment that passes it, filed after that base.
    None when no original or restatement passes."""
    passing = sorted((f for f in filings if f.checksum_ok), key=_order)
    bases = [i for i, f in enumerate(passing) if not f.adds]
    if not bases:
        return None
    base = bases[-1]
    return [passing[base]] + [f for f in passing[base + 1:] if f.adds]


def build_quarter(period: str, filings: Iterable[Filing], entries: Iterable[Entry],
                  tickers: Optional[Mapping[str, str]] = None) -> Quarter:
    """The guru's quarter for *period* from its filings (any filers and periods) and their entries; *tickers* maps a
    CUSIP to its ticker (``cusip_map``). A NEW HOLDINGS amendment's rows that repeat its base's are left out, one for
    one (``_repeats``)."""
    tickers = tickers or {}
    by_filer: Dict[str, List[Filing]] = {}
    for filing in filings:
        if filing.period == period:
            by_filer.setdefault(filing.filer_cik, []).append(filing)
    used: List[Tuple[Filing, Optional[Filing]]] = []  # each filing and, for a NEW HOLDINGS amendment, its base
    complete = True
    for filer in sorted(by_filer):
        standing = standing_filings(by_filer[filer])
        if standing is None:
            complete = False
        else:
            used += [(standing[0], None)] + [(amendment, standing[0]) for amendment in standing[1:]]
    used.sort(key=lambda pair: _order(pair[0]))
    rows: Dict[str, List[Entry]] = {f.accession: [] for f, _ in used}
    for entry in entries:
        if entry.accession in rows:
            rows[entry.accession].append(entry)
    for listed in rows.values():
        listed.sort(key=lambda e: e.row)
    positions: Dict[Key, Position] = {}
    for filing, base in used:
        repeated = Counter(_repeats(e) for e in rows[base.accession]) if base else Counter()
        for entry in rows[filing.accession]:
            if repeated[_repeats(entry)]:  # one of the base's rows, filed again
                repeated[_repeats(entry)] -= 1
                continue
            ticker = tickers.get(entry.cusip) if entry.cusip else None
            key = (ticker or entry.cusip, entry.put_call)
            position = positions.get(key)
            if position is None:
                position = positions[key] = Position(
                    security=key[0], ticker=ticker, put_call=entry.put_call, issuer=entry.issuer,
                    title_of_class=entry.title_of_class, share_type=entry.share_type)
            if entry.cusip and entry.cusip not in position.cusips:
                position.cusips += (entry.cusip,)
            position.shares += entry.shares or 0.0
            position.value_usd += entry.value_usd or 0.0
    total = sum(p.value_usd for p in positions.values())
    for position in positions.values():
        position.weight = position.value_usd / total if total else None
    return Quarter(period=period, positions=positions, total_value=total, complete=complete,
                   accessions=tuple(f.accession for f, _ in used))


def build_quarters(filings: Iterable[Filing], entries: Iterable[Entry],
                   tickers: Optional[Mapping[str, str]] = None) -> Dict[str, Quarter]:
    """Every period of the guru's filings, oldest first (``build_quarter``); a filing without a period is in none."""
    filings, entries = list(filings), list(entries)
    periods = sorted({f.period for f in filings if f.period})
    return {period: build_quarter(period, filings, entries, tickers) for period in periods}


def split_factor(splits: Iterable[Tuple[str, Optional[float]]], after: str, through: str) -> float:
    """The product of the share splits (``split_events``' ratios, new shares per old) dated after *after* and on or
    before *through*. Only share splits count, a reverse one too: a simple ratio (``valuation.inputs._simple_ratio``),
    not the stock dividends (SCCO's 1.01) and spin-off price factors (HON's 1.061 for Solstice) Yahoo's split
    history also holds, which don't change a holder's count as their ratio says."""
    factor = 1.0
    for day, ratio in splits:
        if ratio and ratio > 0 and _simple_ratio(ratio) and after < str(day)[:10] <= through:
            factor *= ratio
    return factor


def quarter_changes(current: Quarter, previous: Optional[Quarter],
                    splits: Optional[Mapping[str, Sequence[Tuple[str, Optional[float]]]]] = None,
                    ) -> Optional[List[Change]]:
    """Each key's change from *previous*, ordered by key; None unless *previous* is the quarter before *current*.
    *splits* maps a ticker to its split events (date, ratio); the shares before are adjusted by the splits of the
    key's ticker between the two periods (``split_factor``), and a security without a ticker isn't adjusted."""
    if previous is None or previous.period != previous_period(current.period):
        return None
    splits = splits or {}
    changes = []
    for key in sorted(set(current.positions) | set(previous.positions), key=lambda k: (k[0] or "", k[1] or "")):
        now, before = current.positions.get(key), previous.positions.get(key)
        held = now or before
        assert held is not None  # the key is one of theirs
        ticker = held.ticker  # one key, one ticker: the security is the ticker when there's one
        factor = split_factor(splits.get(ticker, ()), previous.period, current.period) if ticker else 1.0
        change_pct: Optional[float] = None
        if before is None:
            action, factor = "new", 1.0
        elif now is None:
            action = "sold_out"
        else:
            adjusted = before.shares * factor
            action = "added" if now.shares > adjusted else "reduced" if now.shares < adjusted else "unchanged"
            change_pct = (now.shares - adjusted) / adjusted if adjusted else None
        changes.append(Change(
            security=key[0], ticker=ticker, put_call=key[1],
            cusips=tuple(dict.fromkeys((now.cusips if now else ()) + (before.cusips if before else ()))),
            issuer=held.issuer, title_of_class=held.title_of_class, share_type=held.share_type, action=action,
            shares_before=before.shares if before else None, shares_after=now.shares if now else None,
            value_before=before.value_usd if before else None, value_after=now.value_usd if now else None,
            split_factor=factor, change_pct=change_pct))
    return changes


def _tables(conn: sqlite3.Connection) -> Set[str]:
    return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def load_tickers(conn: sqlite3.Connection) -> Dict[str, str]:
    """Each mapped CUSIP's ticker (``cusip_map``); none for a database without the map."""
    if "cusip_map" not in _tables(conn):
        return {}
    return {cusip: ticker for cusip, ticker in conn.execute(
        "SELECT cusip, ticker FROM cusip_map WHERE ticker IS NOT NULL")}


def load_splits(conn: sqlite3.Connection, tickers: Iterable[str]) -> Dict[str, List[Tuple[str, Optional[float]]]]:
    """The split events (date, ratio) of each of *tickers* that has some (``split_events``)."""
    wanted = set(tickers)
    if not wanted or "split_events" not in _tables(conn):
        return {}
    splits: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    for ticker, day, ratio in conn.execute("SELECT ticker, date, ratio FROM split_events ORDER BY ticker, date"):
        if ticker in wanted:
            splits.setdefault(ticker, []).append((str(day)[:10], ratio))
    return splits


def load_guru(conn: sqlite3.Connection, guru_cik: str) -> GuruQuarters:
    """One guru's quarters and their changes, from its stored filings and entries (a read-only connection will do),
    keyed by the ``cusip_map`` tickers and adjusted by the ``split_events``. Empty for a database without the
    gurus' tables."""
    tables = _tables(conn)
    if not {"guru_filings", "guru_holdings"} <= tables:
        return GuruQuarters(guru_cik)
    filings = [Filing(accession, filer, form, period, filed, amendment, bool(ok))
               for accession, filer, form, period, filed, amendment, ok in conn.execute(
                   "SELECT accession, filer_cik, form, period, filed, amendment_type, checksum_ok FROM guru_filings "
                   "WHERE guru_cik = ?", (guru_cik,))]
    entries = [Entry(*row) for row in conn.execute(
        "SELECT accession, row, cusip, issuer, title_of_class, value_usd, shares, share_type, put_call "
        "FROM guru_holdings WHERE guru_cik = ?", (guru_cik,))]
    tickers = load_tickers(conn)
    quarters = build_quarters(filings, entries, tickers)
    splits = load_splits(conn, {p.ticker for q in quarters.values() for p in q.positions.values() if p.ticker})
    changes = {period: quarter_changes(quarter, quarters.get(previous_period(period)), splits)
               for period, quarter in quarters.items()}
    return GuruQuarters(guru_cik, quarters, changes)

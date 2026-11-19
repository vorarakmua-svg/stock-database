"""The annual record as it was known on each filing date (as-of snapshots).

The as-of view of fiscal year Y on day D is the annual record ``build_annual_record`` builds from the facts filed on
or before D (``facts_known_by``, the only look-ahead gate). The record can change only on a day a fact was filed, so
replaying it on every filing date gives the view on any day: the latest snapshot on or before it. A year's row is
kept only when it differs from its previous one, and a year that leaves the record gets a row that says so
(``present`` False).

Each filing's own view of its years (its vintage, "as filed") is read from the facts known on its day too, so no
vintage's set-asides read a later filing (BUG-120), and a view of a year not over on its filing's day is dropped as
the record drops such a year (BUG-113).

A day whose record is provably the day before's (``_record_unchanged``: no fact the record's parse reads was filed
on it, and no year left out as not yet ended has ended by it) isn't built: it would add no row.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from ..mappings.canonical import CANONICAL_BY_KEY
from .annual_record import PARSED_TAXONOMY, build_annual_record
from .derived_fields import apply_derivations
from .known_facts import facts_known_by, filing_dates, undated_facts
from .share_scale import normalize_share_scale
from .xbrl_parser import _ANNUAL_FORMS, XBRLParser

#: What makes a year's row differ from its previous one, beside its canonical values.
_COMPARED = ("source_tags", "source_filings", "period_end", "calendar_year", "frame")
#: Whether ``build_snapshots`` skips the days ``_record_unchanged`` proves change nothing (False: the record is built
#: on every filing day, the replay the tests compare the skipping one with).
_SKIP_UNCHANGED_DAYS = True
#: How ``build_annual_record`` says a cleaning failed (the record kept uncleaned).
_CLEANING_FAILED = "Data cleaning: "


@dataclass
class SnapshotSet:
    """A filer's snapshots: ``rows`` in the order known (each fiscal year's rows by ``known_from``), each filing's
    views (``{fiscal_year: {accn: period}}``, as ``extract_annual_vintages`` gives them) and the warnings."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    vintages: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _same(a: Any, b: Any) -> bool:
    """Whether two snapshot values are one: equal, NaN beside NaN, dicts key by key."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return bool(a == b)


def _state(period: Dict[str, Any]) -> Dict[str, Any]:
    """What a year's row holds of its period: the canonical values and what ``_COMPARED`` names."""
    state = {key: value for key, value in period.items() if key in CANONICAL_BY_KEY}
    state["source_tags"] = dict(period.get("_source_tags") or {})
    state["source_filings"] = {k: dict(v) for k, v in (period.get("_source_filings") or {}).items()}
    for key in ("period_end", "calendar_year", "frame"):
        state[key] = period.get(key)
    return state


def _latest_filing(source_filings: Dict[str, Dict[str, str]]) -> Dict[str, Optional[str]]:
    """The latest filing (by filing day, then accession) among a period's figures: ``accn``, ``filed_date``,
    ``form`` (None each with none)."""
    filings = [f for f in source_filings.values() if f]
    if not filings:
        return {"accn": None, "filed_date": None, "form": None}
    latest = max(filings, key=lambda f: (f.get("filed") or "", f.get("accn") or ""))
    return {"accn": latest.get("accn"), "filed_date": latest.get("filed"), "form": latest.get("form")}


def _filings_by_day(facts: Dict[str, Any]) -> Tuple[Dict[str, str], Set[str]]:
    """Each filing day's latest accession (by accession) and the days an annual report filed facts on."""
    accns: Dict[str, str] = {}
    annual: Set[str] = set()
    for tags in (facts.get("facts") or {}).values():
        for node in tags.values():
            for entries in (node.get("units") or {}).values():
                for entry in entries:
                    day = entry.get("filed")
                    if not day:
                        continue
                    accn = entry.get("accn") or ""
                    if accn > accns.get(day, ""):
                        accns[day] = accn
                    if entry.get("form", "") in _ANNUAL_FORMS:
                        annual.add(day)
    return accns, annual


def _record_unchanged(day: str, parsed_days: Set[str], unended: List[str]) -> bool:
    """Whether the record known on filing day *day* is, unbuilt, the one last built (on day b), so building it would
    add no row.

    ``build_annual_record`` on ``facts_known_by(facts, day)`` is a function of two things of those facts (the parser
    holds no state):

    1. The entries of the parse's taxonomy (``PARSED_TAXONOMY``: ``extract_annual_financials`` reads only them, through
       ``_filer_facts``). None was filed on *day* (``parsed_days``: that taxonomy's filing days) nor, each having passed
       this test, on a filing day since b, so they are b's, in b's order (``facts_known_by`` keeps it): the parse gives
       b's years.
    2. The latest filed day of every taxonomy (``_last_filed``), which is *day*: a filing day of the facts, all known
       on it. It only decides which of the parse's years have ended (end on or before it): a year with no end or one
       kept on b is kept on *day*, and a year b left out (``unended``: its end after b) is kept iff its end is by *day*.

    With neither moved, the kept years are b's and the cleaning, a function of them alone, gives b's record."""
    return day not in parsed_days and all(end > day for end in unended)


def _vintages_of(parser: XBRLParser, known: Dict[str, Any], day: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """The views of the filings of *day*, from the facts *known* that day, normalised as the run does: a year's
    share counts over all its views filed by *day* (``normalize_share_scale`` without neighbours still gives a count
    no EPS checks the scale its year's corrected views agree on, so a view takes it from earlier filings' views and
    its own day's, never a later one's), then derivations. A view of a year not over on *day* is none (BUG-113).
    The views are this day's extraction's own dicts, so normalising them leaves an earlier day's views as they were."""
    views: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for fy, by_accn in parser.extract_annual_vintages(known, keep_year_end=True).items():
        ends = {accn: period.pop("_year_end", None) or period.get("period_end") for accn, period in by_accn.items()}
        kept = [accn for accn, period in by_accn.items()
                if period.get("filed_date") == day and (not ends[accn] or ends[accn] <= day)]
        if not kept:
            continue
        # No neighbouring year's views: a vintage's view takes no scale from a later filing (the gate).
        normalize_share_scale(by_accn, neighbours=False)
        for accn in kept:
            apply_derivations(by_accn[accn])
        views[fy] = {accn: by_accn[accn] for accn in kept}
    return views


def build_snapshots(parser: XBRLParser, facts: Dict[str, Any], *, ticker: str,
                    years_back: Optional[int]) -> SnapshotSet:
    """*ticker*'s annual record as known on each day its companyfacts *facts* hold a filing of, and each filing's
    views of its years.

    A row is ``fiscal_year``, ``known_from`` (the day), ``known_accn`` (the latest accession filed that day) and
    ``present``; a present row also holds the period's canonical values, ``source_tags``, ``source_filings``,
    ``period_end``, ``calendar_year``, ``frame``, and ``accn``/``filed_date``/``form`` of the latest filing among its
    figures. A year gets a row on a day its row differs from the one before (a float equal under ``==``, or NaN
    beside NaN, is the same), and a ``present`` False row the day it leaves the record. With *years_back*, the rows
    and views of the latest *years_back* years of the last snapshot, replayed on every filing all the same. Each
    day's record is built on all its years and trimmed after, where ``build_annual_record`` trims a *years_back*
    record before cleaning it: share scale reads a year's neighbours, so a *years_back* run's record can differ from
    its latest snapshot of a year at the window's edge (a full run's agree, and the refresh collects full runs).

    A day whose record's cleaning failed (``build_annual_record`` keeps it uncleaned and says "Data cleaning: ...")
    is stored uncleaned; the set's warnings say so once, with the count of such days and the first failure.
    """
    out = SnapshotSet()
    undated = undated_facts(facts)
    if undated:
        out.warnings.append(
            f"SEC EDGAR: {ticker}'s companyfacts hold {undated} fact(s) with no filing date: no as-of snapshot "
            "reads them, as none can be shown to have been public on any day")
    accns, annual_days = _filings_by_day(facts)
    parsed_days = set(filing_dates(facts, PARSED_TAXONOMY))
    unended: Optional[List[str]] = None  # the last built record's (None: none built yet)
    last: Dict[int, Dict[str, Any]] = {}  # fiscal year -> its latest state (None: left the record)
    present: Dict[int, bool] = {}
    vintages: Dict[str, Dict[str, Dict[str, Any]]] = {}
    uncleaned: List[Tuple[str, str]] = []  # (day, error) of each day whose record's cleaning failed
    for day in filing_dates(facts):
        build = not (_SKIP_UNCHANGED_DAYS and unended is not None and _record_unchanged(day, parsed_days, unended))
        if not build and day not in annual_days:
            continue
        known = facts_known_by(facts, day)
        if build:
            record = build_annual_record(parser, known, ticker=ticker, years_back=None)
            unended = record.unended
            failed = [w for w in record.warnings[record.repairs:] if w.startswith(_CLEANING_FAILED)]
            if failed:
                uncleaned.append((day, failed[0][len(_CLEANING_FAILED):]))
            now = {int(fy): _state(period) for fy, period in record.periods.items()}
            for fy in sorted(set(now) | {fy for fy, held in present.items() if held}):
                head = {"fiscal_year": fy, "known_from": day, "known_accn": accns.get(day) or None}
                if fy not in now:
                    out.rows.append({**head, "present": False})
                    present[fy] = False
                    continue
                state = now[fy]
                if present.get(fy) and _same(state, last[fy]):
                    continue
                out.rows.append({**head, "present": True, **state, **_latest_filing(state["source_filings"])})
                last[fy], present[fy] = state, True
        if day in annual_days:
            for fy, views in _vintages_of(parser, known, day).items():
                vintages.setdefault(fy, {}).update(views)
    if uncleaned:
        out.warnings.append(
            f"SEC EDGAR: {ticker}'s as-of snapshots hold the record uncleaned on {len(uncleaned)} filing day(s), its "
            f"cleaning having failed (first on {uncleaned[0][0]}: {uncleaned[0][1]})")
    out.vintages = {fy: vintages[fy] for fy in sorted(vintages, reverse=True)}
    if years_back:
        kept = sorted((fy for fy, held in present.items() if held), reverse=True)[:years_back]
        out.rows = [row for row in out.rows if row["fiscal_year"] in kept]
        out.vintages = {fy: views for fy, views in out.vintages.items() if int(fy) in kept}
    return out

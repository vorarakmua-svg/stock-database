"""Trailing-twelve-month (TTM) series from discrete quarterly financials.

For each quarter-end with four consecutive discrete quarters available, sum the flow
concepts (income, cash flow) over the trailing 4 quarters and pair them with the
balance sheet as of that quarter-end. TTM smooths seasonality and gives a granular,
up-to-date alternative to annual figures.
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ..mappings.canonical import CANONICAL_FIELDS, DURATION, INSTANT

_FLOW_KEYS = [f.key for f in CANONICAL_FIELDS if f.kind == DURATION]
_INSTANT_KEYS = [f.key for f in CANONICAL_FIELDS if f.kind == INSTANT]
#: Weighted-average share counts: a trailing year's is the four quarters' average, not their sum (BUG-074).
_AVERAGE_KEYS = frozenset(f.key for f in CANONICAL_FIELDS if f.kind == DURATION and f.xbrl_unit == "shares")
#: Per-share figures, which a share split restates like the counts (BUG-082).
_PER_SHARE_KEYS = frozenset(f.key for f in CANONICAL_FIELDS if f.kind == DURATION and f.xbrl_unit == "USD/shares")
#: The flows in dollars: a period end holding none of them has no quarter's results of its own (BUG-249).
_DOLLAR_FLOW_KEYS = [k for k in _FLOW_KEYS if k not in _AVERAGE_KEYS and k not in _PER_SHARE_KEYS]

#: Consecutive quarter-ends lie 12 to 17 weeks apart: a 12-12-12-16-week year (COST, PEP) steps 112 or 119 days
#: into its fourth quarter (BUG-095); a missing quarter leaves 160 or more.
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 120


def _parse(d: str) -> Optional[date]:
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _consecutive(ends: List[str]) -> bool:
    """True if each adjacent pair of period-ends is ~one quarter apart (no gaps)."""
    for a, b in zip(ends, ends[1:]):
        da, db = _parse(a), _parse(b)
        if da is None or db is None:
            return False
        if not (_QUARTER_MIN_DAYS <= (db - da).days <= _QUARTER_MAX_DAYS):
            return False
    return True


def _inside_a_quarter(quarterly: Dict[str, Dict[str, Any]], ends: List[str]) -> List[str]:
    """The period ends that lie inside a quarter, which the trailing years step over (BUG-249).

    Such an end holds no flow in dollars and lies between two ends that do and are one quarter apart: an
    adoption-day opening balance sheet (AES's 2018-01-01 retained earnings, ABT's 2018-01-05 long-term
    debt), a count beside a split (GL's 2011-07-01), a quarter a 10-K re-dates with only its share counts
    (ADI's 2011-02-04 beside 2011-01-29, BUG-096). An end without flows anywhere else is a quarter whose
    flows are missing (CB's 2010-12-31) or one in place of a missing quarter, and stays.
    """
    dollars = [e for e in ends if any(isinstance(quarterly[e].get(k), (int, float)) for k in _DOLLAR_FLOW_KEYS)]
    inside: List[str] = []
    for before, after in zip(dollars, dollars[1:]):
        if _consecutive([before, after]):
            inside.extend(e for e in ends if before < e < after)
    return inside


def compute_ttm(quarterly: Dict[str, Dict[str, Any]],
                split_levels: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, Dict[str, Any]]:
    """Build a trailing-twelve-month series from discrete quarterly periods.

    Args:
        quarterly: ``{period_end: {canonical_field: value, ...}}`` of *discrete*
            quarters (as produced by ``XBRLParser.extract_quarterly_financials``).
        split_levels: each quarter's split basis per field (``{period_end: {field:
            level}}``, the parser's ``_split_levels``). A quarter's per-share figures and
            counts are on its own year's basis, so a trailing year's go onto its latest
            quarter's before they add up (BUG-082).

    Returns:
        ``{period_end: {flow fields summed over trailing 4 quarters (weighted-average
        share counts averaged), balance-sheet fields as-of, calendar_year,
        calendar_quarter, fiscal_year, ...}}`` for every quarter-end that has four
        consecutive quarters behind it. A period end inside a quarter is no quarter of
        its own: the windows step over it (``_inside_a_quarter``).
    """
    ends = sorted(quarterly.keys())
    inside = set(_inside_a_quarter(quarterly, ends))
    ends = [e for e in ends if e not in inside]
    out: Dict[str, Dict[str, Any]] = {}

    for i in range(3, len(ends)):
        window = ends[i - 3:i + 1]
        if not _consecutive(window):
            continue
        end = window[-1]
        latest = quarterly[end]
        row: Dict[str, Any] = {
            "period_end": end,
            "calendar_year": latest.get("calendar_year"),
            "calendar_quarter": latest.get("calendar_quarter"),
            "fiscal_year": latest.get("fiscal_year"),
        }

        # Flow concepts: sum across the trailing 4 quarters (only when all present);
        # share counts: their average.
        for key in _FLOW_KEYS:
            vals = [quarterly[w].get(key) for w in window]
            if all(isinstance(v, (int, float)) for v in vals):
                if split_levels and (key in _AVERAGE_KEYS or key in _PER_SHARE_KEYS):
                    last = split_levels.get(end, {}).get(key, 1.0)
                    moved = [last / split_levels.get(w, {}).get(key, 1.0) for w in window]
                    vals = [float(v) * m if key in _AVERAGE_KEYS else float(v) / m
                            for v, m in zip(vals, moved) if isinstance(v, (int, float))]
                total = sum(v for v in vals if isinstance(v, (int, float)))
                row[key] = total / len(vals) if key in _AVERAGE_KEYS else total

        # Balance-sheet concepts: as of the latest quarter-end.
        for key in _INSTANT_KEYS:
            v = latest.get(key)
            if isinstance(v, (int, float)):
                row[key] = v

        out[end] = row

    return out

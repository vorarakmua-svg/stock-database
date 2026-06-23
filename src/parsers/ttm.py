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

_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100


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


def compute_ttm(quarterly: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build a trailing-twelve-month series from discrete quarterly periods.

    Args:
        quarterly: ``{period_end: {canonical_field: value, ...}}`` of *discrete*
            quarters (as produced by ``XBRLParser.extract_quarterly_financials``).

    Returns:
        ``{period_end: {flow fields summed over trailing 4 quarters, balance-sheet
        fields as-of, calendar_year, calendar_quarter, fiscal_year, ...}}`` for every
        quarter-end that has four consecutive quarters behind it.
    """
    ends = sorted(quarterly.keys())
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

        # Flow concepts: sum across the trailing 4 quarters (only when all present).
        for key in _FLOW_KEYS:
            vals = [quarterly[w].get(key) for w in window]
            if all(isinstance(v, (int, float)) for v in vals):
                row[key] = sum(vals)

        # Balance-sheet concepts: as of the latest quarter-end.
        for key in _INSTANT_KEYS:
            v = latest.get(key)
            if isinstance(v, (int, float)):
                row[key] = v

        out[end] = row

    return out

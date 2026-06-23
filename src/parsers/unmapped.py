"""Detect material us-gaap facts whose tag is not in the canonical registry.

The SEC taxonomy evolves and filers tag identical items differently, so a tag we
don't yet map would otherwise make data silently disappear. This module finds such
*unmapped* material facts so they can be persisted (never lost) and surfaced as a
worklist of tags to promote into the registry.

Pure function over already-fetched ``companyfacts`` — no network, fully testable.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..mappings.canonical import ALL_MAPPED_TAGS

# Forms that carry statement facts; ignore other filing types (8-K, etc.).
_STATEMENT_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}

# Defaults bounding the long tail: only material, recent USD facts.
_DEFAULT_MIN_VALUE = 1_000_000.0
_DEFAULT_LOOKBACK_YEARS = 3


def detect_unmapped(
    facts: Dict[str, Any],
    mapped_tags: frozenset = ALL_MAPPED_TAGS,
    min_value: float = _DEFAULT_MIN_VALUE,
    since_year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Find material us-gaap tags not covered by the canonical registry.

    Args:
        facts: Raw SEC ``companyfacts`` payload.
        mapped_tags: Tags already mapped (defaults to the whole registry).
        min_value: Minimum absolute USD value to be considered material.
        since_year: Only consider facts whose period end year >= this. Defaults to
            the current year minus :data:`_DEFAULT_LOOKBACK_YEARS`.

    Returns:
        One record per unmapped tag (its most recent material fact):
        ``{tag, label, unit, period_end, value, form}``, sorted by ``|value|`` desc.
    """
    if since_year is None:
        since_year = datetime.now().year - _DEFAULT_LOOKBACK_YEARS

    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    records: List[Dict[str, Any]] = []

    for tag, tag_data in us_gaap.items():
        if tag in mapped_tags:
            continue
        usd = tag_data.get("units", {}).get("USD", [])
        best: Optional[Dict[str, Any]] = None
        for entry in usd:
            if entry.get("form") not in _STATEMENT_FORMS:
                continue
            val = entry.get("val")
            end = entry.get("end")
            if not isinstance(val, (int, float)) or abs(val) < min_value or not end:
                continue
            try:
                end_year = int(str(end)[:4])
            except ValueError:
                continue
            if end_year < since_year:
                continue
            # Keep the most recent material fact for this tag.
            if best is None or str(end) > str(best["end"]):
                best = {"val": val, "end": end, "form": entry.get("form")}
        if best is not None:
            records.append({
                "tag": tag,
                "label": tag_data.get("label", tag),
                "unit": "USD",
                "period_end": best["end"],
                "value": float(best["val"]),
                "form": best["form"],
            })

    records.sort(key=lambda r: abs(r["value"]), reverse=True)
    return records

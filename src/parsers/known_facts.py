"""The look-ahead gate: which companyfacts were public on a given day.

This is the only place that filters facts by filing date. A fact is known on
day D when its ``filed`` date is on or before D; a fact with no ``filed`` date
is never known (it cannot be shown to have been public).
"""

from typing import Any, Dict, List


def _entries(facts: Dict[str, Any]):
    for tags in (facts.get("facts") or {}).values():
        for node in tags.values():
            for entries in (node.get("units") or {}).values():
                for entry in entries:
                    yield entry


def facts_known_by(facts: Dict[str, Any], day: str) -> Dict[str, Any]:
    """Return companyfacts holding only entries filed on or before ``day`` (ISO date).

    Every taxonomy is gated; empty units, tags and taxonomies are dropped.
    Other top-level keys and each tag's non-``units`` keys are kept. The input
    is not mutated.
    """
    out: Dict[str, Any] = {k: v for k, v in facts.items() if k != "facts"}
    kept: Dict[str, Any] = {}
    for taxonomy, tags in (facts.get("facts") or {}).items():
        kept_tags: Dict[str, Any] = {}
        for tag, node in tags.items():
            units: Dict[str, Any] = {}
            for unit, entries in (node.get("units") or {}).items():
                known = [e for e in entries if e.get("filed") and e["filed"] <= day]
                if known:
                    units[unit] = known
            if units:
                new_node = {k: v for k, v in node.items() if k != "units"}
                new_node["units"] = units
                kept_tags[tag] = new_node
        if kept_tags:
            kept[taxonomy] = kept_tags
    out["facts"] = kept
    return out


def filing_dates(facts: Dict[str, Any]) -> List[str]:
    """Distinct ``filed`` dates of every entry of every taxonomy, ascending."""
    return sorted({e["filed"] for e in _entries(facts) if e.get("filed")})


def undated_facts(facts: Dict[str, Any]) -> int:
    """Number of entries without a ``filed`` date (never known on any day)."""
    return sum(1 for e in _entries(facts) if not e.get("filed"))

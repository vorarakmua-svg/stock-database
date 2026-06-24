"""Shared helper for resolving a canonical financial field by candidate keys.

Lives in its own leaf module so both ``calculated_metrics`` (generic ratios) and
``sector_metrics`` (bank/insurer/REIT ratios) can resolve fields through one
implementation without importing each other.
"""

from typing import Any, Dict, List, Optional


def field_value(data: Dict[str, Any], keys: List[str]) -> Optional[float]:
    """Return the first present, numeric-coercible value among ``keys``, else None.

    Args:
        data: A flat dict of ``canonical_key -> value`` (one fiscal period).
        keys: Candidate keys in order of preference.
    """
    for key in keys:
        if key in data and data[key] is not None:
            try:
                return float(data[key])
            except (ValueError, TypeError):
                continue
    return None

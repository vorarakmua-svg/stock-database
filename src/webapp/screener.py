"""Safe SQL query builder for the cross-company screener.

Security invariant
------------------
Column names placed into SQL come ONLY from the ``_METRIC_COLUMNS`` whitelist
imported from ``sqlite_store``.  Every user-supplied *value* is a bound ``?``
parameter.  ``build_screen_query`` raises ``ValueError`` on any non-whitelisted
field/sort or invalid op/dir.

Note: only the latest-fiscal-year-per-ticker semantics are implemented for v1.
A calendar-year alignment option is explicitly out of scope (log as future work).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..exporters.sqlite_store import _METRIC_COLUMNS

# O(1) whitelist lookup
_METRIC_COL_SET: frozenset[str] = frozenset(_METRIC_COLUMNS)

# Allowed filter operators → SQL operator string
ALLOWED_OPS: Dict[str, str] = {
    "gte": ">=",
    "lte": "<=",
    "gt": ">",
    "lt": "<",
    "eq": "=",
    "ne": "<>",
}

# Full ordered list of columns returned by build_screen_query / Reader.screen.
SCREEN_COLUMNS: List[str] = (
    ["ticker", "company_name", "sector_class", "fiscal_year"] + list(_METRIC_COLUMNS)
)

# SQL fragment for the latest-fiscal-year-per-ticker sub-join (shared between
# the main query and the count query so there is one source of truth).
_LATEST_FY_JOIN: str = (
    "JOIN (\n"
    "    SELECT ticker, MAX(fiscal_year) AS fy\n"
    "    FROM metrics_annual\n"
    "    GROUP BY ticker\n"
    ") latest ON ma.ticker = latest.ticker AND ma.fiscal_year = latest.fy\n"
    "JOIN companies c ON c.ticker = ma.ticker"
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MetricFilter:
    """A single filter predicate applied to one metric column."""

    field: str
    op: str
    value: float
    value2: Optional[float] = None  # only used when op == "between"


@dataclass
class ScreenSpec:
    """Complete specification for a screener query."""

    filters: List[MetricFilter] = field(default_factory=list)
    sector: Optional[str] = None
    sort: Optional[str] = None
    sort_dir: str = "desc"
    limit: int = 100
    offset: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_where(spec: ScreenSpec) -> Tuple[str, List[Any]]:
    """Validate filters/sector and build the WHERE clause + ordered params.

    Raises ``ValueError`` for any non-whitelisted field or unknown op.
    Does NOT validate sort/sort_dir (that is done in ``build_screen_query``).
    """
    params: List[Any] = []
    clauses: List[str] = []

    for f in spec.filters:
        if f.field not in _METRIC_COL_SET:
            raise ValueError(
                f"Invalid filter field {f.field!r}: not in whitelisted _METRIC_COLUMNS."
            )
        if f.op == "between":
            if f.value2 is None:
                raise ValueError(
                    "op='between' requires value2 to be provided."
                )
            clauses.append(f'ma."{f.field}" BETWEEN ? AND ?')
            params.extend([f.value, f.value2])
        elif f.op in ALLOWED_OPS:
            sql_op = ALLOWED_OPS[f.op]
            clauses.append(f'ma."{f.field}" {sql_op} ?')
            params.append(f.value)
        else:
            raise ValueError(
                f"Invalid filter op {f.op!r}: must be one of "
                f"{sorted(ALLOWED_OPS) + ['between']}."
            )

    if spec.sector is not None:
        clauses.append("c.sector_class = ?")
        params.append(spec.sector)

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


# ---------------------------------------------------------------------------
# Public query builders
# ---------------------------------------------------------------------------


def build_screen_query(spec: ScreenSpec) -> Tuple[str, List[Any]]:
    """Return ``(sql, params)`` for the full screen query.

    Pure function — does NOT touch the database.

    Raises ``ValueError`` on:
    - a ``filter.field`` not in ``_METRIC_COLUMNS``
    - an unknown ``op``
    - a ``sort`` not in ``_METRIC_COLUMNS``
    - a ``sort_dir`` that is not ``'asc'`` or ``'desc'``
    - ``op='between'`` without ``value2``
    """
    # Validate sort_dir unconditionally (it always has a value)
    if spec.sort_dir not in ("asc", "desc"):
        raise ValueError(
            f"Invalid sort_dir {spec.sort_dir!r}: must be 'asc' or 'desc'."
        )

    # Validate sort column
    if spec.sort is not None and spec.sort not in _METRIC_COL_SET:
        raise ValueError(
            f"Invalid sort field {spec.sort!r}: not in whitelisted _METRIC_COLUMNS."
        )

    # Build WHERE (also validates filters)
    where_sql, params = _build_where(spec)

    # Build ORDER BY with NULLs-last
    if spec.sort is not None:
        order_sql = (
            f'ORDER BY (ma."{spec.sort}" IS NULL), '
            f'ma."{spec.sort}" {spec.sort_dir.upper()}'
        )
    else:
        order_sql = "ORDER BY ma.ticker"

    # SELECT columns: fixed columns + all metric columns
    metric_cols_sql = ", ".join(f'ma."{c}"' for c in _METRIC_COLUMNS)
    select_sql = (
        f"c.ticker, c.company_name, c.sector_class, ma.fiscal_year, {metric_cols_sql}"
    )

    sql = (
        f"SELECT {select_sql}\n"
        f"FROM metrics_annual ma\n"
        f"{_LATEST_FY_JOIN}\n"
        f"{where_sql}\n"
        f"{order_sql}\n"
        f"LIMIT ? OFFSET ?"
    ).strip()

    params.extend([spec.limit, spec.offset])
    return sql, params


def build_count_query(spec: ScreenSpec) -> Tuple[str, List[Any]]:
    """Return ``(sql, params)`` for a ``COUNT(*)`` version of the same query.

    Used by ``Reader.screen`` to compute the total without LIMIT/OFFSET.
    Validates filters and sector (same as ``build_screen_query``) but does NOT
    validate sort/sort_dir (not needed for a count).
    """
    where_sql, params = _build_where(spec)

    sql = (
        f"SELECT COUNT(*)\n"
        f"FROM metrics_annual ma\n"
        f"{_LATEST_FY_JOIN}\n"
        f"{where_sql}"
    ).strip()

    return sql, params


# ---------------------------------------------------------------------------
# GET-shorthand param parser (shared by screener_api and pages)
# ---------------------------------------------------------------------------


def parse_screen_params(params: Dict[str, str]) -> ScreenSpec:
    """Parse HTTP query-string key/value pairs into a ``ScreenSpec``.

    Keys of the form ``<field>_<op>`` (where op ∈ ALLOWED_OPS) become
    ``MetricFilter`` entries.  Reserved keys ``sector``, ``sort``,
    ``sort_dir``, ``limit``, ``offset`` are handled separately.
    All other keys are silently ignored.

    Raises ``ValueError`` for bad limit/offset or unparseable float values.
    The ``ScreenSpec`` itself is NOT validated here — call ``build_screen_query``
    (or ``Reader.screen``) to apply the whitelist checks.
    """
    RESERVED = {"sector", "sort", "sort_dir", "limit", "offset"}
    VALID_OPS = set(ALLOWED_OPS.keys())

    sector = params.get("sector") or None
    sort_raw = params.get("sort", "").strip()
    sort: Optional[str] = sort_raw if sort_raw else None
    sort_dir = params.get("sort_dir", "desc")

    try:
        limit = int(params.get("limit", 100))
    except (ValueError, TypeError):
        raise ValueError("'limit' must be an integer.")
    try:
        offset = int(params.get("offset", 0))
    except (ValueError, TypeError):
        raise ValueError("'offset' must be an integer.")

    filters: List[MetricFilter] = []
    for key, val_str in params.items():
        if key in RESERVED:
            continue
        # Match <field>_<op> — try longer op names first to avoid prefix ambiguity
        for op in sorted(VALID_OPS, key=len, reverse=True):
            suffix = f"_{op}"
            if key.endswith(suffix) and len(key) > len(suffix):
                field_name = key[: -len(suffix)]
                if not field_name:
                    continue
                try:
                    value = float(val_str)
                except ValueError:
                    raise ValueError(
                        f"Invalid numeric value for parameter {key!r}: {val_str!r}."
                    )
                filters.append(MetricFilter(field=field_name, op=op, value=value))
                break
        # Keys that don't match any op pattern are silently ignored

    return ScreenSpec(
        filters=filters,
        sector=sector,
        sort=sort,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )

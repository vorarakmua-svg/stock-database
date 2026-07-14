"""Safe SQL query builder for the cross-company screener.

Security invariant
------------------
Column names placed into SQL come ONLY from whitelists: ``_METRIC_COLUMNS``
(imported from ``sqlite_store``, qualified ``ma."col"``, latest-fiscal-year
metrics), ``SNAPSHOT_SCREEN_COLUMNS`` (qualified ``ms."col"``, latest
market/valuation snapshot), and ``VALUATION_EXPRS`` (a fixed name -> SQL
expression mapping for computed valuation fields such as ``val_upside_pct``).
The first two sets are disjoint (enforced by a test), so resolution is
deterministic; a filter/sort field is looked up in the metric set first, then
the snapshot set, then the valuation-expression set, and any field in none of
them raises ``ValueError`` BEFORE any SQL is built. Every user-supplied *value*
(including the ``verdict`` and ``oe_verdict`` filters, each of which only ever
selects one of three FIXED SQL clauses after validation against
``ALLOWED_VERDICTS``) is either a bound ``?`` parameter or a hard-coded clause
— never a raw string interpolation.

Note: only the latest-fiscal-year-per-ticker semantics are implemented for v1.
A calendar-year alignment option is explicitly out of scope (log as future work).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from ..exporters.sqlite_store import _METRIC_COLUMNS

# O(1) whitelist lookup
_METRIC_COL_SET: FrozenSet[str] = frozenset(_METRIC_COLUMNS)

# Market/valuation columns exposed to the screener, sourced from the latest
# market_snapshots row per ticker (subset of the widened _SNAPSHOT_COLUMNS).
SNAPSHOT_SCREEN_COLUMNS: List[str] = [
    "pe_trailing", "pe_forward", "dividend_yield", "price_to_book", "peg_ratio",
    "price_to_sales", "market_cap", "beta", "short_percent_of_float",
    "insider_percent", "institutional_percent", "debt_to_equity", "current_ratio",
    "current_price",
]

# O(1) whitelist lookup — disjoint from _METRIC_COL_SET (verified by test).
_SNAPSHOT_COL_SET: FrozenSet[str] = frozenset(SNAPSHOT_SCREEN_COLUMNS)

# Valuation medians (stored in valuation_summary) exposed to the screener.
# val_upside_pct is a computed expression, not a raw column, so it lives in
# its own whitelist mapping field name -> SQL expression.
VALUATION_EXPRS: Dict[str, str] = {
    "val_upside_pct":
        '((vsum."median_base" - ms."current_price") / NULLIF(ms."current_price", 0))',
    "oe_upside_pct":
        '((oe."value_base" - ms."current_price") / NULLIF(ms."current_price", 0))',
}
VALUATION_SELECT_COLUMNS: List[str] = ["median_bear", "median_base", "median_bull"]
ALLOWED_VERDICTS = ("cheap", "fair", "expensive")

# Allowed filter operators → SQL operator string
ALLOWED_OPS: Dict[str, str] = {
    "gte": ">=",
    "lte": "<=",
    "gt": ">",
    "lt": "<",
    "eq": "=",
    "ne": "<>",
}

# Raw owner-earnings columns (not computed expressions) returned alongside
# oe_upside_pct so _annotate_verdicts can derive the Buffett verdict.
OWNER_EARNINGS_SELECT: List[str] = ["oe_base", "oe_assumptions"]

# Full ordered list of columns returned by build_screen_query / Reader.screen.
SCREEN_COLUMNS: List[str] = (
    ["ticker", "company_name", "sector_class", "fiscal_year"]
    + list(_METRIC_COLUMNS)
    + list(SNAPSHOT_SCREEN_COLUMNS)
    + VALUATION_SELECT_COLUMNS
    + OWNER_EARNINGS_SELECT
    + list(VALUATION_EXPRS)
)

# Default metrics for the compare view — single source of truth.
DEFAULT_COMPARE_METRICS: List[str] = ["roic", "roe", "net_margin", "debt_to_ebitda"]

# Display kind for each metric column — used by compare_fragment to pre-format values.
# Metrics not listed here fall back to "raw" in fmt_value.
METRIC_KINDS: Dict[str, str] = {
    # percentages
    "fcf_margin": "pct",
    "roic": "pct",
    "roa": "pct",
    "roe": "pct",
    "gross_margin": "pct",
    "operating_margin": "pct",
    "net_margin": "pct",
    "ebitda_margin": "pct",
    "net_interest_margin": "pct",
    "efficiency_ratio": "pct",
    "loan_to_deposit": "pct",
    "loss_ratio": "pct",
    "combined_ratio": "pct",
    "ffo_payout": "pct",
    # multiples
    "debt_to_ebitda": "mult",
    "interest_coverage": "mult",
    # monetary
    "ebitda": "money",
    "ebit": "money",
    "nopat": "money",
    "free_cash_flow": "money",
    "levered_fcf": "money",
    "net_debt": "money",
    "total_debt": "money",
    "working_capital": "money",
    "invested_capital": "money",
    "ffo": "money",
    "affo": "money",
    # raw (turnovers and per-share)
    "asset_turnover": "raw",
    "inventory_turnover": "raw",
    "receivables_turnover": "raw",
    "ffo_per_share": "raw",
    # market/valuation (SNAPSHOT_SCREEN_COLUMNS) — percentages
    "dividend_yield": "pct",
    "short_percent_of_float": "pct",
    "insider_percent": "pct",
    "institutional_percent": "pct",
    # market/valuation — monetary
    "market_cap": "money",
    # market/valuation — multiples
    "pe_trailing": "mult",
    "pe_forward": "mult",
    "peg_ratio": "mult",
    "price_to_sales": "mult",
    "price_to_book": "mult",
    "debt_to_equity": "mult",
    "current_ratio": "mult",
    # market/valuation — raw
    "beta": "raw",
    "current_price": "raw",
    # valuation medians — raw
    "median_bear": "raw",
    "median_base": "raw",
    "median_bull": "raw",
    # valuation — computed percentage
    "val_upside_pct": "pct",
    "oe_upside_pct": "pct",
}

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

# SQL fragment for the latest-market-snapshot-per-ticker LEFT JOIN (shared
# between the main query and the count query). LEFT JOIN so a ticker with no
# snapshot row still appears (with NULLs for every snapshot column) rather
# than being dropped — filters on snapshot columns then naturally exclude it
# via the NULL comparison, same as an unset metric column would.
_SNAPSHOT_JOIN: str = (
    "LEFT JOIN (\n"
    "    SELECT ticker, MAX(collected_at) AS mx\n"
    "    FROM market_snapshots\n"
    "    GROUP BY ticker\n"
    ") lms ON lms.ticker = ma.ticker\n"
    "LEFT JOIN market_snapshots ms ON ms.ticker = ma.ticker AND ms.collected_at = lms.mx"
)

# Per-ticker valuation medians; LEFT JOIN so unvalued tickers still appear.
_VALUATION_JOIN: str = (
    "LEFT JOIN valuation_summary vsum ON vsum.ticker = ma.ticker"
)

# Owner-earnings row per ticker. Its own LEFT JOIN so an unvalued ticker still appears.
_OWNER_EARNINGS_JOIN: str = (
    "LEFT JOIN valuations oe ON oe.ticker = ma.ticker "
    "AND oe.model = 'owner_earnings' AND oe.applicable = 1"
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
    verdict: Optional[str] = None
    verdict_oe: Optional[str] = None
    sort: Optional[str] = None
    sort_dir: str = "desc"
    limit: int = 100
    offset: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _qualify_column(field_name: str, *, context: str = "filter") -> str:
    """Resolve *field_name* to a qualified, quoted SQL column reference.

    Metric columns (``_METRIC_COL_SET``) resolve to ``ma."col"``; snapshot
    columns (``_SNAPSHOT_COL_SET``) resolve to ``ms."col"``. The metric
    whitelist is checked first — the two whitelists are disjoint (enforced by
    a test), so this is documented precedence rather than a functional
    tie-breaker.

    Raises ``ValueError`` if *field_name* is in neither whitelist. *context*
    (``"filter"`` or ``"sort"``) is folded into the message only, to match the
    caller's existing error-matching expectations.
    """
    if field_name in _METRIC_COL_SET:
        return f'ma."{field_name}"'
    if field_name in _SNAPSHOT_COL_SET:
        return f'ms."{field_name}"'
    if field_name in VALUATION_EXPRS:
        return VALUATION_EXPRS[field_name]
    raise ValueError(
        f"Invalid {context} field {field_name!r}: not in whitelisted "
        "_METRIC_COLUMNS, SNAPSHOT_SCREEN_COLUMNS, or VALUATION_EXPRS."
    )


def _build_where(spec: ScreenSpec) -> Tuple[str, List[Any]]:
    """Validate filters/sector and build the WHERE clause + ordered params.

    Raises ``ValueError`` for any non-whitelisted field or unknown op.
    Does NOT validate sort/sort_dir (that is done in ``build_screen_query``).
    """
    params: List[Any] = []
    clauses: List[str] = []

    for f in spec.filters:
        col = _qualify_column(f.field, context="filter")
        if f.op == "between":
            if f.value2 is None:
                raise ValueError(
                    "op='between' requires value2 to be provided."
                )
            clauses.append(f"{col} BETWEEN ? AND ?")
            params.extend([f.value, f.value2])
        elif f.op in ALLOWED_OPS:
            sql_op = ALLOWED_OPS[f.op]
            clauses.append(f"{col} {sql_op} ?")
            params.append(f.value)
        else:
            raise ValueError(
                f"Invalid filter op {f.op!r}: must be one of "
                f"{sorted(ALLOWED_OPS) + ['between']}."
            )

    if spec.sector is not None:
        clauses.append("c.sector_class = ?")
        params.append(spec.sector)

    if spec.verdict is not None:
        if spec.verdict not in ALLOWED_VERDICTS:
            raise ValueError(
                f"Invalid verdict {spec.verdict!r}: must be one of "
                f"{list(ALLOWED_VERDICTS)}."
            )
        # engine.verdict() renders "—" for a non-positive price; the SQL filter
        # must agree, so every clause is guarded by price > 0.
        price = 'ms."current_price"'
        if spec.verdict == "cheap":
            clauses.append(f'{price} > 0 AND {price} < vsum."median_bear"')
        elif spec.verdict == "expensive":
            clauses.append(f'{price} > 0 AND {price} > vsum."median_bull"')
        else:
            clauses.append(
                f'{price} > 0 AND {price} >= vsum."median_bear" '
                f'AND {price} <= vsum."median_bull"'
            )

    if spec.verdict_oe is not None:
        if spec.verdict_oe not in ALLOWED_VERDICTS:
            raise ValueError(
                f"Invalid oe_verdict {spec.verdict_oe!r}: must be one of "
                f"{list(ALLOWED_VERDICTS)}."
            )
        price = 'ms."current_price"'
        # buy_below lives in the assumptions JSON; SQLite's json_extract reads it.
        buy_below = 'json_extract(oe."assumptions", \'$.buy_below\')'
        if spec.verdict_oe == "cheap":
            clauses.append(f'{price} > 0 AND {price} < {buy_below}')
        elif spec.verdict_oe == "expensive":
            clauses.append(
                f'{price} > 0 AND {buy_below} IS NOT NULL '
                f'AND {price} > oe."value_base"'
            )
        else:
            clauses.append(
                f'{price} > 0 AND {price} >= {buy_below} '
                f'AND {price} <= oe."value_base"'
            )

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

    # Validate sort column (may be either a metric or a snapshot column)
    sort_col: Optional[str] = None
    if spec.sort is not None:
        sort_col = _qualify_column(spec.sort, context="sort")

    # Build WHERE (also validates filters)
    where_sql, params = _build_where(spec)

    # Build ORDER BY with NULLs-last
    if sort_col is not None:
        order_sql = f"ORDER BY ({sort_col} IS NULL), {sort_col} {spec.sort_dir.upper()}"
    else:
        order_sql = "ORDER BY ma.ticker"

    # SELECT columns: fixed columns + all metric columns + all snapshot columns
    # + valuation median columns + valuation computed expressions
    metric_cols_sql = ", ".join(f'ma."{c}"' for c in _METRIC_COLUMNS)
    snapshot_cols_sql = ", ".join(f'ms."{c}"' for c in SNAPSHOT_SCREEN_COLUMNS)
    valuation_cols_sql = ", ".join(f'vsum."{c}"' for c in VALUATION_SELECT_COLUMNS)
    oe_cols_sql = 'oe."value_base" AS oe_base, oe."assumptions" AS oe_assumptions'
    valuation_exprs_sql = ", ".join(
        f"{expr} AS {name}" for name, expr in VALUATION_EXPRS.items()
    )
    select_sql = (
        f"c.ticker, c.company_name, c.sector_class, ma.fiscal_year, "
        f"{metric_cols_sql}, {snapshot_cols_sql}, "
        f"{valuation_cols_sql}, {oe_cols_sql}, {valuation_exprs_sql}"
    )

    sql = (
        f"SELECT {select_sql}\n"
        f"FROM metrics_annual ma\n"
        f"{_LATEST_FY_JOIN}\n"
        f"{_SNAPSHOT_JOIN}\n"
        f"{_VALUATION_JOIN}\n"
        f"{_OWNER_EARNINGS_JOIN}\n"
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
        f"{_SNAPSHOT_JOIN}\n"
        f"{_VALUATION_JOIN}\n"
        f"{_OWNER_EARNINGS_JOIN}\n"
        f"{where_sql}"
    ).strip()

    return sql, params


# ---------------------------------------------------------------------------
# GET-shorthand param parser (shared by screener_api and pages)
# ---------------------------------------------------------------------------


def parse_screen_params(params: Dict[str, str]) -> ScreenSpec:
    """Parse HTTP query-string key/value pairs into a ``ScreenSpec``.

    Keys of the form ``<field>_<op>`` (where op ∈ ALLOWED_OPS) become
    ``MetricFilter`` entries.  Reserved keys ``sector``, ``verdict``,
    ``oe_verdict``, ``sort``, ``sort_dir``, ``limit``, ``offset`` are handled
    separately.  All other keys are silently ignored.

    Raises ``ValueError`` for bad limit/offset, unparseable float values, or an
    unrecognized ``verdict``/``oe_verdict``.
    The ``ScreenSpec`` itself is NOT validated here — call ``build_screen_query``
    (or ``Reader.screen``) to apply the whitelist checks.
    """
    RESERVED = {"sector", "verdict", "oe_verdict", "sort", "sort_dir", "limit", "offset"}
    VALID_OPS = set(ALLOWED_OPS.keys())

    sector = params.get("sector") or None
    verdict = params.get("verdict") or None
    if verdict is not None and verdict not in ALLOWED_VERDICTS:
        raise ValueError(
            f"Invalid verdict {verdict!r}: must be one of {list(ALLOWED_VERDICTS)}."
        )
    verdict_oe = params.get("oe_verdict") or None
    if verdict_oe is not None and verdict_oe not in ALLOWED_VERDICTS:
        raise ValueError(
            f"Invalid oe_verdict {verdict_oe!r}: must be one of {list(ALLOWED_VERDICTS)}."
        )
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
        verdict=verdict,
        verdict_oe=verdict_oe,
        sort=sort,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )

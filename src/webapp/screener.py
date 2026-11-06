"""Safe SQL query builder for the cross-company screener.

Security invariant
------------------
Column names placed into SQL come ONLY from whitelists: ``_METRIC_COLUMNS``
(imported from ``sqlite_store``, qualified ``ma."col"``, latest-fiscal-year
metrics), ``SNAPSHOT_SCREEN_COLUMNS`` (qualified ``ms."col"``, latest
market/valuation snapshot), ``VALUATION_EXPRS`` (a fixed name -> SQL
expression mapping for computed valuation fields such as ``val_upside_pct``),
``HISTORY_EXPRS`` (the valuation history's percentiles and price / value,
over fixed-literal joins of ``valuation_ranges``), ``QUALITY_EXPRS`` (the
quality scores' columns of ``quality_scores``, and price / NCAV), ``INSIDER_EXPRS``
and ``GURU_EXPRS`` (correlated subqueries over the SEC ownership tables; the guru
ones carry our universe's share-class tickers, a curated constant, as literals),
and ``INDICATOR_EXPRS`` (the other valuation indicators: the reverse DCF's
growth, three upsides and price / net-net working capital, over fixed-literal
joins of ``valuation_indicators``). The first two sets are disjoint (enforced
by a test), so resolution is deterministic; a filter/sort field is looked up in
the metric set first, then the snapshot set, then the expression sets in the
order above, and any field in none of them raises ``ValueError`` BEFORE any SQL
is built. Every user-supplied *value* (including the ``verdict`` and
``oe_verdict`` filters, each of which only ever selects one of three FIXED SQL
clauses after validation against ``ALLOWED_VERDICTS``) is either a bound ``?``
parameter or a hard-coded clause — never a raw string interpolation. The
``industry`` filter's value (a 2- or 3-digit SIC prefix) is checked to be 2 or
3 ASCII digits (``industry_prefix``), raising ``ValueError`` otherwise, and then
bound: ``c."sic" LIKE ? || '%'``, its digits holding none of LIKE's wildcards.

Note: only the latest-fiscal-year-per-ticker semantics are implemented for v1.
A calendar-year alignment option is explicitly out of scope (log as future work).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import AbstractSet, Any, Dict, FrozenSet, List, Optional, Tuple, Union

from ..exporters.sqlite_store import _METRIC_COLUMNS
from ..ownership.cusips import UNIVERSE_CUSIPS
from ..ownership.portfolio import share_classes

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
    # The Lynch model's base value over the price, for the Peter Lynch preset.
    "lynch_upside_pct":
        '((ly."value_base" - ms."current_price") / NULLIF(ms."current_price", 0))',
}
VALUATION_SELECT_COLUMNS: List[str] = ["median_bear", "median_base", "median_bull"]

# The valuation history's ranges (valuation_ranges): each multiple's percentile in its
# trailing 10-year window, and the live price over the historical value. One LEFT JOIN
# per metric, its alias and literal from this fixed tuple, never from input; joined only
# when the table exists, so a database from before the history screens on with these
# fields NULL (docs/superpowers/specs/2026-10-21-valuation-history-design.md).
HISTORY_METRICS: Tuple[str, ...] = ("pe", "ps", "pb", "pfcf", "ev_ebitda", "pffo", "div_yield")
HISTORY_EXPRS: Dict[str, str] = {
    **{f"{m}_pct_10y": f'vr_{m}."pct_rank"' for m in HISTORY_METRICS},
    "price_to_hist_value": '(ms."current_price" / NULLIF(vr_fv."current_value", 0))',
}
_HISTORY_JOIN: str = "\n".join(
    [f"LEFT JOIN valuation_ranges vr_{m} ON vr_{m}.ticker = ma.ticker "
     f"AND vr_{m}.metric = '{m}' AND vr_{m}.applicable = 1" for m in HISTORY_METRICS]
    + ["LEFT JOIN valuation_ranges vr_fv ON vr_fv.ticker = ma.ticker "
       "AND vr_fv.metric = 'fair_value' AND vr_fv.applicable = 1"]
)
# The quality scores (quality_scores, one row per ticker): its columns, and Graham's price / NCAV per share,
# none when NCAV isn't positive. One LEFT JOIN, made only when the table exists, so a database from before the
# scores screens on with these fields NULL (docs/superpowers/specs/2026-10-21-quality-scores-design.md).
QUALITY_FIELDS: Tuple[str, ...] = (
    "piotroski_f", "altman_z", "beneish_m", "sloan_ratio", "predictability_stars", "rev_ps_cagr_5y", "eps_cagr_5y",
    "roic_minus_wacc", "rank_strength", "rank_profitability", "rank_growth", "rank_valuation", "rank_momentum",
    "composite_score", "momentum", "magic_formula_rank", "warning_count",
)
#: The five ranks and the composite within the company's SIC industry group (Milestone 5), NULL without one, and in a
#: table the scores wrote before them (docs/superpowers/specs/2026-10-25-universe-automation-design.md).
INDUSTRY_RANK_FIELDS: Tuple[str, ...] = (
    "ind_rank_strength", "ind_rank_profitability", "ind_rank_growth", "ind_rank_valuation", "ind_rank_momentum",
    "ind_composite",
)
QUALITY_EXPRS: Dict[str, str] = {
    **{f: f'qs."{f}"' for f in QUALITY_FIELDS},
    "price_to_ncav": '(CASE WHEN qs."ncav_per_share" > 0 THEN ms."current_price" / qs."ncav_per_share" END)',
    # The scores' debt / EBITDA: 0 for a filer tagging no debt and paying no interest, which metrics_annual
    # leaves NULL; NULL where interest is paid on debt no mapped tag holds, where it reads 0 (BUG-189, BUG-190).
    "scores_debt_to_ebitda": 'qs."debt_to_ebitda"',
    **{f: f'qs."{f}"' for f in INDUSTRY_RANK_FIELDS},
}
_QUALITY_JOIN: str = "LEFT JOIN quality_scores qs ON qs.ticker = ma.ticker"
#: The quality_scores column each quality field reads.
QUALITY_NEEDS: Dict[str, str] = {**{f: f for f in QUALITY_FIELDS}, "price_to_ncav": "ncav_per_share",
                                 "scores_debt_to_ebitda": "debt_to_ebitda", **{f: f for f in INDUSTRY_RANK_FIELDS}}
#: What a query can read of quality_scores: True for every column, False without the table, or the table's
#: columns, since a table the scores wrote with older code may lack one a field reads (BUG-190).
QualityColumns = Union[bool, AbstractSet[str]]

# Insiders' open-market trades from the SEC's Form 4 filings (insider_trades, insider_filings), over the last 365
# days, those no amendment restated (insider_trades.superseded_by): correlated subqueries, NULL when the tables or
# their newer columns don't exist (docs/superpowers/specs/2026-10-22-sec-ownership-design.md). A joint filing counts
# each of its owners. A price that lost its decimal point (insider_trades.price_suspect) adds no dollars.
_LIVE_TRADES = ("FROM insider_trades {t} JOIN insider_filings {f} ON {f}.accession = {t}.accession "
                "WHERE {t}.ticker = ma.ticker AND {t}.kind = 'nonderivative' AND {t}.superseded_by IS NULL ")
INSIDER_EXPRS: Dict[str, str] = {
    # Open-market dollars bought less sold (code P less S), each trade once.
    "ins_net_buy_12m":
        "(SELECT SUM(CASE t.code WHEN 'P' THEN t.shares * t.price ELSE -t.shares * t.price END) "
        "FROM insider_trades t WHERE t.ticker = ma.ticker AND t.kind = 'nonderivative' "
        "AND t.code IN ('P', 'S') AND t.price IS NOT NULL AND t.date >= date('now', '-365 days') "
        "AND t.superseded_by IS NULL AND COALESCE(t.price_suspect, 0) = 0)",
    # Distinct insiders buying in the open market.
    "ins_buyers_12m":
        "(SELECT COUNT(DISTINCT f.owner_cik) " + _LIVE_TRADES.format(t="t", f="f")
        + "AND t.code = 'P' AND t.date >= date('now', '-365 days'))",
    # Open-market purchases by the company's CEO or CFO (filings).
    "ins_ceo_cfo_buys_12m":
        "(SELECT COUNT(DISTINCT t.accession) " + _LIVE_TRADES.format(t="t", f="f")
        + "AND t.code = 'P' AND f.is_ceo_cfo = 1 AND t.date >= date('now', '-365 days'))",
    # The most distinct insiders buying within 30 days of one purchase: 3 or more is a cluster buy.
    "ins_cluster_buyers_12m":
        "(SELECT MAX((SELECT COUNT(DISTINCT f2.owner_cik) " + _LIVE_TRADES.format(t="t2", f="f2")
        .replace("t2.ticker = ma.ticker", "t2.ticker = t1.ticker")
        + "AND t2.code = 'P' AND t2.date BETWEEN t1.date AND date(t1.date, '+30 days'))) "
        "FROM insider_trades t1 WHERE t1.ticker = ma.ticker AND t1.kind = 'nonderivative' AND t1.code = 'P' "
        "AND t1.superseded_by IS NULL AND t1.date >= date('now', '-365 days'))",
}

#: A ticker as it may be written into SQL as a literal: our universe's are all this shape.
_LITERAL_TICKER = re.compile(r"[A-Z0-9.\-]+")


def _classes_sql(column: str) -> str:
    """The SQL list of *column*'s ticker and its other share classes (``portfolio.share_classes``: GOOGL with GOOG,
    BRK-B with BRK-A): the column, then a CASE per further class, NULL for a ticker with fewer. Its literals are our
    universe's tickers (``cusips.UNIVERSE_CUSIPS``), never input, and each is checked to be a plain ticker."""
    classes = {ticker: share_classes(ticker) for ticker in sorted(set(UNIVERSE_CUSIPS.values()))}
    classes = {ticker: others for ticker, others in classes.items() if len(others) > 1}
    unsafe = [t for others in classes.values() for t in others if not _LITERAL_TICKER.fullmatch(t)]
    if unsafe:
        raise ValueError(f"tickers that can't be SQL literals: {unsafe}")
    slots = max((len(others) for others in classes.values()), default=1)
    cases = [f"CASE {column} " + " ".join(f"WHEN '{ticker}' THEN '{others[i]}'" for ticker, others in classes.items()
                                          if len(others) > i) + " END" for i in range(1, slots)]
    return ", ".join([column] + cases)


# The gurus' 13F holdings (guru_quarters, guru_positions), over each guru's latest quarter, as the spec's Aggregates
# read them: the gurus holding the company's shares (put/call '') or its other class's, each guru once, and of those
# the ones whose position is new or added. Correlated subqueries, NULL when the tables don't exist.
_GURU_POSITIONS = (f"FROM guru_positions gp WHERE gp.ticker IN ({_classes_sql('ma.ticker')}) AND gp.put_call = '' "
                   "AND gp.period = (SELECT MAX(gq.period) FROM guru_quarters gq WHERE gq.guru_cik = gp.guru_cik) ")
GURU_EXPRS: Dict[str, str] = {
    "gurus_holding": "(SELECT COUNT(DISTINCT gp.guru_cik) " + _GURU_POSITIONS + "AND gp.action IS NOT 'sold_out')",
    "gurus_buying": "(SELECT COUNT(DISTINCT gp.guru_cik) " + _GURU_POSITIONS + "AND gp.action IN ('new', 'added'))",
}

# The other valuation indicators (valuation_indicators, display-only): the reverse DCF's implied growth at the price it
# was last computed at, the EPV's, median P/S value's and tangible Graham number's upsides from the snapshot price, and
# the price over Graham's net-net working capital per share (the NCAV row's bear), none when that isn't positive. One
# LEFT JOIN per indicator read, its alias and literal from this fixed tuple, never from input, applicable rows only;
# joined only when the table exists, so a database from before the indicators screens on with these fields NULL
# (docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md).
INDICATOR_JOINS: Tuple[str, ...] = ("reverse_dcf", "epv", "ncav", "ps_value", "graham_tangible")
INDICATOR_EXPRS: Dict[str, str] = {
    "reverse_dcf_growth": 'vi_reverse_dcf."value_base"',
    "epv_upside_pct": '((vi_epv."value_base" - ms."current_price") / NULLIF(ms."current_price", 0))',
    "ps_value_upside_pct": '((vi_ps_value."value_base" - ms."current_price") / NULLIF(ms."current_price", 0))',
    "graham_tangible_upside_pct":
        '((vi_graham_tangible."value_base" - ms."current_price") / NULLIF(ms."current_price", 0))',
    "price_to_nnwc": '(CASE WHEN vi_ncav."value_bear" > 0 THEN ms."current_price" / vi_ncav."value_bear" END)',
}
_INDICATOR_JOIN: str = "\n".join(
    f"LEFT JOIN valuation_indicators vi_{i} ON vi_{i}.ticker = ma.ticker "
    f"AND vi_{i}.indicator = '{i}' AND vi_{i}.applicable = 1" for i in INDICATOR_JOINS
)

# The spec's preset screens: (key, label, the query string each fills the screener with).
PRESETS: Tuple[Tuple[str, str, str], ...] = (
    ("buffett_munger", "Buffett-Munger",
     "predictability_stars_gte=4&roic_minus_wacc_gt=0&scores_debt_to_ebitda_lte=3&price_to_hist_value_lte=1.1"),
    ("lynch", "Peter Lynch", "lynch_upside_pct_gt=0&sort=lynch_upside_pct&sort_dir=desc"),
    ("graham_net_net", "Graham net-net", "price_to_ncav_lte=0.67&sort=price_to_ncav&sort_dir=asc"),
    # Banks, insurers, utilities and REITs have no Magic Formula rank, so a rank of at least 1 leaves them out.
    ("magic_formula", "Magic Formula", "magic_formula_rank_gte=1&sort=magic_formula_rank&sort_dir=asc"),
    ("undervalued_predictable", "Undervalued-predictable",
     "predictability_stars_gte=4&price_to_hist_value_lt=0.9&sort=price_to_hist_value&sort_dir=asc"),
)
ALLOWED_VERDICTS = ("cheap", "fair", "expensive")
#: The industry filter's value: a SIC prefix of 2 or 3 ASCII digits (the major group or the industry group), whole.
INDUSTRY_PATTERN = re.compile(r"[0-9]{2,3}")


def industry_prefix(value: str) -> str:
    """*value* when it is a 2- or 3-digit SIC prefix (ASCII digits only, nothing around them); else ``ValueError``."""
    if not isinstance(value, str) or not INDUSTRY_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid industry {value!r}: must be a SIC prefix of 2 or 3 digits (e.g. 28 or 283).")
    return value

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

# Full ordered list of columns returned by build_screen_query / Reader.screen (``sic``: the SEC's SIC code, NULL for a
# companies table from before the codes).
SCREEN_COLUMNS: List[str] = (
    ["ticker", "company_name", "sector_class", "sic", "fiscal_year"]
    + list(_METRIC_COLUMNS)
    + list(SNAPSHOT_SCREEN_COLUMNS)
    + VALUATION_SELECT_COLUMNS
    + OWNER_EARNINGS_SELECT
    + list(VALUATION_EXPRS)
    + list(HISTORY_EXPRS)
    + list(QUALITY_EXPRS)
    + list(INSIDER_EXPRS)
    + list(GURU_EXPRS)
    + list(INDICATOR_EXPRS)
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
    "debt_to_equity": "percent",  # Yahoo's debtToEquity, a percent (BUG-135)
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
    "lynch_upside_pct": "pct",
    # valuation history — percentiles in the 10-year window, and price / value
    **{f"{m}_pct_10y": "pct" for m in HISTORY_METRICS},
    "price_to_hist_value": "mult",
    # quality scores — the scores and ranks are raw (the industry ones too), the ratios and growth percentages
    **{f: "raw" for f in QUALITY_FIELDS + INDUSTRY_RANK_FIELDS},
    "sloan_ratio": "pct",
    "rev_ps_cagr_5y": "pct",
    "eps_cagr_5y": "pct",
    "roic_minus_wacc": "pct",
    "momentum": "pct",
    "price_to_ncav": "mult",
    "scores_debt_to_ebitda": "mult",
    # insiders (SEC Form 4)
    "ins_net_buy_12m": "money",
    "ins_buyers_12m": "raw",
    "ins_ceo_cfo_buys_12m": "raw",
    "ins_cluster_buyers_12m": "raw",
    # gurus (13F), counts of gurus
    "gurus_holding": "count",
    "gurus_buying": "count",
    # the other valuation indicators — the reverse DCF's growth and the upsides are fractions, price / NNWC a multiple
    "reverse_dcf_growth": "pct",
    "epv_upside_pct": "pct",
    "ps_value_upside_pct": "pct",
    "graham_tangible_upside_pct": "pct",
    "price_to_nnwc": "mult",
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

# The Lynch model's row per ticker, for lynch_upside_pct; a LEFT JOIN like the owner-earnings one.
_LYNCH_JOIN: str = (
    "LEFT JOIN valuations ly ON ly.ticker = ma.ticker "
    "AND ly.model = 'lynch' AND ly.applicable = 1"
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
    # A 2- or 3-digit SIC prefix (``industry_prefix``): the companies whose SEC code starts with it.
    industry: Optional[str] = None
    verdict: Optional[str] = None
    verdict_oe: Optional[str] = None
    sort: Optional[str] = None
    sort_dir: str = "desc"
    limit: int = 100
    offset: int = 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _quality_expr(name: str, quality: QualityColumns) -> str:
    """A quality field's SQL, or NULL without the table or the column it reads."""
    if quality is True or (not isinstance(quality, bool) and QUALITY_NEEDS[name] in quality):
        return QUALITY_EXPRS[name]
    return "NULL"


def _qualify_column(field_name: str, *, context: str = "filter", history: bool = True,
                    quality: QualityColumns = True, insiders: bool = True, gurus: bool = True,
                    indicators: bool = True) -> str:
    """Resolve *field_name* to a qualified, quoted SQL column reference.

    Metric columns (``_METRIC_COL_SET``) resolve to ``ma."col"``; snapshot
    columns (``_SNAPSHOT_COL_SET``) resolve to ``ms."col"``. The metric
    whitelist is checked first — the two whitelists are disjoint (enforced by
    a test), so this is documented precedence rather than a functional
    tie-breaker.

    Raises ``ValueError`` if *field_name* is in neither whitelist. *context*
    (``"filter"`` or ``"sort"``) is folded into the message only, to match the
    caller's existing error-matching expectations. A ``HISTORY_EXPRS`` field
    is NULL when *history* is False (no ``valuation_ranges`` table), and a
    ``QUALITY_EXPRS`` field when *quality* is False (no ``quality_scores``) or lacks the column it reads; an
    ``INSIDER_EXPRS`` or ``GURU_EXPRS`` field when *insiders* or *gurus* is False (no tables; for the insiders, or
    no trade-level amendment note or price check); an ``INDICATOR_EXPRS`` field when *indicators* is False (no
    ``valuation_indicators`` table).
    """
    if field_name in _METRIC_COL_SET:
        return f'ma."{field_name}"'
    if field_name in _SNAPSHOT_COL_SET:
        return f'ms."{field_name}"'
    if field_name in VALUATION_EXPRS:
        return VALUATION_EXPRS[field_name]
    if field_name in HISTORY_EXPRS:
        return HISTORY_EXPRS[field_name] if history else "NULL"
    if field_name in QUALITY_EXPRS:
        return _quality_expr(field_name, quality)
    if field_name in INSIDER_EXPRS:
        return INSIDER_EXPRS[field_name] if insiders else "NULL"
    if field_name in GURU_EXPRS:
        return GURU_EXPRS[field_name] if gurus else "NULL"
    if field_name in INDICATOR_EXPRS:
        return INDICATOR_EXPRS[field_name] if indicators else "NULL"
    raise ValueError(
        f"Invalid {context} field {field_name!r}: not in whitelisted _METRIC_COLUMNS, "
        "SNAPSHOT_SCREEN_COLUMNS, VALUATION_EXPRS, HISTORY_EXPRS, QUALITY_EXPRS, INSIDER_EXPRS, GURU_EXPRS, or "
        "INDICATOR_EXPRS."
    )


def _build_where(spec: ScreenSpec, history: bool = True, quality: QualityColumns = True,
                 insiders: bool = True, gurus: bool = True, indicators: bool = True,
                 sic: bool = True) -> Tuple[str, List[Any]]:
    """Validate filters/sector/industry and build the WHERE clause + ordered params.

    Raises ``ValueError`` for any non-whitelisted field, unknown op or an industry that isn't a 2- or 3-digit SIC
    prefix. Does NOT validate sort/sort_dir (that is done in ``build_screen_query``). Without *sic* (a companies
    table from before the codes), an industry matches no company.
    """
    params: List[Any] = []
    clauses: List[str] = []

    for f in spec.filters:
        col = _qualify_column(f.field, context="filter", history=history, quality=quality, insiders=insiders,
                              gurus=gurus, indicators=indicators)
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

    if spec.industry is not None:
        # Validated first: 2 or 3 ASCII digits, then bound, so LIKE sees no wildcard but the one added here.
        prefix = industry_prefix(spec.industry)
        if sic:
            clauses.append("c.\"sic\" LIKE ? || '%'")
            params.append(prefix)
        else:
            clauses.append("0")

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


def build_screen_query(spec: ScreenSpec, history: bool = True, quality: QualityColumns = True,
                       insiders: bool = True, gurus: bool = True, indicators: bool = True,
                       sic: bool = True) -> Tuple[str, List[Any]]:
    """Return ``(sql, params)`` for the full screen query.

    Pure function — does NOT touch the database.

    Raises ``ValueError`` on:
    - a ``filter.field`` not in ``_METRIC_COLUMNS``
    - an unknown ``op``
    - a ``sort`` not in ``_METRIC_COLUMNS``
    - a ``sort_dir`` that is not ``'asc'`` or ``'desc'``
    - ``op='between'`` without ``value2``
    - an ``industry`` that isn't a 2- or 3-digit SIC prefix

    Without *sic* (a companies table from before the SEC codes), ``sic`` is NULL and an industry matches nothing.
    """
    # Validate sort_dir unconditionally (it always has a value)
    if spec.sort_dir not in ("asc", "desc"):
        raise ValueError(
            f"Invalid sort_dir {spec.sort_dir!r}: must be 'asc' or 'desc'."
        )

    # Validate sort column (may be either a metric or a snapshot column)
    sort_col: Optional[str] = None
    if spec.sort is not None:
        sort_col = _qualify_column(spec.sort, context="sort", history=history, quality=quality,
                                   insiders=insiders, gurus=gurus, indicators=indicators)

    # Build WHERE (also validates filters)
    where_sql, params = _build_where(spec, history, quality, insiders, gurus, indicators, sic)

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
    history_exprs_sql = ", ".join(
        f"{expr if history else 'NULL'} AS {name}" for name, expr in HISTORY_EXPRS.items()
    )
    quality_exprs_sql = ", ".join(
        f"{_quality_expr(name, quality)} AS {name}" for name in QUALITY_EXPRS
    )
    insider_exprs_sql = ", ".join(
        f"{expr if insiders else 'NULL'} AS {name}" for name, expr in INSIDER_EXPRS.items()
    )
    guru_exprs_sql = ", ".join(
        f"{expr if gurus else 'NULL'} AS {name}" for name, expr in GURU_EXPRS.items()
    )
    indicator_exprs_sql = ", ".join(
        f"{expr if indicators else 'NULL'} AS {name}" for name, expr in INDICATOR_EXPRS.items()
    )
    sic_sql = 'c."sic"' if sic else "NULL"
    select_sql = (
        f"c.ticker, c.company_name, c.sector_class, {sic_sql} AS sic, ma.fiscal_year, "
        f"{metric_cols_sql}, {snapshot_cols_sql}, "
        f"{valuation_cols_sql}, {oe_cols_sql}, {valuation_exprs_sql}, {history_exprs_sql}, "
        f"{quality_exprs_sql}, {insider_exprs_sql}, {guru_exprs_sql}, {indicator_exprs_sql}"
    )

    sql = (
        f"SELECT {select_sql}\n"
        f"FROM metrics_annual ma\n"
        f"{_LATEST_FY_JOIN}\n"
        f"{_SNAPSHOT_JOIN}\n"
        f"{_VALUATION_JOIN}\n"
        f"{_OWNER_EARNINGS_JOIN}\n"
        f"{_LYNCH_JOIN}\n"
        f"{_HISTORY_JOIN if history else ''}\n"
        f"{_QUALITY_JOIN if quality else ''}\n"
        f"{_INDICATOR_JOIN if indicators else ''}\n"
        f"{where_sql}\n"
        f"{order_sql}\n"
        f"LIMIT ? OFFSET ?"
    ).strip()

    params.extend([spec.limit, spec.offset])
    return sql, params


def build_count_query(spec: ScreenSpec, history: bool = True, quality: QualityColumns = True,
                      insiders: bool = True, gurus: bool = True, indicators: bool = True,
                      sic: bool = True) -> Tuple[str, List[Any]]:
    """Return ``(sql, params)`` for a ``COUNT(*)`` version of the same query.

    Used by ``Reader.screen`` to compute the total without LIMIT/OFFSET.
    Validates filters, sector and industry (same as ``build_screen_query``) but
    does NOT validate sort/sort_dir (not needed for a count).
    """
    where_sql, params = _build_where(spec, history, quality, insiders, gurus, indicators, sic)

    sql = (
        f"SELECT COUNT(*)\n"
        f"FROM metrics_annual ma\n"
        f"{_LATEST_FY_JOIN}\n"
        f"{_SNAPSHOT_JOIN}\n"
        f"{_VALUATION_JOIN}\n"
        f"{_OWNER_EARNINGS_JOIN}\n"
        f"{_LYNCH_JOIN}\n"
        f"{_HISTORY_JOIN if history else ''}\n"
        f"{_QUALITY_JOIN if quality else ''}\n"
        f"{_INDICATOR_JOIN if indicators else ''}\n"
        f"{where_sql}"
    ).strip()

    return sql, params


# ---------------------------------------------------------------------------
# GET-shorthand param parser (shared by screener_api and pages)
# ---------------------------------------------------------------------------


def parse_screen_params(params: Dict[str, str]) -> ScreenSpec:
    """Parse HTTP query-string key/value pairs into a ``ScreenSpec``.

    Keys of the form ``<field>_<op>`` (where op ∈ ALLOWED_OPS) become
    ``MetricFilter`` entries.  Reserved keys ``sector``, ``industry``,
    ``verdict``, ``oe_verdict``, ``sort``, ``sort_dir``, ``limit``, ``offset``
    are handled separately.  All other keys are silently ignored.

    Raises ``ValueError`` for bad limit/offset, unparseable float values, an
    unrecognized ``verdict``/``oe_verdict``, or an ``industry`` that isn't a 2-
    or 3-digit SIC prefix.
    The ``ScreenSpec`` itself is NOT validated here — call ``build_screen_query``
    (or ``Reader.screen``) to apply the whitelist checks.
    """
    RESERVED = {"sector", "industry", "verdict", "oe_verdict", "sort", "sort_dir", "limit", "offset"}
    VALID_OPS = set(ALLOWED_OPS.keys())

    sector = params.get("sector") or None
    industry = params.get("industry") or None
    if industry is not None:
        industry_prefix(industry)
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
        industry=industry,
        verdict=verdict,
        verdict_oe=verdict_oe,
        sort=sort,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )

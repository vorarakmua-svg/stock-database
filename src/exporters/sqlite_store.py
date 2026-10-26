"""SQLite store for standardized stock data.

Because financials are standardized to canonical fields before they reach here,
every company maps onto the same columns — so the DB supports real cross-company
screening (e.g. ``WHERE roic > 0.15 AND ev_to_ebitda < 12``).

Tables:
- ``companies``            one row per ticker (latest profile)
- ``financials_annual``    canonical annual line items, one row per (ticker, fy)
- ``financials_quarterly`` canonical quarterly line items, one row per (ticker, period_end)
- ``metrics_annual``       calculated ratios per (ticker, fy)
- ``market_snapshots``     point-in-time market/valuation data per (ticker, collected_at)
- ``collection_runs``      provenance/quality of each collection

All writes are idempotent upserts on the natural keys.
"""

import json
import logging
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple, Union

if TYPE_CHECKING:  # avoid a runtime exporters -> valuation (or ownership) dependency
    from ..ownership.portfolio import Change, GuruQuarters, Position
    from ..valuation.history import HistoryResult
    from ..valuation.indicators import IndicatorResult
    from ..valuation.models import ValuationResult

from ..mappings.canonical import CANONICAL_FIELDS
from ..models.stock_data import StockData
from ..parsers.calculated_metrics import CalculatedMetrics
from ..universe.industries import normalize_sic, normalize_sic_description
from ..valuation.inputs import SPLIT_JUMP_THRESHOLD, _simple_ratio

# Canonical line-item columns shared by the annual/quarterly financial tables.
_CANONICAL_COLUMNS = [f.key for f in CANONICAL_FIELDS]

# Calculated ratios stored per fiscal year (from CalculatedMetrics historical).
_METRIC_COLUMNS = [
    "ebitda", "ebit", "nopat", "free_cash_flow", "fcf_margin", "levered_fcf",
    "net_debt", "total_debt", "working_capital", "invested_capital",
    "roic", "roa", "roe", "interest_coverage", "debt_to_ebitda",
    "asset_turnover", "inventory_turnover", "receivables_turnover",
    "gross_margin", "operating_margin", "net_margin", "ebitda_margin",
    # Sector-aware ratios (NULL where not applicable to the company's sector).
    "net_interest_margin", "efficiency_ratio", "loan_to_deposit",
    "loss_ratio", "combined_ratio",
    "ffo", "affo", "ffo_per_share", "ffo_payout",
]

# Point-in-time market/valuation columns for the snapshot table.
_SNAPSHOT_COLUMNS = [
    "current_price", "market_cap", "beta", "pe_trailing", "pe_forward",
    "eps_trailing", "price_to_book", "dividend_yield",
    "enterprise_value", "ev_to_ebitda", "ev_to_fcf", "fcf_yield",
    "risk_free_rate",
    "previous_close", "open", "day_high", "day_low", "volume", "avg_volume",
    "avg_volume_10d", "fifty_two_week_high", "fifty_two_week_low", "ma_50", "ma_200",
    "post_market_price", "pre_market_price",
    "peg_ratio", "price_to_sales", "eps_forward",
    "dividend_rate", "payout_ratio",
    "debt_to_equity", "current_ratio", "quick_ratio",
    "shares_outstanding", "float_shares", "shares_short", "shares_short_prior_month",
    "short_ratio", "short_percent_of_float", "insider_percent", "institutional_percent",
]

# Snapshot columns that are text, not numeric (kept separate so _cols_ddl can type them).
_SNAPSHOT_TEXT_COLUMNS = ["ex_dividend_date", "risk_free_rate_date", "risk_free_rate_source"]

# One row per collection: analyst price targets / recommendations (from analyst_estimates).
_ANALYST_COLUMNS = [
    "target_price_low", "target_price_mean", "target_price_median", "target_price_high",
    "recommendation", "recommendation_mean", "number_of_analysts",
    "earnings_date", "forward_eps", "forward_pe",
    "earnings_growth", "revenue_growth", "upside_potential",
]

# Daily OHLCV bars, one row per (ticker, date). Also used for the ^GSPC benchmark
# (see export_benchmark_bars), which writes into this same table.
_PRICE_BAR_COLUMNS = ["open", "high", "low", "close", "volume"]

#: The snapshot's price-dependent metrics a full collection computes itself (``calculated_metrics``).
_EV_FAMILY = ("enterprise_value", "ev_to_ebitda", "ev_to_fcf", "fcf_yield")

# Earnings-surprise history, one row per (ticker, quarter).
_EARNINGS_HISTORY_COLUMNS = ["eps_estimate", "eps_actual", "surprise_pct"]


def read_only_uri(path: Union[str, Path]) -> str:
    """A ``mode=ro`` SQLite URI for the database at *path*.

    SQLite decodes ``%HH`` in a URI's path, and a ``?`` or ``#`` ends the path, so
    those three are escaped: unescaped, a '#' in a folder name cut ``mode=ro`` off
    with the rest of the path, and SQLite opened, read-write, a database it created
    at the truncated path (BUG-080). The rest of the path is left as it is, so a
    UNC path still opens: ``Path.as_uri`` would make its server a URI authority,
    which SQLite refuses.
    """
    text = str(path)
    for char, escape in (("%", "%25"), ("#", "%23"), ("?", "%3f")):
        text = text.replace(char, escape)
    return f"file:{text}?mode=ro"


def _moves_per_share(ratio: float) -> bool:
    """A factor of Yahoo's split history that moves the stored per-share figures: a share
    split (a simple ratio, 5:4 or 1:10 too) or any factor of 3:2 or more (ABT's 2.084 for
    AbbVie), as the valuation reads them (``inputs._apply_later_splits``)."""
    return ratio > 0 and ratio != 1.0 and (_simple_ratio(ratio) or ratio >= SPLIT_JUMP_THRESHOLD
                                           or ratio <= 1.0 / SPLIT_JUMP_THRESHOLD)


def _served_day(stock: StockData, collected_at: str) -> str:
    """The day on Yahoo's calendar a run's dividends were served: the run's last daily bar or
    the latest split Yahoo lists, whichever is later, as a payment stored before
    ``served_on`` is dated. The collector's own date runs ahead of New York's east of it: a
    run before the open on a split's day, which Yahoo hadn't applied yet, dated its pre-split
    payments on the split's day, so they read as split, and DDM read ten times its value after
    a later run whose dividend request failed (BUG-117). A run with neither takes the day
    before its own: Yahoo listed no split, and one it hasn't applied is dated on New York's
    day or later, never before the collector's day less one."""
    days = [str(rec["date"])[:10] for rec in list(stock.price_bars or []) + list(stock.splits or [])
            if rec.get("date")]
    if days:
        return max(days)
    return (date.fromisoformat(collected_at[:10]) - timedelta(days=1)).isoformat()


def _cols_ddl(columns: Sequence[str], col_type: str = "REAL") -> str:
    return ", ".join(f'"{c}" {col_type}' for c in columns)


#: The SEC's SIC code and its description in ``companies`` (Milestone 5): every collection writes them from the
#: submissions feed, and ``python -m src.universe.sic`` fills them for companies collected before.
COMPANY_SIC_COLUMNS: Dict[str, str] = {"sic": "TEXT", "sic_description": "TEXT"}

#: The industry columns the rank pass writes (Milestone 5; docs/superpowers/specs/2026-10-25-universe-automation-
#: design.md, "SIC codes and industries"): the company's SIC industry group, the group's label and member count, and
#: the five ranks and the composite within it, all NULL without a group.
INDUSTRY_COLUMNS: Tuple[str, ...] = (
    "industry_group", "industry_label", "industry_size", "ind_rank_strength", "ind_rank_profitability",
    "ind_rank_growth", "ind_rank_valuation", "ind_rank_momentum", "ind_composite",
)
#: The quality scores' columns (Milestone 2, the industry ones since Milestone 5), in table order. Column names in
#: the export come only from here.
QUALITY_COLUMNS: Tuple[str, ...] = (
    "ticker", "fiscal_year",
    "rev_ps_cagr_3y", "rev_ps_cagr_5y", "rev_ps_cagr_10y", "eps_cagr_3y", "eps_cagr_5y", "eps_cagr_10y",
    "ebitda_ps_cagr_3y", "ebitda_ps_cagr_5y", "ebitda_ps_cagr_10y", "fcf_ps_cagr_5y", "book_ps_cagr_5y",
    "piotroski_f", "piotroski_tested", "piotroski_tests", "altman_z", "altman_zone", "beneish_m",
    "beneish_variables", "sloan_ratio", "roic", "wacc", "roic_minus_wacc", "debt_to_ebitda", "predictability_stars",
    "predictability_r2", "ncav_per_share", "earnings_yield", "return_on_capital", "momentum",
    "rank_strength", "rank_profitability", "rank_growth", "rank_valuation", "rank_momentum", "composite_score",
    "magic_formula_rank", "components", "warning_count", "na_reasons", "computed_at", "ranked_at",
) + INDUSTRY_COLUMNS
#: The columns the rank pass writes.
RANK_COLUMNS: Tuple[str, ...] = ("rank_strength", "rank_profitability", "rank_growth", "rank_valuation",
                                 "rank_momentum", "composite_score", "magic_formula_rank") + INDUSTRY_COLUMNS
_QUALITY_TYPES: Dict[str, str] = {
    "ticker": "TEXT PRIMARY KEY", "fiscal_year": "INTEGER", "piotroski_f": "INTEGER", "piotroski_tested": "INTEGER",
    "piotroski_tests": "TEXT", "altman_zone": "TEXT", "beneish_variables": "TEXT", "predictability_r2": "TEXT",
    "rank_strength": "INTEGER", "rank_profitability": "INTEGER", "rank_growth": "INTEGER",
    "rank_valuation": "INTEGER", "rank_momentum": "INTEGER", "magic_formula_rank": "INTEGER",
    "components": "TEXT", "warning_count": "INTEGER", "na_reasons": "TEXT", "computed_at": "TEXT NOT NULL",
    "ranked_at": "TEXT", "industry_group": "TEXT", "industry_label": "TEXT", "industry_size": "INTEGER",
    "ind_rank_strength": "INTEGER", "ind_rank_profitability": "INTEGER", "ind_rank_growth": "INTEGER",
    "ind_rank_valuation": "INTEGER", "ind_rank_momentum": "INTEGER",
}
#: The columns of ``guru_positions``, in table order.
_GURU_POSITION_COLUMNS: Tuple[str, ...] = (
    "guru_cik", "period", "security", "put_call", "ticker", "cusips", "issuer", "title_of_class", "share_type",
    "shares", "value_usd", "weight", "action", "shares_before", "value_before", "split_factor", "change_pct")


def _position_row(guru_cik: str, period: str, held: "Optional[Position]",
                  change: "Optional[Change]") -> Tuple[Any, ...]:
    """A ``guru_positions`` row: of a position *held* in the quarter, with its *change* from the quarter before (None
    without it), or, *held* None, of a position gone (*change* its 'sold_out'), with no shares, value or weight."""
    source: Any = held if held is not None else change
    after = (held.shares, held.value_usd, held.weight) if held is not None else (None, None, None)
    before = ((change.action, change.shares_before, change.value_before, change.split_factor, change.change_pct)
              if change is not None else (None, None, None, None, None))
    return ((guru_cik, period, source.security, source.put_call or "", source.ticker, ",".join(source.cusips),
             source.issuer, source.title_of_class, source.share_type) + after + before)


@dataclass
class StoreResult:
    """What ``SQLiteStore.write_batch`` did with a batch, in the database at *path*: the tickers *stored* (committed)
    and those *failed*, each with why. A stock whose write failed was rolled back to its own savepoint and the others
    were committed, unless *error* says why the whole export failed (the schema couldn't be made or migrated, the
    database stayed locked, the commit failed, SQLite rolled the transaction back): then none was stored, and every
    ticker is in *failed*."""

    path: Path
    stored: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


class SQLiteStore:
    """Persist StockData into a queryable SQLite database."""

    def __init__(self, db_path: Path, logger: Optional[logging.Logger] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)

    # ---- schema -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS companies (
                ticker TEXT PRIMARY KEY,
                cik TEXT,
                company_name TEXT,
                sector_class TEXT,
                sector TEXT,
                industry TEXT,
                country TEXT,
                employees INTEGER,
                website TEXT,
                fiscal_year_end TEXT,
                description TEXT,
                address TEXT,
                hq_city TEXT,
                hq_state TEXT,
                updated_at TEXT,
                sic TEXT,
                sic_description TEXT
            );

            CREATE TABLE IF NOT EXISTS financials_annual (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                calendar_year INTEGER,
                period_end TEXT,
                filed_date TEXT,
                form TEXT,
                quality_score INTEGER,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, fiscal_year)
            );

            CREATE TABLE IF NOT EXISTS financials_quarterly (
                ticker TEXT NOT NULL,
                period_end TEXT NOT NULL,
                fiscal_year INTEGER,
                fiscal_period TEXT,
                fiscal_quarter INTEGER,
                calendar_year INTEGER,
                calendar_quarter INTEGER,
                filed_date TEXT,
                form TEXT,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, period_end)
            );

            CREATE TABLE IF NOT EXISTS financials_ttm (
                ticker TEXT NOT NULL,
                period_end TEXT NOT NULL,
                fiscal_year INTEGER,
                calendar_year INTEGER,
                calendar_quarter INTEGER,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, period_end)
            );

            CREATE TABLE IF NOT EXISTS financials_annual_vintages (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                accn TEXT NOT NULL,
                filed_date TEXT,
                period_end TEXT,
                form TEXT,
                calendar_year INTEGER,
                {_cols_ddl(_CANONICAL_COLUMNS)},
                PRIMARY KEY (ticker, fiscal_year, accn)
            );

            CREATE INDEX IF NOT EXISTS idx_fa_calendar_year
                ON financials_annual (calendar_year);
            CREATE INDEX IF NOT EXISTS idx_fq_calendar
                ON financials_quarterly (calendar_year, calendar_quarter);
            CREATE INDEX IF NOT EXISTS idx_ttm_calendar
                ON financials_ttm (ticker, calendar_year, calendar_quarter);
            CREATE INDEX IF NOT EXISTS idx_fav_asof
                ON financials_annual_vintages (ticker, fiscal_year, filed_date);

            CREATE TABLE IF NOT EXISTS metrics_annual (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                {_cols_ddl(_METRIC_COLUMNS)},
                PRIMARY KEY (ticker, fiscal_year)
            );

            CREATE TABLE IF NOT EXISTS market_snapshots (
                ticker TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                {_cols_ddl(_SNAPSHOT_COLUMNS)},
                {_cols_ddl(_SNAPSHOT_TEXT_COLUMNS, "TEXT")},
                PRIMARY KEY (ticker, collected_at)
            );

            CREATE TABLE IF NOT EXISTS analyst_snapshots (
                ticker TEXT NOT NULL, collected_at TEXT NOT NULL,
                target_price_low REAL, target_price_mean REAL, target_price_median REAL,
                target_price_high REAL,
                recommendation TEXT, recommendation_mean REAL, number_of_analysts INTEGER,
                earnings_date TEXT, forward_eps REAL, forward_pe REAL,
                earnings_growth REAL, revenue_growth REAL, upside_potential REAL,
                PRIMARY KEY (ticker, collected_at)
            );

            CREATE TABLE IF NOT EXISTS dividend_events (
                ticker TEXT NOT NULL, date TEXT NOT NULL, amount REAL, served_on TEXT,
                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS holders (
                ticker TEXT NOT NULL, holder_type TEXT NOT NULL, holder TEXT NOT NULL,
                shares REAL, date_reported TEXT, pct_held REAL, value REAL, collected_at TEXT,
                PRIMARY KEY (ticker, holder_type, holder)
            );

            CREATE TABLE IF NOT EXISTS insider_transactions (
                ticker TEXT NOT NULL, insider TEXT NOT NULL, start_date TEXT NOT NULL,
                text TEXT NOT NULL,
                position TEXT, shares REAL, value REAL, ownership TEXT, collected_at TEXT,
                PRIMARY KEY (ticker, insider, start_date, text)
            );

            CREATE TABLE IF NOT EXISTS officers (
                ticker TEXT NOT NULL, name TEXT NOT NULL, title TEXT, age INTEGER,
                total_pay REAL,
                PRIMARY KEY (ticker, name)
            );

            CREATE TABLE IF NOT EXISTS price_bars (
                ticker TEXT NOT NULL, date TEXT NOT NULL,
                {_cols_ddl(_PRICE_BAR_COLUMNS)},
                dividend_adjusted INTEGER,
                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS earnings_history (
                ticker TEXT NOT NULL, quarter TEXT NOT NULL,
                {_cols_ddl(_EARNINGS_HISTORY_COLUMNS)},
                PRIMARY KEY (ticker, quarter)
            );

            CREATE TABLE IF NOT EXISTS split_events (
                ticker TEXT NOT NULL, date TEXT NOT NULL, ratio REAL,
                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS collection_runs (
                ticker TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                data_sources TEXT,
                warning_count INTEGER,
                error_count INTEGER,
                quality_score INTEGER,
                warnings TEXT,
                PRIMARY KEY (ticker, collected_at)
            );

            CREATE TABLE IF NOT EXISTS valuations (
                ticker TEXT NOT NULL,
                model TEXT NOT NULL,
                applicable INTEGER NOT NULL,
                na_reason TEXT,
                value_bear REAL, value_base REAL, value_bull REAL,
                assumptions TEXT,
                basis_fiscal_year INTEGER,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (ticker, model)
            );

            CREATE TABLE IF NOT EXISTS valuation_summary (
                ticker TEXT PRIMARY KEY,
                n_applicable INTEGER NOT NULL,
                median_bear REAL, median_base REAL, median_bull REAL,
                computed_at TEXT NOT NULL
            );

            -- The valuation history (docs/superpowers/specs/2026-10-21-valuation-history-design.md):
            -- each month's last close, the multiples on the trailing year known then, and the
            -- historical value, per share on the price bars' basis.
            CREATE TABLE IF NOT EXISTS valuation_history (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL,
                market_cap REAL,
                enterprise_value REAL,
                ttm_period_end TEXT,
                pe REAL, ps REAL, pb REAL, pfcf REAL, ev_ebitda REAL, pffo REAL, div_yield REAL,
                fair_value REAL,
                fair_value_n INTEGER,
                PRIMARY KEY (ticker, date)
            );

            -- Each metric's trailing 10-year range at the latest month, and the historical value
            -- (metric 'fair_value', its current_value, p25 and p75 on the latest quote's basis).
            CREATE TABLE IF NOT EXISTS valuation_ranges (
                ticker TEXT NOT NULL,
                metric TEXT NOT NULL,
                applicable INTEGER NOT NULL,
                na_reason TEXT,
                as_of TEXT,
                window_start TEXT,
                n_months INTEGER,
                current_value REAL,
                pct_rank REAL,
                min REAL, p25 REAL, median REAL, p75 REAL, max REAL,
                implied_value REAL,
                in_fair_value INTEGER,
                n_metrics INTEGER,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (ticker, metric)
            );

            -- The other valuation indicators (docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md):
            -- display-only, like owner earnings, so never in the valuations table, its median or the verdicts.
            -- unit 'per_share' (per share, on the price's basis) or 'rate' (the reverse DCF's implied growth).
            CREATE TABLE IF NOT EXISTS valuation_indicators (
                ticker TEXT NOT NULL,
                indicator TEXT NOT NULL,
                applicable INTEGER NOT NULL,
                na_reason TEXT,
                value_bear REAL, value_base REAL, value_bull REAL,
                unit TEXT NOT NULL,
                assumptions TEXT,
                basis_fiscal_year INTEGER,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (ticker, indicator)
            );

            -- Material us-gaap facts under tags not yet in the canonical registry.
            -- The tag is a row VALUE (not a column), so new taxonomy tags never
            -- require a schema change and material data is never silently lost.
            CREATE TABLE IF NOT EXISTS unmapped_facts (
                ticker TEXT NOT NULL,
                tag TEXT NOT NULL,
                label TEXT,
                unit TEXT,
                period_end TEXT,
                value REAL,
                form TEXT,
                collected_at TEXT,
                PRIMARY KEY (ticker, tag)
            );

            CREATE INDEX IF NOT EXISTS idx_unmapped_tag ON unmapped_facts (tag);
            -- Covers the quality page's top unmapped tags (Reader.unmapped_top: GROUP BY tag, COUNT(DISTINCT ticker),
            -- MAX(label)), so it reads this index instead of a table row a fact (M5-4, the scale pre-flight).
            CREATE INDEX IF NOT EXISTS idx_unmapped_tag_cover ON unmapped_facts (tag, ticker, label);

            -- The quality scores (docs/superpowers/specs/2026-10-21-quality-scores-design.md):
            -- each fiscal year's per-share series on today's split basis, and the warning signs.
            CREATE TABLE IF NOT EXISTS per_share_annual (
                ticker TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                revenue_ps REAL, eps REAL, ebitda_ps REAL, fcf_ps REAL, book_ps REAL,
                PRIMARY KEY (ticker, fiscal_year)
            );

            CREATE TABLE IF NOT EXISTS warning_signs (
                ticker TEXT NOT NULL,
                code TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT,
                value REAL,
                threshold REAL,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (ticker, code)
            );
            """
        )
        # One row per ticker: every score, the CAGRs, the ranks and the composite (QUALITY_COLUMNS).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS quality_scores ("
            + ", ".join(f"{c} {_QUALITY_TYPES.get(c, 'REAL')}" for c in QUALITY_COLUMNS) + ")"
        )
        # Insiders' Form 4 filings (Milestone 3): one row per filing and reporting owner, and the filing's
        # transactions. A filing amended by a 4/A notes its latest amendment (insider_filings.superseded_by), and a
        # trade a 4/A restates names that amendment (insider_trades.superseded_by): the readers count the others. A
        # price that lost its decimal point is flagged (insider_trades.price_suspect: 1, 0 when checked, NULL without
        # a close), kept as filed and left out of the dollar figures.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS insider_filings (
                accession TEXT NOT NULL,
                owner_cik TEXT NOT NULL,
                ticker TEXT NOT NULL,
                form TEXT,
                filed_date TEXT,
                period_of_report TEXT,
                original_filed TEXT,
                owner_name TEXT,
                is_director INTEGER,
                is_officer INTEGER,
                officer_title TEXT,
                is_ten_percent INTEGER,
                is_other INTEGER,
                aff10b5one INTEGER,
                is_ceo_cfo INTEGER,
                superseded_by TEXT,
                collected_at TEXT NOT NULL,
                PRIMARY KEY (accession, owner_cik)
            );
            CREATE INDEX IF NOT EXISTS idx_insider_filings_ticker ON insider_filings (ticker, filed_date);
            CREATE TABLE IF NOT EXISTS insider_trades (
                accession TEXT NOT NULL,
                seq INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                kind TEXT NOT NULL,
                security_title TEXT,
                date TEXT,
                code TEXT,
                shares REAL,
                price REAL,
                price_footnotes TEXT,
                acquired_disposed TEXT,
                owned_after REAL,
                direct_indirect TEXT,
                nature_of_ownership TEXT,
                exercise_price REAL,
                underlying_title TEXT,
                underlying_shares REAL,
                superseded_by TEXT,
                price_suspect INTEGER,
                PRIMARY KEY (accession, seq)
            );
            CREATE INDEX IF NOT EXISTS idx_insider_trades_ticker ON insider_trades (ticker, date);
            -- The screener's insider fields seek a company's trades of one code in the year (its purchases, and for the
            -- net buying its sales) instead of reading all its trades of the year (M5-4, the scale pre-flight).
            CREATE INDEX IF NOT EXISTS idx_insider_trades_code ON insider_trades (ticker, code, date);
            """
        )
        # The gurus' 13F filings (Milestone 3): the curated filers, one row per filing with the cover's totals
        # beside the parsed ones, and each filing's entries as filed (one CUSIP may take several rows). Values are
        # in dollars.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS guru_filers (
                filer_cik TEXT PRIMARY KEY,
                guru_cik TEXT NOT NULL,
                name TEXT,
                manager TEXT
            );
            CREATE TABLE IF NOT EXISTS guru_filings (
                accession TEXT PRIMARY KEY,
                guru_cik TEXT NOT NULL,
                filer_cik TEXT NOT NULL,
                form TEXT,
                period TEXT,
                filed TEXT,
                report_type TEXT,
                amendment_type TEXT,
                entry_total INTEGER,
                value_total REAL,
                rows INTEGER,
                rows_value REAL,
                checksum_ok INTEGER,
                confidential_omitted INTEGER,
                collected_at TEXT NOT NULL,
                multiplier INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_guru_filings_guru ON guru_filings (guru_cik, period);
            CREATE TABLE IF NOT EXISTS guru_holdings (
                accession TEXT NOT NULL,
                row INTEGER NOT NULL,
                guru_cik TEXT NOT NULL,
                cusip TEXT,
                issuer TEXT,
                title_of_class TEXT,
                value_usd REAL,
                shares REAL,
                share_type TEXT,
                put_call TEXT,
                discretion TEXT,
                PRIMARY KEY (accession, row)
            );
            CREATE INDEX IF NOT EXISTS idx_guru_holdings_cusip ON guru_holdings (cusip);
            CREATE INDEX IF NOT EXISTS idx_guru_holdings_guru ON guru_holdings (guru_cik);
            -- The holdings' CUSIPs and their tickers: our universe's from the curated map ('universe'), the others'
            -- through OpenFIGI ('openfigi', or 'none' where it found nothing: no ticker, the holdings' issuer).
            CREATE TABLE IF NOT EXISTS cusip_map (
                cusip TEXT PRIMARY KEY,
                ticker TEXT,
                name TEXT,
                source TEXT NOT NULL,
                security_type TEXT,
                mapped_at TEXT NOT NULL
            );
            -- Each guru's quarters as the collector builds them (ownership.portfolio), for the pages, the holders tab
            -- and the screener: the quarter's total, held positions, completeness, whether the quarter before is
            -- there (so the changes exist), its filings (a JSON list) and the latest of their filing dates.
            CREATE TABLE IF NOT EXISTS guru_quarters (
                guru_cik TEXT NOT NULL,
                period TEXT NOT NULL,
                total_value REAL,
                positions INTEGER,
                complete INTEGER,
                has_prior INTEGER,
                accessions TEXT,
                filed TEXT,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (guru_cik, period)
            );
            -- One row per position of the quarter (put_call '' for shares and principal amounts, else 'Put' or
            -- 'Call') with its change from the quarter before (action NULL without it), and a 'sold_out' row, its
            -- shares, value and weight NULL, for each position of the quarter before that's gone.
            CREATE TABLE IF NOT EXISTS guru_positions (
                guru_cik TEXT NOT NULL,
                period TEXT NOT NULL,
                security TEXT,
                put_call TEXT NOT NULL,
                ticker TEXT,
                cusips TEXT,
                issuer TEXT,
                title_of_class TEXT,
                share_type TEXT,
                shares REAL,
                value_usd REAL,
                weight REAL,
                action TEXT,
                shares_before REAL,
                value_before REAL,
                split_factor REAL,
                change_pct REAL,
                PRIMARY KEY (guru_cik, period, security, put_call)
            );
            CREATE INDEX IF NOT EXISTS idx_guru_positions_ticker ON guru_positions (ticker);
            CREATE INDEX IF NOT EXISTS idx_guru_positions_guru ON guru_positions (guru_cik, period);
            """
        )
        # The market data (Milestone 4; docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md, "The
        # market data"): one row per series and date, the series named by source ("fred:NCBEILQ027S", "fred:GDP",
        # "shiller:price", "shiller:dividend", "shiller:earnings", "shiller:cpi", "shiller:cape", "ust:1m" ...
        # "ust:30y"), each replaced when its source is fetched (whole, the Treasury's from the run's first year on);
        # and each source's latest fetch ("fred:<ID>", "shiller", "treasury"): its url (Shiller's workbook link, the
        # Treasury's latest year), the rows it stored and when its answer was fetched.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_series (
                series TEXT NOT NULL,
                date TEXT NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (series, date)
            );
            CREATE TABLE IF NOT EXISTS market_fetches (
                source TEXT PRIMARY KEY,
                url TEXT,
                rows INTEGER,
                fetched_at TEXT NOT NULL
            );
            """
        )
        # ``stock-data refresh``'s log (Milestone 5; docs/superpowers/specs/2026-10-25-universe-automation-design.md,
        # "stock-data refresh"): one row per step and run, ok 1 or 0 for a step that ran, NULL for one skipped, its
        # summary as JSON. A step is skipped while it succeeded within its period.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS refresh_log (id INTEGER PRIMARY KEY, step TEXT NOT NULL, "
            "started_at TEXT NOT NULL, finished_at TEXT NOT NULL, ok INTEGER, summary TEXT)")

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
        """Add any of ``columns`` (name -> SQL type) missing from ``table``.

        Uses ``PRAGMA table_info`` to inspect the live schema and
        ``ALTER TABLE ... ADD COLUMN`` for anything not already present. Never drops
        or rewrites existing columns, so it is safe to run repeatedly (idempotent)
        and against long-lived production databases created under an older schema.
        ``table`` must be a module-level constant, never user input.
        """
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, col_type in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {col_type}')

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add any columns missing from an existing DB (registry grows over time).

        ``CREATE TABLE IF NOT EXISTS`` won't add new columns to a pre-existing table,
        so we reconcile each table against its current expected column set.
        """
        expected: Dict[str, Dict[str, str]] = {
            "companies": {
                "sector_class": "TEXT", "fiscal_year_end": "TEXT",
                "description": "TEXT", "address": "TEXT",
                "hq_city": "TEXT", "hq_state": "TEXT",
                **COMPANY_SIC_COLUMNS,
            },
            "financials_annual": {"calendar_year": "INTEGER",
                                   **{c: "REAL" for c in _CANONICAL_COLUMNS}},
            "financials_quarterly": {"calendar_year": "INTEGER",
                                      "calendar_quarter": "INTEGER",
                                      "fiscal_quarter": "INTEGER",
                                      **{c: "REAL" for c in _CANONICAL_COLUMNS}},
            "financials_ttm": {"fiscal_year": "INTEGER", "calendar_year": "INTEGER",
                               "calendar_quarter": "INTEGER",
                               **{c: "REAL" for c in _CANONICAL_COLUMNS}},
            "financials_annual_vintages": {"calendar_year": "INTEGER",
                                            **{c: "REAL" for c in _CANONICAL_COLUMNS}},
            "metrics_annual": {c: "REAL" for c in _METRIC_COLUMNS},
            "market_snapshots": {**{c: "REAL" for c in _SNAPSHOT_COLUMNS},
                                 **{c: "TEXT" for c in _SNAPSHOT_TEXT_COLUMNS}},
            "collection_runs": {"warnings": "TEXT"},
        }
        for table, columns in expected.items():
            self._ensure_columns(conn, table, columns)
        # Bars stored before the fetch asked for market prices are Yahoo's
        # dividend-adjusted ones (yfinance's default; BUG-075). Flag them once,
        # as the column arrives, so readers rebuild their market closes: in one
        # transaction, committed before the run writes anything, since an ALTER
        # outside it commits at once and a run that then failed kept the column
        # and lost the flags for good (BUG-083).
        bar_columns = {row[1] for row in conn.execute("PRAGMA table_info(price_bars)")}
        if bar_columns and "dividend_adjusted" not in bar_columns:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Another process may have migrated while this one waited for the lock.
                if "dividend_adjusted" not in {row[1] for row in conn.execute("PRAGMA table_info(price_bars)")}:
                    conn.execute("ALTER TABLE price_bars ADD COLUMN dividend_adjusted INTEGER")
                    conn.execute("UPDATE price_bars SET dividend_adjusted = 1")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        # A payment's amount is Yahoo's, split-adjusted as of the day it served it, which
        # each row now keeps (BUG-103). Until then a run stored no split without the
        # dividends from the same chart request, so a payment stored before the column
        # is on the basis of everything its ticker's series show: dated by its last bar
        # or its latest split, whichever is later, the rule it was read by. As the flags:
        # in one transaction, committed before the run writes anything (BUG-083).
        dividend_columns = {row[1] for row in conn.execute("PRAGMA table_info(dividend_events)")}
        if dividend_columns and "served_on" not in dividend_columns:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if "served_on" not in {row[1] for row in conn.execute("PRAGMA table_info(dividend_events)")}:
                    conn.execute("ALTER TABLE dividend_events ADD COLUMN served_on TEXT")
                    conn.execute(
                        "UPDATE dividend_events SET served_on = (SELECT max(day) FROM ("
                        "SELECT substr(max(date), 1, 10) AS day FROM price_bars WHERE ticker = dividend_events.ticker "
                        "UNION ALL SELECT substr(max(date), 1, 10) FROM split_events "
                        "WHERE ticker = dividend_events.ticker))")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    # ---- upsert helper ----------------------------------------------------

    @staticmethod
    def _upsert(conn: sqlite3.Connection, table: str, keys: Sequence[str],
                row: Dict[str, Any], keep_existing: bool = False, fill_only: bool = False) -> None:
        """Insert or update ``row`` in ``table`` keyed on ``keys``.

        With ``keep_existing``, a NULL in ``row`` leaves the stored value as it is: a
        run that didn't get a field (a failed fetch) mustn't erase it. With ``fill_only``,
        every stored value stays and ``row`` fills only the NULL ones: a run that can't
        speak for what's stored (a successor's whose predecessor wasn't read, BUG-179)
        adds rows and fills gaps.
        """
        columns = list(row.keys())
        placeholders = ", ".join("?" for _ in columns)
        col_list = ", ".join(f'"{c}"' for c in columns)
        updates = ", ".join(
            f'"{c}"=COALESCE("{c}", excluded."{c}")' if fill_only
            else f'"{c}"=COALESCE(excluded."{c}", "{c}")' if keep_existing else f'"{c}"=excluded."{c}"'
            for c in columns if c not in keys
        )
        conflict = ", ".join(f'"{k}"' for k in keys)
        sql = (
            f'INSERT INTO {table} ({col_list}) VALUES ({placeholders}) '
            f'ON CONFLICT ({conflict}) DO UPDATE SET {updates}'
        )
        conn.execute(sql, [row[c] for c in columns])

    @staticmethod
    def _canonical_values(period: Dict[str, Any], restated: bool = False) -> Dict[str, Any]:
        """Pull only canonical numeric columns from a period dict; *restated*, every one of
        them, those the period lacks as NULL (BUG-029)."""
        if restated:
            return {c: period.get(c) for c in _CANONICAL_COLUMNS}
        return {c: period.get(c) for c in _CANONICAL_COLUMNS if c in period}

    @staticmethod
    def _stored_year(conn: sqlite3.Connection, ticker: str, fiscal_year: int) -> Dict[str, Any]:
        """*ticker*'s stored ``financials_annual`` row for *fiscal_year* (one the run has just written), as a
        dict of its columns."""
        cur = conn.execute("SELECT * FROM financials_annual WHERE ticker = ? AND fiscal_year = ?",
                           (ticker, fiscal_year))
        return dict(zip([d[0] for d in cur.description], cur.fetchone()))

    @staticmethod
    def _first(rec: Dict[str, Any], *keys: str) -> Any:
        """Return the first present, non-None value among ``keys``.

        yfinance's DataFrame column names vary across versions/locales (e.g.
        ``"Holder"`` vs ``"holder"``, ``"Date Reported"`` vs ``"dateReported"``), so
        record normalization tries each candidate key defensively rather than
        assuming one literal spelling.
        """
        for key in keys:
            if key in rec and rec[key] is not None:
                return rec[key]
        return None

    # ---- public API -------------------------------------------------------

    def export(self, data: List[StockData]) -> Optional[Path]:
        """Write a batch of StockData rows (``write_batch``); returns the DB path when it stored any of them, else
        None."""
        result = self.write_batch(data)
        return result.path if result.stored else None

    def write_batch(self, data: List[StockData]) -> StoreResult:
        """Write *data* in one transaction, each stock in its own savepoint (code review 19, I-1).

        A stock whose write fails is rolled back to its savepoint alone, logged with its ticker and the error, and
        recorded on its StockData as an error ("SQLite write failed: ..."); the others are committed. The schema, its
        migration and the write lock (``BEGIN IMMEDIATE``, held for the batch) come first. When one of them fails (a
        locked database), the commit fails, or SQLite rolls the whole transaction back (a full disk), none of the batch
        is stored: that is logged as the export's failure and recorded on each stock, and the result's *error* says
        why. Anything else raised (Ctrl+C) rolls the batch back and propagates.
        """
        result = StoreResult(path=self.db_path)
        if not data:
            return result
        try:
            conn = self._connect()
        except Exception as e:
            return self._batch_failed(result, data, e)
        try:
            try:
                self._create_schema(conn)
                self._migrate(conn)
                if conn.in_transaction:
                    conn.commit()
                conn.execute("BEGIN IMMEDIATE")
            except Exception as e:
                return self._batch_failed(result, data, e)
            for stock in data:
                try:
                    conn.execute("SAVEPOINT stock")
                    self._write_stock(conn, stock)
                    conn.execute("RELEASE SAVEPOINT stock")
                except Exception as e:
                    if not self._rolled_back_to_savepoint(conn):
                        return self._batch_failed(result, data, e)
                    result.failed[stock.ticker] = str(e)
                    stock.add_error(f"SQLite write failed: {e}")
                    self.logger.error(f"SQLite write failed for {stock.ticker}: {e} (its rows rolled back; the "
                                      "batch's others go on)")
                    continue
                result.stored.append(stock.ticker)
            try:
                conn.commit()
            except Exception as e:
                return self._batch_failed(result, data, e)
        finally:
            conn.close()  # uncommitted, the transaction is rolled back
        self.logger.info(f"Wrote {len(result.stored)} tickers to {self.db_path}"
                         + (f"; not stored: {', '.join(result.failed)}" if result.failed else ""))
        return result

    @staticmethod
    def _rolled_back_to_savepoint(conn: sqlite3.Connection) -> bool:
        """Undo a failed stock's writes, back to its savepoint, keeping the batch's others. False when SQLite has
        rolled the whole transaction back already (a full disk, an I/O error), the batch's earlier stocks with it."""
        if not conn.in_transaction:
            return False
        try:
            conn.execute("ROLLBACK TO SAVEPOINT stock")
            conn.execute("RELEASE SAVEPOINT stock")
        except sqlite3.Error:
            return False
        return True

    def _batch_failed(self, result: StoreResult, data: List[StockData], error: Exception) -> StoreResult:
        """The export of *data* failed as a whole with *error*: none of it is stored. Each stock that hadn't failed on
        its own records the error, and the result says why."""
        why = str(error)
        own = result.failed
        result.error, result.stored, result.failed = why, [], {}
        for stock in data:
            if stock.ticker in own:
                result.failed[stock.ticker] = own[stock.ticker]
            else:
                result.failed[stock.ticker] = why
                stock.add_error(f"SQLite write failed: {why}")
        self.logger.error(f"Error writing SQLite store: {why}; none of the {len(data)} tickers was stored "
                          f"({', '.join(result.failed)})")
        return result

    def _write_stock(self, conn: sqlite3.Connection, stock: StockData) -> None:
        collected_at = (
            stock.collected_at.isoformat()
            if hasattr(stock.collected_at, "isoformat")
            else str(stock.collected_at)
        )
        quality_score = (stock.data_quality or {}).get("score")
        info = stock.company_info or {}
        submissions = stock.sec_submissions or {}

        # companies: the SEC's SIC code and description from the submissions feed (Milestone 5), NULL where the feed
        # has none; a run without the feed (a failed request) keeps the stored ones, as every field here.
        self._upsert(conn, "companies", ["ticker"], {
            "ticker": stock.ticker,
            "cik": stock.cik,
            "company_name": stock.company_name,
            "sector_class": stock.sector_class,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "employees": info.get("full_time_employees"),
            "website": info.get("website"),
            "fiscal_year_end": submissions.get("fiscal_year_end"),
            "description": info.get("description") or info.get("longBusinessSummary"),
            "address": info.get("address") or info.get("address1"),
            "hq_city": info.get("hq_city") or info.get("city"),
            "hq_state": info.get("hq_state") or info.get("state"),
            "updated_at": collected_at,
            "sic": normalize_sic(submissions.get("sic")),
            "sic_description": normalize_sic_description(submissions.get("sic_description")),
        }, keep_existing=True)

        # A run that parsed the filer's annual record restates every period it gives: a field its
        # parse leaves empty is cleared, not kept from an earlier parse (BUG-029). A filer with no
        # 10-K yet (XOM's new holding company, SPCX) gives a few quarters only, which can't speak
        # for what a predecessor filed, so its run only adds. A successor's run whose predecessor's
        # filings couldn't be read (a 503, a 404) speaks for nothing they hold: its own reports give
        # the years before them as their comparatives do (BLK's balance sheets start at 2023-12-31),
        # some figures from another tag (BLK's and XOM's equity with the noncontrolling interests)
        # and quarters without their fiscal quarter. So it only fills, in every table its SEC record
        # writes: a period or a figure not stored yet, never one stored, until a run reads both
        # filers again (BUG-179).
        restated = bool(stock.financials_annual)
        fill = bool(stock.predecessor_error)

        # financials_annual + metrics_annual
        historical = (stock.calculated_metrics or {}).get("historical", {})
        for year, period in (stock.financials_annual or {}).items():
            row = {
                "ticker": stock.ticker,
                "fiscal_year": int(year) if str(year).isdigit() else None,
                "calendar_year": period.get("calendar_year"),
                "period_end": period.get("period_end"),
                "filed_date": period.get("filed_date"),
                "form": period.get("form"),
                "quality_score": quality_score,
            }
            row.update(self._canonical_values(period, restated))
            self._upsert(conn, "financials_annual", ["ticker", "fiscal_year"], row, fill_only=fill)

            if fill:
                # A year's metrics are its own figures' (calculate_all reads the one year), so a run
                # that only fills computes them on the year as stored, the figures it kept beside
                # those it filled in, not on its own part of the year (BUG-179).
                year_metrics = CalculatedMetrics(self.logger).calculate_historical(
                    {year: self._stored_year(conn, stock.ticker, row["fiscal_year"])},
                    sector=stock.sector_class)[year]
            else:
                year_metrics = historical.get(year) or {}
            if year_metrics and "error" not in year_metrics:
                mrow = {"ticker": stock.ticker,
                        "fiscal_year": row["fiscal_year"]}
                mrow.update({c: year_metrics.get(c) for c in _METRIC_COLUMNS})
                self._upsert(conn, "metrics_annual", ["ticker", "fiscal_year"], mrow)

        # financials_quarterly
        for period_end, period in (stock.financials_quarterly or {}).items():
            row = {
                "ticker": stock.ticker,
                "period_end": period_end,
                "fiscal_year": period.get("fiscal_year"),
                "fiscal_period": period.get("fiscal_period"),
                "fiscal_quarter": period.get("fiscal_quarter"),
                "calendar_year": period.get("calendar_year"),
                "calendar_quarter": period.get("calendar_quarter"),
                "filed_date": period.get("filed_date"),
                "form": period.get("form"),
            }
            row.update(self._canonical_values(period, restated))
            self._upsert(conn, "financials_quarterly", ["ticker", "period_end"], row, fill_only=fill)

        # financials_ttm (trailing-twelve-month series)
        for period_end, period in (stock.financials_ttm or {}).items():
            row = {
                "ticker": stock.ticker,
                "period_end": period_end,
                "fiscal_year": period.get("fiscal_year"),
                "calendar_year": period.get("calendar_year"),
                "calendar_quarter": period.get("calendar_quarter"),
            }
            row.update(self._canonical_values(period, restated))
            self._upsert(conn, "financials_ttm", ["ticker", "period_end"], row, fill_only=fill)

        # financials_annual_vintages (point-in-time: one row per filing/accession)
        for fy, by_accn in (stock.financials_annual_vintages or {}).items():
            for accn, period in by_accn.items():
                vrow = {
                    "ticker": stock.ticker,
                    "fiscal_year": int(fy) if str(fy).isdigit() else None,
                    "accn": accn,
                    "filed_date": period.get("filed_date"),
                    "period_end": period.get("period_end"),
                    "form": period.get("form"),
                    "calendar_year": period.get("calendar_year"),
                }
                vrow.update(self._canonical_values(period, restated))
                self._upsert(conn, "financials_annual_vintages",
                             ["ticker", "fiscal_year", "accn"], vrow, fill_only=fill)

        # market_snapshots (point-in-time)
        md = stock.market_data or {}
        val = stock.valuation or {}
        shareholders = stock.shareholders or {}
        cm = stock.calculated_metrics or {}
        rf = stock.risk_free_rate or {}
        # Existing pattern: merge the source dicts and pick columns by name — every
        # new snapshot column added to _SNAPSHOT_COLUMNS/_SNAPSHOT_TEXT_COLUMNS is
        # picked up automatically as long as its key matches across market_data/
        # valuation/shareholders.
        merged: Dict[str, Any] = {**md, **val, **shareholders}
        snapshot: Dict[str, Any] = {
            "ticker": stock.ticker,
            "collected_at": collected_at,
        }
        for col in _SNAPSHOT_COLUMNS:
            snapshot[col] = merged.get(col)
        for col in _SNAPSHOT_TEXT_COLUMNS:
            snapshot[col] = merged.get(col)
        # These are computed downstream (calculated_metrics / risk_free_rate), not
        # present verbatim on market_data/valuation/shareholders.
        snapshot["enterprise_value"] = cm.get("enterprise_value")
        snapshot["ev_to_ebitda"] = cm.get("ev_to_ebitda")
        snapshot["ev_to_fcf"] = cm.get("ev_to_fcf")
        snapshot["fcf_yield"] = cm.get("fcf_yield")
        snapshot["risk_free_rate"] = rf.get("risk_free_rate")
        snapshot["risk_free_rate_date"] = rf.get("date")
        snapshot["risk_free_rate_source"] = rf.get("source")
        # Only record a snapshot when the run got a price: the latest snapshot is where the
        # price, verdict and upside come from. A Yahoo failure still gets a risk-free rate,
        # and the price history (the moving averages) can work while the quote fails.
        if snapshot.get("current_price") is not None:
            self._upsert(conn, "market_snapshots", ["ticker", "collected_at"], snapshot)

        # collection_runs
        self._upsert(conn, "collection_runs", ["ticker", "collected_at"], {
            "ticker": stock.ticker,
            "collected_at": collected_at,
            "data_sources": ", ".join(stock.data_sources),
            "warning_count": len(stock.warnings),
            "error_count": len(stock.errors),
            "quality_score": quality_score,
            # The text too: the web app shows why a record came back empty.
            "warnings": json.dumps(stock.warnings),
        })

        # unmapped_facts (tags not yet in the canonical registry; tag is a row value): each tag's
        # latest material fact, which a run without the predecessor's facts can give as an older one
        # of the successor's, so that run only fills here too (BUG-179)
        for fact in (stock.unmapped_facts or []):
            self._upsert(conn, "unmapped_facts", ["ticker", "tag"], {
                "ticker": stock.ticker,
                "tag": fact.get("tag"),
                "label": fact.get("label"),
                "unit": fact.get("unit"),
                "period_end": fact.get("period_end"),
                "value": fact.get("value"),
                "form": fact.get("form"),
                "collected_at": collected_at,
            }, fill_only=fill)

        # holders (replace-per-run: institutional + mutualfund)
        self._write_holders(conn, stock.ticker, collected_at, shareholders)

        # insider_transactions (upsert on 4-part natural key)
        self._write_insider_transactions(conn, stock.ticker, collected_at, shareholders)

        # officers (replace-per-run)
        self._write_officers(conn, stock.ticker, info)

        # analyst_snapshots (one row per collection)
        self._write_analyst_snapshot(conn, stock.ticker, collected_at, stock.analyst_estimates or {})

        # dividend_events (upsert all known payments, with the day Yahoo served them)
        self._write_dividend_events(conn, stock.ticker, stock.dividend_history or {},
                                    _served_day(stock, collected_at))

        # price_bars / earnings_history / split_events (upsert all known records)
        self._write_price_bars(conn, stock.ticker, stock.price_bars or [])
        self._write_earnings_history(conn, stock.ticker, stock.earnings_history or [])
        self._write_split_events(conn, stock.ticker, stock.splits or [])

    def _write_holders(self, conn: sqlite3.Connection, ticker: str, collected_at: str,
                        shareholders: Dict[str, Any]) -> None:
        """Replace-per-run: institutional + mutualfund holders from yfinance DataFrames.

        yfinance's column names vary by version, so every field is looked up
        defensively via ``_first``; records with no holder name are skipped.

        The DELETE only runs when there's at least one insertable record: a
        transient sub-fetch failure yields empty holder lists, and wiping the
        previously-good rows on every such hiccup would lose data until the
        next successful run for no benefit.
        """
        insertable = []
        for holder_type, key in (("institutional", "institutional_holders"),
                                  ("mutualfund", "mutualfund_holders")):
            for rec in shareholders.get(key) or []:
                holder = self._first(rec, "Holder", "holder")
                if not holder:
                    continue
                insertable.append((holder_type, holder, rec))

        if not insertable:
            self.logger.debug(
                f"No holders returned for {ticker} this run; keeping previous rows"
            )
            return

        conn.execute("DELETE FROM holders WHERE ticker = ?", (ticker,))
        for holder_type, holder, rec in insertable:
            self._upsert(conn, "holders", ["ticker", "holder_type", "holder"], {
                "ticker": ticker,
                "holder_type": holder_type,
                "holder": holder,
                "shares": self._first(rec, "Shares", "shares"),
                "date_reported": self._first(rec, "Date Reported", "dateReported",
                                              "date_reported"),
                "pct_held": self._first(rec, "pctHeld", "% Out", "pct_held"),
                "value": self._first(rec, "Value", "value"),
                "collected_at": collected_at,
            })

    def _write_insider_transactions(self, conn: sqlite3.Connection, ticker: str,
                                     collected_at: str, shareholders: Dict[str, Any]) -> None:
        """Upsert insider Form-4-derived transactions on the (ticker, insider, start_date, text) key.

        Records missing an insider name or start date are skipped (they can't form
        a valid natural key); missing text is coerced to ``""`` since it's part of
        the primary key but not always populated by yfinance.
        """
        for rec in shareholders.get("insider_transactions") or []:
            insider = self._first(rec, "Insider", "insider")
            start_date = self._first(rec, "Start Date", "startDate", "start_date")
            if not insider or not start_date:
                continue
            text = self._first(rec, "Text", "text") or ""
            self._upsert(conn, "insider_transactions",
                         ["ticker", "insider", "start_date", "text"], {
                "ticker": ticker,
                "insider": insider,
                "start_date": start_date,
                "text": text,
                "position": self._first(rec, "Position", "position"),
                "shares": self._first(rec, "Shares", "shares"),
                "value": self._first(rec, "Value", "value"),
                "ownership": self._first(rec, "Ownership", "ownership"),
                "collected_at": collected_at,
            })

    def _write_officers(self, conn: sqlite3.Connection, ticker: str,
                         info: Dict[str, Any]) -> None:
        """Replace-per-run: company officers (roster changes over time, not additive).

        The DELETE only runs when there's at least one insertable record: a
        transient sub-fetch failure yields an empty officer list, and wiping
        the previously-good rows on every such hiccup would lose data until
        the next successful run for no benefit.
        """
        insertable = [o for o in (info.get("officers") or []) if o.get("name")]
        if not insertable:
            self.logger.debug(
                f"No officers returned for {ticker} this run; keeping previous rows"
            )
            return

        conn.execute("DELETE FROM officers WHERE ticker = ?", (ticker,))
        for officer in insertable:
            self._upsert(conn, "officers", ["ticker", "name"], {
                "ticker": ticker,
                "name": officer["name"],
                "title": officer.get("title"),
                "age": officer.get("age"),
                "total_pay": officer.get("total_pay"),
            })

    def _write_analyst_snapshot(self, conn: sqlite3.Connection, ticker: str, collected_at: str,
                                 analyst: Dict[str, Any]) -> None:
        """One row per collection run, only when at least one analyst field is known."""
        if not analyst:
            return
        row: Dict[str, Any] = {"ticker": ticker, "collected_at": collected_at}
        row.update({c: analyst.get(c) for c in _ANALYST_COLUMNS})
        if any(row.get(c) is not None for c in _ANALYST_COLUMNS):
            self._upsert(conn, "analyst_snapshots", ["ticker", "collected_at"], row)

    def _write_dividend_events(self, conn: sqlite3.Connection, ticker: str,
                                dividend_history: Dict[str, Any], served_on: str) -> None:
        """Upsert every known dividend payment (append-only in practice; history rarely changes),
        each with *served_on*, the run's day on Yahoo's calendar (``_served_day``): Yahoo
        split-adjusts a payment for every split up to the day it serves it (BUG-103). A run
        whose dividend request failed writes none, so the stored ones keep their day."""
        for rec in dividend_history.get("dividend_payments") or []:
            date = rec.get("date")
            if not date:
                continue
            self._upsert(conn, "dividend_events", ["ticker", "date"], {
                "ticker": ticker,
                "date": date,
                "amount": rec.get("amount"),
                "served_on": served_on,
            })

    def _write_price_bars(self, conn: sqlite3.Connection, ticker: str,
                           bars: List[Dict[str, Any]]) -> None:
        """Upsert daily OHLCV bars on (ticker, date). Shared by per-stock export and
        ``export_benchmark_bars`` (e.g. ^GSPC), which uses ``ticker`` as the index symbol."""
        for bar in bars:
            date = bar.get("date")
            if not date:
                continue
            row = {"ticker": ticker, "date": date}
            row.update({c: bar.get(c) for c in _PRICE_BAR_COLUMNS})
            row["dividend_adjusted"] = None  # market prices (see _migrate)
            self._upsert(conn, "price_bars", ["ticker", "date"], row)

    def _write_earnings_history(self, conn: sqlite3.Connection, ticker: str,
                                 earnings_history: List[Dict[str, Any]]) -> None:
        """Upsert earnings-surprise records (estimate vs. actual EPS) on (ticker, quarter)."""
        for rec in earnings_history:
            quarter = rec.get("quarter")
            if not quarter:
                continue
            row = {"ticker": ticker, "quarter": quarter}
            row.update({c: rec.get(c) for c in _EARNINGS_HISTORY_COLUMNS})
            self._upsert(conn, "earnings_history", ["ticker", "quarter"], row)

    def _write_split_events(self, conn: sqlite3.Connection, ticker: str,
                             splits: List[Dict[str, Any]]) -> None:
        """Upsert stock split events on (ticker, date)."""
        for rec in splits:
            date = rec.get("date")
            if not date:
                continue
            self._upsert(conn, "split_events", ["ticker", "date"], {
                "ticker": ticker,
                "date": date,
                "ratio": rec.get("ratio"),
            })

    def replace_price_bars_from(self, ticker: str, start: str, bars: List[Dict[str, Any]]) -> Tuple[int, int]:
        """Replace *ticker*'s daily bars dated *start* (ISO) or later with *bars*, each dated *start* or later, in
        one transaction: a failure changes nothing. The bars are market prices as a collection stores them
        (``_write_price_bars``: ``dividend_adjusted`` NULL). ``stock-data refresh``'s bars step: the last stored bar
        may have been partial, so it is replaced with the days since. Returns the rows removed and written; raises
        ValueError for a bar before *start* (none is written) and sqlite3.Error for a failed write."""
        early = [str(bar.get("date")) for bar in bars if not bar.get("date") or str(bar["date"])[:10] < start[:10]]
        if early:
            raise ValueError(f"{ticker}: bars before {start} can't replace the bars from {start} on: "
                             f"{', '.join(early)}")
        conn = self._connect()
        try:
            self._create_schema(conn)
            removed = conn.execute("DELETE FROM price_bars WHERE ticker = ? AND date >= ?", (ticker, start)).rowcount
            self._write_price_bars(conn, ticker, bars)
            conn.commit()
        finally:
            conn.close()  # uncommitted, the transaction is rolled back
        return removed, len(bars)

    def record_refresh_step(self, step: str, started_at: str, finished_at: str, ok: Optional[bool],
                            summary: Dict[str, Any]) -> None:
        """Append a ``refresh_log`` row: a ``stock-data refresh`` run's *step*, when it started and finished, *ok*
        (True or False for a step that ran, None for one skipped) and its *summary* as JSON."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            conn.execute("INSERT INTO refresh_log (step, started_at, finished_at, ok, summary) VALUES (?, ?, ?, ?, ?)",
                         (step, started_at, finished_at, None if ok is None else int(ok),
                          json.dumps(summary, default=str)))
            conn.commit()
        finally:
            conn.close()

    def last_refresh_success(self, step: str) -> Optional[str]:
        """When *step* last finished ok (``refresh_log``'s latest ``finished_at`` with ok 1), read through a
        read-only connection; None without one, or without the table."""
        try:
            conn = sqlite3.connect(read_only_uri(self.db_path), uri=True)
        except sqlite3.Error:
            return None  # nothing stored yet
        try:
            row = conn.execute("SELECT max(finished_at) FROM refresh_log WHERE step = ? AND ok = 1", (step,)).fetchone()
        except sqlite3.OperationalError:  # a database from before the refresh's log
            return None
        finally:
            conn.close()
        return row[0] if row else None

    def splits_since_collection(self, ticker: str, splits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Of the factors since the last collection (``factors_since_collection``), those
        a refresh can't put right: a share split (a simple ratio) or a factor of 3:2 or
        more moves the stored per-share figures, which only a full collection restates
        (BUG-093, BUG-106). Yahoo's split history also holds stock dividends (SCCO's
        quarterly 1.01) and spin-off price factors (HON's 0.9535): a saved refresh
        stores them, and the quote job values the ticker on them (BUG-128).
        """
        return [s for s in self.factors_since_collection(ticker, splits) if _moves_per_share(float(s["ratio"]))]

    def factors_since_collection(self, ticker: str, splits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Of *splits* (Yahoo's ``{date, ratio}`` history for *ticker*), those the
        stored valuations aren't on the basis of, so a quote after them would sit
        beside them on another (BUG-093).

        The valuations are compared with the latest snapshot's price, as the engine
        reads it (``inputs.load_inputs``' ``priced_on``), so a split dated after that
        price's day counts even when stored: a later collection whose quote failed
        stores the split but keeps the older price (BUG-102). A split missing from
        ``split_events`` counts from the price's day, or from the ticker's
        last collection with Yahoo if that is earlier (Yahoo can list a split days
        after a refresh priced it): a split on either day may have come after it.
        Every factor counts but 1.0: a stock dividend's or a spin-off's rebases
        Yahoo's prices and dividends, which the DDM and the multiples band are
        built on, like a split of its size (BUG-128).
        """
        candidates = [s for s in splits if s.get("date") and s.get("ratio") and float(s["ratio"]) > 0
                      and float(s["ratio"]) != 1.0]
        if not candidates:
            return []
        try:
            conn = sqlite3.connect(read_only_uri(self.db_path), uri=True)
        except sqlite3.Error:
            return []  # nothing stored yet
        try:
            known = {str(d)[:10] for (d,) in conn.execute(
                "SELECT date FROM split_events WHERE ticker = ?", (ticker,))}
            snap = conn.execute("SELECT collected_at, current_price FROM market_snapshots WHERE ticker = ? "
                                "ORDER BY collected_at DESC LIMIT 1", (ticker,)).fetchone()
            priced_on = str(snap[0])[:10] if snap and snap[0] and snap[1] is not None else None
            since = None
            # The last collection with Yahoo: its run row, else (no such row) its
            # latest price bar, which only Yahoo gives.
            for sql in ("SELECT max(collected_at) FROM collection_runs WHERE ticker = ? "
                        "AND data_sources LIKE '%yahoo%'",
                        "SELECT max(date) FROM price_bars WHERE ticker = ?"):
                row = conn.execute(sql, (ticker,)).fetchone()
                if row and row[0]:
                    since = str(row[0])[:10]
                    break
        except sqlite3.Error:
            return []
        finally:
            conn.close()
        starts = [day for day in (priced_on, since) if day]
        if not starts:
            return []
        return [s for s in candidates
                if (priced_on and str(s["date"])[:10] > priced_on)
                or (str(s["date"])[:10] not in known and str(s["date"])[:10] >= min(starts))]

    def upsert_quote(self, ticker: str, quote: Dict[str, Any], collected_at: str,
                     splits: Optional[List[Dict[str, Any]]] = None) -> None:
        """Write ONE ``market_snapshots`` row + ONE ``analyst_snapshots`` row from
        a quote-only fetch (``YahooHandler.fetch_quote``) — nothing else, but for
        *splits*: Yahoo's split history, stored with the price as a collection
        stores it, which the quote job passes when it holds a stock dividend or a
        spin-off factor the stored valuations aren't on (the price is on its basis)
        and then values the ticker on it (BUG-128).

        ``market_snapshots``' primary key is ``(ticker, collected_at)``, so
        this INSERTS a new row rather than overwriting the snapshot from the
        last full collection — a quote refresh never clobbers full-collection
        history.

        ``fetch_quote``'s ``market_data`` omits ``ma_50``/``ma_200`` (no
        history call is made for a quote-only refresh). Rather than let the
        DES page's moving averages go blank after every quote refresh, this
        carries the PREVIOUS snapshot's ``ma_50``/``ma_200``/``beta`` and
        ``risk_free_rate`` (computed only during a full export's pass) forward
        into the new row whenever the fresh quote didn't return them (a one-row
        read-only ``SELECT`` against ``market_snapshots``, no writer contention —
        this method is only ever called from the single-writer job worker).

        The EV family (``_EV_FAMILY``) is ours, as a full export's
        ``calculated_metrics`` computes it: the quote's market cap with the latest
        stored year's net debt, EBITDA and FCF (``_ev_family``). Yahoo's own
        ``enterpriseValue`` and ``enterpriseToEbitda`` are another definition,
        more than 5% off ours for 47 of 111 companies, about 2x for the banks, and
        a refresh wrote them into the same columns (BUG-126). Without a market cap
        or a stored year, the previous snapshot's carry forward.
        """
        try:
            conn = self._connect()
            try:
                self._create_schema(conn)
                self._migrate(conn)

                md = quote.get("market_data") or {}
                val = quote.get("valuation") or {}
                shareholders = quote.get("shareholders") or {}
                merged: Dict[str, Any] = {**md, **val, **shareholders}

                snapshot: Dict[str, Any] = {"ticker": ticker, "collected_at": collected_at}
                for col in _SNAPSHOT_COLUMNS:
                    snapshot[col] = merged.get(col)
                for col in _SNAPSHOT_TEXT_COLUMNS:
                    snapshot[col] = merged.get(col)

                # A quote without a price writes no snapshot: with the fields carried
                # forward below it would become the latest one, with no price, verdict
                # or upside behind it (a Yahoo rate limit leaves every extractor empty).
                if snapshot.get("current_price") is not None:
                    ours = self._ev_family(conn, ticker, md, val)
                    carried = ["ma_50", "ma_200", "beta", "risk_free_rate", "risk_free_rate_date",
                               "risk_free_rate_source"]
                    if ours is None:
                        snapshot.update(dict.fromkeys(_EV_FAMILY))
                        carried += _EV_FAMILY
                    else:
                        snapshot.update(ours)
                    # Carry the rest forward from the latest prior snapshot when this
                    # quote-only fetch didn't return them.
                    prev = self._latest_snapshot_row(conn, ticker)
                    if prev is not None:
                        for col in carried:
                            if snapshot.get(col) is None:
                                snapshot[col] = prev.get(col)
                    self._upsert(conn, "market_snapshots", ["ticker", "collected_at"], snapshot)
                    self._write_split_events(conn, ticker, splits or [])

                self._write_analyst_snapshot(
                    conn, ticker, collected_at, quote.get("analyst_estimates") or {}
                )

                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error writing quote for {ticker}: {e}")

    @staticmethod
    def _ev_family(conn: sqlite3.Connection, ticker: str, market_data: Dict[str, Any],
                   valuation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """A quote's EV family as a full export's ``calculated_metrics`` has it: the
        calculator on the latest stored year, the quote's market data and the company's
        sector. ``None`` without a market cap or a stored year."""
        if not market_data.get("market_cap"):
            return None
        cur = conn.execute("SELECT * FROM financials_annual WHERE ticker = ? ORDER BY fiscal_year DESC LIMIT 1",
                           (ticker,))
        row = cur.fetchone()
        if row is None:
            return None
        year = dict(zip([d[0] for d in cur.description], row))
        sector = conn.execute("SELECT sector_class FROM companies WHERE ticker = ?", (ticker,)).fetchone()
        metrics = CalculatedMetrics().calculate_all(year, market_data=market_data, valuation=valuation,
                                                    sector=sector[0] if sector else None)
        return {col: metrics.get(col) for col in _EV_FAMILY}

    @staticmethod
    def _latest_snapshot_row(conn: sqlite3.Connection, ticker: str) -> Optional[Dict[str, Any]]:
        """Newest ``market_snapshots`` row for *ticker* by ``collected_at``, or ``None``."""
        cur = conn.execute(
            "SELECT * FROM market_snapshots WHERE ticker = ? ORDER BY collected_at DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [d[0] for d in cur.description]
        return dict(zip(columns, row))

    def export_benchmark_bars(self, symbol: str, bars: List[Dict[str, Any]]) -> None:
        """Write benchmark index OHLCV bars (e.g. ^GSPC) into ``price_bars``.

        Collected once per run rather than once per ticker, so market-relative
        metrics (beta, relative strength) can be computed against a common index.
        Writes bars only — unlike ``export``, this never creates/updates a
        ``companies`` row, since a benchmark index is not a company.
        """
        if not bars:
            return
        try:
            conn = self._connect()
            try:
                self._create_schema(conn)
                self._migrate(conn)
                self._write_price_bars(conn, symbol, bars)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Error writing benchmark bars for {symbol}: {e}")

    def export_valuations(self, ticker: str, results: "List[ValuationResult]",
                          summary: Dict[str, Any], computed_at: str) -> None:
        """Upsert one ticker's per-model valuation rows + the medians summary."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            for res in results:
                self._upsert(conn, "valuations", ["ticker", "model"], {
                    "ticker": ticker,
                    "model": res.model,
                    "applicable": 1 if res.applicable else 0,
                    "na_reason": res.na_reason,
                    "value_bear": res.value_bear,
                    "value_base": res.value_base,
                    "value_bull": res.value_bull,
                    "assumptions": json.dumps(res.assumptions),
                    "basis_fiscal_year": res.basis_fiscal_year,
                    "computed_at": computed_at,
                })
            self._upsert(conn, "valuation_summary", ["ticker"], {
                "ticker": ticker,
                "n_applicable": summary["n_applicable"],
                "median_bear": summary["median_bear"],
                "median_base": summary["median_base"],
                "median_bull": summary["median_bull"],
                "computed_at": computed_at,
            })
            conn.commit()
        finally:
            conn.close()

    def export_quality_scores(self, ticker: str, row: Dict[str, Any], per_share: List[Dict[str, Any]],
                              signs: Sequence[Any], computed_at: str) -> None:
        """Replace one ticker's quality scores, per-share rows and warning signs, in one transaction. Its
        ranks stay empty until the rank pass (``update_quality_ranks``)."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            # A table from before the scores' own debt / EBITDA (BUG-190).
            self._ensure_columns(conn, "quality_scores", {"debt_to_ebitda": "REAL"})
            for table in ("per_share_annual", "warning_signs", "quality_scores"):
                conn.execute(f"DELETE FROM {table} WHERE ticker = ?", (ticker,))
            conn.executemany(
                "INSERT INTO per_share_annual (ticker, fiscal_year, revenue_ps, eps, ebitda_ps, fcf_ps, book_ps) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(ticker, r["fiscal_year"], r.get("revenue_ps"), r.get("eps"), r.get("ebitda_ps"), r.get("fcf_ps"),
                  r.get("book_ps")) for r in per_share])
            values = {**row, "ticker": ticker, "computed_at": computed_at}
            columns = [c for c in QUALITY_COLUMNS if c in values]
            conn.execute(
                f"INSERT INTO quality_scores ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                [values[c] for c in columns])
            conn.executemany(
                "INSERT INTO warning_signs (ticker, code, severity, message, value, threshold, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(ticker, s.code, s.severity, s.message, s.value, s.threshold, computed_at) for s in signs])
            conn.commit()
        finally:
            conn.close()

    def export_insider_filings(self, ticker: str, filings: Sequence[Any], collected_at: str,
                               left_out: Sequence[str] = ()) -> None:
        """Store one company's Form 4 filings, ``(FilingRef, Form4)`` pairs, in one transaction.

        Each filing's rows are replaced by accession, so a filing an earlier run stored and this one couldn't
        fetch stays; the company's rows of the accessions *left_out* (the collector's: another issuer's filings,
        say) are removed. Then the company's amendments are worked out again from all its stored filings
        (``insiders.supersessions``): each filing amended notes its latest 4/A (``insider_filings.superseded_by``),
        and each trade a 4/A restates names it (``insider_trades.superseded_by``). And each of its non-derivative
        trades' prices is checked against the stock's latest close on or before the trade's date
        (``insiders.price_suspect``: ``insider_trades.price_suspect``), so a close stored since is used too."""
        from ..ownership.insiders import ceo_or_cfo, price_suspect, supersessions

        conn = self._connect()
        try:
            self._create_schema(conn)
            self._ensure_columns(conn, "insider_filings", {"is_ceo_cfo": "INTEGER"})
            # A table from before a trade had its own amendment note, or its price check.
            self._ensure_columns(conn, "insider_trades", {"superseded_by": "TEXT", "price_suspect": "INTEGER"})
            for accession in left_out:
                conn.execute("DELETE FROM insider_trades WHERE accession = ? AND ticker = ?", (accession, ticker))
                conn.execute("DELETE FROM insider_filings WHERE accession = ? AND ticker = ?", (accession, ticker))
            for ref, filing in filings:
                conn.execute("DELETE FROM insider_trades WHERE accession = ?", (ref.accession,))
                conn.execute("DELETE FROM insider_filings WHERE accession = ?", (ref.accession,))
                conn.executemany(
                    "INSERT INTO insider_filings (accession, owner_cik, ticker, form, filed_date, period_of_report, "
                    "original_filed, owner_name, is_director, is_officer, officer_title, is_ten_percent, is_other, "
                    "aff10b5one, is_ceo_cfo, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(ref.accession, owner.cik or "", ticker, filing.form or ref.form, ref.filed,
                      filing.period_of_report, filing.original_filed, owner.name, int(owner.is_director),
                      int(owner.is_officer), owner.officer_title, int(owner.is_ten_percent), int(owner.is_other),
                      None if filing.aff10b5one is None else int(filing.aff10b5one),
                      int(ceo_or_cfo(owner.officer_title)), collected_at)
                     for owner in filing.owners])
                conn.executemany(
                    "INSERT INTO insider_trades (accession, seq, ticker, kind, security_title, date, code, shares, "
                    "price, price_footnotes, acquired_disposed, owned_after, direct_indirect, nature_of_ownership, "
                    "exercise_price, underlying_title, underlying_shares) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(ref.accession, t.seq, ticker, t.table, t.security_title, t.date, t.code, t.shares, t.price,
                      ",".join(t.price_footnotes) or None, t.acquired_disposed, t.owned_after, t.direct_indirect,
                      t.nature_of_ownership, t.exercise_price, t.underlying_title, t.underlying_shares)
                     for t in filing.trades])
            filing_columns = ("accession", "owner_cik", "form", "filed_date", "period_of_report", "original_filed")
            trade_columns = ("accession", "seq", "kind", "code", "security_title", "date", "shares", "price")
            amended_by, restated = supersessions(
                [dict(zip(filing_columns, row)) for row in conn.execute(
                    f"SELECT {', '.join(filing_columns)} FROM insider_filings WHERE ticker = ?", (ticker,))],
                [dict(zip(trade_columns, row)) for row in conn.execute(
                    f"SELECT {', '.join(trade_columns)} FROM insider_trades WHERE ticker = ?", (ticker,))])
            conn.execute("UPDATE insider_filings SET superseded_by = NULL WHERE ticker = ?", (ticker,))
            conn.execute("UPDATE insider_trades SET superseded_by = NULL WHERE ticker = ?", (ticker,))
            conn.executemany("UPDATE insider_filings SET superseded_by = ? WHERE accession = ? AND ticker = ?",
                             [(amendment, accession, ticker) for accession, amendment in amended_by.items()])
            conn.executemany(
                "UPDATE insider_trades SET superseded_by = ? WHERE accession = ? AND seq = ? AND ticker = ?",
                [(amendment, accession, seq, ticker) for (accession, seq), amendment in restated.items()])
            # Each of the company's trades' prices against the stock's latest close on or before the trade's date
            # (insiders.price_suspect). A derivative's price is its own, not the stock's: unchecked (NULL).
            bars = conn.execute("SELECT date, close FROM price_bars WHERE ticker = ? AND close IS NOT NULL "
                                "ORDER BY date", (ticker,)).fetchall()
            days = [day for day, _ in bars]
            flags = []
            trades = conn.execute("SELECT accession, seq, kind, date, price FROM insider_trades WHERE ticker = ?",
                                  (ticker,)).fetchall()
            for accession, seq, kind, day, price in trades:
                at = bisect_right(days, day) if day else 0  # the latest close on or before the trade's date
                suspect = price_suspect(price, bars[at - 1][1] if at else None) if kind == "nonderivative" else None
                flags.append((None if suspect is None else int(suspect), accession, seq, ticker))
            conn.executemany(
                "UPDATE insider_trades SET price_suspect = ? WHERE accession = ? AND seq = ? AND ticker = ?", flags)
            conn.commit()
        finally:
            conn.close()

    def export_guru_filings(self, guru: Any, filings: Sequence[Any], collected_at: str) -> None:
        """Store one guru's 13F filings, ``(FilingRef, ThirteenF)`` pairs, in one transaction.

        The guru's filers are upserted from the curated list (``ownership.gurus``), one row per filer CIK with
        that filer's own name. Each
        filing's row and entries are replaced by accession, so a filing an earlier run stored and this one couldn't
        fetch stays. The filing's own form, filing date and period are stored, else the feed's; values in dollars by
        the units the filing was read in (*multiplier*), the cover's totals beside the parsed ones."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            # A table from before the units were read from the holdings' prices.
            self._ensure_columns(conn, "guru_filings", {"multiplier": "INTEGER"})
            for cik, name in guru.filers:
                self._upsert(conn, "guru_filers", ["filer_cik"], {
                    "filer_cik": cik, "guru_cik": guru.cik, "name": name, "manager": guru.manager})
            for ref, filing in filings:
                conn.execute("DELETE FROM guru_holdings WHERE accession = ?", (ref.accession,))
                conn.execute("DELETE FROM guru_filings WHERE accession = ?", (ref.accession,))
                conn.execute(
                    "INSERT INTO guru_filings (accession, guru_cik, filer_cik, form, period, filed, report_type, "
                    "amendment_type, entry_total, value_total, rows, rows_value, checksum_ok, confidential_omitted, "
                    "collected_at, multiplier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ref.accession, guru.cik, ref.cik, filing.form or ref.form, filing.period or ref.report_date,
                     filing.filed or ref.filed, filing.report_type, filing.amendment_type, filing.entry_total,
                     filing.dollars(filing.value_total), filing.rows, filing.dollars(filing.rows_value),
                     int(filing.checksum_ok),
                     None if filing.confidential_omitted is None else int(filing.confidential_omitted), collected_at,
                     filing.multiplier))
                conn.executemany(
                    "INSERT INTO guru_holdings (accession, row, guru_cik, cusip, issuer, title_of_class, value_usd, "
                    "shares, share_type, put_call, discretion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(ref.accession, h.row, guru.cik, h.cusip, h.issuer, h.title_of_class, h.value_usd, h.shares,
                      h.share_type, h.put_call, h.discretion) for h in filing.holdings])
            conn.commit()
        finally:
            conn.close()

    def export_cusip_map(self, rows: Sequence[Dict[str, Any]]) -> None:
        """Upsert ``cusip_map`` rows (cusip, ticker, name, source, security_type, mapped_at), in one transaction: a
        row replaces its CUSIP's earlier one."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            for row in rows:
                self._upsert(conn, "cusip_map", ["cusip"], {column: row.get(column) for column in (
                    "cusip", "ticker", "name", "source", "security_type", "mapped_at")})
            conn.commit()
        finally:
            conn.close()

    def export_guru_positions(self, guru: "GuruQuarters", computed_at: str) -> int:
        """Replace one guru's quarters and positions (``guru_quarters``, ``guru_positions``) with *guru*'s, as
        ``ownership.portfolio.load_guru`` builds them, in one transaction. Returns the position rows written.

        Each quarter's row: its total value, the positions it holds, whether every filer's report stands, whether the
        quarter before is there (so its changes exist), its filings as a JSON list and the latest of their filing
        dates. Each position held, put/call '' for shares and principal amounts, with its change from the quarter
        before (none without it); then a 'sold_out' row for each position of the quarter before that's gone, without
        shares, value or weight."""
        written = 0
        conn = self._connect()
        try:
            self._create_schema(conn)
            conn.execute("DELETE FROM guru_positions WHERE guru_cik = ?", (guru.guru_cik,))
            conn.execute("DELETE FROM guru_quarters WHERE guru_cik = ?", (guru.guru_cik,))
            for period, quarter in guru.quarters.items():
                changes = guru.changes.get(period)
                conn.execute(
                    "INSERT INTO guru_quarters (guru_cik, period, total_value, positions, complete, has_prior, "
                    "accessions, filed, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (guru.guru_cik, period, quarter.total_value, len(quarter.positions), int(quarter.complete),
                     int(changes is not None), json.dumps(list(quarter.accessions)), quarter.filed, computed_at))
                change_of = {(c.security, c.put_call): c for c in changes or ()}
                rows = [_position_row(guru.guru_cik, period, held, change_of.get(key))
                        for key, held in quarter.positions.items()]
                rows += [_position_row(guru.guru_cik, period, None, c) for c in changes or ()
                         if c.action == "sold_out"]
                conn.executemany(
                    f"INSERT INTO guru_positions ({', '.join(_GURU_POSITION_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(_GURU_POSITION_COLUMNS))})", rows)
                written += len(rows)
            conn.commit()
        finally:
            conn.close()
        return written

    def update_quality_ranks(self, ranks: Dict[str, Dict[str, Any]], ranked_at: str) -> None:
        """Write the rank pass's ranks, composite and Magic Formula rank, and the industry ones (INDUSTRY_COLUMNS,
        NULL where a ticker's result has none), for every ticker, in one transaction."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            # A table from before the industry ranks (Milestone 5).
            self._ensure_columns(conn, "quality_scores", {c: _QUALITY_TYPES.get(c, "REAL") for c in INDUSTRY_COLUMNS})
            assignments = ", ".join(f"{c} = ?" for c in RANK_COLUMNS)
            conn.executemany(
                f"UPDATE quality_scores SET {assignments}, ranked_at = ? WHERE ticker = ?",
                [[result.get(c) for c in RANK_COLUMNS] + [ranked_at, ticker] for ticker, result in ranks.items()])
            conn.commit()
        finally:
            conn.close()

    def export_valuation_history(self, ticker: str, result: "HistoryResult", computed_at: str) -> None:
        """Replace one ticker's valuation history and ranges, in one transaction."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            conn.execute("DELETE FROM valuation_history WHERE ticker = ?", (ticker,))
            conn.execute("DELETE FROM valuation_ranges WHERE ticker = ?", (ticker,))
            conn.executemany(
                "INSERT INTO valuation_history (ticker, date, close, market_cap, enterprise_value, "
                "ttm_period_end, pe, ps, pb, pfcf, ev_ebitda, pffo, div_yield, fair_value, fair_value_n) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(ticker, row.date, row.close, row.market_cap, row.enterprise_value, row.ttm_period_end,
                  *(row.multiples.get(m) for m in ("pe", "ps", "pb", "pfcf", "ev_ebitda", "pffo", "div_yield")),
                  row.fair_value, row.fair_value_n) for row in result.rows])
            conn.executemany(
                "INSERT INTO valuation_ranges (ticker, metric, applicable, na_reason, as_of, window_start, "
                "n_months, current_value, pct_rank, min, p25, median, p75, max, implied_value, in_fair_value, "
                "n_metrics, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(ticker, r.metric, 1 if r.applicable else 0, r.na_reason, r.as_of, r.window_start, r.n_months,
                  r.current, r.pct_rank, r.min, r.p25, r.median, r.p75, r.max, r.implied_value,
                  1 if r.in_fair_value else 0, r.n_metrics, computed_at) for r in result.ranges])
            conn.commit()
        finally:
            conn.close()

    def export_valuation_indicators(self, ticker: str, results: "List[IndicatorResult]", computed_at: str) -> None:
        """Replace one ticker's valuation indicators, in one transaction."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            conn.execute("DELETE FROM valuation_indicators WHERE ticker = ?", (ticker,))
            conn.executemany(
                "INSERT INTO valuation_indicators (ticker, indicator, applicable, na_reason, value_bear, value_base, "
                "value_bull, unit, assumptions, basis_fiscal_year, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(ticker, r.indicator, 1 if r.applicable else 0, r.na_reason, r.value_bear, r.value_base,
                  r.value_bull, r.unit, json.dumps(r.assumptions), r.basis_fiscal_year, computed_at)
                 for r in results])
            conn.commit()
        finally:
            conn.close()

    def export_market_series(self, source: str, series: Dict[str, List[Tuple[str, float]]], url: Optional[str],
                             fetched_at: str, replace_from: Optional[str] = None) -> None:
        """Replace each of *series* (its name -> (date, value) rows) whole, or, with *replace_from* (an ISO date), only
        its rows dated on or after that day, the earlier ones kept (the Treasury's from its first year, BUG-224); and
        *source*'s ``market_fetches`` row (its url, the rows stored and when its answer was fetched), in one
        transaction: a store that fails changes nothing. A series not named is left as it is."""
        conn = self._connect()
        try:
            self._create_schema(conn)
            for name, rows in series.items():
                if replace_from is None:
                    conn.execute("DELETE FROM market_series WHERE series = ?", (name,))
                else:
                    conn.execute("DELETE FROM market_series WHERE series = ? AND date >= ?", (name, replace_from))
                conn.executemany("INSERT INTO market_series (series, date, value) VALUES (?, ?, ?)",
                                 [(name, day, value) for day, value in rows])
            self._upsert(conn, "market_fetches", ["source"], {
                "source": source, "url": url, "rows": sum(len(rows) for rows in series.values()),
                "fetched_at": fetched_at})
            conn.commit()
        finally:
            conn.close()

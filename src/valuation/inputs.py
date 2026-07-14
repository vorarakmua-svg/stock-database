"""ValuationInputs: the per-ticker input bundle for the valuation models.

Pure-read composition over the existing SQLite schema. ``load_inputs`` gathers
everything a model needs — annual fundamentals joined with calculated metrics,
the latest market snapshot (beta / risk-free / shares), the latest analyst
growth estimate, the full dividend history, and each fiscal year's on-or-before
period-end closing price — so the models themselves never touch the database.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

_MAX_FY_HISTORY = 10

#: A year-over-year rise in the share count of at least this factor is read as a
#: stock split, not as ordinary issuance.
SPLIT_JUMP_THRESHOLD = 1.5


@dataclass
class FYRecord:
    """One fiscal year of fundamentals relevant to valuation.

    Per-share figures (``eps_diluted``, ``ffo_per_share``) and ``shares`` are
    normalized onto the CURRENT (latest-year) share basis by ``load_inputs`` —
    the same convention Yahoo's adjusted price bars use — so that multi-year
    per-share series and price/EPS multiples are comparable across a split.
    ``split_factor`` records the factor that was applied to this year (1.0 =
    as reported).
    """

    fiscal_year: int
    period_end: Optional[str]
    net_income: Optional[float]
    total_equity: Optional[float]
    eps_diluted: Optional[float]
    shares: Optional[float]
    fcf: Optional[float]
    ffo_per_share: Optional[float]
    split_factor: float = 1.0

    def eps(self) -> Optional[float]:
        """Diluted EPS as reported, else net income / shares."""
        if self.eps_diluted is not None:
            return self.eps_diluted
        if self.net_income is not None and self.shares:
            return self.net_income / self.shares
        return None

    def bvps(self) -> Optional[float]:
        """Book value (total equity) per share."""
        if self.total_equity is not None and self.shares:
            return self.total_equity / self.shares
        return None


@dataclass
class ValuationInputs:
    """Everything the model suite needs for one ticker."""

    ticker: str
    sector_class: str = "general"
    fy_records: List[FYRecord] = field(default_factory=list)
    shares_outstanding: Optional[float] = None
    beta: Optional[float] = None
    risk_free_rate: Optional[float] = None
    analyst_growth: Optional[float] = None
    dividends: List[Tuple[str, float]] = field(default_factory=list)
    fy_end_prices: Dict[int, float] = field(default_factory=dict)
    as_of: Optional[date] = None


def _normalize_splits(records: List[FYRecord],
                      shares_detect: List[Optional[float]]) -> None:
    """Restate every year's per-share figures onto the current share basis.

    ``financials_annual`` stores per-share values AS REPORTED, and SEC filings
    restate only ~3 prior years after a split, so a long per-share series has a
    hard discontinuity at each split. ``split_events`` cannot be relied on (it
    is empty for most tickers), so splits are detected from the share-count
    series itself: walking newest -> oldest, a >= 1.5x rise in the share count
    at a seam means every year at or before that seam is on a pre-split basis.

    Mutates *records* in place. Per-share values are DIVIDED by the cumulative
    factor and share counts MULTIPLIED by it; split-invariant absolutes
    (net_income, total_equity) are untouched — which keeps ``bvps()`` correct.
    """
    factor = 1.0
    for i in range(len(records) - 2, -1, -1):
        older, newer = shares_detect[i], shares_detect[i + 1]
        if older and newer and older > 0 and newer > 0:
            jump = newer / older
            if jump >= SPLIT_JUMP_THRESHOLD:
                factor *= jump
        rec = records[i]
        rec.split_factor = factor
        if factor == 1.0:
            continue
        if rec.eps_diluted is not None:
            rec.eps_diluted /= factor
        if rec.ffo_per_share is not None:
            rec.ffo_per_share /= factor
        if rec.shares is not None:
            rec.shares *= factor


def load_inputs(conn: sqlite3.Connection, ticker: str) -> ValuationInputs:
    """Build the ValuationInputs bundle for *ticker*.

    ``conn.row_factory`` must be ``sqlite3.Row``. Missing rows degrade to
    ``None``/empty fields — applicability is the models' concern, not the loader's.
    """
    row = conn.execute(
        "SELECT sector_class FROM companies WHERE ticker = ?", (ticker,)
    ).fetchone()
    sector = (row["sector_class"] if row is not None else None) or "general"

    fy_rows = conn.execute(
        "SELECT fa.fiscal_year, fa.period_end, fa.net_income, fa.total_equity, "
        "fa.eps_diluted, fa.weighted_avg_shares_diluted, fa.shares_outstanding, "
        "ma.levered_fcf, ma.free_cash_flow, ma.ffo_per_share "
        "FROM financials_annual fa "
        "LEFT JOIN metrics_annual ma "
        "  ON ma.ticker = fa.ticker AND ma.fiscal_year = fa.fiscal_year "
        "WHERE fa.ticker = ? AND fa.fiscal_year IS NOT NULL "
        "ORDER BY fa.fiscal_year ASC",
        (ticker,),
    ).fetchall()
    records: List[FYRecord] = []
    shares_detect: List[Optional[float]] = []
    for r in fy_rows[-_MAX_FY_HISTORY:]:
        fcf = r["levered_fcf"] if r["levered_fcf"] is not None else r["free_cash_flow"]
        shares = (
            r["weighted_avg_shares_diluted"]
            if r["weighted_avg_shares_diluted"] is not None
            else r["shares_outstanding"]
        )
        # Split detection needs the share count that MATCHES the reported EPS,
        # so net_income / eps_diluted is preferred over shares_outstanding
        # (weighted_avg_shares_diluted is NULL for e.g. GOOGL).
        detect = r["weighted_avg_shares_diluted"]
        if detect is None and r["net_income"] is not None and r["eps_diluted"]:
            detect = r["net_income"] / r["eps_diluted"]
        if detect is None:
            detect = r["shares_outstanding"]
        shares_detect.append(detect)
        records.append(
            FYRecord(
                fiscal_year=int(r["fiscal_year"]),
                period_end=r["period_end"],
                net_income=r["net_income"],
                total_equity=r["total_equity"],
                eps_diluted=r["eps_diluted"],
                shares=shares,
                fcf=fcf,
                ffo_per_share=r["ffo_per_share"],
            )
        )
    _normalize_splits(records, shares_detect)

    snap = conn.execute(
        "SELECT shares_outstanding, beta, risk_free_rate, collected_at "
        "FROM market_snapshots "
        "WHERE ticker = ? ORDER BY collected_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    as_of = date.today()
    if snap is not None and snap["collected_at"]:
        try:
            as_of = date.fromisoformat(str(snap["collected_at"])[:10])
        except ValueError:  # unparseable timestamp -> today
            pass
    analyst = conn.execute(
        "SELECT earnings_growth FROM analyst_snapshots "
        "WHERE ticker = ? ORDER BY collected_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    dividends = [
        (r["date"], r["amount"])
        for r in conn.execute(
            "SELECT date, amount FROM dividend_events "
            "WHERE ticker = ? AND amount IS NOT NULL ORDER BY date ASC",
            (ticker,),
        ).fetchall()
    ]

    fy_end_prices: Dict[int, float] = {}
    for rec in records:
        if rec.period_end is None:
            continue
        bar = conn.execute(
            "SELECT close FROM price_bars WHERE ticker = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (ticker, rec.period_end),
        ).fetchone()
        if bar is not None and bar["close"] is not None:
            fy_end_prices[rec.fiscal_year] = bar["close"]

    return ValuationInputs(
        ticker=ticker,
        sector_class=sector,
        fy_records=records,
        shares_outstanding=snap["shares_outstanding"] if snap else None,
        beta=snap["beta"] if snap else None,
        risk_free_rate=snap["risk_free_rate"] if snap else None,
        analyst_growth=analyst["earnings_growth"] if analyst else None,
        dividends=dividends,
        fy_end_prices=fy_end_prices,
        as_of=as_of,
    )

"""ValuationInputs: the per-ticker input bundle for the valuation models.

Pure-read composition over the existing SQLite schema. ``load_inputs`` gathers
everything a model needs — annual fundamentals joined with calculated metrics,
the latest market snapshot (beta / risk-free / shares), the latest analyst
growth estimate, the full dividend history, and each fiscal year's on-or-before
period-end closing price — so the models themselves never touch the database.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_MAX_FY_HISTORY = 10


@dataclass
class FYRecord:
    """One fiscal year of fundamentals relevant to valuation."""

    fiscal_year: int
    period_end: Optional[str]
    net_income: Optional[float]
    total_equity: Optional[float]
    eps_diluted: Optional[float]
    shares: Optional[float]
    fcf: Optional[float]
    ffo_per_share: Optional[float]

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
    for r in fy_rows[-_MAX_FY_HISTORY:]:
        fcf = r["levered_fcf"] if r["levered_fcf"] is not None else r["free_cash_flow"]
        shares = (
            r["weighted_avg_shares_diluted"]
            if r["weighted_avg_shares_diluted"] is not None
            else r["shares_outstanding"]
        )
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

    snap = conn.execute(
        "SELECT shares_outstanding, beta, risk_free_rate FROM market_snapshots "
        "WHERE ticker = ? ORDER BY collected_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
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
    )

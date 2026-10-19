"""ValuationInputs: the per-ticker input bundle for the valuation models.

Pure-read composition over the existing SQLite schema. ``load_inputs`` gathers
everything a model needs — annual fundamentals joined with calculated metrics,
the latest market snapshot (beta / risk-free / shares), the latest analyst
growth estimate, the full dividend history, and each fiscal year's on-or-before
period-end closing price — so the models themselves never touch the database.
"""

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

_MAX_FY_HISTORY = 10

#: A year-over-year rise in the share count of at least this factor is read as a
#: stock split, not as ordinary issuance.
SPLIT_JUMP_THRESHOLD = 1.5

#: The largest ratio still credible as a real split. No listed company has ever
#: split 50:1 (or 1:50) in one step, so a seam beyond this is a unit/scale error
#: in the source data (some fiscal years are filed in thousands, others in
#: units), not a corporate action.
MAX_SPLIT_RATIO = 50.0

#: A declared ratio from ``split_events`` replaces a detected share-count jump
#: when it is within this factor of it. The jump also carries a year of buybacks
#: or issuance (AAPL's 2020 4:1 shows up as 20.0B / 5.252B = 3.81), so the
#: declared ratio is the exact one; a jump nowhere near any declared ratio keeps
#: the detected value (the only evidence there is).
DECLARED_SPLIT_TOLERANCE = 1.25


def _declared_ratio(observed: float, period_end: Optional[str],
                    splits: Sequence[Tuple[str, float]]) -> float:
    """The declared split ratio, or run of consecutive ratios, dated after
    *period_end* that is closest to the *observed* seam ratio, if within
    DECLARED_SPLIT_TOLERANCE of it; otherwise *observed*. A run covers a seam
    that spans two splits (e.g. restated for both NVDA's 4:1 and 10:1)."""
    later = [ratio for when, ratio in splits
             if ratio and ratio > 0 and (period_end is None or str(when)[:10] > str(period_end)[:10])]
    best, best_gap = observed, math.log(DECLARED_SPLIT_TOLERANCE)
    for start in range(len(later)):
        run = 1.0
        for ratio in later[start:]:
            run *= ratio
            gap = abs(math.log(observed / run))
            if gap <= best_gap:
                best, best_gap = run, gap
    return best


@dataclass
class FYRecord:
    """One fiscal year of fundamentals relevant to valuation.

    Per-share figures (``eps_diluted``, ``ffo_per_share``) and ``shares`` are
    normalized onto TODAY's share basis by ``load_inputs`` (the latest year's,
    moved by any split after its filing) —
    the same convention Yahoo's adjusted price bars use — so that multi-year
    per-share series and price/EPS multiples are comparable across a split.
    ``split_factor`` records the factor that was applied to this year (1.0 =
    as reported).

    ``depreciation_amortization`` and ``capex`` are absolute dollar figures, not
    per-share, so they are never split-adjusted.
    """

    fiscal_year: int
    period_end: Optional[str]
    net_income: Optional[float]
    total_equity: Optional[float]
    eps_diluted: Optional[float]
    shares: Optional[float]
    fcf: Optional[float]
    ffo_per_share: Optional[float]
    depreciation_amortization: Optional[float] = None
    capex: Optional[float] = None
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
    #: True when a unit/scale discontinuity in the source share counts forced
    #: the older fiscal years to be dropped (see ``_normalize_splits``).
    history_truncated: bool = False


def _eps_moved_inversely(ratio: float, eps_older: Optional[float],
                         eps_newer: Optional[float]) -> bool:
    """Did EPS move opposite to the share count across this seam?

    A split multiplies the share count and divides as-reported EPS by the same
    factor, so EPS must fall across a forward split (and rise across a reverse
    one). An acquisition-scale share issuance — the real hazard near the 1.5
    threshold, e.g. Realty Income's 1.48x VEREIT merger — brings its own
    earnings with it and does not invert EPS, so the share jump alone would
    otherwise be enough to rescale a whole history wrongly.

    The comparison is on MAGNITUDE, not signed value: a split rescales EPS
    multiplicatively whatever its sign, so a loss-making company's per-share
    loss grows across a reverse split (GE: -2.62 -> -4.99 over its 1:8). A
    seam whose EPS changes sign carries no directional information, and neither
    does a missing or zero EPS; those fall back to the share-count signal alone.
    """
    if not eps_older or not eps_newer:
        return True
    if (eps_older > 0) != (eps_newer > 0):
        return True
    if ratio > 1.0:
        return abs(eps_newer) < abs(eps_older)
    return abs(eps_newer) > abs(eps_older)


def _normalize_splits(records: List[FYRecord],
                      shares_detect: List[Optional[float]],
                      splits: Sequence[Tuple[str, float]] = (),
                      ) -> Tuple[List[FYRecord], bool]:
    """Restate every year's per-share figures onto the current share basis.

    ``financials_annual`` stores per-share values AS REPORTED, and SEC filings
    restate only ~3 prior years after a split, so a long per-share series has a
    hard discontinuity where the stored data switches basis — not at the split
    date itself. Splits are therefore detected from the share-count series.
    When ``split_events`` declares a ratio close to a detected jump, that exact
    ratio is applied (see ``_declared_ratio``), since the jump also carries a
    year of buybacks or issuance. Walking newest -> oldest, each seam's ratio
    ``shares[i + 1] / shares[i]`` is classified:

    * ``1.5 .. 50``    -> forward split; fold into the cumulative factor.
    * ``1/50 .. 1/1.5`` -> reverse split; same treatment with a factor < 1,
      which scales the older years' per-share values UP and their share counts
      DOWN — exactly the correction a reverse split needs.
    * beyond ``+/- 50x`` -> not a credible corporate action. The source data
      mixes units across fiscal years (thousands vs units), so everything older
      than this seam is on an unknown scale and simply CANNOT be compared to the
      modern series. Truncate: drop the older records and stop walking. The
      models' "insufficient history" guards then take over honestly.
    * otherwise -> ordinary issuance / buyback; no adjustment.

    Mutates the surviving *records* in place and returns
    ``(surviving_records, history_truncated)``. Per-share values are DIVIDED by
    the cumulative factor and share counts MULTIPLIED by it; split-invariant
    absolutes (net_income, total_equity) are untouched — which keeps ``bvps()``
    correct.
    """
    factor = 1.0
    for i in range(len(records) - 2, -1, -1):
        older, newer = shares_detect[i], shares_detect[i + 1]
        if older and newer and older > 0 and newer > 0:
            ratio = newer / older
            forward = SPLIT_JUMP_THRESHOLD <= ratio <= MAX_SPLIT_RATIO
            reverse = (1.0 / MAX_SPLIT_RATIO) <= ratio <= (1.0 / SPLIT_JUMP_THRESHOLD)
            if ratio > MAX_SPLIT_RATIO or ratio < 1.0 / MAX_SPLIT_RATIO:
                # Unit discontinuity: the pre-seam years are uncomparable.
                return records[i + 1:], True
            if (forward or reverse) and _eps_moved_inversely(
                ratio, records[i].eps_diluted, records[i + 1].eps_diluted
            ):
                factor *= _declared_ratio(ratio, records[i].period_end, splits)
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
    return records, False


def _apply_later_splits(records: List[FYRecord], splits: Sequence[Tuple[str, float]],
                        filed: Optional[str]) -> None:
    """Move every year onto today's basis when a split came after the latest filing.

    Filings restate per-share figures only for splits made before they are
    issued, so ``_normalize_splits`` reaches the latest FY's basis and no
    further. KLAC's FY2025 10-K (filed 2025-08-08) predates its 10:1 split of
    2026-06-12: every stored EPS is pre-split while the price is post-split.
    Only share splits count (at least 3-for-2 or 1-for-2): Yahoo's split
    history also holds spin-off price factors and stock dividends (HON 0.9535).
    Without a filing date nothing moves, because a split between the year end
    and the filing is already restated in it.
    """
    if not filed:
        return
    factor = 1.0
    for when, ratio in splits:
        if ratio and str(when)[:10] > str(filed)[:10] and (
                SPLIT_JUMP_THRESHOLD <= ratio <= MAX_SPLIT_RATIO
                or 1.0 / MAX_SPLIT_RATIO <= ratio <= 1.0 / SPLIT_JUMP_THRESHOLD):
            factor *= ratio
    if factor == 1.0:
        return
    for rec in records:
        rec.split_factor *= factor
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
        "SELECT fa.fiscal_year, fa.period_end, fa.filed_date, fa.net_income, fa.total_equity, "
        "fa.eps_diluted, fa.weighted_avg_shares_diluted, fa.shares_outstanding, "
        "fa.depreciation_amortization, fa.capex, "
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
                depreciation_amortization=r["depreciation_amortization"],
                capex=r["capex"],
            )
        )
    splits = [
        (s["date"], s["ratio"])
        for s in conn.execute(
            "SELECT date, ratio FROM split_events WHERE ticker = ? ORDER BY date", (ticker,)
        ).fetchall()
    ]
    records, history_truncated = _normalize_splits(records, shares_detect, splits)
    _apply_later_splits(records, splits, fy_rows[-1]["filed_date"] if fy_rows else None)

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
        history_truncated=history_truncated,
    )

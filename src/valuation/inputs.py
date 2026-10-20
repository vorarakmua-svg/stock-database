"""ValuationInputs: the per-ticker input bundle for the valuation models.

Pure-read composition over the existing SQLite schema. ``load_inputs`` gathers
everything a model needs — annual fundamentals joined with calculated metrics,
the latest market snapshot (beta / risk-free / shares), the latest analyst
growth estimate, the full dividend history, and each fiscal year's on-or-before
period-end closing price — so the models themselves never touch the database.
"""

import math
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
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

#: A "dividend" at least this many times the payments around it, on a day the
#: split history records a spin-off price factor, is the spun-off shares' value.
SPIN_OFF_DIVIDEND_MULTIPLE = 5.0

#: Market cap / price more than this factor above the snapshot share count means
#: the count misses a share class (or units) that the market cap includes.
SHARE_CLASS_GAP = 1.02

#: Below this factor the gap can also be Yahoo's two figures disagreeing (MRVL,
#: one class, 2.5%), so the one nearer the latest FY's count is taken. Every
#: wider gap in the data is a class or units (APP 10%, META 15%, BX 65%).
SHARE_SOURCE_GAP = 1.05

#: A share count under this share of the latest FY's diluted shares (on today's
#: basis) is incomplete: buybacks don't shrink a company by a quarter in a year.
MIN_SHARE_COUNT_RATIO = 0.75


def _declared_run(observed: float, period_end: Optional[str],
                  splits: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """The declared split, or run of consecutive splits, dated after
    *period_end* whose ratio is closest to the *observed* seam ratio, if within
    DECLARED_SPLIT_TOLERANCE of it; otherwise none. A run covers a seam that
    spans two splits (e.g. restated for both NVDA's 4:1 and 10:1)."""
    later = [(when, ratio) for when, ratio in splits
             if ratio and ratio > 0 and (period_end is None or str(when)[:10] > str(period_end)[:10])]
    best: List[Tuple[str, float]] = []
    best_gap = math.log(DECLARED_SPLIT_TOLERANCE)
    for start in range(len(later)):
        run = 1.0
        for end in range(start, len(later)):
            run *= later[end][1]
            gap = abs(math.log(observed / run))
            if gap <= best_gap:
                best, best_gap = later[start:end + 1], gap
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
    #: Where ``shares_outstanding`` came from (see ``_total_shares``).
    shares_source: str = "market_snapshot"
    #: Ordinary shares per ADS when a foreign issuer's per-share figures were
    #: moved to the ADS basis Yahoo prices it on (see ``_ads_ratio``); 1.0 otherwise.
    ads_ratio: float = 1.0
    beta: Optional[float] = None
    risk_free_rate: Optional[float] = None
    #: The day the rate is for, and where it came from ("fred", "treasury_gov" or
    #: "estimate"); None for snapshots written before they were recorded.
    risk_free_rate_date: Optional[str] = None
    risk_free_rate_source: Optional[str] = None
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
    otherwise be enough to rescale a whole history wrongly. Both figures are
    as reported, and the test only judges seams no declared split explains:
    earnings can outgrow a split's ratio in its year (NOW 2023).

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


def _filed_between(split: Tuple[str, float], f_old: Optional[str], f_new: Optional[str]) -> bool:
    """Whether *split* can explain the seam between two years whose share figures
    were last filed on *f_old* and *f_new*: a share split restates the filings made
    after it, so it lies between them. Unknown dates don't decide, nor do the
    spin-off and merger price factors no filing restates (not simple ratios)."""
    when, ratio = split
    if not (f_old and f_new and ratio and ratio > 0 and _simple_ratio(ratio)):
        return True
    return f_old[:10] < str(when)[:10] <= f_new[:10]


def _normalize_splits(records: List[FYRecord],
                      shares_detect: List[Optional[float]],
                      splits: Sequence[Tuple[str, float]] = (),
                      detect_filed: Optional[Sequence[Optional[str]]] = None,
                      ) -> Tuple[List[FYRecord], bool]:
    """Restate every year's per-share figures onto the current share basis.

    ``financials_annual`` stores per-share values AS REPORTED, and SEC filings
    restate only ~3 prior years after a split, so a long per-share series has a
    hard discontinuity where the stored data switches basis — not at the split
    date itself. Splits are therefore detected from the share-count series.
    When ``split_events`` declares a ratio close to a detected jump, that exact
    ratio is applied (see ``_declared_run``), since the jump also carries a
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

    A filing restates its figures for every share split made before it, so
    when *detect_filed* says when each year's share figure was last filed (the
    vintages), a share split explains a seam only if it lies between the two
    years' filings: one that every stored year already reflects can't have made
    a seam, though a stock-paid merger's may sit near its ratio.

    Mutates the surviving *records* in place and returns
    ``(surviving_records, history_truncated)``. Per-share values are DIVIDED by
    the cumulative factor and share counts MULTIPLIED by it; split-invariant
    absolutes (net_income, total_equity) are untouched — which keeps ``bvps()``
    correct.
    """
    factor = 1.0
    # The walk rescales each year as it goes, so the EPS test reads the figures
    # as reported, on the basis the share counts are.
    reported_eps = [rec.eps_diluted for rec in records]
    # Each declared split explains one seam: an older jump (an acquisition paid
    # in shares) can't take a split a newer seam already used.
    unused = list(splits)
    for i in range(len(records) - 2, -1, -1):
        older, newer = shares_detect[i], shares_detect[i + 1]
        if older and newer and older > 0 and newer > 0:
            ratio = newer / older
            forward = SPLIT_JUMP_THRESHOLD <= ratio <= MAX_SPLIT_RATIO
            reverse = (1.0 / MAX_SPLIT_RATIO) <= ratio <= (1.0 / SPLIT_JUMP_THRESHOLD)
            if ratio > MAX_SPLIT_RATIO or ratio < 1.0 / MAX_SPLIT_RATIO:
                # Unit discontinuity: the pre-seam years are uncomparable.
                return records[i + 1:], True
            if forward or reverse:
                f_old, f_new = (detect_filed[i], detect_filed[i + 1]) if detect_filed else (None, None)
                candidates = [split for split in unused if _filed_between(split, f_old, f_new)]
                run = _declared_run(ratio, records[i].period_end, candidates)
                if run:
                    unused = [split for split in unused if split not in run]
                    factor *= math.prod(split_ratio for _, split_ratio in run)
                elif _eps_moved_inversely(ratio, reported_eps[i], reported_eps[i + 1]):
                    factor *= ratio
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


def _simple_ratio(ratio: float) -> bool:
    """True for a share split's ratio, a simple fraction (2:1, 3:2, 9:4, 1:8,
    10:1). The spin-off and merger price factors in Yahoo's split history are
    not (ABT's 2.0842 for AbbVie, DELL's 1.806, RTX's 1.589); on the real-data
    copy every simple factor of 3:2 or more is a share split."""
    return any(abs(float(Fraction(r).limit_denominator(4)) - r) <= 1e-9 * r for r in (ratio, 1.0 / ratio))


def _apply_later_splits(records: List[FYRecord], splits: Sequence[Tuple[str, float]],
                        filed: Optional[str], today_counts: Sequence[float] = (),
                        priced_on: Optional[str] = None) -> None:
    """Move every year onto today's basis when a split came after the latest filing.

    Filings restate per-share figures only for splits made before they are
    issued, so ``_normalize_splits`` reaches the latest FY's basis and no
    further. KLAC's FY2025 10-K (filed 2025-08-08) predates its 10:1 split of
    2026-06-12: every stored EPS is pre-split while the price is post-split.
    Only share splits count (at least 3-for-2 or 1-for-2): Yahoo's split
    history also holds spin-off price factors and stock dividends (HON 0.9535).
    Without a filing date nothing moves, because a split between the year end
    and the filing is already restated in it. Spin-off and merger factors of
    3:2 or more sit in the split history too (ABT's 2.084 for AbbVie), but
    their ratios aren't simple (``_simple_ratio``): such a factor applies only
    when one of Yahoo's counts (``today_counts``) moved by it, within
    DECLARED_SPLIT_TOLERANCE (its own count, when market cap / price also
    counts units). A simple ratio is a split even before Yahoo's count catches
    up with it (KLAC: 133.75M filed, 1.31B today). A split after *priced_on*,
    the day of the price the values are compared with, isn't in that price
    (a quote failed since), so it moves nothing.
    """
    if not filed:
        return
    factor = 1.0
    needs_count = False
    for when, ratio in splits:
        if ratio and str(when)[:10] > str(filed)[:10] and (priced_on is None or str(when)[:10] <= priced_on) and (
                SPLIT_JUMP_THRESHOLD <= ratio <= MAX_SPLIT_RATIO
                or 1.0 / MAX_SPLIT_RATIO <= ratio <= 1.0 / SPLIT_JUMP_THRESHOLD):
            factor *= ratio
            needs_count = needs_count or not _simple_ratio(ratio)
    if factor == 1.0:
        return
    filed_shares = next((r.shares for r in reversed(records) if r.shares and r.shares > 0), None)
    if needs_count and filed_shares and today_counts and not any(
            abs(math.log(count / (filed_shares * factor))) <= math.log(DECLARED_SPLIT_TOLERANCE)
            for count in today_counts):
        return
    for rec in records:
        rec.split_factor *= factor
        if rec.eps_diluted is not None:
            rec.eps_diluted /= factor
        if rec.ffo_per_share is not None:
            rec.ffo_per_share /= factor
        if rec.shares is not None:
            rec.shares *= factor


def _ads_ratio(filed_to_yahoo: float) -> float:
    """The ADS ratio a filed-to-Yahoo share-count ratio shows: a whole number n >= 2
    of ordinary shares per ADS (or of ADSs per share) within 5%, else 1.

    A foreign issuer's 20-F or 40-F counts ordinary shares, while Yahoo quotes the
    ADS and counts ADS-equivalents (TSM: 25.93B ordinary, 5.186B ADS).
    """
    for candidate, per_ads in ((filed_to_yahoo, True), (1.0 / filed_to_yahoo, False)):
        n = round(candidate)
        if n >= 2 and abs(candidate / n - 1) <= 0.05:
            return float(n) if per_ads else 1.0 / n
    return 1.0


def _undo_splits_after_price(records: List[FYRecord], splits: Sequence[Tuple[str, float]],
                             filed: Optional[str], priced_on: Optional[str]) -> None:
    """Take a split the filings restated after the price's day back out of every year.

    The latest filing restates its years for a split made before it, and
    ``_normalize_splits`` brings the older ones onto that basis; but a price
    quoted before the split isn't on it. That happens when a later run's quote
    failed, keeping a snapshot older than a split the latest 10-K already
    reflects (BUG-060). Only share splits count (simple ratios of at least
    3-for-2 or 1-for-2): no filing restates for a spin-off's price factor.
    """
    if not (filed and priced_on):
        return
    factor = math.prod(ratio for when, ratio in splits
                       if ratio and priced_on < str(when)[:10] <= str(filed)[:10]
                       and (SPLIT_JUMP_THRESHOLD <= ratio <= MAX_SPLIT_RATIO
                            or 1.0 / MAX_SPLIT_RATIO <= ratio <= 1.0 / SPLIT_JUMP_THRESHOLD)
                       and _simple_ratio(ratio))
    if factor == 1.0:
        return
    for rec in records:
        rec.split_factor /= factor
        if rec.eps_diluted is not None:
            rec.eps_diluted *= factor
        if rec.ffo_per_share is not None:
            rec.ffo_per_share *= factor
        if rec.shares is not None:
            rec.shares /= factor


def _drop_spin_off_dividends(dividends: List[Tuple[str, float]],
                             splits: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Cash dividends only: drop a spin-off that Yahoo also records as a dividend.

    Yahoo records some spin-offs twice: a price factor in the split history and,
    the same day, a "dividend" worth the distributed shares (DHR's 2016 Fortive
    spin-off: 1.319 and $24.56, 228x its regular payment; ETN 2001, ABT 2004).
    Both signals are needed: stock dividends also leave small factors in the
    split history beside ordinary cash dividends (SCCO), and a large payment on
    any other day is a special dividend, not a spin-off.
    """
    spin_off_days = {str(when)[:10] for when, ratio in splits
                     if ratio and ratio != 1.0 and 1.0 / SPLIT_JUMP_THRESHOLD < ratio < SPLIT_JUMP_THRESHOLD}
    kept = []
    for when, amount in dividends:
        day = str(when)[:10]
        if day in spin_off_days:
            others = [a for w, a in dividends if str(w)[:10] != day and abs(
                (date.fromisoformat(str(w)[:10]) - date.fromisoformat(day)).days) <= 400]
            if others and amount > SPIN_OFF_DIVIDEND_MULTIPLE * statistics.median(others):
                continue
        kept.append((when, amount))
    return kept


def _total_shares(snapshot: Optional[float], market_cap: Optional[float], price: Optional[float],
                  records: List[FYRecord]) -> Tuple[Optional[float], str]:
    """Shares of every class, for per-share values that are compared with the price.

    Yahoo's share count can cover one class of several (GOOGL's class A: 5.87B
    of 12.23B shares), while market cap / price counts them all, the way Yahoo
    values the company. A count still under MIN_SHARE_COUNT_RATIO of the latest
    FY's diluted shares on today's basis is incomplete too (HON, whose market cap
    is halved as well), so that FY count is used instead. A small gap is settled
    by that FY count: whichever of Yahoo's two figures is nearer it wins.
    """
    latest = next((r.shares for r in reversed(records) if r.shares and r.shares > 0), None)
    count, source = snapshot, "market_snapshot"
    by_value = market_cap / price if market_cap and price else None
    if snapshot and by_value and by_value > SHARE_CLASS_GAP * snapshot and not (
            latest and by_value < SHARE_SOURCE_GAP * snapshot
            and abs(math.log(snapshot / latest)) < abs(math.log(by_value / latest))):
        count, source = by_value, "market_cap / price"
    if count and latest and count < MIN_SHARE_COUNT_RATIO * latest:
        count, source = latest, "financials_annual"
    return count, source


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
        "SELECT fa.fiscal_year, fa.period_end, fa.filed_date, fa.form, fa.net_income, fa.total_equity, "
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
    # When each year's share figures were last filed, per field (the vintages, every
    # filing's view of each year): a share split restates only the filings made after it.
    filed_by_field: Dict[Tuple[int, str], str] = {}
    try:
        for v in conn.execute(
            "SELECT fiscal_year, "
            "MAX(CASE WHEN weighted_avg_shares_diluted IS NOT NULL THEN filed_date END), "
            "MAX(CASE WHEN eps_diluted IS NOT NULL THEN filed_date END), "
            "MAX(CASE WHEN shares_outstanding IS NOT NULL THEN filed_date END) "
            "FROM financials_annual_vintages WHERE ticker = ? GROUP BY fiscal_year",
            (ticker,),
        ).fetchall():
            for field_name, filed_on in zip(("weighted_avg_shares_diluted", "eps_diluted", "shares_outstanding"),
                                            (v[1], v[2], v[3])):
                if filed_on:
                    filed_by_field[(int(v[0]), field_name)] = filed_on
    except sqlite3.OperationalError:  # a database from before the vintages were kept
        pass
    records: List[FYRecord] = []
    shares_detect: List[Optional[float]] = []
    detect_filed: List[Optional[str]] = []
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
        detect, detect_field = r["weighted_avg_shares_diluted"], "weighted_avg_shares_diluted"
        if detect is None and r["net_income"] is not None and r["eps_diluted"]:
            detect, detect_field = r["net_income"] / r["eps_diluted"], "eps_diluted"
        if detect is None:
            detect, detect_field = r["shares_outstanding"], "shares_outstanding"
        shares_detect.append(detect)
        detect_filed.append(filed_by_field.get((int(r["fiscal_year"]), detect_field)))
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
    filed = fy_rows[-1]["filed_date"] if fy_rows else None
    # A split made after the latest filing restated nothing stored, so it can't
    # explain a seam (an older merger's would take it); _apply_later_splits
    # moves every year by it instead.
    restated = [s for s in splits if not filed or str(s[0])[:10] <= str(filed)[:10]]
    records, history_truncated = _normalize_splits(records, shares_detect, restated, detect_filed)

    # SELECT *: a database not yet migrated lacks the rate's date and source columns,
    # and the backfill reads it without migrating.
    snap = conn.execute(
        "SELECT * FROM market_snapshots "
        "WHERE ticker = ? ORDER BY collected_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    snap_columns = set(snap.keys()) if snap is not None else set()
    # Today's count of every class, as Yahoo has it, to confirm a split after the filing.
    yahoo_counts = [snap["shares_outstanding"]] if snap and snap["shares_outstanding"] else []
    if snap and snap["market_cap"] and snap["current_price"]:
        yahoo_counts.append(snap["market_cap"] / snap["current_price"])
    # Values are compared with this snapshot's price, so they stay on its basis: when a
    # later run's quote failed (no new snapshot) but it stored a split made since, that
    # split moves no year (BUG-048), one the latest filing already restated is taken back
    # out (BUG-060), and Yahoo's dividends and price bars, adjusted for every split it
    # knew, are taken back to the price's basis.
    priced_on = (str(snap["collected_at"])[:10]
                 if snap is not None and snap["current_price"] is not None and snap["collected_at"] else None)
    _apply_later_splits(records, splits, filed, yahoo_counts, priced_on)
    _undo_splits_after_price(records, splits, filed, priced_on)
    # A foreign issuer's filings count ordinary shares while the price is per ADS
    # (BUG-065): its per-share figures move to the ADS basis, before the share count
    # is chosen, so the 75% rule compares like with like.
    ads = 1.0
    filed_count = next((r.shares for r in reversed(records) if r.shares and r.shares > 0), None)
    yahoo_count = snap["shares_outstanding"] if snap is not None else None
    if fy_rows and str(fy_rows[-1]["form"] or "")[:4] in ("20-F", "40-F") and filed_count and yahoo_count:
        ads = _ads_ratio(filed_count / yahoo_count)
        for rec in records if ads != 1.0 else ():
            rec.split_factor /= ads
            if rec.eps_diluted is not None:
                rec.eps_diluted *= ads
            if rec.ffo_per_share is not None:
                rec.ffo_per_share *= ads
            if rec.shares is not None:
                rec.shares /= ads
    unpriced = math.prod(ratio for when, ratio in splits
                         if ratio and filed and priced_on and str(when)[:10] > priced_on)
    shares, shares_source = _total_shares(
        snap["shares_outstanding"] if snap else None,
        snap["market_cap"] if snap else None,
        snap["current_price"] if snap else None,
        records,
    )
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
    dividends = [(day, amount * unpriced) for day, amount in _drop_spin_off_dividends([
        (r["date"], r["amount"])
        for r in conn.execute(
            "SELECT date, amount FROM dividend_events "
            "WHERE ticker = ? AND amount IS NOT NULL ORDER BY date ASC",
            (ticker,),
        ).fetchall()
    ], splits)]

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
            fy_end_prices[rec.fiscal_year] = bar["close"] * unpriced

    return ValuationInputs(
        ticker=ticker,
        sector_class=sector,
        fy_records=records,
        shares_outstanding=shares,
        shares_source=shares_source,
        ads_ratio=ads,
        beta=snap["beta"] if snap else None,
        risk_free_rate=snap["risk_free_rate"] if snap else None,
        risk_free_rate_date=snap["risk_free_rate_date"] if "risk_free_rate_date" in snap_columns else None,
        risk_free_rate_source=snap["risk_free_rate_source"] if "risk_free_rate_source" in snap_columns else None,
        analyst_growth=analyst["earnings_growth"] if analyst else None,
        dividends=dividends,
        fy_end_prices=fy_end_prices,
        as_of=as_of,
        history_truncated=history_truncated,
    )

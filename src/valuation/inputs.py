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
from datetime import date, timedelta
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

#: An undeclared seam folds by its own ratio only on a split's marks: EPS of the same sign on both sides moved
#: against the count by at least this share of the seam's ratio (in log terms), and revenue didn't move with the
#: count by as much. A stock-paid merger brings its revenue and earnings with it (DUK's Progress Energy: 1.567x
#: the shares, revenue 1.88x, EPS 0.99 to 0.86; LIN's Linde AG: 1.88x and revenue 1.89x, though EPS fell), and
#: an exchange offer's EPS changes sign (Citigroup's 2009, 2.242x): they stay organic (BUG-175, BUG-185).
SPLIT_EVIDENCE_SHARE = 0.5

#: A fiscal year ending less than this many days after the listing (the first price bar) is the listing's year
#: or one before it: its weighted count leaves out the preferred stock that converted at the listing (PLTR's
#: 577M in FY2019 against 1.92B after), so a seam from it into the public count folds, whatever EPS did (BUG-185).
LISTING_YEAR_DAYS = 366

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
    as reported). ``private_count`` marks a year that ended before the listing
    and that only a listing's jump put on the public count: the converted
    preferred that jump stands for can't be told from a merger around the
    listing (BUG-188).

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
    private_count: bool = False

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
    #: Why a foreign issuer's ADS ratio couldn't be read, if it couldn't: the models
    #: on filed per-share figures or counts are then N/A with this reason.
    ads_unreadable: Optional[str] = None
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


def _split_marks(ratio: float, eps_older: Optional[float], eps_newer: Optional[float],
                 revenue_older: Optional[float], revenue_newer: Optional[float]) -> bool:
    """Whether an undeclared seam of *ratio* bears a split's marks (SPLIT_EVIDENCE_SHARE).

    A split multiplies the share count and divides as-reported EPS by the same factor, so EPS of one sign must
    move against the count, by magnitude: a loss-making company's per-share loss grows across a reverse split
    (GE: -2.62 to -4.99 over its 1:8). A seam whose EPS changes sign, or is missing or zero, carries no such mark.
    Revenue, where both periods have it, must not move with the count by as much: a split leaves it alone, a
    stock-paid merger brings its own. Both figures are as reported, and the test only judges seams no declared
    split explains: earnings can outgrow a split's ratio in its year (NOW 2023)."""
    if not eps_older or not eps_newer or (eps_older > 0) != (eps_newer > 0):
        return False
    direction = 1.0 if ratio > 1.0 else -1.0
    enough = SPLIT_EVIDENCE_SHARE * abs(math.log(ratio))
    if -direction * math.log(eps_newer / eps_older) < enough:
        return False
    if revenue_older and revenue_newer and revenue_older > 0 and revenue_newer > 0:
        return direction * math.log(revenue_newer / revenue_older) < enough
    return True


def _implied_count_moves(ratio: float, older: "FYRecord", newer: "FYRecord",
                         eps_older: Optional[float], eps_newer: Optional[float]) -> bool:
    """Whether the count a year's EPS divides by (net income / diluted EPS) moved with a seam of *ratio*, within
    DECLARED_SPLIT_TOLERANCE. A split restates the count and EPS together; BX's diluted count takes the Holdings
    units in and out (1,195M, 666M, 1,207M) while its EPS keeps dividing by about 670M (BUG-185). Unknown when a
    figure is missing, and then it doesn't decide."""
    if not (older.net_income and newer.net_income and eps_older and eps_newer):
        return True
    implied = (newer.net_income / eps_newer) / (older.net_income / eps_older)
    return implied > 0 and abs(math.log(implied / ratio)) <= math.log(DECLARED_SPLIT_TOLERANCE)


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
                      revenue: Optional[Sequence[Optional[float]]] = None,
                      first_bar: Optional[str] = None,
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
      Either folds by a declared share split (a simple ratio: a spin-off's price
      factor changes no share count), by its own ratio on a split's marks in EPS
      and *revenue* (``_split_marks``) when the count EPS implies moved with it
      (``_implied_count_moves``), or by its own ratio when the older year ends
      before the listing (*first_bar*) or in its year (LISTING_YEAR_DAYS).
      Otherwise it stays: a merger, an exchange offer, a count that swings alone
      (BUG-185). A year that ended before the first bar and that only a listing's
      jump folds is marked ``private_count``: a merger around the listing jumps
      the same way (DELL's EMC, BUG-188).
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
    listed = ((date.fromisoformat(str(first_bar)[:10]) + timedelta(days=LISTING_YEAR_DAYS)).isoformat()
              if first_bar else None)
    # From a seam only the listing explains, the years that ended before the first bar have the private count.
    private = False
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
                candidates = [split for split in unused
                              if _simple_ratio(split[1]) and _filed_between(split, f_old, f_new)]
                run = _declared_run(ratio, records[i].period_end, candidates)
                listing = (listed is not None and records[i].period_end is not None
                           and str(records[i].period_end)[:10] < listed)
                if run:
                    unused = [split for split in unused if split not in run]
                    factor *= math.prod(split_ratio for _, split_ratio in run)
                elif (_split_marks(ratio, reported_eps[i], reported_eps[i + 1],
                                   revenue[i] if revenue else None, revenue[i + 1] if revenue else None)
                      and _implied_count_moves(ratio, records[i], records[i + 1],
                                               reported_eps[i], reported_eps[i + 1])):
                    factor *= ratio
                elif listing:
                    factor *= ratio
                    private = True
        rec = records[i]
        rec.split_factor = factor
        rec.private_count = (private and first_bar is not None and rec.period_end is not None
                             and str(rec.period_end)[:10] < str(first_bar)[:10])
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


def _ads_ratio(filed_to_market: float) -> Optional[float]:
    """The ADS ratio a filed-to-market share-count ratio shows, or None when it
    can't be read.

    A foreign issuer's 20-F or 40-F counts ordinary shares, while Yahoo quotes the
    ADS (TSM: 25.93B ordinary, 5.186B ADS). A ratio in [0.8, 1.25] is a 1:1 listing,
    as buybacks and issuance drift a filed count that far from today's; any other is
    a whole number n >= 2 of shares per ADS (or of ADSs per share) within 5% and a
    quarter of a share, so a drifted 8-share ADS doesn't read as 9 (BUG-076).
    """
    if 0.8 <= filed_to_market <= 1.25:
        return 1.0
    for candidate, per_ads in ((filed_to_market, True), (1.0 / filed_to_market, False)):
        n = round(candidate)
        if n >= 2 and abs(candidate - n) <= min(0.05 * n, 0.25):
            return float(n) if per_ads else 1.0 / n
    return None


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


def _yahoo_dividend_factors(conn: sqlite3.Connection, ticker: str, flagged: bool,
                             splits: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Yahoo's dividend adjustment as ``[(ex_date, factor)]``, newest first.

    Yahoo lowers every price before an ex-date by 1 - d / (the close before it), so
    a stored dividend-adjusted price divided by the factors of the dividends after
    it is the market close. The stored close before an ex-date carries the later
    dividends' factors too, so each factor is solved with them:
    f = 1 / (1 + d * later / close). Against Yahoo's unadjusted closes, the golden
    basket's FY-end prices come back within 0.0001% (iteration 53's probe).

    Yahoo adjusted the stored bars for the dividends it knew when it served them,
    which the last such bar dates (the last flagged one, or any bar of a database
    from before the flag): a dividend after it, a later run's whose bar request
    failed, was never in them, so it isn't undone (BUG-084). Each payment goes onto the
    bars' split basis first, from the day Yahoo served it: a later run that stored a split
    and its dividends on the new basis while its bar request failed left post-split amounts
    beside pre-split closes, and KO's FY-end prices came out up to 12.8% low (BUG-104).
    """
    served = conn.execute(
        "SELECT max(date) FROM price_bars WHERE ticker = ?" + (" AND dividend_adjusted" if flagged else ""),
        (ticker,),
    ).fetchone()[0]
    factors: List[Tuple[str, float]] = []
    later = 1.0
    dated = "served_on" in {r[1] for r in conn.execute("PRAGMA table_info(dividend_events)")}
    for ex, amount, paid in conn.execute(
        "SELECT substr(date, 1, 10), amount, " + ("served_on" if dated else "NULL") + " FROM dividend_events "
        "WHERE ticker = ? AND amount > 0 AND substr(date, 1, 10) <= ? ORDER BY date DESC",
        (ticker, str(served or "")[:10]),
    ).fetchall():
        prev = conn.execute(
            "SELECT close FROM price_bars WHERE ticker = ? AND date < ? ORDER BY date DESC LIMIT 1",
            (ticker, ex),
        ).fetchone()
        if prev is None or not prev[0]:
            continue
        factor = 1.0 / (1.0 + amount * _dividend_onto(splits, served, paid) * later / prev[0])
        factors.append((ex, factor))
        later *= factor
    return factors


def _dividend_onto(splits: Sequence[Tuple[str, float]], onto: Optional[str],
                   served_on: Optional[str]) -> float:
    """The factor that puts a dividend Yahoo served on *served_on* onto the split basis of
    the day *onto*: the price's (BUG-103), or the stored bars' last day (BUG-104).

    Yahoo adjusts a payment for every split up to the day it serves it, so a split between
    that day and *onto* is in one of them only: a later run whose dividend request failed
    keeps the payments from before a split it stored, beside a price after it, and DDM read
    ten times its value (BUG-103). The bars' rule (``bars_to_price``), payment by payment. A
    payment without a served day is on the basis of every stored split, as before: until
    BUG-103's fix a run stored no split without the dividends from the same chart request.
    Without *onto* (no price, no bars) nothing moves.
    """
    if not onto:
        return 1.0
    target = str(onto)[:10]
    served = str(served_on)[:10] if served_on else None
    factor = 1.0
    for when, ratio in splits:
        day = str(when)[:10]
        if ratio and target < day and (served is None or day <= served):
            factor *= ratio
        elif ratio and served is not None and served < day <= target:
            factor /= ratio
    return factor


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


def bars_to_price_factor(splits: Sequence[Tuple[str, float]], priced_on: Optional[str],
                         last_bar: Optional[str]) -> float:
    """The factor that puts a price bar onto the basis of the price quoted on *priced_on*.

    The bars are on the basis of every split up to their last day, which their daily
    history reaches. A split between that day and the price's is in one of them only, as
    when a later run stored it while its price-history request failed (BUG-094).
    """
    factor = 1.0
    if priced_on and last_bar:
        last = str(last_bar)[:10]
        for when, ratio in splits:
            if ratio and priced_on < str(when)[:10] <= last:
                factor *= ratio
            elif ratio and last < str(when)[:10] <= priced_on:
                factor /= ratio
    return factor


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


def load_inputs(conn: sqlite3.Connection, ticker: str, max_years: int = _MAX_FY_HISTORY) -> ValuationInputs:
    """Build the ValuationInputs bundle for *ticker*.

    ``conn.row_factory`` must be ``sqlite3.Row``. Missing rows degrade to
    ``None``/empty fields — applicability is the models' concern, not the loader's.
    """
    row = conn.execute(
        "SELECT sector_class FROM companies WHERE ticker = ?", (ticker,)
    ).fetchone()
    sector = (row["sector_class"] if row is not None else None) or "general"

    fy_rows = conn.execute(
        "SELECT fa.fiscal_year, fa.period_end, fa.filed_date, fa.form, fa.net_income, fa.total_equity, fa.revenue, "
        "fa.eps_diluted, fa.weighted_avg_shares_diluted, fa.shares_outstanding, "
        "fa.depreciation_amortization, fa.capex, "
        "ma.free_cash_flow, ma.ffo_per_share "
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
    # The valuations read the latest _MAX_FY_HISTORY years; the quality scores ask for one more (a 10-year
    # CAGR). Splits fold newest first, so an older year never moves a newer one.
    for r in fy_rows[-max_years:]:
        # OCF - capex, interest already through OCF. A stored levered_fcf from before
        # BUG-169's fix took interest off again, so it isn't read.
        fcf = r["free_cash_flow"]
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
    first_bar = conn.execute("SELECT MIN(date) FROM price_bars WHERE ticker = ?", (ticker,)).fetchone()[0]
    records, history_truncated = _normalize_splits(records, shares_detect, restated, detect_filed,
                                                   [r["revenue"] for r in fy_rows[-max_years:]],
                                                   str(first_bar)[:10] if first_bar else None)

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
    # is chosen, so the 75% rule compares like with like. The ratio is the latest FY's
    # period-end count (else its diluted one), on today's basis, over market cap /
    # price, which counts every class where Yahoo's count can be one (BUG-076).
    ads = 1.0
    ads_unreadable = None
    latest = next((r for r in reversed(records) if r.shares and r.shares > 0), None)
    form = str(fy_rows[-1]["form"] or "") if fy_rows else ""
    if form[:4] in ("20-F", "40-F") and latest is not None and snap is not None:
        market_count = (snap["market_cap"] / snap["current_price"]
                        if snap["market_cap"] and snap["current_price"] else snap["shares_outstanding"])
        row = next(r for r in fy_rows if int(r["fiscal_year"]) == latest.fiscal_year)
        basis = row["weighted_avg_shares_diluted"] or row["shares_outstanding"]
        filed_count = (row["shares_outstanding"] * latest.shares / basis
                       if row["shares_outstanding"] and basis else latest.shares)
        read = _ads_ratio(filed_count / market_count) if market_count else 1.0
        if read is None:
            ads_unreadable = (f"the ADS ratio can't be read: the latest {form} counts "
                              f"{filed_count / market_count:.2f} shares for each one the market counts")
        else:
            ads = read
        for rec in records if ads != 1.0 else ():
            rec.split_factor /= ads
            if rec.eps_diluted is not None:
                rec.eps_diluted *= ads
            if rec.ffo_per_share is not None:
                rec.ffo_per_share *= ads
            if rec.shares is not None:
                rec.shares /= ads
    served = conn.execute("SELECT max(date) FROM price_bars WHERE ticker = ?", (ticker,)).fetchone()[0]
    bars_to_price = bars_to_price_factor(splits, priced_on, str(served)[:10] if served else None)
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
    # Yahoo's dividends come to the price's basis from the day Yahoo served them, as the
    # bars do (BUG-103), whatever the SEC record holds (BUG-078). A database from before
    # the column, read without migrating, keeps every stored split's basis.
    served_column = "served_on" in {r[1] for r in conn.execute("PRAGMA table_info(dividend_events)")}
    payments = conn.execute(
        "SELECT date, amount, " + ("served_on" if served_column else "NULL") + " AS served_on "
        "FROM dividend_events WHERE ticker = ? AND amount IS NOT NULL ORDER BY date ASC",
        (ticker,),
    ).fetchall()
    served_on = {r["date"]: r["served_on"] for r in payments}
    dividends = [(day, amount * _dividend_onto(splits, priced_on, served_on.get(day)))
                 for day, amount in _drop_spin_off_dividends([(r["date"], r["amount"]) for r in payments], splits)]

    # A bar from yfinance's default history is Yahoo's dividend-adjusted price,
    # lower by every dividend paid since (BUG-075): one flagged so, or any bar of a
    # database from before the flag. The multiples band wants the market close.
    flagged = "dividend_adjusted" in {r[1] for r in conn.execute("PRAGMA table_info(price_bars)")}
    factors: Optional[List[Tuple[str, float]]] = None
    fy_end_prices: Dict[int, float] = {}
    for rec in records:
        if rec.period_end is None:
            continue
        bar = conn.execute(
            "SELECT date, close" + (", dividend_adjusted" if flagged else "") + " FROM price_bars "
            "WHERE ticker = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (ticker, rec.period_end),
        ).fetchone()
        if bar is None or bar["close"] is None:
            continue
        close = bar["close"]
        if not flagged or bar["dividend_adjusted"]:
            if factors is None:
                factors = _yahoo_dividend_factors(conn, ticker, flagged, splits)
            close /= math.prod(f for ex, f in factors if ex > str(bar["date"])[:10])
        fy_end_prices[rec.fiscal_year] = close * bars_to_price

    return ValuationInputs(
        ticker=ticker,
        sector_class=sector,
        fy_records=records,
        shares_outstanding=shares,
        shares_source=shares_source,
        ads_ratio=ads,
        ads_unreadable=ads_unreadable,
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

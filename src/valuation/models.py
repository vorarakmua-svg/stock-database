"""Valuation models: DCF, DDM, Graham, Lynch, historical multiples band.

Each model is a pure function ``ValuationInputs -> ValuationResult`` producing
a bear/base/bull per-share fair-value range plus the exact assumptions used.
A model that does not apply returns ``applicable=False`` with a user-facing
``na_reason`` — no number is better than a fake number.
"""

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .assumptions import (
    DDM_GROWTH_CAP,
    DISCOUNT_SPREAD,
    MARGIN_OF_SAFETY,
    OE_DISCOUNT_FLOOR,
    OE_HISTORY_WINDOW,
    OE_MIN_POSITIVE_YEARS,
    TERMINAL_GROWTH,
    derive_discount,
    derive_growth,
    growth_scenarios,
)
from .inputs import FYRecord, ValuationInputs

DCF_SECTORS = ("general", "utility", "energy")

#: A payer whose most recent dividend is older than this (15 months — one
#: missed annual payment plus a grace quarter) is treated as having stopped.
DIVIDEND_STALE_DAYS = 456

#: Dividend growth uses at most this many years (the spec's "hist dividend CAGR
#: (<= 10 yrs)", the same window as the FY-based models): the fiscal years the
#: valuation reads, or, for a company without them, the complete calendar years
#: before the anchor's. Decades-old growth says little about the next five years.
DIVIDEND_CAGR_YEARS = 10

#: SEC's dividends per share for a fiscal year of two or more payments that is
#: within this share of one of them is that payment, not the year's: a filer
#: that tags each quarter's rate in a full-year context (V, CEG, BKNG, AMAT,
#: IBKR; BUG-020). One below every payment can't be their total either (PLD's
#: stray 0.01 beside 3.84 paid).
ONE_PAYMENT_TOLERANCE = 0.02


def _one_payment(sec: float, payments: List[float]) -> bool:
    """Whether SEC's figure for a year holds at most one of its *payments* (ONE_PAYMENT_TOLERANCE)."""
    return len(payments) >= 2 and (sec < min(payments) or any(
        abs(sec - paid) <= ONE_PAYMENT_TOLERANCE * paid for paid in payments))


#: Q3 (decided): a payment more than this many times the regular payments on each side of it (the median of the
#: other payments ex-dated in the year before it, and in the year after it) is outside the regular schedule: a
#: special (COST's $15 of 2023 beside $1.02 a quarter, PSA's $13.15), unless it recurs (ONE_OFF_RECURS_IN). A
#: payment with no payment after it is never one: it can be a raise (NVDA's $0.25 after $0.01 a quarter).
ONE_OFF_MULTIPLE = 3.0

#: A large payment recurs, and so stays (PGR's annual variable dividend each January, CME's each December, PCAR's
#: extra), when a payment over ONE_OFF_MULTIPLE times its regular level falls within ONE_OFF_SEASON_DAYS of its
#: anniversary in at least this many of the ONE_OFF_LOOKBACK_YEARS years before it. COST's specials (2012, 2015,
#: 2017, 2020, 2023) are two to three years apart and PSA's is the only one. The season is a quarter either way:
#: CME's for 2025 came on 2026-03-10, 73 days from its late-December anniversary.
ONE_OFF_RECURS_IN = 2
ONE_OFF_LOOKBACK_YEARS = 3
ONE_OFF_SEASON_DAYS = 91

#: A one-off whose neighbours are more than this many usual intervals apart takes a regular payment's place: Yahoo
#: served the special and that quarter's payment as one amount (HST's $0.92 of 2026-06-30, WRB's, CME's), so only
#: its excess over the regular level is one-off. Neighbours one usual interval apart (COST, PSA) leave the whole
#: payment an extra one. 1.5 is midway between the two.
ONE_OFF_SLOT_INTERVALS = 1.5


def one_off_dividends(dividends: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """The one-off part of each payment in *dividends* (``(ex_date, amount)``, oldest first) that is outside the
    regular schedule (Q3: ONE_OFF_MULTIPLE, ONE_OFF_RECURS_IN, ONE_OFF_SLOT_INTERVALS), as ``(ex_date, amount)``.

    The regular level on each side is the median of the other payments in the 365 days before the payment, and
    in the 365 days after it; the payment is a one-off when it exceeds ONE_OFF_MULTIPLE times the higher of the
    two (a raise has its new level after it) and doesn't recur. Its regular part, where it took a regular
    payment's place, is the lower of the two levels.
    """
    days = [date.fromisoformat(day[:10]) for day, _ in dividends]
    amounts = [amount for _, amount in dividends]
    intervals = [(later - earlier).days for earlier, later in zip(days, days[1:])]
    usual = statistics.median(intervals) if intervals else None
    one_offs: List[Tuple[str, float]] = []
    for i, (day, amount) in enumerate(zip(days, amounts)):
        before = [amounts[j] for j in range(len(days)) if j != i and day - timedelta(days=365) < days[j] <= day]
        after = [amounts[j] for j in range(len(days)) if j != i and day < days[j] <= day + timedelta(days=365)]
        if not before or not after:
            continue
        levels = (statistics.median(before), statistics.median(after))
        if amount <= ONE_OFF_MULTIPLE * max(levels):
            continue
        regular = min(levels)
        seasons = sum(
            any(amounts[j] > ONE_OFF_MULTIPLE * regular
                and abs((days[j] - (day - timedelta(days=365 * k))).days) <= ONE_OFF_SEASON_DAYS
                for j in range(i))
            for k in range(1, ONE_OFF_LOOKBACK_YEARS + 1))
        if seasons >= ONE_OFF_RECURS_IN:
            continue
        slot = (usual is not None and 0 < i < len(days) - 1
                and (days[i + 1] - days[i - 1]).days > ONE_OFF_SLOT_INTERVALS * usual)
        one_offs.append((dividends[i][0][:10], amount - regular if slot else amount))
    return one_offs


@dataclass
class DividendYear:
    """One year of the history DDM measures dividend growth over (``dividend_history``).

    ``amount`` is the year's dividends per share on today's basis; ``source`` says where
    it came from ("sec": the 10-K's dividends per share; "yahoo": the payments with
    ex-dates in the year); ``payments`` are those payments, whichever was used.
    ``restart`` marks a year whose dividend started or restarted during it: it has no
    payment, or its first one follows none, or follows the one before by more than
    DIVIDEND_STALE_DAYS (the dividend had stopped). ``one_off`` is the part of those
    payments that ``one_off_dividends`` finds outside the regular schedule (Q3).
    """

    year: int
    amount: Optional[float]
    source: Optional[str]
    payments: List[float] = field(default_factory=list)
    restart: bool = False
    one_off: float = 0.0

    def regular_amount(self) -> Optional[float]:
        """``amount`` without the year's one-off payments (Q3), for a CAGR endpoint. SEC's figure loses them only
        when it holds them: when it is nearer the year's payments with them than without them (COST's FY2017
        declared $8.90 with its $7 special; LDOS's FY2016 $1.28 left its $13.64 out). Yahoo's sum always holds them."""
        if self.amount is None or not self.one_off or self.amount <= sum(self.payments) - self.one_off / 2:
            return self.amount
        return self.amount - self.one_off


@dataclass
class ValuationResult:
    """Outcome of one model for one ticker."""

    model: str
    applicable: bool
    na_reason: Optional[str] = None
    value_bear: Optional[float] = None
    value_base: Optional[float] = None
    value_bull: Optional[float] = None
    assumptions: Dict[str, Any] = field(default_factory=dict)
    basis_fiscal_year: Optional[int] = None


def _rate_provenance(inputs: ValuationInputs) -> Dict[str, Any]:
    """The risk-free rate's day and source, for the models that discount with it.

    The spec stores "the rate and its as-of date so staleness is visible in the
    UI" (the VAL tab lists every assumption). The static estimate the rate falls
    back to when FRED and Treasury.gov both fail is flagged; with no rate at all,
    the model's ``rf_fallback`` already says so.
    """
    if inputs.risk_free_rate is None:
        return {}
    meta: Dict[str, Any] = {
        "risk_free_rate_as_of": inputs.risk_free_rate_date,
        "risk_free_rate_source": inputs.risk_free_rate_source,
    }
    if inputs.risk_free_rate_source == "estimate":
        meta["rf_estimate"] = True
    return meta


#: Q8 (decided): a per-share value on a fiscal year more than this many years before the valuation's day is N/A: a
#: company whose usable annual reports stopped (IFRS, BUG-066; no longer filing) was valued on FY2017 in 2026.
MAX_BASIS_AGE_FYS = 2


def stale_basis_reason(inputs: ValuationInputs, basis_fy: Optional[int]) -> Optional[str]:
    """The N/A reason when *basis_fy* is more than MAX_BASIS_AGE_FYS fiscal years before *inputs*' day, else None.
    No day (``as_of`` unset) or no basis year: None."""
    if inputs.as_of is None or basis_fy is None or inputs.as_of.year - basis_fy <= MAX_BASIS_AGE_FYS:
        return None
    return f"basis FY{basis_fy} is more than {MAX_BASIS_AGE_FYS} fiscal years old"


def _na(model: str, reason: str,
        basis_fy: Optional[int] = None) -> ValuationResult:
    return ValuationResult(model=model, applicable=False, na_reason=reason,
                           basis_fiscal_year=basis_fy)


def dcf_per_share(fcf0: float, shares: float, growth: float, discount: float,
                  terminal_growth: float = TERMINAL_GROWTH) -> float:
    """Two-stage 10-year DCF on equity free cash flow, per share.

    Years 1-5 grow at ``growth``; years 6-10 fade linearly to
    ``terminal_growth``; Gordon terminal value at year 10. ``discount`` must
    exceed ``terminal_growth`` (guaranteed by the DISCOUNT_FLOOR clamp).
    """
    value = 0.0
    fcf = fcf0
    for t in range(1, 11):
        rate = growth if t <= 5 else growth + (terminal_growth - growth) * (t - 5) / 5.0
        fcf *= 1.0 + rate
        value += fcf / (1.0 + discount) ** t
    terminal = fcf * (1.0 + terminal_growth) / (discount - terminal_growth)
    value += terminal / (1.0 + discount) ** 10
    return value / shares


def value_dcf(inputs: ValuationInputs) -> ValuationResult:
    """FCF DCF for operating companies (general/utility/energy)."""
    if inputs.sector_class not in DCF_SECTORS:
        return _na("dcf", f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if r.fcf is not None]
    if len(recs) < 4:
        return _na("dcf", "insufficient FCF history (need >= 4 fiscal years)")
    basis_fy = recs[-1].fiscal_year
    fcf_hist = [r.fcf for r in recs]
    basis = statistics.median([f for f in fcf_hist[-3:] if f is not None])
    if basis <= 0:
        return _na("dcf", "median 3-year FCF is not positive", basis_fy=basis_fy)
    shares = inputs.shares_outstanding
    shares_source = inputs.shares_source
    if not shares or shares <= 0:
        shares = None
        for r in reversed(recs):
            if r.shares is not None and r.shares > 0:
                shares = r.shares
                shares_source = "financials_annual"
                break
        if not shares:
            return _na("dcf", "shares outstanding unavailable", basis_fy=basis_fy)

    growth, gmeta = derive_growth(fcf_hist,
                                  periods=[r.fiscal_year for r in recs])
    discount, dmeta = derive_discount(inputs.risk_free_rate, inputs.beta)
    g_bear, g_base, g_bull = growth_scenarios(growth)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update(dmeta)
    assumptions.update(_rate_provenance(inputs))
    assumptions.update({
        "fcf_basis": basis,
        "fcf_years": len(fcf_hist),
        "history_truncated": inputs.history_truncated,
        "terminal_growth": TERMINAL_GROWTH,
        "shares_outstanding": shares,
        "shares_source": shares_source,
    })
    return ValuationResult(
        model="dcf",
        applicable=True,
        value_bear=dcf_per_share(basis, shares, g_bear, discount + DISCOUNT_SPREAD),
        value_base=dcf_per_share(basis, shares, g_base, discount),
        value_bull=dcf_per_share(basis, shares, g_bull, discount - DISCOUNT_SPREAD),
        assumptions=assumptions,
        basis_fiscal_year=basis_fy,
    )


def ddm_per_share(ttm_dps: float, growth: float, discount: float,
                  terminal_growth: float = TERMINAL_GROWTH) -> float:
    """Multi-stage Gordon DDM: 5 years at ``growth``, then terminal growth."""
    value = 0.0
    d = ttm_dps
    for t in range(1, 6):
        d *= 1.0 + growth
        value += d / (1.0 + discount) ** t
    terminal = d * (1.0 + terminal_growth) / (discount - terminal_growth)
    value += terminal / (1.0 + discount) ** 5
    return value


def _annual_dividend_totals(dividends: List[Tuple[str, float]]) -> List[Tuple[int, float]]:
    totals: Dict[int, float] = {}
    for date_str, amount in dividends:
        year = int(date_str[:4])
        totals[year] = totals.get(year, 0.0) + amount
    return sorted(totals.items())


def _paid_between(dividends: List[Tuple[str, float]], start: str, end: str) -> List[float]:
    """The payments with ex-dates in (*start*, *end*]."""
    return [amount for day, amount in dividends if start < day[:10] <= end]


def dividend_history(inputs: ValuationInputs, anchor: date,
                     one_offs: Sequence[Tuple[str, float]] = ()) -> List[DividendYear]:
    """Each year's dividends per share, oldest first, for DDM's growth (BUG-049).

    The years are the fiscal years the valuation reads (``fy_records``, up to
    DIVIDEND_CAGR_YEARS), each from the day after the year before ended (else a
    year before its end) to its end. A year's amount is SEC's dividends per share
    (``FYRecord.dividends_per_share``: declared, else cash paid, on the basis of
    Yahoo's payments). Counting Yahoo's ex-dates can't tell a payment that slipped
    across a year end from an extra or special one, or a slip from a change of
    schedule, and each count-based rule misread a new shape (BUG-017, BUG-049).
    Where SEC has no usable figure, Yahoo's payments with ex-dates in the year are
    summed: a filer that tags only point-in-time payments leaves the year empty
    (BUG-013), and one that tags a single payment as the year is no better
    (``_one_payment``). A company without fiscal years (no SEC annual record) uses
    the complete calendar years before the anchor's, on Yahoo's payments alone. A
    year with neither source paid nothing (amount None). Each year's ``one_off`` sums
    the *one_offs* (``one_off_dividends``) ex-dated in it.
    """
    dividends = inputs.dividends
    spans: List[Tuple[int, str, str, Optional[float]]] = []
    records = [rec for rec in inputs.fy_records if rec.period_end][-DIVIDEND_CAGR_YEARS:]
    if records:
        previous: Optional[FYRecord] = None
        for rec in records:
            end = str(rec.period_end)[:10]
            start = (str(previous.period_end)[:10] if previous is not None
                     and previous.fiscal_year == rec.fiscal_year - 1
                     else (date.fromisoformat(end) - timedelta(days=365)).isoformat())
            spans.append((rec.fiscal_year, start, end, rec.dividends_per_share))
            previous = rec
    else:
        spans = [(year, f"{year - 1}-12-31", f"{year}-12-31", None)
                 for year in range(anchor.year - DIVIDEND_CAGR_YEARS, anchor.year)]
    history = []
    for year, start, end, sec in spans:
        paid = [(day[:10], amount) for day, amount in dividends if start < day[:10] <= end]
        payments = [amount for _, amount in paid]
        before = [day[:10] for day, _ in dividends if day[:10] <= start]
        restart = not paid or not before or (
            date.fromisoformat(paid[0][0]) - date.fromisoformat(before[-1])).days > DIVIDEND_STALE_DAYS
        one_off = sum(amount for day, amount in one_offs if start < day[:10] <= end)
        if sec is not None and sec > 0 and not _one_payment(sec, payments):
            history.append(DividendYear(year, sec, "sec", payments, restart, one_off))
        elif payments:
            history.append(DividendYear(year, sum(payments), "yahoo", payments, restart, one_off))
        else:
            history.append(DividendYear(year, None, None, payments, restart, one_off))
    return history


def _growth_years(history: List[DividendYear]) -> List[DividendYear]:
    """The years DDM's CAGR spans: the paying years of *history* (``dividend_history``)
    from the first one whose dividend didn't start or restart during it. Such a year is
    partial (TMUS's 2023), so growth measured from it counts payments, not raises.
    Payments, not SEC's figure, mark the start: a first dividend declared at a year's end
    is in that year's figure though paid in the next, which declares one fewer (MU: 0.10
    declared in FY2021, which paid nothing; 0.315 declared in FY2022, which paid 0.415),
    so both years go. None when fewer than 4 remain (the spec's historical CAGR "requires
    >= 4 FYs"). A short last year stays: it can be a real cut."""
    start = 0
    while start < len(history) and (history[start].amount is None or history[start].restart):
        start += 1
    years = [entry for entry in history[start:] if entry.amount is not None]
    return years if len(years) >= 4 else []


def value_ddm(inputs: ValuationInputs) -> ValuationResult:
    """Multi-stage dividend discount model — any steady payer, and the primary
    model for banks/insurers where FCF is meaningless."""
    if not inputs.dividends:
        return _na("ddm", "no dividend history")
    annual = _annual_dividend_totals(inputs.dividends)
    if len(annual) < 3:
        return _na("ddm", "insufficient dividend history (need >= 3 calendar years)")

    # Anchor the TTM window to "now" (the latest data collection), NOT to the
    # last dividend in the table — otherwise a company that suspended its
    # dividend years ago still shows a full DDM fair value today.
    anchor = inputs.as_of or date.today()
    last_paid = date.fromisoformat(inputs.dividends[-1][0][:10])
    if (anchor - last_paid).days > DIVIDEND_STALE_DAYS:
        return _na("ddm", "dividends discontinued (no payment in the last 15 months)")
    cutoff = (anchor - timedelta(days=365)).isoformat()
    # Growth from the yearly dividends per share (SEC's, else Yahoo's payments in
    # the year; ``dividend_history``), over the years ``_growth_years`` keeps. The
    # TTM basis stays Yahoo's payments.
    one_offs = one_off_dividends(inputs.dividends)
    years = _growth_years(dividend_history(inputs, anchor, one_offs))
    # Q4 (decided): where the completeness check finds Yahoo missing payments that DDM reads from Yahoo (its gap
    # runs into the trailing twelve months, or its year is a growth year without SEC's figure), there is no honest
    # basis. An older flagged year with SEC's figure takes SEC's figure and so stays.
    from_yahoo = {entry.year for entry in years if entry.source == "yahoo"}
    incomplete = [year for year, gap_from, gap_to in inputs.dividend_gaps
                  if year in from_yahoo or (gap_to[:10] > cutoff and gap_from[:10] < anchor.isoformat())]
    if incomplete:
        return _na("ddm", "dividend history incomplete (Yahoo is missing payments in "
                   + ", ".join(f"FY{year}" for year in incomplete) + ")")
    # Q3 (decided): a one-off payment leaves the trailing twelve months and the CAGR's endpoints; a recurring
    # variable dividend (PGR's) stays.
    in_ttm = [(day, amount) for day, amount in one_offs if cutoff < day <= anchor.isoformat()]
    ttm = sum(a for d, a in inputs.dividends if cutoff < d[:10] <= anchor.isoformat()) - sum(a for _, a in in_ttm)
    if ttm <= 0:
        return _na("ddm", "no dividends in trailing 12 months")
    amounts = [entry.amount for entry in years]
    if years:
        amounts[0], amounts[-1] = years[0].regular_amount(), years[-1].regular_amount()
    growth, gmeta = derive_growth(amounts, cap=DDM_GROWTH_CAP,
                                  periods=[entry.year for entry in years])
    discount, dmeta = derive_discount(inputs.risk_free_rate, inputs.beta)
    g_bear, g_base, g_bull = growth_scenarios(growth, cap=DDM_GROWTH_CAP)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update(dmeta)
    assumptions.update(_rate_provenance(inputs))
    assumptions.update({
        "ttm_dps": ttm,
        "ttm_anchor": anchor.isoformat(),
        "dividend_years": len(annual),
        "dividend_growth_years": [[entry.year, amount, entry.source] for entry, amount in zip(years, amounts)],
        "history_truncated": inputs.history_truncated,
        "terminal_growth": TERMINAL_GROWTH,
    })
    if in_ttm:
        assumptions["ttm_one_offs"] = [[day, amount] for day, amount in in_ttm]
    removed = [[entry.year, entry.amount - amount] for entry, amount in zip(years, amounts)
               if entry.amount is not None and amount is not None and amount != entry.amount]
    if removed:
        assumptions["growth_one_offs"] = removed
    return ValuationResult(
        model="ddm",
        applicable=True,
        value_bear=ddm_per_share(ttm, g_bear, discount + DISCOUNT_SPREAD),
        value_base=ddm_per_share(ttm, g_base, discount),
        value_bull=ddm_per_share(ttm, g_bull, discount - DISCOUNT_SPREAD),
        assumptions=assumptions,
        basis_fiscal_year=anchor.year,
    )


LYNCH_PE_FLOOR = 5.0
LYNCH_PE_CAP = 25.0


def value_graham(inputs: ValuationInputs) -> ValuationResult:
    """Graham Number: sqrt(22.5 * EPS * BVPS) on the latest fiscal year. Bear/bull vary EPS over 3 FYs.

    The latest fiscal year must carry EPS and book value per share; an older year never stands in for it
    (BUG-246: ARES, LEN and VTRS were valued on FY2023-24 beside today's price)."""
    recs = [r for r in inputs.fy_records
            if r.eps() is not None and r.bvps() is not None]
    if not recs:
        return _na("graham", "EPS or book value unavailable")
    newest = inputs.fy_records[-1]
    if newest.eps() is None:
        return _na("graham", f"EPS unavailable for the latest fiscal year (FY{newest.fiscal_year})",
                   basis_fy=newest.fiscal_year)
    if newest.bvps() is None:
        return _na("graham",
                   f"book value per share unavailable for the latest fiscal year (FY{newest.fiscal_year})",
                   basis_fy=newest.fiscal_year)
    latest = recs[-1]
    eps = latest.eps()
    bvps = latest.bvps()
    assert eps is not None and bvps is not None  # narrowed by filter above
    if eps <= 0:
        return _na("graham", "EPS is not positive", basis_fy=latest.fiscal_year)
    if bvps <= 0:
        return _na("graham", "book value per share is not positive",
                   basis_fy=latest.fiscal_year)
    # The last 3 fiscal years on EPS alone: book value is needed for the latest year only (BUG-305).
    window = [e for e in (r.eps() for r in inputs.fy_records[-3:]) if e is not None]
    last3 = [e for e in window if e > 0]
    # The spec's bear is the lowest EPS of the last 3 FYs; a loss there has no Graham number, so the bear is N/A
    # and the bear median is the other models' (BUG-007), not the base case again.
    loss_in_window = len(last3) < len(window)
    eps_bear: Optional[float] = None if loss_in_window else min(last3)
    eps_bull = max(last3)
    assumptions: Dict[str, Any] = {
        "eps_base": eps, "eps_bear": eps_bear, "eps_bull": eps_bull,
        "bvps": bvps, "multiplier": 22.5,
        "history_truncated": inputs.history_truncated,
    }
    if loss_in_window:
        assumptions["bear_na_reason"] = "a loss year in the last 3 fiscal years"
    return ValuationResult(
        model="graham",
        applicable=True,
        value_bear=None if eps_bear is None else math.sqrt(22.5 * eps_bear * bvps),
        value_base=math.sqrt(22.5 * eps * bvps),
        value_bull=math.sqrt(22.5 * eps_bull * bvps),
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )


def _lynch_fair_pe(growth: float) -> float:
    return min(max(growth * 100.0, LYNCH_PE_FLOOR), LYNCH_PE_CAP)


def _split_adjusted(recs: List[FYRecord]) -> bool:
    """True iff any record used was restated onto the current share basis."""
    return any(r.split_factor != 1.0 for r in recs)


def value_lynch(inputs: ValuationInputs) -> ValuationResult:
    """Peter Lynch fair value: growth-rate-as-fair-P/E times latest EPS."""
    if inputs.sector_class not in DCF_SECTORS:
        return _na("lynch", f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if r.eps() is not None]
    if len(recs) < 4:
        return _na("lynch", "insufficient EPS history (need >= 4 fiscal years)")
    latest = recs[-1]
    eps = latest.eps()
    assert eps is not None
    if eps <= 0:
        return _na("lynch", "EPS is not positive", basis_fy=latest.fiscal_year)
    growth, gmeta = derive_growth([r.eps() for r in recs],
                                  periods=[r.fiscal_year for r in recs])
    if gmeta["growth_source"] == "none":
        # Lynch IS the growth rate: with no CAGR, even from the first profitable year (Q5), the
        # P/E floor would publish 5 x EPS — a number with no information in it.
        return _na("lynch", "no usable earnings-growth history",
                   basis_fy=latest.fiscal_year)
    g_bear, g_base, g_bull = growth_scenarios(growth)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update({
        "eps_base": eps,
        "fair_pe_base": _lynch_fair_pe(g_base),
        "fair_pe_floor": LYNCH_PE_FLOOR,
        "fair_pe_cap": LYNCH_PE_CAP,
        "split_adjusted": _split_adjusted(recs),
        "history_truncated": inputs.history_truncated,
    })
    return ValuationResult(
        model="lynch",
        applicable=True,
        value_bear=_lynch_fair_pe(g_bear) * eps,
        value_base=_lynch_fair_pe(g_base) * eps,
        value_bull=_lynch_fair_pe(g_bull) * eps,
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )


def _multiple_basis(sector_class: str) -> Tuple[str, Callable[[FYRecord], Optional[float]]]:
    if sector_class in ("bank", "insurance"):
        return "pb", lambda r: r.bvps()
    if sector_class == "reit":
        return "pffo", lambda r: r.ffo_per_share
    return "pe", lambda r: r.eps()


def value_multiples(inputs: ValuationInputs) -> ValuationResult:
    """Price vs the company's own 5-year band of its sector-appropriate multiple."""
    kind, basis_fn = _multiple_basis(inputs.sector_class)
    recs = inputs.fy_records[-5:]
    multiples = []
    for r in recs:
        b = basis_fn(r)
        p = inputs.fy_end_prices.get(r.fiscal_year)
        if b is not None and b > 0 and p is not None and p > 0:
            multiples.append(p / b)
    if len(multiples) < 3:
        return _na("multiples",
                   "insufficient multiple history (need >= 3 fiscal years)")
    latest = inputs.fy_records[-1]
    latest_basis = basis_fn(latest)
    if latest_basis is None or latest_basis <= 0:
        return _na("multiples", "latest per-share basis is not positive",
                   basis_fy=latest.fiscal_year)
    # The band is the yearly multiples' inclusive quartiles, as the valuation history's ranges are, so one year's
    # tiny earnings can't set the bull case (BUG-172: GE's FY2022 read 1,044x and a bull of $8,498.58).
    band_low, band_mid, band_high = statistics.quantiles(multiples, n=4, method="inclusive")
    assumptions: Dict[str, Any] = {
        "multiple_kind": kind,
        "band_low": band_low, "band_median": band_mid, "band_high": band_high,
        "band_min": min(multiples), "band_max": max(multiples),
        "n_years": len(multiples),
        "latest_basis": latest_basis,
        "split_adjusted": _split_adjusted(recs),
        "history_truncated": inputs.history_truncated,
    }
    return ValuationResult(
        model="multiples",
        applicable=True,
        value_bear=band_low * latest_basis,
        value_base=band_mid * latest_basis,
        value_bull=band_high * latest_basis,
        assumptions=assumptions,
        basis_fiscal_year=latest.fiscal_year,
    )


def owner_earnings(rec: FYRecord) -> Optional[float]:
    """Buffett's owner earnings: net income + non-cash charges - MAINTENANCE capex.

    Maintenance capex is the one figure he leaves to judgment; depreciation is the
    accounting estimate of the same thing (what it costs to stand still), so we use
    ``min(D&A, |capex|)`` — capped at what the company actually spent, since you
    cannot spend more maintaining assets than you spent in total. Capex above that
    is growth spending and is NOT subtracted: the whole point of the measure is to
    stop treating expansion as a cost of standing still.

    Stock-based compensation is deliberately NOT added back. It is a real cost of
    running the business, whatever its cash character.
    """
    if rec.net_income is None or rec.depreciation_amortization is None:
        return None
    da = rec.depreciation_amortization
    capex = abs(rec.capex) if rec.capex is not None else da
    maintenance = min(da, capex)
    return rec.net_income + da - maintenance


def owner_earnings_volatility(history: List[float]) -> Dict[str, Any]:
    """How steady is this owner-earnings history?

    The model cannot mechanically make Buffett's circle-of-competence call — no
    simple metric separates an oil major from a soft-drink company without also
    misjudging one of them (both a trend-fit and a drawdown filter were tried
    against real data and each refused businesses the other kept). So it does not
    pretend to. It records the evidence and lets the reader judge:

    * ``collapse_years`` — years in which owner earnings fell below HALF their
      trailing 3-year median, or went negative. A steady business rarely halves
      against its own recent normal; a cyclical does it every cycle.
    * ``worst_drop`` — the deepest single-year fall, as a fraction (0.72 = a 72% fall).
    * ``positive_years`` / ``total_years`` — the raw sign record.
    """
    total = len(history)
    positive = sum(1 for v in history if v > 0)
    collapses = 0
    worst_drop = 0.0
    for i in range(1, total):
        prev, cur = history[i - 1], history[i]
        if prev > 0 and cur < prev:
            # Capped at 100%: a year that swings to a loss has wiped out its
            # earnings entirely, and reporting "a 334% fall" (SLB's raw figure)
            # would read as nonsense rather than as evidence.
            worst_drop = max(worst_drop, min(1.0, (prev - cur) / prev))
    for i in range(3, total):
        base = statistics.median(history[i - 3:i])
        if history[i] <= 0 or (base > 0 and history[i] < 0.5 * base):
            collapses += 1
    return {
        "collapse_years": collapses,
        "worst_drop": worst_drop,
        "positive_years": positive,
        "total_years": total,
    }


def value_owner_earnings(inputs: ValuationInputs) -> ValuationResult:
    """Owner earnings discounted at the Treasury rate, with a margin of safety.

    Deliberately unlike ``value_dcf``: no beta, no equity-risk premium, growth capex
    credited back, and a refusal to value a business whose earnings cannot be
    forecast. Its verdict is its own (see ``engine.owner_earnings_verdict``) and it is
    excluded from the cross-model median — averaging a Treasury-discounted value with
    CAPM-discounted ones would silently drag every verdict toward "cheap".
    """
    if inputs.sector_class not in DCF_SECTORS:
        return _na("owner_earnings",
                   f"not applicable to sector '{inputs.sector_class}'")
    recs = [r for r in inputs.fy_records if owner_earnings(r) is not None]
    if len(recs) < 4:
        return _na("owner_earnings",
                   "insufficient history (need >= 4 fiscal years)")
    basis_fy = recs[-1].fiscal_year

    window = recs[-OE_HISTORY_WINDOW:]
    if len(window) < OE_HISTORY_WINDOW:
        return _na("owner_earnings",
                   "insufficient history for the predictability test (need >= 10 fiscal years)",
                   basis_fy=basis_fy)
    oe_hist = [owner_earnings(r) for r in window]
    positive_years = sum(1 for v in oe_hist if v is not None and v > 0)
    if positive_years < OE_MIN_POSITIVE_YEARS:
        return _na("owner_earnings", "owner earnings too erratic to forecast",
                   basis_fy=basis_fy)

    basis = statistics.median([v for v in oe_hist[-3:] if v is not None])
    if basis <= 0:
        return _na("owner_earnings",
                   "median 3-year owner earnings is not positive", basis_fy=basis_fy)

    shares = inputs.shares_outstanding
    shares_source = inputs.shares_source
    if not shares or shares <= 0:
        shares = None
        for r in reversed(recs):
            if r.shares is not None and r.shares > 0:
                shares = r.shares
                shares_source = "financials_annual"
                break
        if not shares:
            return _na("owner_earnings", "shares outstanding unavailable",
                       basis_fy=basis_fy)

    growth, gmeta = derive_growth(
        oe_hist,
        periods=[r.fiscal_year for r in window],
    )
    g_bear, g_base, g_bull = growth_scenarios(growth)

    # Buffett's discount rate: the long-term government rate, floored. No beta.
    rf = inputs.risk_free_rate
    rf_fallback = rf is None
    if rf is None:
        rf = OE_DISCOUNT_FLOOR
    discount = max(rf, OE_DISCOUNT_FLOOR)

    # The discount rate is held FIXED across scenarios: under this method it is an
    # observable market fact, not a risk knob. Only growth varies.
    latest = recs[-1]
    da = latest.depreciation_amortization or 0.0
    latest_capex = abs(latest.capex) if latest.capex is not None else da
    maintenance = min(da, latest_capex)

    value_base = dcf_per_share(basis, shares, g_base, discount)
    assumptions: Dict[str, Any] = {}
    assumptions.update(gmeta)
    assumptions.update({
        "owner_earnings_basis": basis,
        "maintenance_capex": maintenance,
        "growth_capex_added_back": latest_capex - maintenance,
        "discount_base": discount,
        "discount_floor": OE_DISCOUNT_FLOOR,
        "risk_free_rate": rf,
        "beta_used": False,
        "sbc_added_back": False,
        "margin_of_safety": MARGIN_OF_SAFETY,
        "buy_below": value_base * (1.0 - MARGIN_OF_SAFETY),
        "positive_years": positive_years,
        "history_truncated": inputs.history_truncated,
        "terminal_growth": TERMINAL_GROWTH,
        "shares_outstanding": shares,
        "shares_source": shares_source,
        "volatility": owner_earnings_volatility([v for v in oe_hist if v is not None]),
    })
    if rf_fallback:
        assumptions["rf_fallback"] = True
    assumptions.update(_rate_provenance(inputs))
    return ValuationResult(
        model="owner_earnings",
        applicable=True,
        value_bear=dcf_per_share(basis, shares, g_bear, discount),
        value_base=value_base,
        value_bull=dcf_per_share(basis, shares, g_bull, discount),
        assumptions=assumptions,
        basis_fiscal_year=basis_fy,
    )

"""The other valuation indicators (Milestone 4): the reverse DCF, the earnings power value, NCAV with Graham's
net-net, the median P/S value and the Graham number on tangible book.

Display-only, like owner earnings: they never enter MEDIAN_MODELS, the verdicts or the upside. Each is a pure
function of ``IndicatorInputs`` (read by ``load_indicator_inputs``) giving a base value, a bear and a bull where the
method has them, an N/A reason otherwise, and its inputs in ``assumptions``. See
docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md ("Indicators").
"""

import logging
import math
import sqlite3
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..scores.fundamentals import AnnualRow, Fundamentals, load_fundamentals
from ..scores.scores import DEBT_UNKNOWN, DEFAULT_TAX, TAX_CAP, Market, _total_debt, wacc
from .inputs import ValuationInputs, load_inputs
from .models import dcf_per_share, value_dcf, value_graham

#: The indicators, in their stored and displayed order.
INDICATORS: Tuple[str, ...] = ("reverse_dcf", "epv", "ncav", "ps_value", "graham_tangible")

#: The reverse DCF searches the growth of the DCF's years 1-5 over this range.
REVERSE_DCF_LOW = -0.50
REVERSE_DCF_HIGH = 1.00
#: The bisection stops once its bracket is narrower than this, or after this many halvings.
REVERSE_DCF_TOLERANCE = 1e-7
REVERSE_DCF_MAX_STEPS = 100

#: EPV's normalized margin is the mean over the last EPV_YEARS fiscal years, of which at least EPV_MIN_YEARS must
#: have operating income and a positive revenue.
EPV_YEARS = 5
EPV_MIN_YEARS = 3
#: EPV's bear and bull cases are at the WACC plus and minus this.
EPV_WACC_SPREAD = 0.01
#: The sectors with an operating EBIT: banks, insurers and REITs have no EPV.
EPV_SECTORS: Tuple[str, ...] = ("general", "utility", "energy")

#: Graham's net-net working capital counts receivables and inventory at these shares of their book value.
NNWC_RECEIVABLES = 0.75
NNWC_INVENTORY = 0.5
#: Graham's buy line: two thirds of NCAV.
NETNET_BUY = 2 / 3
#: Banks and insurers file no current assets.
_NO_CURRENT_ASSETS: Tuple[str, ...] = ("bank", "insurance")

#: The Graham number's multiplier: a P/E of 15 times a P/B of 1.5.
GRAHAM_MULTIPLIER = 22.5

#: The sectors' names in the N/A reasons.
_SECTOR_NAMES: Dict[str, str] = {"bank": "banks", "insurance": "insurers", "reit": "REITs"}
#: Each indicator's unit, where it isn't a value per share on the price's basis.
_UNITS: Dict[str, str] = {"reverse_dcf": "rate"}


@dataclass
class IndicatorResult:
    """One indicator for one ticker. An N/A row may still carry the assumptions computed before the method stopped
    (a negative NCAV, say), so the page can say how far off it is."""

    indicator: str
    applicable: bool
    na_reason: Optional[str] = None
    value_bear: Optional[float] = None
    value_base: Optional[float] = None
    value_bull: Optional[float] = None
    unit: str = "per_share"
    assumptions: Dict[str, Any] = field(default_factory=dict)
    basis_fiscal_year: Optional[int] = None


@dataclass
class PsRange:
    """The stored ``valuation_ranges`` row for P/S (Milestone 1). Its implied value is the 10-year median P/S times
    the trailing revenue per share, on the latest quote's basis."""

    applicable: bool
    na_reason: Optional[str] = None
    as_of: Optional[str] = None
    n_months: Optional[int] = None
    current: Optional[float] = None
    p25: Optional[float] = None
    median: Optional[float] = None
    p75: Optional[float] = None
    implied_value: Optional[float] = None


@dataclass
class IndicatorInputs:
    """Everything the indicators read for one ticker."""

    ticker: str
    #: The six models' own inputs (``inputs.load_inputs``).
    valuation: ValuationInputs
    #: M2's annual rows (``scores.fundamentals.load_fundamentals``), with the debt reading.
    fundamentals: Fundamentals
    #: The latest snapshot with a price.
    market: Market = field(default_factory=Market)
    price_date: Optional[str] = None
    #: None before the valuation history made one.
    ps_range: Optional[PsRange] = None
    #: fiscal_year -> {"total_equity", "goodwill", "intangible_assets"}, from ``financials_annual``.
    book: Dict[int, Dict[str, Optional[float]]] = field(default_factory=dict)


def _na(indicator: str, reason: str, assumptions: Optional[Dict[str, Any]] = None,
        basis_fy: Optional[int] = None) -> IndicatorResult:
    return IndicatorResult(indicator=indicator, applicable=False, na_reason=reason,
                           unit=_UNITS.get(indicator, "per_share"), assumptions=dict(assumptions or {}),
                           basis_fiscal_year=basis_fy)


def _sector_name(sector: str) -> str:
    return _SECTOR_NAMES.get(sector, f"sector '{sector}'")


def implied_growth(fcf0: float, shares: float, price: float, discount: float, terminal_growth: float,
                   low: float = REVERSE_DCF_LOW, high: float = REVERSE_DCF_HIGH
                   ) -> Tuple[Optional[float], Optional[str]]:
    """The growth of years 1-5 at which ``models.dcf_per_share`` gives *price*, or (None, why not).

    The DCF's value rises with growth for a positive FCF, so a bisection over [*low*, *high*] finds it: the bracket's
    low end stays below the price and its high end at or above it, until the bracket is narrower than
    REVERSE_DCF_TOLERANCE. A price below the value at *low*, or above the value at *high*, has no growth in the range.
    Public: the VAL tab solves it again at the live price from the DCF's stored assumptions.
    """
    if dcf_per_share(fcf0, shares, low, discount, terminal_growth) > price:
        return None, f"the price is below the DCF's value at {low:.0%} growth a year"
    if dcf_per_share(fcf0, shares, high, discount, terminal_growth) < price:
        return None, f"the price is above the DCF's value at {high:.0%} growth a year"
    lo, hi = low, high
    for _ in range(REVERSE_DCF_MAX_STEPS):
        if hi - lo < REVERSE_DCF_TOLERANCE:
            break
        mid = (lo + hi) / 2.0
        if dcf_per_share(fcf0, shares, mid, discount, terminal_growth) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0, None


def reverse_dcf(inputs: IndicatorInputs) -> IndicatorResult:
    """The growth the snapshot's price implies: ``implied_growth`` on the DCF's own FCF basis, share count, base
    discount and terminal growth, shown beside the DCF's own growth (``dcf_growth_base``)."""
    name = "reverse_dcf"
    if inputs.valuation.ads_unreadable:
        return _na(name, inputs.valuation.ads_unreadable)
    dcf = value_dcf(inputs.valuation)
    if not dcf.applicable:
        return _na(name, f"no DCF: {dcf.na_reason}", basis_fy=dcf.basis_fiscal_year)
    price = inputs.market.price
    if price is None or price <= 0:
        return _na(name, "no price", basis_fy=dcf.basis_fiscal_year)
    a = dcf.assumptions
    assumptions: Dict[str, Any] = {
        "price": price,
        "price_date": inputs.price_date,
        "fcf_basis": a["fcf_basis"],
        "shares_outstanding": a["shares_outstanding"],
        "discount_base": a["discount_base"],
        "terminal_growth": a["terminal_growth"],
        "dcf_growth_base": a["growth_base"],
        "dcf_value_base": dcf.value_base,
        "search_low": REVERSE_DCF_LOW,
        "search_high": REVERSE_DCF_HIGH,
    }
    growth, reason = implied_growth(a["fcf_basis"], a["shares_outstanding"], price, a["discount_base"],
                                    a["terminal_growth"])
    if growth is None:
        return _na(name, reason or "no growth reaches the price", assumptions, dcf.basis_fiscal_year)
    return IndicatorResult(indicator=name, applicable=True, value_base=growth, unit="rate", assumptions=assumptions,
                           basis_fiscal_year=dcf.basis_fiscal_year)


def _tax_rate(cur: AnnualRow) -> Tuple[float, str]:
    """The effective tax rate as M2's WACC takes it (``scores.wacc``): income tax over pretax income, clamped to
    [0, TAX_CAP]; DEFAULT_TAX when pretax income is missing or not positive, or there is no tax figure."""
    tax, pretax = cur.figures.get("income_tax_expense"), cur.figures.get("pretax_income")
    if tax is None or pretax is None or pretax <= 0:
        return DEFAULT_TAX, "default"
    return min(max(tax / pretax, 0.0), TAX_CAP), "effective"


def _epv_per_share(adjusted: float, rate: float, debt: float, cash: float, shares: float) -> Optional[float]:
    """The equity's earnings power per share at *rate*: adjusted earnings / rate - debt + cash, over the count."""
    if rate <= 0:
        return None
    return (adjusted / rate - debt + cash) / shares


def epv(inputs: IndicatorInputs) -> IndicatorResult:
    """Greenwald's earnings power value: the firm's no-growth value, its normalized after-tax operating earnings
    over the WACC, less the debt and plus the cash and short-term investments, per share over today's count.

    Normalized EBIT is the mean operating margin of the last EPV_YEARS fiscal years times the latest revenue; a year
    without operating income takes pretax income plus interest expense. Maintenance capex is taken as equal to D&A,
    so neither is added back. A value below zero (debt above the earnings power) is stored as it is.
    """
    name = "epv"
    sector = inputs.fundamentals.sector_class
    if sector not in EPV_SECTORS:
        return _na(name, f"not meaningful for {_sector_name(sector)}")
    if inputs.valuation.ads_unreadable:
        return _na(name, inputs.valuation.ads_unreadable)
    rows = inputs.fundamentals.rows
    if not rows:
        return _na(name, "no annual financials")
    cur = rows[-1]
    basis = cur.fiscal_year
    if cur.debt_unknown:
        return _na(name, DEBT_UNKNOWN, basis_fy=basis)
    margins: List[Tuple[int, float]] = []
    pretax_years: List[int] = []
    no_interest_years: List[int] = []
    for row in rows[-EPV_YEARS:]:
        revenue, income = row.figures.get("revenue"), row.figures.get("operating_income")
        pretax, interest = row.figures.get("pretax_income"), row.figures.get("interest_expense")
        from_pretax = False
        if income is None and pretax is not None:
            # A filer stating no operating income (XOM, JNJ, LLY and ten more): EBIT is pretax income plus interest
            # expense, GuruFocus's EBIT, an untagged interest counting as 0 as metrics_annual's EBITDA takes it.
            income, from_pretax = pretax + (interest or 0.0), True
        if revenue is not None and revenue > 0 and income is not None:
            margins.append((row.fiscal_year, income / revenue))
            if from_pretax:
                pretax_years.append(row.fiscal_year)
                if interest is None:
                    no_interest_years.append(row.fiscal_year)
    if len(margins) < EPV_MIN_YEARS:
        return _na(name, f"operating income (or pretax income) and revenue in fewer than {EPV_MIN_YEARS} of the "
                         f"last {EPV_YEARS} fiscal years", basis_fy=basis)
    mean_margin = statistics.mean(m for _, m in margins)
    assumptions: Dict[str, Any] = {"margin_years": [y for y, _ in margins], "mean_operating_margin": mean_margin,
                                   "pretax_years": pretax_years, "no_interest_years": no_interest_years}
    latest_revenue = cur.figures.get("revenue")
    if latest_revenue is None or latest_revenue <= 0:
        return _na(name, "no revenue in the latest fiscal year", assumptions, basis)
    ebit = mean_margin * latest_revenue
    tax, tax_source = _tax_rate(cur)
    adjusted = ebit * (1.0 - tax)
    assumptions.update({"latest_revenue": latest_revenue, "normalized_ebit": ebit, "tax_rate": tax,
                        "tax_source": tax_source, "adjusted_earnings": adjusted})
    if adjusted <= 0:
        return _na(name, "normalized after-tax operating earnings are not positive", assumptions, basis)
    rate = wacc(cur, inputs.market)
    if rate is None:
        return _na(name, "no WACC: no market cap", assumptions, basis)
    debt = _total_debt(cur)
    cash = (cur.figures.get("cash_and_equivalents") or 0.0) + (cur.figures.get("short_term_investments") or 0.0)
    shares = inputs.valuation.shares_outstanding
    assumptions.update({"wacc": rate, "total_debt": debt if debt is not None else 0.0, "debt_missing": debt is None,
                        "cash_and_sti": cash, "shares_outstanding": shares,
                        "maintenance_capex": "taken as equal to D&A"})
    if not shares or shares <= 0:
        return _na(name, "no share count", assumptions, basis)
    owed = debt or 0.0
    return IndicatorResult(
        indicator=name,
        applicable=True,
        value_bear=_epv_per_share(adjusted, rate + EPV_WACC_SPREAD, owed, cash, shares),
        value_base=_epv_per_share(adjusted, rate, owed, cash, shares),
        value_bull=_epv_per_share(adjusted, rate - EPV_WACC_SPREAD, owed, cash, shares),
        assumptions=assumptions,
        basis_fiscal_year=basis,
    )


#: Graham's net-net working capital: each part and its weight, less total liabilities.
_NNWC_PARTS: Tuple[Tuple[str, float], ...] = (
    ("cash_and_equivalents", 1.0), ("short_term_investments", 1.0),
    ("accounts_receivable", NNWC_RECEIVABLES), ("inventory", NNWC_INVENTORY),
)


def ncav(inputs: IndicatorInputs) -> IndicatorResult:
    """NCAV per share (M2's: current assets less total liabilities, over the latest fiscal year's count) as the
    base; as the bear, Graham's stricter net-net working capital per share when it is positive. Graham's buy line,
    NETNET_BUY of NCAV, is in the assumptions. A missing net-net part counts as 0 and is listed."""
    name = "ncav"
    sector = inputs.fundamentals.sector_class
    if sector in _NO_CURRENT_ASSETS:
        return _na(name, f"not meaningful for {_sector_name(sector)} (no current assets)")
    if inputs.valuation.ads_unreadable:
        return _na(name, inputs.valuation.ads_unreadable)
    rows = inputs.fundamentals.rows
    if not rows:
        return _na(name, "no annual financials")
    cur = rows[-1]
    current_assets, liabilities = cur.figures.get("current_assets"), cur.figures.get("total_liabilities")
    if current_assets is None or liabilities is None:
        return _na(name, "no current assets or total liabilities", basis_fy=cur.fiscal_year)
    shares = cur.shares
    if not shares or shares <= 0:
        return _na(name, "no share count", basis_fy=cur.fiscal_year)
    ncav_ps = (current_assets - liabilities) / shares
    parts = {part: cur.figures.get(part) for part, _ in _NNWC_PARTS}
    nnwc_ps = (sum(weight * (parts[part] or 0.0) for part, weight in _NNWC_PARTS) - liabilities) / shares
    assumptions: Dict[str, Any] = {
        "ncav_per_share": ncav_ps,
        "nnwc_per_share": nnwc_ps,
        "current_assets": current_assets,
        "total_liabilities": liabilities,
        "shares": shares,
        **parts,
        "receivables_weight": NNWC_RECEIVABLES,
        "inventory_weight": NNWC_INVENTORY,
        "nnwc_missing": [part for part, value in parts.items() if value is None],
    }
    if ncav_ps <= 0:
        return _na(name, "net current assets are negative: total liabilities exceed current assets", assumptions,
                   cur.fiscal_year)
    assumptions["netnet_buy_price"] = NETNET_BUY * ncav_ps
    return IndicatorResult(indicator=name, applicable=True, value_bear=nnwc_ps if nnwc_ps > 0 else None,
                           value_base=ncav_ps, assumptions=assumptions, basis_fiscal_year=cur.fiscal_year)


def ps_value(inputs: IndicatorInputs) -> IndicatorResult:
    """The median P/S value: the valuation history's ``implied_value`` for P/S as the base, the bear and bull scaled
    by the range's p25 and p75 (P/S is linear in its multiple)."""
    name = "ps_value"
    ps = inputs.ps_range
    if ps is None:
        return _na(name, "no valuation history (run the backfill)")
    assumptions: Dict[str, Any] = {"median_ps": ps.median, "p25_ps": ps.p25, "p75_ps": ps.p75,
                                   "current_ps": ps.current, "as_of": ps.as_of, "n_months": ps.n_months}
    if not ps.applicable:
        return _na(name, f"no P/S range: {ps.na_reason or 'not applicable'}", assumptions)
    if ps.implied_value is None:
        return _na(name, f"no current P/S value: {ps.na_reason or 'no trailing revenue'}", assumptions)
    base, median = ps.implied_value, ps.median

    def scaled(quartile: Optional[float]) -> Optional[float]:
        if quartile is None or median is None or median <= 0:
            return None
        return base * quartile / median

    return IndicatorResult(indicator=name, applicable=True, value_bear=scaled(ps.p25), value_base=base,
                           value_bull=scaled(ps.p75), assumptions=assumptions)


def graham_tangible(inputs: IndicatorInputs) -> IndicatorResult:
    """The Graham number on tangible book: sqrt(GRAHAM_MULTIPLIER x EPS x tangible book per share), tangible book
    being total equity less goodwill and intangible assets, on the Graham model's EPS and share basis, with its
    bear and bull EPS (the lowest and highest positive EPS of the last 3 fiscal years). A missing goodwill or
    intangibles figure counts as 0 and is listed."""
    name = "graham_tangible"
    if inputs.valuation.ads_unreadable:
        return _na(name, inputs.valuation.ads_unreadable)
    graham = value_graham(inputs.valuation)
    basis = graham.basis_fiscal_year
    if not graham.applicable:
        return _na(name, graham.na_reason or "no Graham number", basis_fy=basis)
    figures = (inputs.book.get(basis) if basis is not None else None) or {}
    equity = figures.get("total_equity")
    if equity is None or equity <= 0:
        return _na(name, "no total equity", basis_fy=basis)
    goodwill, intangibles = figures.get("goodwill"), figures.get("intangible_assets")
    missing = [k for k, value in (("goodwill", goodwill), ("intangible_assets", intangibles)) if value is None]
    goodwill, intangibles = goodwill or 0.0, intangibles or 0.0
    a = graham.assumptions
    tangible_bvps = a["bvps"] * (equity - goodwill - intangibles) / equity
    assumptions: Dict[str, Any] = {
        "eps_base": a["eps_base"], "eps_bear": a["eps_bear"], "eps_bull": a["eps_bull"],
        "bvps": a["bvps"], "tangible_bvps": tangible_bvps,
        "total_equity": equity, "goodwill": goodwill, "intangible_assets": intangibles,
        "multiplier": GRAHAM_MULTIPLIER, "missing": missing,
    }
    if tangible_bvps <= 0:
        return _na(name, "tangible book is negative: goodwill and intangibles exceed equity", assumptions, basis)

    def number(eps: float) -> float:
        return math.sqrt(GRAHAM_MULTIPLIER * eps * tangible_bvps)

    return IndicatorResult(indicator=name, applicable=True, value_bear=number(a["eps_bear"]),
                           value_base=number(a["eps_base"]), value_bull=number(a["eps_bull"]),
                           assumptions=assumptions, basis_fiscal_year=basis)


def compute_indicators(inputs: IndicatorInputs, logger: Optional[logging.Logger] = None) -> List[IndicatorResult]:
    """All five indicators for one ticker, in INDICATORS order. One that raises becomes an N/A row
    (``internal error: ...``) and the others still run, as ``engine.run_valuations`` does for the models."""
    log = logger or logging.getLogger(__name__)
    # Read from the module on each call, so each indicator can be replaced on its own (a test breaks one).
    functions: Dict[str, Callable[[IndicatorInputs], IndicatorResult]] = {
        "reverse_dcf": reverse_dcf, "epv": epv, "ncav": ncav, "ps_value": ps_value,
        "graham_tangible": graham_tangible,
    }
    results: List[IndicatorResult] = []
    for name in INDICATORS:
        try:
            results.append(functions[name](inputs))
        except Exception as e:  # never abort a ticker's indicators for one of them
            log.warning(f"{inputs.ticker}: {name} indicator failed: {e}")
            results.append(_na(name, f"internal error: {e}"))
    return results


def load_indicator_inputs(conn: sqlite3.Connection, ticker: str) -> IndicatorInputs:
    """Read what the indicators need for *ticker*: the six models' own inputs, M2's annual rows, the latest snapshot
    with a price (a run whose quote failed stores one without price, market cap or beta: BUG-187), the valuation
    history's P/S range (None without it, or before the table) and each fiscal year's book figures.

    ``conn.row_factory`` must be ``sqlite3.Row``.
    """
    snap = conn.execute(
        "SELECT market_cap, current_price, risk_free_rate, beta, collected_at FROM market_snapshots "
        "WHERE ticker = ? AND current_price IS NOT NULL ORDER BY collected_at DESC LIMIT 1", (ticker,)).fetchone()
    market = Market() if snap is None else Market(market_cap=snap["market_cap"], price=snap["current_price"],
                                                  risk_free_rate=snap["risk_free_rate"], beta=snap["beta"])
    try:
        ps = conn.execute(
            "SELECT applicable, na_reason, as_of, n_months, current_value, p25, median, p75, implied_value "
            "FROM valuation_ranges WHERE ticker = ? AND metric = 'ps'", (ticker,)).fetchone()
    except sqlite3.OperationalError:  # a database from before the valuation history
        ps = None
    book: Dict[int, Dict[str, Optional[float]]] = {
        int(r["fiscal_year"]): {"total_equity": r["total_equity"], "goodwill": r["goodwill"],
                                "intangible_assets": r["intangible_assets"]}
        for r in conn.execute("SELECT fiscal_year, total_equity, goodwill, intangible_assets FROM financials_annual "
                              "WHERE ticker = ? AND fiscal_year IS NOT NULL", (ticker,))
    }
    return IndicatorInputs(
        ticker=ticker,
        valuation=load_inputs(conn, ticker),
        fundamentals=load_fundamentals(conn, ticker),
        market=market,
        price_date=str(snap["collected_at"]) if snap is not None else None,
        ps_range=None if ps is None else PsRange(
            applicable=bool(ps["applicable"]), na_reason=ps["na_reason"], as_of=ps["as_of"],
            n_months=ps["n_months"], current=ps["current_value"], p25=ps["p25"], median=ps["median"],
            p75=ps["p75"], implied_value=ps["implied_value"]),
        book=book,
    )

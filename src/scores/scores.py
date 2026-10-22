"""The quality scores: Piotroski F, Altman Z, Beneish M, the Sloan ratio, ROIC vs WACC, predictability stars,
and the inputs of the Graham net-net and Magic Formula screens.

Each is computed on the latest fiscal year (and the one before it, for changes) from ``fundamentals``,
with its formula published in docs/superpowers/specs/2026-10-21-quality-scores-design.md.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..parsers.sector_metrics import SUPPRESSED_BY_SECTOR
from ..valuation.assumptions import derive_discount
from .fundamentals import AnnualRow, Fundamentals

ALTMAN_DISTRESS = 1.81
ALTMAN_SAFE = 2.99
BENEISH_FLAG = -1.78
#: Rd is the interest expense over total debt, clamped to [rf, rf + this].
DEBT_COST_SPREAD_CAP = 0.10
#: The effective tax rate is clamped to [0, this]; DEFAULT_TAX when it can't be read.
TAX_CAP = 0.35
DEFAULT_TAX = 0.21
#: Shares rising by no more than this aren't "new shares" (rounding in the filings).
SHARE_TOLERANCE = 0.005
PREDICTABILITY_YEARS = 10

#: The sectors a score doesn't apply to (the sector rules suppress or bend its inputs).
_FINANCIALS = ("bank", "insurance")
_NOT_SCORED = {
    "piotroski": _FINANCIALS,
    # The original Altman Z was fitted on manufacturers; a regulated utility's asset-heavy balance sheet reads as
    # distress. Z'' for non-manufacturers needs the SIC code (M5).
    "altman": _FINANCIALS + ("reit", "utility"),
    "beneish": _FINANCIALS + ("reit",),
    "wacc": _FINANCIALS,
}
_SECTOR_NAMES = {"bank": "banks", "insurance": "insurers", "reit": "REITs", "utility": "utilities"}

PIOTROSKI_TESTS = ("roa_positive", "cfo_positive", "roa_up", "cfo_above_ni", "leverage_down",
                   "current_ratio_up", "no_new_shares", "gross_margin_up", "asset_turnover_up")
BENEISH_WEIGHTS = {"dsri": 0.920, "gmi": 0.528, "aqi": 0.404, "sgi": 0.892, "depi": 0.115, "sgai": -0.172,
                   "tata": 4.679, "lvgi": -0.327}
BENEISH_INTERCEPT = -4.84


@dataclass
class Market:
    """The latest market snapshot's figures the scores need."""

    market_cap: Optional[float] = None
    price: Optional[float] = None
    risk_free_rate: Optional[float] = None
    beta: Optional[float] = None


@dataclass
class Scores:
    """One company's scores on its latest fiscal year; ``na_reasons`` says why a score is missing."""

    fiscal_year: Optional[int] = None
    piotroski_f: Optional[int] = None
    piotroski_tested: Optional[int] = None
    piotroski_tests: Dict[str, Optional[bool]] = field(default_factory=dict)
    altman_z: Optional[float] = None
    altman_zone: Optional[str] = None
    beneish_m: Optional[float] = None
    beneish_variables: Dict[str, Optional[float]] = field(default_factory=dict)
    sloan_ratio: Optional[float] = None
    roic: Optional[float] = None
    wacc: Optional[float] = None
    roic_minus_wacc: Optional[float] = None
    predictability_stars: Optional[float] = None
    predictability_r2: Dict[str, float] = field(default_factory=dict)
    ncav_per_share: Optional[float] = None
    earnings_yield: Optional[float] = None
    return_on_capital: Optional[float] = None
    debt_to_ebitda: Optional[float] = None
    na_reasons: Dict[str, str] = field(default_factory=dict)


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _gross_margin(row: AnnualRow) -> Optional[float]:
    gp, revenue, cogs = (row.figures.get(k) for k in ("gross_profit", "revenue", "cost_of_revenue"))
    if gp is None and revenue is not None and cogs is not None:
        gp = revenue - cogs
    return _div(gp, revenue)


#: Why a figure that needs the debt is N/A in a year that paid interest on debt no mapped tag holds (BUG-189).
DEBT_UNKNOWN = "debt unknown: interest paid on debt no mapped tag holds"


def _total_debt(row: AnnualRow) -> Optional[float]:
    if row.debt_unknown:
        return None
    if row.figures.get("total_debt") is not None:
        return row.figures["total_debt"]
    parts = [row.figures.get(k) for k in ("long_term_debt", "short_term_debt", "commercial_paper")]
    return sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None


def _roic_reason(row: AnnualRow, sector: str) -> str:
    """Why the year has no ROIC (BUG-190): ``metrics_annual``'s NOPAT over equity + debt - cash, which its sector
    rules suppress for banks, insurers and REITs."""
    if "roic" in SUPPRESSED_BY_SECTOR.get(sector, frozenset()):
        return f"no ROIC: not meaningful for {_SECTOR_NAMES[sector]}"
    if row.debt_unknown:
        return "no ROIC: " + DEBT_UNKNOWN
    if row.figures.get("operating_income") is None:
        return "no ROIC: no operating income"
    if row.figures.get("total_equity") is None or _total_debt(row) is None:
        return "no ROIC: no equity or debt figure"
    return "no ROIC: invested capital (equity + debt - cash) of 0 or less"


def _higher(a: Optional[float], b: Optional[float]) -> Optional[bool]:
    return None if a is None or b is None else a > b


def piotroski(cur: AnnualRow, prev: AnnualRow) -> Tuple[int, int, Dict[str, Optional[bool]]]:
    """(F, tests with inputs, each test's pass/fail or None) on *cur* against *prev*."""
    c, p = cur.figures, prev.figures
    roa = _div(c.get("net_income"), c.get("total_assets"))
    roa_prev = _div(p.get("net_income"), p.get("total_assets"))
    cfo, ni = c.get("operating_cash_flow"), c.get("net_income")
    shares, shares_prev = cur.shares, prev.shares
    tests: Dict[str, Optional[bool]] = {
        "roa_positive": None if roa is None else roa > 0,
        "cfo_positive": None if cfo is None else cfo > 0,
        "roa_up": _higher(roa, roa_prev),
        "cfo_above_ni": _higher(cfo, ni),
        "leverage_down": _higher(_div(p.get("long_term_debt"), p.get("total_assets")),
                                 _div(c.get("long_term_debt"), c.get("total_assets"))),
        "current_ratio_up": _higher(_div(c.get("current_assets"), c.get("current_liabilities")),
                                    _div(p.get("current_assets"), p.get("current_liabilities"))),
        "no_new_shares": (None if not shares or not shares_prev
                          else shares <= shares_prev * (1 + SHARE_TOLERANCE)),
        "gross_margin_up": _higher(_gross_margin(cur), _gross_margin(prev)),
        "asset_turnover_up": _higher(_div(c.get("revenue"), c.get("total_assets")),
                                     _div(p.get("revenue"), p.get("total_assets"))),
    }
    tested = sum(1 for v in tests.values() if v is not None)
    return sum(1 for v in tests.values() if v), tested, tests


def altman(cur: AnnualRow, market_cap: Optional[float]) -> Tuple[Optional[float], Optional[str]]:
    """The original Altman Z and its zone (distress, grey, safe), or (None, None) when an input is missing."""
    ta, tl, ca, cl, retained, ebit, sales = (cur.figures.get(k) for k in (
        "total_assets", "total_liabilities", "current_assets", "current_liabilities", "retained_earnings",
        "operating_income", "revenue"))
    if (ta is None or tl is None or ca is None or cl is None or retained is None or ebit is None
            or sales is None or market_cap is None or ta == 0 or tl == 0):
        return None, None
    z = 1.2 * (ca - cl) / ta + 1.4 * retained / ta + 3.3 * ebit / ta + 0.6 * market_cap / tl + 1.0 * sales / ta
    zone = "distress" if z < ALTMAN_DISTRESS else "grey" if z <= ALTMAN_SAFE else "safe"
    return z, zone


def beneish(cur: AnnualRow, prev: AnnualRow) -> Tuple[Optional[float], Dict[str, Optional[float]]]:
    """Beneish's M and its eight variables. SGAI and DEPI are 1 when their inputs are missing; any other gap
    leaves M None."""
    c, p = cur.figures, prev.figures

    def ratio_change(num: str, den: str) -> Optional[float]:
        return _div(_div(c.get(num), c.get(den)), _div(p.get(num), p.get(den)))

    def asset_quality(f: Dict[str, Optional[float]]) -> Optional[float]:
        ca, ppe = f.get("current_assets"), f.get("ppe_net")
        share = None if ca is None or ppe is None else _div(ca + ppe, f.get("total_assets"))
        return None if share is None else 1.0 - share

    def depreciation_rate(f: Dict[str, Optional[float]]) -> Optional[float]:
        dep, ppe = f.get("depreciation_amortization"), f.get("ppe_net")
        return None if dep is None or ppe is None else _div(dep, dep + ppe)

    def leverage(f: Dict[str, Optional[float]]) -> Optional[float]:
        cl = f.get("current_liabilities")
        return None if cl is None else _div(cl + (f.get("long_term_debt") or 0.0), f.get("total_assets"))

    gm, gm_prev = _gross_margin(cur), _gross_margin(prev)
    depi = _div(depreciation_rate(p), depreciation_rate(c))
    sgai = ratio_change("sga_expense", "revenue")
    ni, cfo = c.get("net_income"), c.get("operating_cash_flow")
    tata = None if ni is None or cfo is None else _div(ni - cfo, c.get("total_assets"))
    variables: Dict[str, Optional[float]] = {
        "dsri": ratio_change("accounts_receivable", "revenue"),
        "gmi": _div(gm_prev, gm),
        "aqi": _div(asset_quality(c), asset_quality(p)),
        "sgi": _div(c.get("revenue"), p.get("revenue")),
        "depi": 1.0 if depi is None else depi,
        "sgai": 1.0 if sgai is None else sgai,
        "lvgi": _div(leverage(c), leverage(p)),
        "tata": tata,
    }
    m = BENEISH_INTERCEPT
    for name, value in variables.items():
        if value is None:
            return None, variables
        m += BENEISH_WEIGHTS[name] * value
    return m, variables


def sloan(cur: AnnualRow) -> Optional[float]:
    """(net income - operating cash flow - investing cash flow) / total assets."""
    ni, cfo, cfi = (cur.figures.get(k) for k in ("net_income", "operating_cash_flow", "investing_cash_flow"))
    if ni is None or cfo is None or cfi is None:
        return None
    return _div(ni - cfo - cfi, cur.figures.get("total_assets"))


def wacc(cur: AnnualRow, market: Market) -> Optional[float]:
    """E/(D+E)·Re + D/(D+E)·Rd·(1 - t): Re the DCF's CAPM cost of equity, Rd the interest expense over the
    debt (clamped to [rf, rf + 10%]), t the effective tax rate (clamped to [0, 35%], else 21%)."""
    equity = market.market_cap
    if not equity or equity <= 0:
        return None
    debt = _total_debt(cur) or 0.0
    re, meta = derive_discount(market.risk_free_rate, market.beta)
    rf = meta["risk_free_rate"]
    c = cur.figures
    rd = _div(c.get("interest_expense"), debt) if debt > 0 else None
    rd = rf if rd is None else min(max(abs(rd), rf), rf + DEBT_COST_SPREAD_CAP)
    pretax = c.get("pretax_income")
    tax = _div(c.get("income_tax_expense"), pretax)
    tax = DEFAULT_TAX if tax is None or pretax is None or pretax <= 0 else min(max(tax, 0.0), TAX_CAP)
    total = equity + debt
    return float(equity / total * re + debt / total * rd * (1 - tax))


def _trend(points: List[Tuple[int, float]]) -> Tuple[float, float]:
    """(slope, R²) of ln(value) against the year."""
    xs = [float(y) for y, _ in points]
    ys = [math.log(v) for _, v in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx if sxx else 0.0
    r2 = 1.0 if syy == 0 else (sxy * sxy) / (sxx * syy) if sxx else 0.0
    return slope, r2


def predictability(f: Fundamentals) -> Tuple[Optional[float], Dict[str, float]]:
    """1-5 stars in half steps from the last 10 fiscal years: 1 on any loss year or a falling trend; else
    1 + 4·min(R² of the revenue/share and EBITDA/share log-trends), rounded down to the half star. Banks and
    insurers use EPS for EBITDA/share. (None, {}) with fewer than 10 years."""
    second = "eps" if f.sector_class in _FINANCIALS else "ebitda_ps"
    series: Dict[str, List[Tuple[int, float]]] = {}
    for name, key in (("revenue", "revenue_ps"), ("eps" if second == "eps" else "ebitda", second)):
        raw = f.series(key)[-PREDICTABILITY_YEARS:]
        points = [(y, v) for y, v in raw if v is not None]
        if len(raw) < PREDICTABILITY_YEARS or len(points) < len(raw):
            return None, {}
        series[name] = points
    if any(v <= 0 for points in series.values() for _, v in points):
        return 1.0, {}
    r2: Dict[str, float] = {}
    falling = False
    for name, points in series.items():
        slope, r2[name] = _trend(points)
        falling = falling or slope <= 0
    if falling:
        return 1.0, r2
    return stars_for(min(r2.values())), r2


def stars_for(r2: float) -> float:
    """1 + 4·R², rounded down to the half star: a rating is earned, never rounded up to."""
    return math.floor((1.0 + 4.0 * r2) * 2) / 2


def compute_scores(f: Fundamentals, market: Market) -> Scores:
    """Every score on the latest fiscal year, with a reason for each one that can't be computed."""
    s = Scores()
    if not f.rows:
        s.na_reasons = {k: "no annual financials" for k in ("piotroski", "altman", "beneish", "sloan", "wacc",
                                                              "predictability")}
        return s
    cur = f.rows[-1]
    prev = f.rows[-2] if len(f.rows) > 1 else None
    s.fiscal_year = cur.fiscal_year
    sector = f.sector_class

    def skipped(score: str) -> bool:
        if sector in _NOT_SCORED.get(score, ()):
            s.na_reasons[score] = f"not meaningful for {_SECTOR_NAMES[sector]}"
            return True
        return False

    if not skipped("piotroski"):
        if prev is None:
            s.na_reasons["piotroski"] = "needs two fiscal years"
        else:
            s.piotroski_f, s.piotroski_tested, s.piotroski_tests = piotroski(cur, prev)
    if not skipped("altman"):
        s.altman_z, s.altman_zone = altman(cur, market.market_cap)
        if s.altman_z is None:
            s.na_reasons["altman"] = "an input is missing"
    if not skipped("beneish"):
        if prev is None:
            s.na_reasons["beneish"] = "needs two fiscal years"
        else:
            s.beneish_m, s.beneish_variables = beneish(cur, prev)
            if s.beneish_m is None:
                missing = [k for k, v in s.beneish_variables.items() if v is None]
                s.na_reasons["beneish"] = "missing " + ", ".join(missing)
    s.sloan_ratio = sloan(cur)
    if s.sloan_ratio is None:
        s.na_reasons["sloan"] = "an input is missing"
    s.roic = cur.figures.get("roic")
    if s.roic is None:
        s.na_reasons["roic"] = _roic_reason(cur, sector)
    if not skipped("wacc"):
        if cur.debt_unknown:
            s.na_reasons["wacc"] = DEBT_UNKNOWN
        else:
            s.wacc = wacc(cur, market)
            if s.wacc is None:
                s.na_reasons["wacc"] = "no market cap"
            elif s.roic is not None:
                s.roic_minus_wacc = s.roic - s.wacc
    s.predictability_stars, s.predictability_r2 = predictability(f)
    if s.predictability_stars is None:
        s.na_reasons["predictability"] = f"fewer than {PREDICTABILITY_YEARS} fiscal years of data"
    c = cur.figures
    ca, cl, tl, ppe = (c.get(k) for k in ("current_assets", "current_liabilities", "total_liabilities", "ppe_net"))
    if ca is not None and tl is not None and cur.shares:
        s.ncav_per_share = (ca - tl) / cur.shares
    ebit = c.get("ebit") if c.get("ebit") is not None else c.get("operating_income")
    debt, ebitda = _total_debt(cur), c.get("ebitda")
    s.debt_to_ebitda = debt / ebitda if debt is not None and ebitda is not None and ebitda > 0 else None
    if ebit is not None and market.market_cap and sector not in _FINANCIALS:
        if cur.debt_unknown:
            s.na_reasons["magic_formula"] = DEBT_UNKNOWN
        else:
            ev = market.market_cap + (c.get("net_debt") or 0.0)
            s.earnings_yield = ebit / ev if ev > 0 else None
        if ca is not None and cl is not None and ppe is not None:
            # Greenblatt's capital: net working capital without cash or short-term debt, plus net PPE.
            nwc = (ca - (c.get("cash_and_equivalents") or 0.0)) - (cl - (c.get("short_term_debt") or 0.0))
            capital = nwc + ppe
            s.return_on_capital = ebit / capital if capital > 0 else None
    return s

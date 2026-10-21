"""The warning signs: twenty published rules over a company's scores, its last five years and its valuation
history, each with a severity, the figure that fired it and its threshold.

docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .fundamentals import Fundamentals, cagr
from .scores import ALTMAN_DISTRESS, BENEISH_FLAG, Scores

SEVERE, MEDIUM = "severe", "medium"
SIGN_CODES: Tuple[str, ...] = (
    "altman_distress", "beneish_flag", "sloan_high", "piotroski_low", "interest_coverage_low", "revenue_decline",
    "margin_decline", "fcf_negative", "dilution", "debt_outgrows_revenue", "receivables_outgrow_revenue",
    "inventory_outgrows_revenue", "assets_outgrow_revenue", "goodwill_heavy", "negative_equity", "payout_over_fcf",
    "roic_below_wacc", "price_far_above_value", "pe_at_high", "gross_margin_decline",
)
SLOAN_SEVERE, SLOAN_MEDIUM = 0.25, 0.10
PIOTROSKI_LOW, PIOTROSKI_MIN_TESTED = 3, 7
COVERAGE_SEVERE, COVERAGE_MEDIUM = 1.5, 3.0
MARGIN_DROP = 0.05  # five points over five years, operating or gross
DILUTION = 0.03  # a year, over three years
OUTGROWTH = 0.10  # ten points of CAGR over three years
TURN_RISE = 1.20  # receivables or inventory per dollar of revenue, up a fifth over three years
GOODWILL_SHARE = 0.50
PRICE_TO_VALUE_HIGH = 1.3  # the valuation history's "far above"
PE_PCT_HIGH = 0.95


@dataclass
class HistoryContext:
    """What the valuation history (Milestone 1) says now: price over the historical value, and the P/E's
    percentile in its 10 years."""

    price_to_value: Optional[float] = None
    pe_pct_rank: Optional[float] = None


@dataclass
class Sign:
    """A warning sign that fired: its code, severity, the message stating the figure, the figure, the line."""

    code: str
    severity: str
    message: str
    value: Optional[float]
    threshold: Optional[float]


def _ratio(f: Fundamentals, num: str, den: str, back: int) -> Tuple[Optional[float], Optional[float]]:
    """num/den now and *back* years before, or Nones."""
    rows = f.rows
    if len(rows) <= back:
        return None, None

    def at(i: int) -> Optional[float]:
        n, d = rows[i].figures.get(num), rows[i].figures.get(den)
        return None if n is None or d is None or d == 0 else n / d

    return at(-1), at(-1 - back)


def _gross_margin_change(f: Fundamentals, years: int) -> Optional[float]:
    if len(f.rows) <= years:
        return None
    now, then = f.rows[-1], f.rows[-1 - years]

    def margin(row_figures: Dict[str, Any]) -> Optional[float]:
        gp, revenue = row_figures.get("gross_profit"), row_figures.get("revenue")
        return None if gp is None or not revenue else gp / revenue

    a, b = margin(now.figures), margin(then.figures)
    return None if a is None or b is None else a - b


def warning_signs(f: Fundamentals, s: Scores, history: HistoryContext) -> List[Sign]:
    """The signs that fire, severe first."""
    out: List[Sign] = []

    def fire(code: str, severity: str, message: str, value: Optional[float], threshold: Optional[float]) -> None:
        out.append(Sign(code, severity, message, value, threshold))

    if s.altman_z is not None and s.altman_z < ALTMAN_DISTRESS:
        fire("altman_distress", SEVERE, f"Altman Z of {s.altman_z:.2f} is in the distress zone (below "
                                        f"{ALTMAN_DISTRESS})", s.altman_z, ALTMAN_DISTRESS)
    if s.beneish_m is not None and s.beneish_m > BENEISH_FLAG:
        fire("beneish_flag", SEVERE, f"Beneish M of {s.beneish_m:.2f} is above {BENEISH_FLAG}: the earnings may "
                                     f"be managed", s.beneish_m, BENEISH_FLAG)
    if s.sloan_ratio is not None and abs(s.sloan_ratio) > SLOAN_MEDIUM:
        severe = abs(s.sloan_ratio) > SLOAN_SEVERE
        fire("sloan_high", SEVERE if severe else MEDIUM,
             f"accruals of {s.sloan_ratio:.0%} of assets (Sloan ratio): earnings and cash flows part ways",
             s.sloan_ratio, SLOAN_SEVERE if severe else SLOAN_MEDIUM)
    if (s.piotroski_f is not None and (s.piotroski_tested or 0) >= PIOTROSKI_MIN_TESTED
            and s.piotroski_f <= PIOTROSKI_LOW):
        fire("piotroski_low", MEDIUM, f"Piotroski F of {s.piotroski_f} out of {s.piotroski_tested} tested",
             float(s.piotroski_f), float(PIOTROSKI_LOW))
    rows = f.rows
    latest = rows[-1].figures if rows else {}
    coverage = latest.get("interest_coverage")
    if coverage is not None and coverage < COVERAGE_MEDIUM:
        severe = coverage < COVERAGE_SEVERE
        fire("interest_coverage_low", SEVERE if severe else MEDIUM,
             f"operating income covers interest {coverage:.1f} times", coverage,
             COVERAGE_SEVERE if severe else COVERAGE_MEDIUM)
    revenues = [v for v in (r.figures.get("revenue") for r in rows[-4:]) if v is not None]
    if len(rows) >= 4 and len(revenues) == 4 and all(b < a for a, b in zip(revenues, revenues[1:])):
        fire("revenue_decline", MEDIUM, "revenue fell in each of the last three years", revenues[-1], revenues[0])
    if len(rows) > 5:
        a, b = rows[-1].figures.get("operating_margin"), rows[-6].figures.get("operating_margin")
        if a is not None and b is not None and a - b < -MARGIN_DROP:
            fire("margin_decline", MEDIUM, f"operating margin down {100 * (b - a):.1f} points over five years",
                 a - b, -MARGIN_DROP)
    fcfs = [r.figures.get("free_cash_flow") for r in rows[-3:]]
    if len(fcfs) == 3 and sum(1 for v in fcfs if v is not None and v < 0) >= 2:
        fire("fcf_negative", MEDIUM, "free cash flow was negative in two of the last three years",
             float(sum(1 for v in fcfs if v is not None and v < 0)), 2.0)
    shares = cagr([(r.fiscal_year, r.shares) for r in rows], 3)
    if shares is not None and shares > DILUTION:
        fire("dilution", MEDIUM, f"the share count grew {shares:.1%} a year over three years", shares, DILUTION)
    revenue_growth = cagr(f.series("revenue"), 3)
    for code, field, label in (("debt_outgrows_revenue", "total_debt", "debt"),
                               ("assets_outgrow_revenue", "total_assets", "assets")):
        growth = cagr(f.series(field), 3)
        if growth is not None and revenue_growth is not None and growth - revenue_growth > OUTGROWTH:
            fire(code, MEDIUM, f"{label} grew {growth:.1%} a year over three years, revenue {revenue_growth:.1%}",
                 growth - revenue_growth, OUTGROWTH)
    for code, field, label in (("receivables_outgrow_revenue", "accounts_receivable", "receivables"),
                               ("inventory_outgrows_revenue", "inventory", "inventory")):
        now, then = _ratio(f, field, "revenue", 3)
        if now is not None and then and now / then > TURN_RISE:
            fire(code, MEDIUM, f"{label} per dollar of revenue up {now / then - 1:.0%} over three years",
                 now / then, TURN_RISE)
    goodwill, equity = latest.get("goodwill"), latest.get("total_equity")
    if goodwill is not None and equity is not None and equity > 0 and goodwill > GOODWILL_SHARE * equity:
        fire("goodwill_heavy", MEDIUM, f"goodwill is {goodwill / equity:.0%} of equity", goodwill / equity,
             GOODWILL_SHARE)
    if equity is not None and equity < 0:
        fire("negative_equity", MEDIUM, "equity is negative", equity, 0.0)
    dividends, fcf = latest.get("dividends_paid"), latest.get("free_cash_flow")
    if dividends is not None and dividends > 0 and fcf is not None and dividends > fcf:
        fire("payout_over_fcf", MEDIUM, "dividends paid exceed free cash flow", dividends, fcf)
    if s.roic is not None and s.wacc is not None and s.roic < s.wacc:
        fire("roic_below_wacc", MEDIUM, f"ROIC of {s.roic:.1%} is below the WACC of {s.wacc:.1%}", s.roic, s.wacc)
    if history.price_to_value is not None and history.price_to_value > PRICE_TO_VALUE_HIGH:
        fire("price_far_above_value", MEDIUM, f"the price is {history.price_to_value:.2f}x its historical value",
             history.price_to_value, PRICE_TO_VALUE_HIGH)
    if history.pe_pct_rank is not None and history.pe_pct_rank >= PE_PCT_HIGH:
        fire("pe_at_high", MEDIUM, f"P/E at its {history.pe_pct_rank:.0%} percentile of ten years",
             history.pe_pct_rank, PE_PCT_HIGH)
    change = _gross_margin_change(f, 5)
    if change is not None and change < -MARGIN_DROP:
        fire("gross_margin_decline", MEDIUM, f"gross margin down {-100 * change:.1f} points over five years",
             change, -MARGIN_DROP)
    return sorted(out, key=_order)


def _order(sign: Sign) -> Tuple[int, int]:
    return (0 if sign.severity == SEVERE else 1, SIGN_CODES.index(sign.code))

"""Independent check of Milestone 4's valuation indicators (build task M4-5) against the database's raw tables.

See "Indicators" and "Validation" in docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md. It reads
SQLite only and never imports `src`: Milestone 4's formulas below are re-derived from the spec, so agreeing with the
code under test means something only because the check can't share its bugs.

Scope: Milestone 4's own formulas. The inputs earlier milestones' oracles validated are taken as stored: the DCF
row's fcf_basis, shares_outstanding, discount_base and terminal_growth, and the Graham row's EPS (base, bear, bull),
book value per share and basis year (`valuations`: Milestone 0's valuation oracle); the P/S range
(`valuation_ranges`: Milestone 1's history oracle); and Milestone 2's per-share rows (`per_share_annual`: its
scores oracle). The EPV row's share count is the valuation inputs' (Milestone 0's logic, not Milestone 4's): it is
checked against the DCF row's count where the DCF applies, else against that logic's candidates, and then used.
Everything else comes from the raw tables: financials_annual, metrics_annual, market_snapshots and companies.

Gates (each where both sides apply, and N/A on the same companies: applicability must agree, with the reasons
printed side by side); any that fails makes the exit code 1:
- Reverse DCF: this oracle's own two-stage DCF (years 1-5 at g, years 6-10 fading linearly to the terminal growth,
  a Gordon terminal value at year 10, per share) solved for g by its own root finder (Brent's method, written here)
  at the latest snapshot with a price: the implied growth within 0.05 percentage points; a price out of the -50% to
  +100% range N/A on both sides, on the same side of the range.
- EPV: the mean EBIT margin of the last 5 fiscal years (at least 3) x the latest revenue; x (1 - the latest year's
  effective tax); over the WACC re-derived here; less the debt, plus the cash and short-term investments; over the
  count. The count is the valuation inputs' (gated as above). EBIT, the tax rate, the adjusted earnings, the WACC,
  the debt, the cash and the per-share base, bear (WACC + 1 point) and bull (WACC - 1 point), each within 0.5%, and
  the margin years and the years whose EBIT is pretax income + interest equal (also on the N/A rows that carry
  them); N/A for banks, insurers and REITs.
- NCAV and the net-net: NCAV per share and Graham's net-net working capital per share within 0.5% (also on the N/A
  rows that carry them); the bear only for a positive net-net; N/A for banks and insurers, missing figures, and an
  NCAV that isn't positive.
- Median P/S value: valuation_ranges' P/S implied value as the base, the bear and bull scaled by p25 / median and
  p75 / median, exact to 1e-9 relative.
- Graham number on tangible book: sqrt(22.5 x EPS x bvps x (equity - goodwill - intangibles) / equity) on the
  Graham row's three EPS, within 0.5% (tangible book per share also on the N/A rows that carry it); N/A when the
  Graham number is, or tangible book isn't positive.

Reported, not gated: per indicator the applicable count, the N/A reasons (both sides) and the five largest gaps
between the stored base and the snapshot price (for the reverse DCF, between the implied growth and the DCF's own
growth); self-checks: this oracle's DCF at the DCF row's own growth against the row's base value, the reverse DCF's
inputs (fcf_basis, shares, discount, terminal growth, the DCF's growth) and price against the DCF row's and the
snapshot's, this oracle's WACC against Milestone 2's quality_scores.wacc, and its NCAV and count against Milestone 2's
ncav_per_share and the NCAV row's count.

Readings of the spec where it leaves a choice (each also noted where it's used):
- "The latest snapshot with a price": the market_snapshots row with the latest collected_at whose current_price is
  set; its market cap, beta and risk-free rate are the WACC's.
- The DCF's fade: year 5 + k grows at g + (terminal - g) x k / 5 (k = 1..5, so year 10 grows at the terminal rate),
  and the terminal value is year 10's FCF x (1 + terminal) / (discount - terminal), discounted ten years.
- "The last 5 fiscal years" are the labels fy-4..fy, fy the latest in financials_annual; a year among them with a
  revenue above 0 and an EBIT gives a margin, and at least 3 must. A year's EBIT is its operating income, else its
  pretax income plus its interest expense (an untagged interest as 0); without either it has none.
- The effective tax rate (EPV's and the WACC's) is the latest year's income tax / pretax income, clamped to
  [0, 35%]; 21% when the pretax income is missing or not positive, or no tax is filed.
- The debt (the latest year), as Milestone 2 reads it: known when the long-term debt, short-term debt, commercial
  paper or metrics_annual's total debt is positive, the amount then being metrics_annual's total debt (the three
  tags' sum where it isn't stored); unknown (EPV N/A) when none is positive but interest expense is; 0 when neither
  (banks and insurers keep theirs as filed, but have no EPV).
- The WACC: E/(D+E) x Re + D/(D+E) x Rd x (1 - t), E the snapshot's market cap (none or not positive: N/A),
  Re = rf + beta x 4.5% clamped to [8%, 14%] (rf 4.5% and beta 1.0 when the snapshot has none), Rd = interest
  expense / debt clamped to [rf, rf + 10%], rf itself without debt or without an interest figure. A negative
  interest expense clamps to rf here (the companies that have one are printed, with the Rd an absolute value would
  give instead).
- EPV's bull needs a WACC above 1 point; a negative EPV is a value (the spec stores it as it is).
- NCAV's count is per_share_annual's latest year's: revenue / revenue_ps, else total equity / book_ps; a missing
  net-net part (cash, short-term investments, receivables, inventory) counts as 0.
- Graham on tangible book: the basis year's total equity must be positive; a missing goodwill or intangibles figure
  counts as 0.
- A company whose DCF or Graham row says its ADS ratio can't be read has every indicator N/A but the P/S value (the
  valuation inputs can't put its filed figures on the price's basis).

Usage: python qa/oracle/indicators_oracle.py --db PATH
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
from collections import Counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Num = Optional[float]

#: Tolerances, from the task brief (M4-5). None may be loosened to make a gate pass.
GROWTH_TOL = 0.0005  # absolute: 0.05 percentage points
VALUE_TOL = 0.005  # relative: 0.5%
EXACT_TOL = 1e-9  # relative: the P/S value is exact arithmetic on stored numbers
COUNT_TOL = 1e-9  # relative: the EPV count is the DCF's count, stored twice
CANDIDATE_TOL = 1e-6  # relative: a count rebuilt from the snapshot or the financials

#: The spec's figures.
RDCF_LOW, RDCF_HIGH = -0.50, 1.00
EPV_YEARS, EPV_MIN_YEARS = 5, 3
EPV_SPREAD = 0.01
TAX_CAP, TAX_DEFAULT = 0.35, 0.21
ERP, RF_DEFAULT, BETA_DEFAULT = 0.045, 0.045, 1.0
RE_FLOOR, RE_CAP = 0.08, 0.14
RD_SPREAD = 0.10
NNWC_WEIGHTS = (("cash_and_equivalents", 1.0), ("short_term_investments", 1.0), ("accounts_receivable", 0.75),
                ("inventory", 0.5))
GRAHAM = 22.5
NO_EPV = ("bank", "insurance", "reit")
NO_NCAV = ("bank", "insurance")
FINANCIALS = ("bank", "insurance")
ADS = "the ADS ratio can't be read"

INDICATORS = ("reverse_dcf", "epv", "ncav", "ps_value", "graham_tangible")
FA = ("revenue", "operating_income", "pretax_income", "interest_expense", "income_tax_expense", "long_term_debt",
      "short_term_debt", "commercial_paper", "cash_and_equivalents", "short_term_investments", "accounts_receivable",
      "inventory", "current_assets", "total_liabilities", "total_equity", "goodwill", "intangible_assets",
      "shares_outstanding", "weighted_avg_shares_diluted")
SNAP = ("collected_at", "current_price", "market_cap", "beta", "risk_free_rate", "shares_outstanding")

#: Every gate run, for the summary line.
GATES: List[str] = []


def gate(name: str, ok: bool, detail: str, failures: List[str]) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    GATES.append(name)
    if not ok:
        failures.append(name)


def fmt(x: Any) -> str:
    """A figure for the report: N/A, an amount in millions, or six significant digits."""
    if x is None:
        return "N/A"
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, (int, float)):
        if abs(x) >= 1e6:
            return f"{x / 1e6:,.3f}M"
        return f"{x:.6g}"
    return str(x)


def pct(x: Num) -> str:
    return "N/A" if x is None else f"{100 * x:+.2f}%"


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def same(mine: Num, stored: Num, tol: float, relative: bool = True) -> bool:
    """Both N/A, or both valued within *tol* (of the larger magnitude when *relative*)."""
    if mine is None or stored is None:
        return mine is None and stored is None
    return abs(mine - stored) <= (tol * max(abs(mine), abs(stored)) if relative else tol)


def gap(mine: Num, stored: Num) -> str:
    """The relative gap of two values, for a report line."""
    if mine is None or stored is None or max(abs(mine), abs(stored)) == 0:
        return ""
    return f" (gap {abs(mine - stored) / max(abs(mine), abs(stored)):.3g})"


def share(n: int, d: int) -> str:
    return f"{n}/{d}"


def rows(db: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    cur = db.execute(sql, tuple(params))
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def load_json(raw: Optional[str]) -> Dict[str, Any]:
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def shape(reason: Optional[str]) -> str:
    """A reason with its numbers masked, for tallies."""
    return re.sub(r"-?\d[\d.,]*", "#", reason or "")


# ---------------------------------------------------------------------------------------------------------------
# Loading


class Data:
    """The raw tables the oracle reads, the earlier milestones' stored inputs, and the code's indicators."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.sector: Dict[str, str] = {t: s or "general" for t, s in db.execute(
            "SELECT ticker, sector_class FROM companies")}
        self.fa: Dict[str, Dict[int, Dict[str, Num]]] = {}
        for r in db.execute(f"SELECT ticker, fiscal_year, {', '.join(FA)} FROM financials_annual "
                            "WHERE fiscal_year IS NOT NULL"):
            self.fa.setdefault(r[0], {})[int(r[1])] = dict(zip(FA, r[2:]))
        self.ma_debt: Dict[Tuple[str, int], Num] = {
            (t, int(y)): v for t, y, v in db.execute(
                "SELECT ticker, fiscal_year, total_debt FROM metrics_annual WHERE fiscal_year IS NOT NULL")}
        # The latest snapshot with a price, and the latest of all (the valuation inputs read that one).
        self.priced: Dict[str, Dict[str, Any]] = {}
        self.latest: Dict[str, Dict[str, Any]] = {}
        for r in rows(db, f"SELECT ticker, {', '.join(SNAP)} FROM market_snapshots ORDER BY ticker, collected_at"):
            self.latest[r["ticker"]] = r
            if r["current_price"] is not None:
                self.priced[r["ticker"]] = r
        self.val: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in rows(db, "SELECT ticker, model, applicable, na_reason, value_bear, value_base, value_bull, "
                          "assumptions, basis_fiscal_year FROM valuations WHERE model IN ('dcf', 'graham')"):
            r["assumptions"] = load_json(r["assumptions"])
            self.val[(r["ticker"], r["model"])] = r
        self.ps_range: Dict[str, Dict[str, Any]] = {r["ticker"]: r for r in rows(
            db, "SELECT ticker, applicable, na_reason, p25, median, p75, implied_value FROM valuation_ranges "
                "WHERE metric = 'ps'")}
        self.per_share: Dict[Tuple[str, int], Dict[str, Num]] = {
            (r["ticker"], int(r["fiscal_year"])): r for r in rows(
                db, "SELECT ticker, fiscal_year, revenue_ps, book_ps FROM per_share_annual")}
        self.qs: Dict[str, Dict[str, Any]] = {r["ticker"]: r for r in rows(
            db, "SELECT ticker, fiscal_year, wacc, ncav_per_share FROM quality_scores")}
        # The code's output.
        self.ind: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in rows(db, "SELECT ticker, indicator, applicable, na_reason, value_bear, value_base, value_bull, unit, "
                          "assumptions, basis_fiscal_year FROM valuation_indicators"):
            r["assumptions"] = load_json(r["assumptions"])
            r["applicable"] = bool(r["applicable"])
            self.ind[(r["ticker"], r["indicator"])] = r
        self.tickers = sorted(set(self.sector) | {t for t, _ in self.ind})

    def ads(self, t: str) -> Optional[str]:
        """The DCF or Graham row's N/A reason when it says the ADS ratio can't be read (an M0 input)."""
        for model in ("dcf", "graham"):
            reason = (self.val.get((t, model)) or {}).get("na_reason") or ""
            if reason.startswith(ADS):
                return reason
        return None

    def price(self, t: str) -> Num:
        snap = self.priced.get(t)
        return snap["current_price"] if snap else None


def na(reason: str, figures: Optional[Dict[str, Any]] = None, side: Optional[str] = None) -> Dict[str, Any]:
    return {"applicable": False, "reason": reason, "base": None, "bear": None, "bull": None,
            "figures": dict(figures or {}), "side": side}


def ok(base: Num, bear: Num = None, bull: Num = None, figures: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"applicable": True, "reason": None, "base": base, "bear": bear, "bull": bull,
            "figures": dict(figures or {}), "side": None}


# ---------------------------------------------------------------------------------------------------------------
# The reverse DCF


def dcf_value(fcf: float, shares: float, g: float, r: float, terminal: float) -> float:
    """The spec's two-stage DCF per share: years 1-5 grow at *g*; years 6-10 fade linearly from *g* to *terminal*
    (year 5 + k at g + (terminal - g) x k / 5: a reading); a Gordon terminal value on year 10's FCF; at *r*."""
    path = [g] * 5 + [g + (terminal - g) * k / 5.0 for k in range(1, 6)]
    flow, present = fcf, 0.0
    discount = 1.0
    for growth in path:
        flow *= 1.0 + growth
        discount *= 1.0 + r
        present += flow / discount
    present += flow * (1.0 + terminal) / (r - terminal) / discount
    return present / shares


def brent(f: Callable[[float], float], a: float, b: float, xtol: float = 1e-13, maxiter: int = 500) -> float:
    """A root of *f* in [a, b], where f(a) and f(b) differ in sign (or one is 0), by Brent's method: inverse quadratic
    interpolation or the secant step where they stay inside the bracket and shrink it fast enough, else bisection."""
    fa, fb = f(a), f(b)
    if fa == 0:
        return a
    if fb == 0:
        return b
    if fa * fb > 0:
        raise ValueError("the bracket doesn't change sign")
    if abs(fa) < abs(fb):
        a, b, fa, fb = b, a, fb, fa
    c, fc = a, fa
    d = c
    bisected = True
    for _ in range(maxiter):
        if fb == 0 or abs(b - a) <= xtol:
            return b
        if fa != fc and fb != fc:
            s = (a * fb * fc / ((fa - fb) * (fa - fc)) + b * fa * fc / ((fb - fa) * (fb - fc))
                 + c * fa * fb / ((fc - fa) * (fc - fb)))
        else:
            s = b - fb * (b - a) / (fb - fa)
        lo, hi = sorted(((3 * a + b) / 4.0, b))
        if (not lo < s < hi
                or (bisected and abs(s - b) >= abs(b - c) / 2.0)
                or (not bisected and abs(s - b) >= abs(c - d) / 2.0)
                or (bisected and abs(b - c) < xtol)
                or (not bisected and abs(c - d) < xtol)):
            s = (a + b) / 2.0
            bisected = True
        else:
            bisected = False
        fs = f(s)
        d, c, fc = c, b, fb
        if fa * fs < 0:
            b, fb = s, fs
        else:
            a, fa = s, fs
        if abs(fa) < abs(fb):
            a, b, fa, fb = b, a, fb, fa
    return b


def reverse_dcf(d: Data, t: str) -> Dict[str, Any]:
    dcf = d.val.get((t, "dcf"))
    if dcf is None:
        return na("no DCF row")
    if not dcf["applicable"]:
        return na(f"the DCF is N/A: {dcf['na_reason']}")
    a = dcf["assumptions"]
    price = d.price(t)
    if price is None or price <= 0:
        return na("no snapshot with a price")
    fcf, shares, r, terminal = a["fcf_basis"], a["shares_outstanding"], a["discount_base"], a["terminal_growth"]
    figures = {"price": price, "dcf_growth_base": a.get("growth_base"), "at_growth_base":
               dcf_value(fcf, shares, a["growth_base"], r, terminal) if a.get("growth_base") is not None else None,
               "dcf_value_base": dcf["value_base"]}

    def excess(g: float) -> float:
        return dcf_value(fcf, shares, g, r, terminal) - price

    low, high = excess(RDCF_LOW), excess(RDCF_HIGH)
    figures.update(value_at_low=low + price, value_at_high=high + price)
    if low > 0:
        return na(f"the price {price:g} is below the DCF's value at {RDCF_LOW:.0%} ({low + price:.4g})", figures,
                  "below")
    if high < 0:
        return na(f"the price {price:g} is above the DCF's value at {RDCF_HIGH:.0%} ({high + price:.4g})", figures,
                  "above")
    return ok(brent(excess, RDCF_LOW, RDCF_HIGH), figures=figures)


# ---------------------------------------------------------------------------------------------------------------
# EPV


def ebit(f: Dict[str, Num]) -> Tuple[Num, str]:
    """Operating income, else pretax income + interest expense (an untagged interest as 0)."""
    if f.get("operating_income") is not None:
        return f["operating_income"], "operating income"
    if f.get("pretax_income") is not None:
        return f["pretax_income"] + (f.get("interest_expense") or 0.0), "pretax income + interest"  # type: ignore
    return None, "none"


def effective_tax(f: Dict[str, Num]) -> Tuple[float, str]:
    tax, pretax = f.get("income_tax_expense"), f.get("pretax_income")
    if tax is None or pretax is None or pretax <= 0:
        return TAX_DEFAULT, "default"
    return clamp(tax / pretax, 0.0, TAX_CAP), "effective"


def debt(d: Data, t: str, fy: int, f: Dict[str, Num], sector: str) -> Tuple[Num, str]:
    """(the year's debt, how it reads): known, unknown, none (0) or as filed (banks and insurers)."""
    total = d.ma_debt.get((t, fy))
    tags = [f.get("long_term_debt"), f.get("short_term_debt"), f.get("commercial_paper")]
    present = [v for v in tags if v is not None]
    filed = total if total is not None else (sum(present) if present else None)
    if any(v is not None and v > 0 for v in tags + [total]):
        return filed, "known"
    if (f.get("interest_expense") or 0.0) > 0:
        return None, "unknown"
    if sector in FINANCIALS:
        return filed, "as filed"
    return 0.0, "none"


def wacc(f: Dict[str, Num], owed: float, snap: Optional[Dict[str, Any]]) -> Tuple[Num, Dict[str, Any]]:
    equity = snap.get("market_cap") if snap else None
    if equity is None or equity <= 0:
        return None, {"market_cap": equity}
    rf = snap["risk_free_rate"] if snap["risk_free_rate"] is not None else RF_DEFAULT  # type: ignore[index]
    beta = snap["beta"] if snap["beta"] is not None else BETA_DEFAULT  # type: ignore[index]
    re_ = clamp(rf + beta * ERP, RE_FLOOR, RE_CAP)
    interest = f.get("interest_expense")
    rd = rf if owed <= 0 or interest is None else clamp(interest / owed, rf, rf + RD_SPREAD)
    tax, _ = effective_tax(f)
    total = equity + owed
    rate = equity / total * re_ + owed / total * rd * (1.0 - tax)
    return rate, {"market_cap": equity, "rf": rf, "beta": beta, "re": re_, "rd": rd, "interest": interest}


def epv_count(d: Data, t: str) -> Tuple[Num, str]:
    """The valuation inputs' count: the EPV row's own (gated against the DCF's and the candidates), else the DCF
    row's, else the latest snapshot's shares outstanding."""
    stored = (d.ind.get((t, "epv")) or {}).get("assumptions", {}).get("shares_outstanding")
    if stored:
        return float(stored), "the EPV row's"
    dcf = (d.val.get((t, "dcf")) or {}).get("assumptions", {}).get("shares_outstanding")
    if dcf:
        return float(dcf), "the DCF row's"
    snap = d.latest.get(t) or {}
    return snap.get("shares_outstanding"), "the latest snapshot's"


def epv(d: Data, t: str) -> Dict[str, Any]:
    sector = d.sector.get(t, "general")
    if sector in NO_EPV:
        return na(f"N/A for {sector}")
    if d.ads(t):
        return na(f"ADS: {d.ads(t)}")
    years = d.fa.get(t) or {}
    if not years:
        return na("no financials_annual rows")
    fy = max(years)
    cur = years[fy]
    margins: List[Tuple[int, float]] = []
    pretax: List[int] = []
    for y in range(fy - EPV_YEARS + 1, fy + 1):  # the labels fy-4..fy (a reading)
        f = years.get(y)
        if f is None:
            continue
        e, source = ebit(f)
        if f.get("revenue") is not None and f["revenue"] > 0 and e is not None:  # type: ignore[operator]
            margins.append((y, e / f["revenue"]))  # type: ignore[operator]
            if source != "operating income":
                pretax.append(y)
    owed, reading = debt(d, t, fy, cur, sector)
    figures: Dict[str, Any] = {"fy": fy, "margin_years": [y for y, _ in margins], "pretax_years": pretax,
                               "debt_reading": reading, "total_debt": owed}
    if reading == "unknown":
        return na(f"debt unknown in FY{fy}: no positive debt tag, interest expense "
                  f"{fmt(cur.get('interest_expense'))}", figures)
    if len(margins) < EPV_MIN_YEARS:
        return na(f"an EBIT margin in {len(margins)} of FY{fy - EPV_YEARS + 1}-FY{fy}", figures)
    revenue = cur.get("revenue")
    if revenue is None or revenue <= 0:
        return na(f"no revenue in FY{fy}", figures)
    mean_margin = statistics.fmean(m for _, m in margins)
    normalized = mean_margin * revenue
    tax, tax_source = effective_tax(cur)
    adjusted = normalized * (1.0 - tax)
    figures.update(mean_margin=mean_margin, normalized_ebit=normalized, tax_rate=tax, tax_source=tax_source,
                   adjusted_earnings=adjusted)
    if adjusted <= 0:
        return na(f"adjusted earnings {fmt(adjusted)} not positive", figures)
    rate, parts = wacc(cur, owed or 0.0, d.priced.get(t))
    figures.update(wacc=rate, wacc_parts=parts)
    if rate is None:
        return na("no WACC: no market cap on the latest snapshot with a price", figures)
    cash = (cur.get("cash_and_equivalents") or 0.0) + (cur.get("short_term_investments") or 0.0)
    count, whose = epv_count(d, t)
    figures.update(cash_and_sti=cash, shares=count, shares_from=whose)
    if not count or count <= 0:
        return na("no share count", figures)

    def per_share(at: float) -> Num:
        return None if at <= 0 else (adjusted / at - (owed or 0.0) + cash) / count  # type: ignore[operator]

    return ok(per_share(rate), per_share(rate + EPV_SPREAD), per_share(rate - EPV_SPREAD), figures)


def count_candidates(d: Data, t: str) -> List[Tuple[str, float]]:
    """Milestone 0's candidates for today's share count: each snapshot's shares outstanding and market cap / price,
    and the latest fiscal year's counts (as filed, and per_share_annual's on today's split basis)."""
    out: List[Tuple[str, float]] = []
    for name, snap in (("latest snapshot", d.latest.get(t)), ("latest priced snapshot", d.priced.get(t))):
        if not snap:
            continue
        if snap.get("shares_outstanding"):
            out.append((f"{name}'s shares outstanding", float(snap["shares_outstanding"])))
        if snap.get("market_cap") and snap.get("current_price"):
            out.append((f"{name}'s market cap / price", snap["market_cap"] / snap["current_price"]))
    years = d.fa.get(t) or {}
    if years:
        fy = max(years)
        for k in ("weighted_avg_shares_diluted", "shares_outstanding"):
            if years[fy].get(k):
                out.append((f"FY{fy} {k}", float(years[fy][k])))  # type: ignore[arg-type]
        count, how = ps_count(d, t, fy, years[fy])
        if count:
            out.append((f"FY{fy} per_share_annual count ({how})", count))
    return out


# ---------------------------------------------------------------------------------------------------------------
# NCAV, the P/S value, Graham on tangible book


def ps_count(d: Data, t: str, fy: int, f: Dict[str, Num]) -> Tuple[Num, str]:
    """per_share_annual's count for the year: revenue / revenue_ps, else total equity / book_ps."""
    ps = d.per_share.get((t, fy))
    if ps is None:
        return None, "no per_share_annual row"
    for total, per in (("revenue", "revenue_ps"), ("total_equity", "book_ps")):
        if f.get(total) and ps.get(per):
            count = f[total] / ps[per]  # type: ignore[operator]
            if count > 0:
                return count, f"{total} / {per}"
    return None, "no revenue / revenue_ps or equity / book_ps"


def ncav(d: Data, t: str) -> Dict[str, Any]:
    sector = d.sector.get(t, "general")
    if sector in NO_NCAV:
        return na(f"N/A for {sector}")
    if d.ads(t):
        return na(f"ADS: {d.ads(t)}")
    years = d.fa.get(t) or {}
    if not years:
        return na("no financials_annual rows")
    fy = max(years)
    f = years[fy]
    ca, tl = f.get("current_assets"), f.get("total_liabilities")
    if ca is None or tl is None:
        return na(f"FY{fy}: current assets {fmt(ca)}, total liabilities {fmt(tl)}")
    count, how = ps_count(d, t, fy, f)
    if not count:
        return na(f"FY{fy}: no share count ({how})")
    ncav_ps = (ca - tl) / count
    nnwc_ps = (sum(w * (f.get(k) or 0.0) for k, w in NNWC_WEIGHTS) - tl) / count  # missing parts as 0 (a reading)
    figures = {"fy": fy, "count": count, "count_from": how, "ncav_per_share": ncav_ps, "nnwc_per_share": nnwc_ps}
    if ncav_ps <= 0:
        return na(f"NCAV per share {ncav_ps:.4g} not positive", figures)
    return ok(ncav_ps, nnwc_ps if nnwc_ps > 0 else None, None, figures)


def ps_value(d: Data, t: str) -> Dict[str, Any]:
    r = d.ps_range.get(t)
    if r is None:
        return na("no P/S row in valuation_ranges")
    if not r["applicable"]:
        return na(f"the P/S range is N/A: {r['na_reason']}")
    iv, med = r["implied_value"], r["median"]
    if iv is None:
        return na(f"the P/S range has no implied value: {r['na_reason']}")

    def scaled(q: Num) -> Num:
        return None if q is None or med is None or med <= 0 else iv * q / med

    return ok(iv, scaled(r["p25"]), scaled(r["p75"]))


def graham_tangible(d: Data, t: str) -> Dict[str, Any]:
    if d.ads(t):
        return na(f"ADS: {d.ads(t)}")
    g = d.val.get((t, "graham"))
    if g is None:
        return na("no Graham row")
    if not g["applicable"]:
        return na(f"the Graham number is N/A: {g['na_reason']}")
    a, fy = g["assumptions"], g["basis_fiscal_year"]
    f = (d.fa.get(t) or {}).get(int(fy)) if fy is not None else None
    equity = (f or {}).get("total_equity")
    if f is None or equity is None or equity <= 0:
        return na(f"FY{fy}: total equity {fmt(equity)}")
    goodwill, intangibles = f.get("goodwill") or 0.0, f.get("intangible_assets") or 0.0  # missing as 0
    tangible_bvps = a["bvps"] * (equity - goodwill - intangibles) / equity
    figures = {"fy": fy, "tangible_bvps": tangible_bvps, "missing": [k for k in ("goodwill", "intangible_assets")
                                                                    if f.get(k) is None]}
    if tangible_bvps <= 0:
        return na(f"tangible book per share {tangible_bvps:.4g} not positive", figures)

    def number(eps: Num) -> Num:
        return None if eps is None or eps <= 0 else math.sqrt(GRAHAM * eps * tangible_bvps)

    return ok(number(a["eps_base"]), number(a["eps_bear"]), number(a["eps_bull"]), figures)


MINE: Dict[str, Callable[[Data, str], Dict[str, Any]]] = {
    "reverse_dcf": reverse_dcf, "epv": epv, "ncav": ncav, "ps_value": ps_value, "graham_tangible": graham_tangible}


# ---------------------------------------------------------------------------------------------------------------
# The gates


def check_applicability(d: Data, name: str, mine: Dict[str, Dict[str, Any]], failures: List[str]) -> None:
    """Applicable on both sides or N/A on both, every company; the reasons side by side."""
    agreed = 0
    lines: List[str] = []
    pairs: Dict[Tuple[str, str], List[str]] = {}
    for t in d.tickers:
        m, s = mine[t], d.ind.get((t, name))
        if s is None:
            lines.append(f"    {t}: no stored row; this oracle {'applicable' if m['applicable'] else m['reason']}")
            continue
        if m["applicable"] == s["applicable"]:
            agreed += 1
            if not m["applicable"]:
                pairs.setdefault((shape(m["reason"]), shape(s["na_reason"])), []).append(t)
            continue
        lines.append(f"    {t} ({d.sector.get(t)}): this oracle {'applicable' if m['applicable'] else 'N/A'} "
                     f"({m['reason'] or fmt(m['base'])}), stored {'applicable' if s['applicable'] else 'N/A'} "
                     f"({s['na_reason'] or fmt(s['value_base'])})")
    gate(f"{name}: N/A on the same companies", agreed == len(d.tickers),
         f"agree {share(agreed, len(d.tickers))}; applicable on both "
         f"{sum(1 for t in d.tickers if mine[t]['applicable'] and (d.ind.get((t, name)) or {}).get('applicable'))}",
         failures)
    for line in lines:
        print(line)
    print("    N/A on both, the reasons side by side (this oracle | stored: companies), numbers masked:")
    for (a, b), ts in sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"        {a} | {b}: {len(ts)} ({', '.join(ts)})")


def compare(t: str, label: str, mine: Num, stored: Num, tol: float, relative: bool, bad: List[str]) -> bool:
    if same(mine, stored, tol, relative):
        return True
    bad.append(f"{label} stored {fmt(stored)} oracle {fmt(mine)}{gap(mine, stored)}")
    return False


def check_reverse_dcf(d: Data, mine: Dict[str, Dict[str, Any]], failures: List[str]) -> None:
    print("== reverse DCF: the growth of years 1-5 the latest priced snapshot implies, solved by Brent's method")
    check_applicability(d, "reverse_dcf", mine, failures)
    checked = agreed = 0
    lines = []
    worst = 0.0
    for t in d.tickers:
        m, s = mine[t], d.ind.get((t, "reverse_dcf"))
        if not (s and m["applicable"] and s["applicable"]):
            continue
        checked += 1
        if same(m["base"], s["value_base"], GROWTH_TOL, False):
            agreed += 1
            worst = max(worst, abs(m["base"] - s["value_base"]))
        else:
            f = m["figures"]
            lines.append(f"    {t}: stored {pct(s['value_base'])} oracle {pct(m['base'])} at price {fmt(f['price'])} "
                         f"(stored price {fmt(s['assumptions'].get('price'))})")
    gate("reverse_dcf: implied growth within 0.05 percentage points", checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)}; the largest gap among those agreeing {worst * 100:.2e} percentage points",
         failures)
    for line in lines:
        print(line)
    # The prices out of the range: N/A on both sides, on the same side.
    sides = []
    bad = []
    for t in d.tickers:
        m, s = mine[t], d.ind.get((t, "reverse_dcf")) or {}
        reason = s.get("na_reason") or ""
        stored_side = "below" if "is below the DCF's value" in reason else "above" if "is above the DCF's value" \
            in reason else None
        if m["side"] is None and stored_side is None:
            continue
        sides.append(t)
        if m["side"] != stored_side:
            bad.append(f"    {t}: this oracle {m['side'] or m['reason'] or 'in range'}, stored {reason or 'in range'}")
    gate("reverse_dcf: out of the -50% to +100% range on the same side", not bad,
         f"agree {share(len(sides) - len(bad), len(sides))} (companies out of range on either side"
         + (": none on this data)" if not sides else f": {', '.join(sides)})"), failures)
    for line in bad:
        print(line)
    for t in sides:
        m = mine[t]
        f = m["figures"]
        print(f"    {t}: price {fmt(f.get('price'))}, the DCF's value at -50% {fmt(f.get('value_at_low'))}, at +100% "
              f"{fmt(f.get('value_at_high'))}; this oracle: {m['reason'] or pct(m['base'])}; stored: "
              f"{(d.ind.get((t, 'reverse_dcf')) or {}).get('na_reason')}")


EPV_FIGURES = (("normalized_ebit", "normalized_ebit"), ("tax_rate", "tax_rate"),
               ("adjusted_earnings", "adjusted_earnings"), ("wacc", "wacc"), ("total_debt", "total_debt"),
               ("cash_and_sti", "cash_and_sti"))


def check_epv(d: Data, mine: Dict[str, Dict[str, Any]], failures: List[str]) -> None:
    print("== EPV: (mean EBIT margin x latest revenue x (1 - t)) / WACC - debt + cash and STI, per share; "
          "bear and bull at WACC +/- 1 point")
    # The count: the valuation inputs' (M0's), stored on the EPV row.
    checked = agreed = 0
    lines = []
    sources: Counter = Counter()
    for t in d.tickers:
        s = d.ind.get((t, "epv"))
        stored = (s or {}).get("assumptions", {}).get("shares_outstanding")
        if not stored:
            continue
        checked += 1
        dcf = d.val.get((t, "dcf")) or {}
        if dcf.get("applicable"):
            ref = dcf["assumptions"].get("shares_outstanding")
            if same(float(stored), ref, COUNT_TOL):
                agreed += 1
                sources["the DCF row's count"] += 1
            else:
                lines.append(f"    {t}: the EPV row's count {fmt(stored)}, the DCF row's {fmt(ref)}")
            continue
        hits = [name for name, value in count_candidates(d, t) if same(float(stored), value, CANDIDATE_TOL)]
        if hits:
            agreed += 1
            sources[f"a candidate (the DCF is N/A): {hits[0]}"] += 1
        else:
            lines.append(f"    {t}: the EPV row's count {fmt(stored)} (DCF N/A) matches none of "
                         + "; ".join(f"{name} {fmt(value)}" for name, value in count_candidates(d, t)))
    gate("epv: the count is the valuation inputs' count", checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)} ({'; '.join(f'{k}: {v}' for k, v in sorted(sources.items()))})", failures)
    for line in lines:
        print(line)
    check_applicability(d, "epv", mine, failures)
    checked = agreed = 0
    lines = []
    extra = 0
    for t in d.tickers:
        m, s = mine[t], d.ind.get((t, "epv"))
        if s is None:
            continue
        a, f = s["assumptions"], m["figures"]
        bad: List[str] = []
        both = m["applicable"] and s["applicable"]
        n = 0
        for mine_key, stored_key in EPV_FIGURES:
            if both or (mine_key in f and stored_key in a):  # the N/A rows that carry the figure, too
                n += 1
                compare(t, stored_key, f.get(mine_key), a.get(stored_key), VALUE_TOL, True, bad)
        for key in ("margin_years", "pretax_years"):
            if both or key in a:
                n += 1
                if f.get(key) != a.get(key):
                    bad.append(f"{key} stored {a.get(key)} oracle {f.get(key)}")
        if both:
            for label, mk, sk in (("base", "base", "value_base"), ("bear", "bear", "value_bear"),
                                  ("bull", "bull", "value_bull")):
                n += 1
                compare(t, label, m[mk], s[sk], VALUE_TOL, True, bad)
        elif n:
            extra += 1
        checked += n
        agreed += n - len(bad)
        if bad:
            parts = f.get("wacc_parts") or {}
            lines.append(f"    {t} ({d.sector.get(t)}, FY{f.get('fy')}): {'; '.join(bad)}; debt "
                         f"{f.get('debt_reading')}, rf {fmt(parts.get('rf'))}, beta {fmt(parts.get('beta'))}, Re "
                         f"{fmt(parts.get('re'))}, "
                         f"Rd {fmt(parts.get('rd'))}, E {fmt(parts.get('market_cap'))}, interest "
                         f"{fmt(parts.get('interest'))}, count {fmt(f.get('shares'))} ({f.get('shares_from')})")
    gate("epv: EBIT, tax, adjusted earnings, WACC, debt, cash and the per-share values within 0.5%",
         checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)} figures (including {extra} N/A rows' stored figures)", failures)
    for line in lines:
        print(line)
    pretax = sorted(t for t in d.tickers if mine[t]["figures"].get("pretax_years"))
    print(f"    EBIT from pretax income + interest in some year (this oracle): {len(pretax)} companies: "
          + ", ".join(f"{t} FY{','.join(str(y) for y in mine[t]['figures']['pretax_years'])}" for t in pretax))
    readings = Counter(mine[t]["figures"].get("debt_reading") for t in d.tickers if "debt_reading" in
                       mine[t]["figures"])
    print(f"    the debt readings (this oracle, EPV's sectors): {dict(sorted(readings.items()))}")
    negative = []
    for t in d.tickers:
        f = mine[t]["figures"]
        parts = f.get("wacc_parts") or {}
        interest, owed = parts.get("interest"), f.get("total_debt")
        if interest is not None and interest < 0 and owed:
            other = clamp(abs(interest) / owed, parts["rf"], parts["rf"] + RD_SPREAD)
            negative.append(f"{t} FY{f.get('fy')} interest {fmt(interest)} over debt {fmt(owed)}: Rd {parts['rd']:.6g} "
                            f"here, {other:.6g} with |interest|" + (" (the same)" if other == parts["rd"] else ""))
    print(f"    a negative interest expense in the latest year: {'; '.join(negative) or 'none'}")
    near = sorted(t for t in d.tickers if mine[t]["applicable"] and (d.qs.get(t) or {}).get("wacc") is not None
                  and same(mine[t]["figures"]["wacc"], d.qs[t]["wacc"], 1e-9))
    have = sorted(t for t in d.tickers if mine[t]["applicable"] and (d.qs.get(t) or {}).get("wacc") is not None)
    print(f"    (reported) this oracle's WACC equal to M2's quality_scores.wacc (1e-9): {share(len(near), len(have))}"
          + ("" if len(near) == len(have) else ": differ " + ", ".join(
              f"{t} {fmt(mine[t]['figures']['wacc'])} / {fmt(d.qs[t]['wacc'])}" for t in have if t not in near)))


def check_ncav(d: Data, mine: Dict[str, Dict[str, Any]], failures: List[str]) -> None:
    print("== NCAV and the net-net: (current assets - total liabilities) / count; bear (cash + STI + 0.75 AR + "
          "0.5 inventory - total liabilities) / count when positive")
    check_applicability(d, "ncav", mine, failures)
    checked = agreed = 0
    extra = 0
    lines = []
    for t in d.tickers:
        m, s = mine[t], d.ind.get((t, "ncav"))
        if s is None:
            continue
        a, f = s["assumptions"], m["figures"]
        bad: List[str] = []
        both = m["applicable"] and s["applicable"]
        n = 0
        for key in ("ncav_per_share", "nnwc_per_share"):
            if both or (key in f and key in a):
                n += 1
                compare(t, key, f.get(key), a.get(key), VALUE_TOL, True, bad)
        if both:
            for label, mk, sk in (("base", "base", "value_base"), ("bear", "bear", "value_bear"),
                                  ("bull", "bull", "value_bull")):
                n += 1
                compare(t, label, m[mk], s[sk], VALUE_TOL, True, bad)
        elif n:
            extra += 1
        checked += n
        agreed += n - len(bad)
        if bad:
            lines.append(f"    {t} FY{f.get('fy')}: {'; '.join(bad)}; count {fmt(f.get('count'))} "
                         f"({f.get('count_from')}), stored shares {fmt(a.get('shares'))}")
    gate("ncav: NCAV and net-net per share within 0.5%, the bear only for a positive net-net",
         checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)} figures (including {extra} N/A rows' stored figures)", failures)
    for line in lines:
        print(line)
    have = [t for t in d.tickers if mine[t]["figures"].get("ncav_per_share") is not None
            and (d.qs.get(t) or {}).get("ncav_per_share") is not None]
    near = [t for t in have if same(mine[t]["figures"]["ncav_per_share"], d.qs[t]["ncav_per_share"], VALUE_TOL)]
    print(f"    (reported) this oracle's NCAV per share against M2's quality_scores.ncav_per_share within 0.5%: "
          f"{share(len(near), len(have))}" + ("" if len(near) == len(have) else ": differ " + ", ".join(
              f"{t} {fmt(mine[t]['figures']['ncav_per_share'])} / {fmt(d.qs[t]['ncav_per_share'])}"
              for t in have if t not in near)))
    counted = [t for t in d.tickers if mine[t]["figures"].get("count") and (d.ind.get((t, "ncav")) or {}).get(
        "assumptions", {}).get("shares")]
    equal = [t for t in counted if same(mine[t]["figures"]["count"], d.ind[(t, "ncav")]["assumptions"]["shares"],
                                        1e-9)]
    print(f"    (reported) this oracle's count (per_share_annual) equal to the stored NCAV row's shares (1e-9): "
          f"{share(len(equal), len(counted))}" + ("" if len(equal) == len(counted) else ": differ " + ", ".join(
              t for t in counted if t not in equal)))
    positive = [t for t in d.tickers if mine[t]["applicable"]]
    print(f"    (reported) NCAV positive: {len(positive)} of {len(d.tickers)} ({', '.join(positive)}); a positive "
          f"net-net among them: {', '.join(t for t in positive if mine[t]['bear'] is not None) or 'none'}")


def check_values(d: Data, name: str, title: str, mine: Dict[str, Dict[str, Any]], tol: float,
                 failures: List[str], carried: Sequence[str] = ()) -> None:
    """The base, bear and bull where both sides apply (and *carried* figures on the N/A rows that carry them)."""
    checked = agreed = 0
    extra = 0
    lines = []
    for t in d.tickers:
        m, s = mine[t], d.ind.get((t, name))
        if s is None:
            continue
        bad: List[str] = []
        both = m["applicable"] and s["applicable"]
        n = 0
        for key in carried:
            if both or (key in m["figures"] and key in s["assumptions"]):
                n += 1
                compare(t, key, m["figures"].get(key), s["assumptions"].get(key), tol, True, bad)
        if both:
            for label, mk, sk in (("base", "base", "value_base"), ("bear", "bear", "value_bear"),
                                  ("bull", "bull", "value_bull")):
                n += 1
                compare(t, label, m[mk], s[sk], tol, True, bad)
        elif n:
            extra += 1
        checked += n
        agreed += n - len(bad)
        if bad:
            lines.append(f"    {t}: {'; '.join(bad)}")
    gate(title, checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)} figures" + (f" (including {extra} N/A rows' stored figures)" if carried
                                                      else ""), failures)
    for line in lines:
        print(line)


# ---------------------------------------------------------------------------------------------------------------
# Reported, not gated


def report(d: Data, mine: Dict[str, Dict[str, Dict[str, Any]]]) -> None:
    print("== reported, not gated")
    for name in INDICATORS:
        stored = [d.ind[(t, name)] for t in d.tickers if (t, name) in d.ind]
        applicable = [s for s in stored if s["applicable"]]
        reasons = Counter(shape(s["na_reason"]) for s in stored if not s["applicable"])
        ours = sum(1 for t in d.tickers if mine[name][t]["applicable"])
        print(f"    {name}: applicable {len(applicable)} stored, {ours} this oracle, of {len(d.tickers)}; stored N/A "
              "reasons: "
              + "; ".join(f"{r} ({n})" for r, n in reasons.most_common()))
        gaps = []
        for t in d.tickers:
            s = d.ind.get((t, name))
            if not s or not s["applicable"] or s["value_base"] is None:
                continue
            if name == "reverse_dcf":
                own = s["assumptions"].get("dcf_growth_base")
                if own is not None:
                    gaps.append((abs(s["value_base"] - own), t,
                                 f"{t} implied {pct(s['value_base'])} vs the DCF's {pct(own)} "
                                 f"({100 * (s['value_base'] - own):+.1f} points)"))
                continue
            price = d.price(t)
            if price:
                g = s["value_base"] / price - 1
                gaps.append((abs(g), t, f"{t} base {s['value_base']:.4g} / price {price:.4g} {pct(g)} (oracle "
                                        f"{fmt(mine[name][t]['base'])})"))
        gaps.sort(reverse=True)
        what = "implied growth less the DCF's own" if name == "reverse_dcf" else "base / snapshot price - 1"
        print(f"        the five largest gaps ({what}): " + "; ".join(g[2] for g in gaps[:5]))
    # Self-checks.
    rows_ = [(t, mine["reverse_dcf"][t]["figures"]) for t in d.tickers
             if mine["reverse_dcf"][t]["figures"].get("at_growth_base") is not None]
    exact = [t for t, f in rows_ if same(f["at_growth_base"], f["dcf_value_base"], 1e-9)]
    print(f"    self-check: this oracle's DCF at the DCF row's growth_base equals the row's value_base (1e-9): "
          f"{share(len(exact), len(rows_))}" + ("" if len(exact) == len(rows_) else ": differ " + ", ".join(
              f"{t} {fmt(f['at_growth_base'])} / {fmt(f['dcf_value_base'])}" for t, f in rows_ if t not in exact)))
    # The reverse DCF row's inputs (its own copy) against the DCF row's (the stored M0 inputs this oracle solves on).
    pairs = (("fcf_basis", "fcf_basis"), ("shares_outstanding", "shares_outstanding"),
             ("discount_base", "discount_base"), ("terminal_growth", "terminal_growth"),
             ("dcf_growth_base", "growth_base"))
    rdcf = [t for t in d.tickers if (d.ind.get((t, "reverse_dcf")) or {}).get("applicable")]
    differ = []
    for t in rdcf:
        own = d.ind[(t, "reverse_dcf")]["assumptions"]
        dcf = (d.val.get((t, "dcf")) or {}).get("assumptions") or {}
        if any(own.get(a) != dcf.get(b) for a, b in pairs):
            differ.append(t)
    print("    self-check: the reverse DCF row's fcf_basis, shares, discount, terminal growth and DCF growth equal the "
          f"DCF row's: {share(len(rdcf) - len(differ), len(rdcf))}" + (": differ " + ", ".join(differ) if differ
                                                                      else ""))
    priced = [t for t in d.tickers if (d.ind.get((t, "reverse_dcf")) or {}).get("assumptions", {}).get("price")
              is not None]
    eq = [t for t in priced if same(d.ind[(t, "reverse_dcf")]["assumptions"]["price"], d.price(t), 0.0)]
    print(f"    self-check: the reverse DCF's stored price equals the latest priced snapshot's: "
          f"{share(len(eq), len(priced))}" + ("" if len(eq) == len(priced) else ": differ " + ", ".join(
              t for t in priced if t not in eq)))
    missing = [k for k in INDICATORS for t in d.tickers if (t, k) not in d.ind]
    extra = sorted({k for _, k in d.ind} - set(INDICATORS))
    print(f"    stored rows: {len(d.ind)} for {len({t for t, _ in d.ind})} companies; missing (company, indicator) "
          f"pairs: {len(missing)}; indicators outside the spec's five: {', '.join(extra) or 'none'}")
    units = Counter((k, s["unit"]) for (_, k), s in d.ind.items())
    print(f"    units: {', '.join(f'{k} {u} {n}' for (k, u), n in sorted(units.items()))}")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", required=True)
    args = p.parse_args(argv)
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    failures: List[str] = []
    GATES.clear()
    d = Data(db)
    mine = {name: {t: MINE[name](d, t) for t in d.tickers} for name in INDICATORS}
    print(f"database {args.db}: {len(d.tickers)} companies, {len(d.ind)} valuation_indicators rows, "
          f"{len(d.val)} DCF and Graham rows, {len(d.ps_range)} P/S ranges, {len(d.priced)} companies with a priced "
          f"snapshot")

    check_reverse_dcf(d, mine["reverse_dcf"], failures)
    check_epv(d, mine["epv"], failures)
    check_ncav(d, mine["ncav"], failures)
    print("== median P/S value: the P/S range's implied value; bear and bull x p25 / median and x p75 / median")
    check_applicability(d, "ps_value", mine["ps_value"], failures)
    check_values(d, "ps_value", "ps_value: base, bear and bull exact (1e-9 relative)", mine["ps_value"], EXACT_TOL,
                 failures)
    print("== Graham number on tangible book: sqrt(22.5 x EPS x bvps x (equity - goodwill - intangibles) / equity)")
    check_applicability(d, "graham_tangible", mine["graham_tangible"], failures)
    check_values(d, "graham_tangible", "graham_tangible: base, bear and bull within 0.5%", mine["graham_tangible"],
                 VALUE_TOL, failures, carried=("tangible_bvps",))
    report(d, mine)

    print(f"gates: {len(GATES) - len(failures)} of {len(GATES)} pass; "
          + ("FAILED: " + "; ".join(failures) if failures else "all gates pass"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

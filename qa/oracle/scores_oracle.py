"""Independent check of Milestone 2's quality scores (build task M2-5) against the database's raw tables.

See the "Validation" section of docs/superpowers/specs/2026-10-21-quality-scores-design.md. It reads SQLite only
and never imports `src`: every formula below is re-derived from the spec, so agreeing with the code under test
means something only because the check can't share its bugs.

Gates (each figure valued on both sides, or N/A on both); any that fails makes the exit code 1:
- The scored fiscal year: the latest one in financials_annual.
- per_share_annual: revenue/share, EPS, EBITDA/share, FCF/share and book/share within 2%, on this oracle's own
  split basis (below), for the latest 11 fiscal years, except the pre-listing ones (below).
- The CAGRs stored in quality_scores, from those series: within 0.5 percentage points, except those starting in a
  pre-listing year.
- Piotroski: each of the nine tests, F and the number tested; N/A for banks and insurers.
- Altman Z within 0.5% and its zone (MVE the latest snapshot's market cap); N/A for banks, insurers, REITs and
  utilities.
- Beneish M and its eight variables within 0.5%; N/A for banks, insurers and REITs.
- The Sloan ratio within 0.1 percentage points.
- Predictability stars exactly, and each fitted series' R-squared within 0.01.
- The five ranks, the composite and the Magic Formula rank, exactly, from the stored components.
- The industry ranks (Milestone 5; docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and
  industries"), exactly, from companies.sic and the stored components: each company's industry group, its label and
  size; the five industry ranks and the industry composite; and the same companies without any industry rank.

Reported, not gated: the real-data distributions (the F-score histogram, Altman zones, Beneish flags,
predictability stars, the composite), each severe warning sign's figure recomputed from the raw tables, and the
industry groups formed (each one's size, label and members) with the companies left without one, and why.

Today's split basis, this oracle's own way (the code detects seams in the annual share-count series instead, so
the two methods check each other): a fiscal year's share count is the diluted weighted count from the latest
filing that gives one; without any, net income / diluted EPS (when |EPS| >= 0.10) from the latest filing that
gives both; then the shares outstanding from the latest filing that gives them; times every declared share split
dated after that filing. A diluted count more than 100x away from the same filing's net income / diluted EPS is a
units error, and that quotient is used instead. A count its filing can't check that way is checked against the
same fiscal year's count in financials_annual (the latest parse of the filings), else against the neighbouring
years' counts, and replaced by that reference when more than 100x away; every replacement is printed. The year's
EPS is the latest filing's diluted EPS over the splits dated after that filing.

Pre-listing years: a fiscal year ending before the company's first price bar + 1 year (before the listing, or
the listing year) isn't compared in the per-share and CAGR gates. The code keeps the listing seam folded there
(pre-listing counts leave out the preferred that converted), an approximation this oracle can't check
independently; the values left out are printed as "not compared: pre-listing basis".

Readings of the spec where it leaves a choice (each also noted where it's used):
- t-1 is fiscal year t - 1 exactly, not whichever stored year comes before t.
- ROA, LTD/TA and asset turnover use the same year's total assets (not opening or average assets).
- Gross margin is gross profit / revenue, with revenue - cost of revenue when gross profit isn't filed.
- "Rose" and "fell" are strict: an unchanged ratio fails the test.
- A missing long-term debt leaves Piotroski's leverage test untested (missing in either year), and is read as zero
  in Beneish's LVGI (debt-free filers tag none): the build's decision on the spec (M2-5).
- Altman's total liabilities are taken as filed, never derived; Z of exactly 1.81 or 2.99 is grey.
- Predictability needs the 10 consecutive fiscal years t-9..t, each with both series; a year with revenue/share
  <= 0 is 1 star like a loss year (no log fit exists); the R-squared is compared only where a fit was made.
- Percentile: the share of the group's values at or below the company's (at or above it for a lower-is-better
  component), so ties take the top of their run and the result is in (0, 1]; the group is the company's
  sector_class when 10 or more of the class have that component, else every row of quality_scores with it.
- "At least two" components applies to the ranks that have several; valuation and momentum have one each.
- Magic Formula: rank 1 is the highest earnings yield (return on capital), tied values share the best rank; the
  stored rank is the company's position when ordered by the sum of its two ranks (1 = lowest sum), and any
  order inside an exact tie of the sum is accepted, since the spec names no tie-break.
- Industries: the groups are formed over the companies with a quality_scores row, a code being four ASCII digits
  as stored (anything else is no code). A company's group is its 3-digit prefix when 10 or more of them share it,
  else its 2-digit prefix when 10 or more do; a group's members are every one whose code starts with it, so a
  2-digit group takes in its 3-digit groups' companies and has 10 members or more. Its label is "SIC <group>: " and
  the members' three most common descriptions as stored, by count, then in string order, joined with ", " ("SIC
  <group>" without any); its size, the members. A component counts toward a company's industry rank when 10 or more
  of its group's members have it (the percentile then among them, as above), and a rank needs two such components
  (one for valuation and momentum), so a component left out doesn't count toward that minimum; the industry
  composite is 10 x the mean of four or more industry ranks.

Usage: python qa/oracle/scores_oracle.py --db PATH
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
from datetime import date
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

Num = Optional[float]

#: Tolerances, from the task brief (M2-5). None may be loosened to make a gate pass.
PER_SHARE_TOL = 0.02  # relative
CAGR_TOL = 0.005  # absolute: 0.5 percentage points
ALTMAN_TOL = 0.005  # relative
BENEISH_TOL = 0.005  # relative
SLOAN_TOL = 0.001  # absolute: 0.1 percentage points
R2_TOL = 0.01  # absolute
COMPOSITE_TOL = 1e-9  # the composite is exact arithmetic on integer ranks

#: This oracle's share counts (the build's M2-5 decisions): net income / diluted EPS counts only from an EPS of
#: at least this (EPS is filed to the cent), and a filed count this many times away from it is in other units.
MIN_EPS_FOR_COUNT = 0.10
UNITS_GAP = 100.0

#: The spec's figures.
YEARS_KEPT = 11  # "The latest 11 fiscal years are used."
PREDICTABILITY_YEARS = 10
SHARE_ROUNDING = 0.005  # "did not rise ... within 0.5% (rounding)"
ALTMAN_DISTRESS, ALTMAN_SAFE = 1.81, 2.99
BENEISH_FLAG = -1.78
BENEISH_CONSTANT = -4.84
BENEISH_WEIGHTS = (("dsri", 0.920), ("gmi", 0.528), ("aqi", 0.404), ("sgi", 0.892), ("depi", 0.115),
                   ("sgai", -0.172), ("tata", 4.679), ("lvgi", -0.327))
SLOAN_SEVERE = 0.25
COVERAGE_SEVERE = 1.5
MIN_PEERS = 10
MIN_RANKS = 4
FINANCIALS = ("bank", "insurance")
NO_PIOTROSKI = FINANCIALS
NO_ALTMAN = FINANCIALS + ("reit", "utility")
NO_BENEISH = FINANCIALS + ("reit",)
NO_MAGIC = FINANCIALS + ("utility", "reit")

#: The stored JSON's names (src/scores' layouts, read for the names only).
PIOTROSKI_TESTS = ("roa_positive", "cfo_positive", "roa_up", "cfo_above_ni", "leverage_down", "current_ratio_up",
                   "no_new_shares", "gross_margin_up", "asset_turnover_up")
RANKS = ("strength", "profitability", "growth", "valuation", "momentum")
#: The spec's table: debt/revenue, price / historical value and the P/E percentile are lower-is-better.
LOWER_IS_BETTER = frozenset({"debt_to_revenue", "price_to_value", "pe_pct_rank"})
#: The spec's components, by the names the stored JSON gives them.
SPEC_COMPONENTS = {
    "strength": {"interest_coverage", "altman_z", "equity_to_assets", "cash_to_debt", "debt_to_revenue"},
    "profitability": {"operating_margin", "operating_margin_10y_mean", "operating_margin_5y_slope",
                      "piotroski_share", "predictability_stars", "roic_10y_median", "profitable_years"},
    "growth": {"rev_ps_cagr_3y", "rev_ps_cagr_5y", "ebitda_ps_cagr_5y", "eps_cagr_5y"},
    "valuation": {"price_to_value", "pe_pct_rank"},
    "momentum": {"momentum"},
}
#: Milestone 5's industries (docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and
#: industries"): the scored companies a SIC prefix needs to be a group, and the members with a component its industry
#: percentile needs; the descriptions a group's label names; the stored columns.
MIN_INDUSTRY = 10
LABEL_TOP = 3
INDUSTRY_GROUP_COLUMNS = ("industry_group", "industry_label", "industry_size")
INDUSTRY_RANK_COLUMNS = tuple(f"ind_rank_{r}" for r in RANKS) + ("ind_composite",)
SERIES = ("revenue_ps", "eps", "ebitda_ps", "fcf_ps", "book_ps")
#: The total each per-share series divides by the year's share count (EPS is the filed EPS instead).
SERIES_TOTAL = {"revenue_ps": ("fa", "revenue"), "ebitda_ps": ("ma", "ebitda"), "fcf_ps": ("ma", "free_cash_flow"),
                "book_ps": ("fa", "total_equity")}
CAGR_SERIES = {"rev_ps": "revenue_ps", "eps": "eps", "ebitda_ps": "ebitda_ps", "fcf_ps": "fcf_ps",
               "book_ps": "book_ps"}

FA = ("revenue", "cost_of_revenue", "gross_profit", "operating_income", "sga_expense", "interest_expense",
      "net_income", "eps_diluted", "total_assets", "current_assets", "current_liabilities", "total_liabilities",
      "long_term_debt", "accounts_receivable", "ppe_net", "retained_earnings", "total_equity", "operating_cash_flow",
      "investing_cash_flow", "depreciation_amortization", "weighted_avg_shares_diluted", "shares_outstanding")
MA = ("ebitda", "ebit", "free_cash_flow", "interest_coverage")
#: Vintage columns, by position in the rows loaded below.
V_FILED, V_ACCN, V_EPS, V_DILUTED, V_OUTSTANDING, V_NI = range(6)


def share_split(ratio: float) -> bool:
    """A share split's ratio is a simple fraction (2:1, 3:2, 1:8); spin-off price factors aren't (1.281)."""
    if not ratio or ratio <= 0 or abs(ratio - 1) < 1e-9:
        return False
    return any(abs(float(Fraction(r).limit_denominator(4)) - r) <= 1e-9 * r for r in (ratio, 1 / ratio))


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
        return "pass" if x else "fail"
    if isinstance(x, (int, float)):
        if isinstance(x, float) and abs(x) >= 1e6:
            return f"{x / 1e6:,.3f}M"
        return f"{x:.6g}"
    return str(x)


def div(a: Num, b: Num) -> Num:
    """a / b, or None when either is missing or b is zero."""
    return None if a is None or b is None or b == 0 else a / b


def above(a: Num, b: Num) -> Optional[bool]:
    """a > b (strict), or None when either is missing."""
    return None if a is None or b is None else a > b


def same(mine: Num, stored: Num, tol: float, relative: bool) -> bool:
    """Both N/A, or both valued within *tol* (of the larger magnitude when *relative*)."""
    if mine is None or stored is None:
        return mine is None and stored is None
    return abs(mine - stored) <= (tol * max(abs(mine), abs(stored)) if relative else tol)


def share(n: int, d: int) -> str:
    return f"{n}/{d}" if d else "0/0"


def rows(db: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    cur = db.execute(sql, tuple(params))
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------------------------------------------
# Loading


class Data:
    """The raw tables the oracle reads, and the code's stored outputs."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.sector: Dict[str, str] = {t: s or "general" for t, s in db.execute(
            "SELECT ticker, sector_class FROM companies")}
        # The SEC's SIC codes and descriptions (Milestone 5); none from a companies table before them.
        self.sic: Dict[str, Optional[str]] = {}
        self.sic_description: Dict[str, Optional[str]] = {}
        if {"sic", "sic_description"} <= {r[1] for r in db.execute("PRAGMA table_info(companies)")}:
            for t, code, description in db.execute("SELECT ticker, sic, sic_description FROM companies"):
                self.sic[t], self.sic_description[t] = code, description
        self.fa: Dict[str, Dict[int, Dict[str, Num]]] = {}
        self.period_end: Dict[Tuple[str, int], Optional[str]] = {}
        for r in db.execute(f"SELECT ticker, fiscal_year, substr(period_end, 1, 10), {', '.join(FA)} "
                            "FROM financials_annual WHERE fiscal_year IS NOT NULL"):
            self.fa.setdefault(r[0], {})[int(r[1])] = dict(zip(FA, r[3:]))
            self.period_end[(r[0], int(r[1]))] = r[2]
        # A fiscal year ending before this day (the first price bar + 1 year) is before the listing or in its
        # year: its count is on a pre-listing basis.
        self.listed: Dict[str, str] = {}
        for t, first in db.execute("SELECT ticker, MIN(substr(date, 1, 10)) FROM price_bars GROUP BY ticker"):
            if first:
                day = date.fromisoformat(first)
                self.listed[t] = (day.replace(year=day.year + 1) if (day.month, day.day) != (2, 29)
                                  else date(day.year + 1, 2, 28)).isoformat()
        self.ma: Dict[str, Dict[int, Dict[str, Num]]] = {}
        for r in db.execute(f"SELECT ticker, fiscal_year, {', '.join(MA)} FROM metrics_annual "
                            "WHERE fiscal_year IS NOT NULL"):
            self.ma.setdefault(r[0], {})[int(r[1])] = dict(zip(MA, r[2:]))
        # Every filing's view of each fiscal year, oldest first (same-day filings by accession).
        self.vintages: Dict[Tuple[str, int], List[Tuple]] = {}
        for r in db.execute("SELECT ticker, fiscal_year, substr(filed_date, 1, 10), accn, eps_diluted, "
                            "weighted_avg_shares_diluted, shares_outstanding, net_income "
                            "FROM financials_annual_vintages WHERE fiscal_year IS NOT NULL AND filed_date IS NOT NULL "
                            "ORDER BY ticker, fiscal_year, filed_date, accn"):
            self.vintages.setdefault((r[0], int(r[1])), []).append(r[2:])
        self.splits: Dict[str, List[Tuple[str, float]]] = {}
        for t, d, r in db.execute("SELECT ticker, substr(date, 1, 10), ratio FROM split_events WHERE ratio > 0 "
                                  "ORDER BY ticker, date"):
            self.splits.setdefault(t, []).append((d, float(r)))
        self.snap: Dict[str, Dict[str, Any]] = {r["ticker"]: r for r in rows(
            db, "SELECT m.ticker, m.collected_at, m.current_price, m.market_cap, m.beta FROM market_snapshots m "
            "JOIN (SELECT ticker, MAX(collected_at) c FROM market_snapshots GROUP BY ticker) x "
            "ON x.ticker = m.ticker AND x.c = m.collected_at")}
        # The code's outputs.
        self.qs: Dict[str, Dict[str, Any]] = {r["ticker"]: r for r in rows(db, "SELECT * FROM quality_scores")}
        self.qs_columns = [r[1] for r in db.execute("PRAGMA table_info(quality_scores)")]
        self.ps: Dict[Tuple[str, int], Dict[str, Num]] = {}
        for r in rows(db, "SELECT * FROM per_share_annual"):
            self.ps[(r["ticker"], int(r["fiscal_year"]))] = {k: r[k] for k in SERIES}
        self.signs = rows(db, "SELECT * FROM warning_signs ORDER BY ticker, code")
        self.tickers = sorted(set(self.sector) | set(self.qs))

    def stored_json(self, t: str, column: str) -> Dict[str, Any]:
        raw = (self.qs.get(t) or {}).get(column)
        return json.loads(raw) if raw else {}

    def pre_listing(self, t: str, y: int) -> bool:
        """Whether fiscal year *y* ends before the first price bar + 1 year (no bars or no year end: no)."""
        end, cutoff = self.period_end.get((t, y)), self.listed.get(t)
        return bool(end and cutoff and end < cutoff)


# ---------------------------------------------------------------------------------------------------------------
# This oracle's figures


def latest_reported(vintages: List[Tuple], i: int) -> Optional[Tuple[float, str]]:
    """Column *i*'s value in the latest filing that reports it, and that filing's date."""
    for v in reversed(vintages):
        if v[i] is not None:
            return float(v[i]), v[V_FILED]
    return None


def splits_after(splits: List[Tuple[str, float]], filed: str) -> float:
    """The product of the declared share splits dated after *filed*: what that filing's basis is from today's."""
    return math.prod(r for d, r in splits if share_split(r) and d > filed)


def eps_count(v: Tuple) -> Num:
    """One filing's net income / diluted EPS for the year (|EPS| of at least MIN_EPS_FOR_COUNT), or None."""
    ni, eps = v[V_NI], v[V_EPS]
    if ni is None or eps is None or abs(eps) < MIN_EPS_FOR_COUNT:
        return None
    count = ni / eps
    return count if count > 0 else None


def year_count(vintages: List[Tuple]) -> Optional[Tuple[float, str, Num, Tuple]]:
    """The year's share count: (count, where it came from, that filing's NI / diluted EPS, the filing).

    The diluted weighted count from the latest filing that gives one, unless it is more than UNITS_GAP times away
    from the same filing's net income / diluted EPS (a units error), which is used instead; without any, net
    income / diluted EPS from the latest filing that gives both; then the latest filing's shares outstanding.
    The preference runs across the filings (later filings often repeat an old year's balance-sheet share figure
    alone), while a quotient and its guard stay within one filing, on its split basis."""
    for v in reversed(vintages):
        if v[V_DILUTED] is not None and v[V_DILUTED] > 0:
            ref = eps_count(v)
            if ref is not None and abs(math.log(v[V_DILUTED] / ref)) > math.log(UNITS_GAP):
                return ref, f"NI / diluted EPS (the diluted count {fmt(float(v[V_DILUTED]))} is in other units)", ref, v
            return float(v[V_DILUTED]), "diluted weighted", ref, v
    for v in reversed(vintages):
        ref = eps_count(v)
        if ref is not None:
            return ref, "NI / diluted EPS", ref, v
    for v in reversed(vintages):
        if v[V_OUTSTANDING] is not None and v[V_OUTSTANDING] > 0:
            return float(v[V_OUTSTANDING]), "outstanding", None, v
    return None


def basis(vintages: List[Tuple], splits: List[Tuple[str, float]]) -> Dict[str, Any]:
    """One fiscal year's share count and diluted EPS on today's split basis, from its latest filings."""
    out: Dict[str, Any] = {"count": None, "source": None, "filed": None, "factor": None, "shares": None,
                           "eps_count": None, "eps_filed_value": None, "eps_filed": None, "eps_factor": None,
                           "eps": None}
    hit = year_count(vintages)
    if hit is not None:
        count, source, ref, v = hit
        factor = splits_after(splits, v[V_FILED])
        out.update(count=count, source=source, eps_count=ref, filed=v[V_FILED], factor=factor, shares=count * factor)
    eps = latest_reported(vintages, V_EPS)
    if eps is not None:
        factor = splits_after(splits, eps[1])
        out.update(eps_filed_value=eps[0], eps_filed=eps[1], eps_factor=factor, eps=eps[0] / factor)
    return out


def gross_margin(f: Dict[str, Num]) -> Num:
    """Gross profit / revenue; revenue - cost of revenue when gross profit isn't filed (a reading)."""
    gp = f.get("gross_profit")
    if gp is None and f.get("revenue") is not None and f.get("cost_of_revenue") is not None:
        gp = f["revenue"] - f["cost_of_revenue"]  # type: ignore[operator]
    return div(gp, f.get("revenue"))


def piotroski(c: Dict[str, Num], p: Dict[str, Num], shares: Num, shares_prev: Num) -> Dict[str, Optional[bool]]:
    """The spec's nine tests on year t (*c*) against t-1 (*p*): pass, fail, or None when an input is missing.
    Every ratio is on the same year's total assets, and "rose"/"fell" are strict (readings)."""
    roa, roa_prev = div(c["net_income"], c["total_assets"]), div(p["net_income"], p["total_assets"])
    cfo = c["operating_cash_flow"]
    return {
        "roa_positive": None if roa is None else roa > 0,
        "cfo_positive": None if cfo is None else cfo > 0,
        "roa_up": above(roa, roa_prev),
        "cfo_above_ni": above(cfo, c["net_income"]),
        # LTD / TA fell: last year's ratio above this year's. A missing LTD leaves the test untested (a reading).
        "leverage_down": above(div(p["long_term_debt"], p["total_assets"]),
                               div(c["long_term_debt"], c["total_assets"])),
        "current_ratio_up": above(div(c["current_assets"], c["current_liabilities"]),
                                  div(p["current_assets"], p["current_liabilities"])),
        # The split-normalized counts are this oracle's own (see basis()).
        "no_new_shares": (None if not shares or not shares_prev or shares <= 0 or shares_prev <= 0
                          else shares <= shares_prev * (1 + SHARE_ROUNDING)),
        "gross_margin_up": above(gross_margin(c), gross_margin(p)),
        "asset_turnover_up": above(div(c["revenue"], c["total_assets"]), div(p["revenue"], p["total_assets"])),
    }


def piotroski_inputs(c: Dict[str, Num], p: Dict[str, Num], shares: Num, shares_prev: Num) -> Dict[str, str]:
    """Each test's inputs at t and t-1, for the report."""
    def show(*keys: str) -> str:
        return ", ".join(f"{k} {fmt(c[k])} / {fmt(p[k])}" for k in keys)
    return {
        "roa_positive": show("net_income", "total_assets"),
        "cfo_positive": show("operating_cash_flow"),
        "roa_up": show("net_income", "total_assets"),
        "cfo_above_ni": show("operating_cash_flow", "net_income"),
        "leverage_down": show("long_term_debt", "total_assets"),
        "current_ratio_up": show("current_assets", "current_liabilities"),
        "no_new_shares": (f"oracle shares {fmt(shares)} / {fmt(shares_prev)}; financials_annual diluted "
                          f"{fmt(c['weighted_avg_shares_diluted'])} / {fmt(p['weighted_avg_shares_diluted'])}, "
                          f"outstanding {fmt(c['shares_outstanding'])} / {fmt(p['shares_outstanding'])}"),
        "gross_margin_up": show("gross_profit", "revenue", "cost_of_revenue"),
        "asset_turnover_up": show("revenue", "total_assets"),
    }


def altman(c: Dict[str, Num], mve: Num) -> Optional[Tuple[float, str, Dict[str, float]]]:
    """The original Altman Z, its zone and its five terms, or None when an input is missing (TL as filed)."""
    need = ("total_assets", "total_liabilities", "current_assets", "current_liabilities", "retained_earnings",
            "operating_income", "revenue")
    if mve is None or any(c[k] is None for k in need) or not c["total_assets"] or not c["total_liabilities"]:
        return None
    ta, tl = float(c["total_assets"]), float(c["total_liabilities"])  # type: ignore[arg-type]
    terms = {
        "1.2*WC/TA": 1.2 * (c["current_assets"] - c["current_liabilities"]) / ta,  # type: ignore[operator]
        "1.4*RE/TA": 1.4 * c["retained_earnings"] / ta,  # type: ignore[operator]
        "3.3*EBIT/TA": 3.3 * c["operating_income"] / ta,  # type: ignore[operator]
        "0.6*MVE/TL": 0.6 * mve / tl,
        "1.0*Sales/TA": 1.0 * c["revenue"] / ta,  # type: ignore[operator]
    }
    z = sum(terms.values())
    zone = "distress" if z < ALTMAN_DISTRESS else "safe" if z > ALTMAN_SAFE else "grey"
    return z, zone, terms


def beneish(c: Dict[str, Num], p: Dict[str, Num]) -> Tuple[Num, Dict[str, Num], List[str], List[str]]:
    """Beneish's M, its eight variables, the ones set to a neutral 1 (SGAI, DEPI) and the ones missing."""
    def receivables(f: Dict[str, Num]) -> Num:
        return div(f["accounts_receivable"], f["revenue"])

    def soft_assets(f: Dict[str, Num]) -> Num:  # 1 - (CA + PPE) / TA
        if f["current_assets"] is None or f["ppe_net"] is None or not f["total_assets"]:
            return None
        return 1.0 - (f["current_assets"] + f["ppe_net"]) / f["total_assets"]

    def depreciation(f: Dict[str, Num]) -> Num:  # D&A / (D&A + PPE)
        if f["depreciation_amortization"] is None or f["ppe_net"] is None:
            return None
        return div(f["depreciation_amortization"], f["depreciation_amortization"] + f["ppe_net"])

    def sga(f: Dict[str, Num]) -> Num:
        return div(f["sga_expense"], f["revenue"])

    def leverage(f: Dict[str, Num]) -> Num:  # (CL + LTD) / TA
        # A missing long-term debt reads as zero here, the build's decision on the spec (M2-5): debt-free filers
        # tag none (ANET, ISRG, PANW, PLTR). Piotroski's leverage test still goes untested without it.
        if f["current_liabilities"] is None:
            return None
        return div(f["current_liabilities"] + (f["long_term_debt"] or 0.0), f["total_assets"])

    ni, cfo = c["net_income"], c["operating_cash_flow"]
    v: Dict[str, Num] = {
        "dsri": div(receivables(c), receivables(p)),
        "gmi": div(gross_margin(p), gross_margin(c)),
        "aqi": div(soft_assets(c), soft_assets(p)),
        "sgi": div(c["revenue"], p["revenue"]),
        "depi": div(depreciation(p), depreciation(c)),
        "sgai": div(sga(c), sga(p)),
        "tata": None if ni is None or cfo is None else div(ni - cfo, c["total_assets"]),
        "lvgi": div(leverage(c), leverage(p)),
    }
    neutral = [k for k in ("sgai", "depi") if v[k] is None]
    for k in neutral:
        v[k] = 1.0
    missing = [k for k, x in v.items() if x is None]
    m = None if missing else BENEISH_CONSTANT + sum(w * v[k] for k, w in BENEISH_WEIGHTS)  # type: ignore[operator]
    return m, v, neutral, missing


def beneish_inputs(c: Dict[str, Num], p: Dict[str, Num]) -> str:
    keys = ("accounts_receivable", "revenue", "gross_profit", "cost_of_revenue", "current_assets", "ppe_net",
            "total_assets", "depreciation_amortization", "sga_expense", "current_liabilities", "long_term_debt",
            "net_income", "operating_cash_flow")
    return "; ".join(f"{k} {fmt(c[k])} / {fmt(p[k])}" for k in keys)


def sloan(c: Dict[str, Num]) -> Num:
    """(NI - CFO - CFI) / TA."""
    ni, cfo, cfi = c["net_income"], c["operating_cash_flow"], c["investing_cash_flow"]
    if ni is None or cfo is None or cfi is None:
        return None
    return div(ni - cfo - cfi, c["total_assets"])


def log_fit(points: List[Tuple[int, float]]) -> Tuple[float, Num]:
    """Least squares of ln(value) on the fiscal year: (slope, R-squared as 1 - SSres / SStot; None if flat)."""
    xs = [float(x) for x, _ in points]
    ys = [math.log(v) for _, v in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    ss_tot = sum((y - my) ** 2 for y in ys)
    if ss_tot == 0:
        return slope, None
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    return slope, 1.0 - ss_res / ss_tot


def predictability(ps: Dict[int, Dict[str, Num]], t: int, financial: bool
                   ) -> Tuple[Num, Dict[str, float], Dict[str, float], str]:
    """(stars, R-squared by series when fitted, slopes, why) over fiscal years t-9..t (all ten needed)."""
    window = list(range(t - PREDICTABILITY_YEARS + 1, t + 1))
    second = ("eps", "eps") if financial else ("ebitda", "ebitda_ps")
    series: Dict[str, List[Tuple[int, float]]] = {}
    for name, column in (("revenue", "revenue_ps"), second):
        points = [(y, (ps.get(y) or {}).get(column)) for y in window]
        if any(v is None for _, v in points):
            return None, {}, {}, f"{name}: not all of FY{window[0]}-FY{t} valued"
        series[name] = points  # type: ignore[assignment]
    if any(v <= 0 for _, v in series[second[0]]):
        return 1.0, {}, {}, f"a year with {second[0]} <= 0"
    if any(v <= 0 for _, v in series["revenue"]):
        return 1.0, {}, {}, "a year with revenue/share <= 0"
    r2: Dict[str, float] = {}
    slopes: Dict[str, float] = {}
    for name, points in series.items():
        slopes[name], fitted = log_fit(points)
        if fitted is not None:
            r2[name] = fitted
    if any(s <= 0 for s in slopes.values()):
        return 1.0, r2, slopes, "a slope <= 0"
    if len(r2) < 2:
        return None, r2, slopes, "a flat series has no R-squared"
    return math.floor(2.0 * (1.0 + 4.0 * min(r2.values()))) / 2.0, r2, slopes, "fit"


class Oracle:
    """This oracle's figures for one company."""

    def __init__(self, d: Data, t: str) -> None:
        self.t = t
        self.sector = d.sector.get(t, "general")
        fa = d.fa.get(t, {})
        self.fy: Optional[int] = max(fa) if fa else None
        # "The latest 11 fiscal years": the labels t-10..t that are stored.
        self.years = [y for y in sorted(fa) if self.fy is not None and y > self.fy - YEARS_KEPT]
        # Each kept year's count, and its neighbours' (the units guard's last reference).
        around = sorted(set(self.years) | {y + k for y in self.years for k in (-1, 1)})
        self.basis: Dict[int, Dict[str, Any]] = {y: basis(d.vintages.get((t, y), []), d.splits.get(t, []))
                                                 for y in around}
        #: The counts the units guard replaced, for the report.
        self.units: List[str] = []
        unchecked = [y for y in around if self._check_with_annual(d, y)]
        for y in self.years:
            if y in unchecked:
                self._check_with_neighbours(y)
            if "in other units" in str(self.basis[y]["source"]) and str(self.basis[y]["source"]).startswith("NI"):
                b = self.basis[y]
                self.units.append(f"{t} FY{y}: {b['source']}, filed {b['filed']}: used that filing's NI / diluted "
                                  f"EPS, {fmt(b['count'])}")
        self.ps: Dict[int, Dict[str, Num]] = {}
        for y in self.years:
            b = self.basis[y]
            figures = {"fa": fa[y], "ma": d.ma.get(t, {}).get(y) or {}}
            self.ps[y] = {"eps": b["eps"]}
            for name, (table, column) in SERIES_TOTAL.items():
                total = figures[table].get(column)
                self.ps[y][name] = None if total is None or not b["shares"] else total / b["shares"]
        self.cur = fa.get(self.fy) if self.fy is not None else None
        self.prev = fa.get(self.fy - 1) if self.fy is not None else None  # t-1 exactly (a reading)

    def shares(self, y: Optional[int]) -> Num:
        return (self.basis.get(y) or {}).get("shares") if y is not None else None

    def _check_with_annual(self, d: Data, y: int) -> bool:
        """The units guard's second reference, for a count its own filing can't check (no NI / diluted EPS): the
        same fiscal year's count in financials_annual (the latest parse of the raw filings, not an M2 output),
        its diluted weighted count else its shares outstanding. A count more than UNITS_GAP times away from it is
        a units error, and the reference is used, on the filing's split basis (the same figure, in its units).
        Returns True when there's no such reference, for the neighbours to check."""
        b = self.basis[y]
        if b["count"] is None or b["eps_count"] is not None:
            return False
        f = d.fa.get(self.t, {}).get(y) or {}
        ref = next((float(f[k]) for k in ("weighted_avg_shares_diluted", "shares_outstanding")
                    if f.get(k) is not None and f[k] > 0), None)
        if ref is None:
            return True
        if abs(math.log(b["count"] / ref)) > math.log(UNITS_GAP):
            self.units.append(f"{self.t} FY{y}: the {b['source']} count {fmt(b['count'])} (filed {b['filed']}) is "
                              f"x{b['count'] / ref:.4g} financials_annual's {fmt(ref)}: used financials_annual's")
            b.update(count=ref, source=f"financials_annual (the filed {b['source']} count is in other units)",
                     shares=ref * b["factor"])
        return False

    def _check_with_neighbours(self, y: int) -> None:
        """The guard's last reference, without one in financials_annual: the neighbouring years' counts on today's
        basis (their geometric mean when both exist), used as the year's count when it's UNITS_GAP times away."""
        b = self.basis[y]
        near = [self.basis[n]["shares"] for n in (y - 1, y + 1) if (self.basis.get(n) or {}).get("shares")]
        if not near or not b["shares"]:
            return
        ref = math.exp(statistics.fmean(math.log(s) for s in near))
        if abs(math.log(b["shares"] / ref)) > math.log(UNITS_GAP):
            self.units.append(f"{self.t} FY{y}: the {b['source']} count {fmt(b['count'])} (filed {b['filed']}) is "
                              f"x{b['shares'] / ref:.4g} the neighbouring years' {fmt(ref)} (today's basis): "
                              "used theirs")
            b.update(count=ref / b["factor"], shares=ref,
                     source=f"neighbouring years (the filed {b['source']} count is in other units)")


# ---------------------------------------------------------------------------------------------------------------
# The gates


def check_fiscal_year(d: Data, mine: Dict[str, Oracle], failures: List[str]) -> None:
    print("== the scored fiscal year")
    off = [t for t in d.tickers if (d.qs.get(t) or {}).get("fiscal_year") != mine[t].fy]
    gate("scored fiscal year is the latest in financials_annual", not off,
         f"agree {share(len(d.tickers) - len(off), len(d.tickers))}", failures)
    for t in off:
        print(f"    {t}: stored FY{(d.qs.get(t) or {}).get('fiscal_year')} oracle FY{mine[t].fy}")
    missing = [t for t in d.tickers if t not in d.qs]
    if missing:
        print(f"    companies without a quality_scores row: {', '.join(missing)}")


def diagnose(factor: float, b: Optional[Dict[str, Any]], splits: List[Tuple[str, float]]) -> str:
    """Why the code's count can be *factor* times this oracle's (stored per-share = oracle's / factor); *b* is the
    oracle's basis for the year (None when only EPS disagrees)."""
    source = str((b or {}).get("source") or "")
    if any(abs(math.log(factor / u)) < 0.01 for u in (1000.0, 0.001)):
        why = "units: one count is in thousands"
        if b is not None:
            why += f" (the oracle's {source} count of the filing of {b.get('filed')} passed the units guard)"
        return why
    for when, ratio in splits:
        if not share_split(ratio) and abs(math.log(factor / ratio)) < 0.005:
            return f"the code applied the declared price factor {ratio:g} ({when}), which is not a share split"
    if source == "outstanding":
        return "the oracle's count is the shares outstanding (no filing gives a weighted count or NI / diluted EPS)"
    if source.startswith("NI / diluted EPS"):
        return f"the oracle's count is the {source} of the filing of {(b or {}).get('filed')}"
    return "no declared share split explains it: the code folded a share-count jump"


def check_per_share(d: Data, mine: Dict[str, Oracle], failures: List[str]) -> set:
    """The gate; returns the (ticker, fiscal year, series) that disagree, for the gates built on them."""
    print("== per_share_annual: the five per-share series on today's split basis (this oracle's own)")
    differ = set()
    checked = agreed = 0
    lines: List[str] = []
    by_series: Counter = Counter()
    gaps: List[float] = []
    inexact: Dict[str, set] = {}
    #: (ticker, implied factor rounded) -> (fiscal years, the oracle's basis of a count-based series that differs)
    causes: Dict[Tuple[str, float], Tuple[List[int], Optional[Dict[str, Any]]]] = {}
    skipped: Dict[str, List[int]] = {}
    for t in d.tickers:
        o = mine[t]
        stored_years = sorted(y for (s, y) in d.ps if s == t)
        for y in sorted(set(o.years) | set(stored_years)):
            if d.pre_listing(t, y):
                skipped.setdefault(t, []).append(y)
                continue
            if y not in o.years or (t, y) not in d.ps:
                checked += 1
                by_series["row"] += 1
                lines.append(f"    {t} FY{y}: row {'only stored' if y not in o.years else 'not stored'}")
                continue
            b, fa = o.basis[y], d.fa[t][y]
            ma = d.ma.get(t, {}).get(y) or {}
            for name in SERIES:
                m, s = o.ps[y][name], d.ps[(t, y)][name]
                checked += 1
                if same(m, s, PER_SHARE_TOL, True):
                    agreed += 1
                    if m is not None and s is not None and m != 0 and s != 0:
                        gaps.append(abs(m - s) / max(abs(m), abs(s)))
                        if gaps[-1] > 1e-6:
                            inexact.setdefault(t, set()).add(y)
                    continue
                by_series[name] += 1
                differ.add((t, y, name))
                gap = f" (x{m / s:.4f})" if m is not None and s else ""
                if m is not None and s:
                    years, seen = causes.get((t, round(m / s, 4)), ([], None))
                    causes[(t, round(m / s, 4))] = (sorted(set(years) | {y}), seen or (b if name != "eps" else None))
                if name == "eps":
                    how = (f"filed EPS {fmt(b['eps_filed_value'])} ({b['eps_filed']}) / {fmt(b['eps_factor'])}; "
                           f"financials_annual EPS {fmt(fa['eps_diluted'])}")
                else:
                    table, column = SERIES_TOTAL[name]
                    total = (fa if table == "fa" else ma).get(column)
                    implied = f"; stored implies count {fmt(total / s)}" if total is not None and s else ""
                    how = (f"{column} {fmt(total)} / count {fmt(b['count'])} ({b['source']}, filed {b['filed']}) "
                           f"x{fmt(b['factor'])} = {fmt(b['shares'])}{implied}; financials_annual diluted "
                           f"{fmt(fa['weighted_avg_shares_diluted'])}, outstanding {fmt(fa['shares_outstanding'])}")
                lines.append(f"    {t} FY{y} {name}: stored {fmt(s)} oracle {fmt(m)}{gap}; {how}")
    gate("per_share_annual within 2%", checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)}; disagreements by series: "
         f"{', '.join(f'{k} {v}' for k, v in sorted(by_series.items())) or 'none'}", failures)
    for line in lines:
        print(line)
    print("    the disagreements by cause (oracle / stored per-share = the code's count / the oracle's):"
          + ("" if causes else " none"))
    for (t, factor), (years, b) in sorted(causes.items()):
        why = diagnose(factor, b, d.splits.get(t, []))
        print(f"        {t} FY{', FY'.join(map(str, years))}: x{factor:g}: {why}")
    replaced = [line for t in d.tickers for line in mine[t].units]
    print(f"    units guard: {len(replaced)} counts more than {UNITS_GAP:g}x from their reference, replaced"
          + (": " + "; ".join(replaced) if replaced else ""))
    print(f"    not compared: pre-listing basis: {len(SERIES) * sum(len(ys) for ys in skipped.values())} values, "
          f"{sum(len(ys) for ys in skipped.values())} fiscal years ending before the first price bar + 1 year: "
          + "; ".join(f"{t} FY{', FY'.join(map(str, ys))} (first bar + 1 year {d.listed[t]})"
                      for t, ys in sorted(skipped.items())))
    if gaps:
        print("    agreeing values' relative gaps: " + ", ".join(
            f"<= {label}: {sum(1 for g in gaps if g <= tol)}"
            for label, tol in (("1e-6", 1e-6), ("0.1%", 0.001), ("0.5%", 0.005), ("1%", 0.01), ("2%", 0.02)))
            + f" (of {len(gaps)} valued pairs)")
        print("    agreeing within 2% but not exactly: " + ("; ".join(
            f"{t} FY{', FY'.join(map(str, sorted(ys)))}" for t, ys in sorted(inexact.items())) or "none"))
    return differ


def downstream(differ: set, d: Data, t: str, years: Sequence[int], series: Sequence[str]) -> str:
    """The years among *years* where a per-share series these figures use already disagrees, or wasn't compared
    (pre-listing)."""
    hit = sorted({y for y in years for s in series if (t, y, s) in differ})
    early = sorted(y for y in years if d.pre_listing(t, y))
    out = f"; per-share disagrees at FY{', FY'.join(map(str, hit))}" if hit else "; per-share agrees"
    return out + (f" (pre-listing, not compared: FY{', FY'.join(map(str, early))})" if early else "")


def check_cagrs(d: Data, mine: Dict[str, Oracle], differ: set, failures: List[str]) -> None:
    print("== CAGRs: (last / first) ** (1 / years) - 1, both ends positive, the start exactly `years` back")
    columns = [c for c in d.qs_columns if re.fullmatch(r"[a-z_]+_cagr_\d+y", c)]
    checked = agreed = 0
    lines = []
    skipped: Dict[str, List[str]] = {}
    for t in d.tickers:
        o = mine[t]
        for col in columns:
            m_ = re.fullmatch(r"([a-z_]+)_cagr_(\d+)y", col)
            assert m_ is not None
            series, years = CAGR_SERIES[m_.group(1)], int(m_.group(2))
            if o.fy is not None and (d.pre_listing(t, o.fy - years) or d.pre_listing(t, o.fy)):
                skipped.setdefault(t, []).append(col)
                continue
            mine_v: Num = None
            last = first = None
            if o.fy is not None:
                last = (o.ps.get(o.fy) or {}).get(series)
                first = (o.ps.get(o.fy - years) or {}).get(series)
                if last is not None and first is not None and last > 0 and first > 0:
                    mine_v = (last / first) ** (1.0 / years) - 1.0
            stored = (d.qs.get(t) or {}).get(col)
            checked += 1
            if same(mine_v, stored, CAGR_TOL, False):
                agreed += 1
            else:
                lines.append(f"    {t} {col}: stored {fmt(stored)} oracle {fmt(mine_v)}; {series} "
                             f"FY{o.fy} {fmt(last)}, FY{(o.fy or 0) - years} {fmt(first)}"
                             + downstream(differ, d, t, ((o.fy or 0) - years, o.fy or 0), (series,)))
    gate("CAGRs within 0.5 percentage points", checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)} over {len(columns)} columns ({', '.join(columns)})", failures)
    for line in lines:
        print(line)
    print(f"    not compared: pre-listing basis: {sum(len(c) for c in skipped.values())} CAGRs starting in a fiscal "
          f"year that ends before the first price bar + 1 year: "
          + "; ".join(f"{t} {', '.join(cols)}" for t, cols in sorted(skipped.items())))


def check_piotroski(d: Data, mine: Dict[str, Oracle], failures: List[str]) -> None:
    print("== Piotroski F: nine tests on FY t against FY t-1; N/A for banks and insurers")
    checked = agreed = 0
    lines = []
    for t in d.tickers:
        o, q = mine[t], d.qs.get(t) or {}
        stored_tests = d.stored_json(t, "piotroski_tests")
        tests: Dict[str, Optional[bool]] = {}
        f = tested = None
        why = ""
        if o.sector in NO_PIOTROSKI:
            why = f"N/A for {o.sector}"
        elif o.cur is None or o.prev is None:
            why = f"N/A: FY{o.fy} or FY{(o.fy or 0) - 1} not stored"
        else:
            tests = piotroski(o.cur, o.prev, o.shares(o.fy), o.shares(o.fy - 1))  # type: ignore[operator]
            f, tested = sum(1 for v in tests.values() if v), sum(1 for v in tests.values() if v is not None)
        bad = []
        for name in PIOTROSKI_TESTS:
            checked += 1
            if tests.get(name) is stored_tests.get(name):  # pass, fail or untested (None) on both sides
                agreed += 1
            else:
                bad.append(name)
        for name, mine_v, stored_v in (("piotroski_f", f, q.get("piotroski_f")),
                                       ("piotroski_tested", tested, q.get("piotroski_tested"))):
            checked += 1
            if mine_v == stored_v:
                agreed += 1
            else:
                bad.append(name)
        if bad:
            inputs = (piotroski_inputs(o.cur, o.prev, o.shares(o.fy), o.shares(o.fy - 1))  # type: ignore[operator]
                      if tests else {})
            lines.append(f"    {t} ({o.sector}, FY{o.fy}): stored F {fmt(q.get('piotroski_f'))}/"
                         f"{fmt(q.get('piotroski_tested'))}, oracle F {fmt(f)}/{fmt(tested)} {why}")
            for name in bad:
                if name in PIOTROSKI_TESTS:
                    lines.append(f"        {name}: stored {fmt(stored_tests.get(name))} oracle {fmt(tests.get(name))}"
                                 f"; t / t-1: {inputs.get(name, '')}")
    gate("Piotroski tests, F and tested", checked > 0 and agreed == checked, f"agree {share(agreed, checked)}",
         failures)
    for line in lines:
        print(line)


def check_altman(d: Data, mine: Dict[str, Oracle], failures: List[str]) -> None:
    print("== Altman Z (original): MVE the latest snapshot's market cap; N/A for banks, insurers, REITs, utilities")
    checked = agreed = 0
    lines = []
    for t in d.tickers:
        o, q = mine[t], d.qs.get(t) or {}
        mve = (d.snap.get(t) or {}).get("market_cap")
        result = None if o.sector in NO_ALTMAN or o.cur is None else altman(o.cur, mve)
        z, zone = (result[0], result[1]) if result else (None, None)
        checked += 1
        if same(z, q.get("altman_z"), ALTMAN_TOL, True) and zone == q.get("altman_zone"):
            agreed += 1
            continue
        inputs = (", ".join(f"{k} {fmt(o.cur[k])}" for k in ("current_assets", "current_liabilities",
                                                             "retained_earnings", "operating_income", "revenue",
                                                             "total_assets", "total_liabilities"))
                  if o.cur else "no FY row")
        terms = ", ".join(f"{k} {v:.4f}" for k, v in result[2].items()) if result else ""
        lines.append(f"    {t} ({o.sector}, FY{o.fy}): stored {fmt(q.get('altman_z'))} {q.get('altman_zone')}, "
                     f"oracle {fmt(z)} {zone}; MVE {fmt(mve)}, {inputs}; {terms}; stored na: "
                     f"{d.stored_json(t, 'na_reasons').get('altman', '')}")
    gate("Altman Z within 0.5% and its zone", checked > 0 and agreed == checked, f"agree {share(agreed, checked)}",
         failures)
    for line in lines:
        print(line)


def check_beneish(d: Data, mine: Dict[str, Oracle], failures: List[str]) -> None:
    print("== Beneish M (8 variables; SGAI and DEPI 1 when their inputs are missing); N/A for banks, insurers, REITs")
    checked = agreed = 0
    lines = []
    no_ltd: List[str] = []
    for t in d.tickers:
        o, q = mine[t], d.qs.get(t) or {}
        stored_v = d.stored_json(t, "beneish_variables")
        m: Num = None
        v: Dict[str, Num] = {}
        neutral: List[str] = []
        missing: List[str] = []
        if o.sector not in NO_BENEISH and o.cur is not None and o.prev is not None:
            m, v, neutral, missing = beneish(o.cur, o.prev)
        bad = []
        checked += 1
        if same(m, q.get("beneish_m"), BENEISH_TOL, True):
            agreed += 1
        else:
            bad.append("M")
        for name, _ in BENEISH_WEIGHTS:
            checked += 1
            if same(v.get(name), stored_v.get(name), BENEISH_TOL, True):
                agreed += 1
            else:
                bad.append(name)
        if bad:
            lines.append(f"    {t} ({o.sector}, FY{o.fy}): M stored {fmt(q.get('beneish_m'))} oracle {fmt(m)}; "
                         f"disagree on {', '.join(bad)}; neutral {neutral or '-'}, missing {missing or '-'}; "
                         f"stored na: {d.stored_json(t, 'na_reasons').get('beneish', '')}")
            lines.append("        variables stored / oracle: " + ", ".join(
                f"{name} {fmt(stored_v.get(name))} / {fmt(v.get(name))}" for name, _ in BENEISH_WEIGHTS))
            if o.cur is not None and o.prev is not None:
                lines.append(f"        t / t-1: {beneish_inputs(o.cur, o.prev)}")
        if o.cur is not None and o.prev is not None and o.sector not in NO_BENEISH and None in (
                o.cur["long_term_debt"], o.prev["long_term_debt"]):
            no_ltd.append(t)
    gate("Beneish M and its variables within 0.5%", checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)}", failures)
    for line in lines:
        print(line)
    print(f"    LVGI with a missing long-term debt read as zero: {len(no_ltd)} companies ({', '.join(no_ltd)})")


def check_sloan(d: Data, mine: Dict[str, Oracle], failures: List[str]) -> None:
    print("== Sloan ratio: (NI - CFO - CFI) / TA on FY t")
    checked = agreed = 0
    lines = []
    for t in d.tickers:
        o, q = mine[t], d.qs.get(t) or {}
        s = sloan(o.cur) if o.cur else None
        checked += 1
        if same(s, q.get("sloan_ratio"), SLOAN_TOL, False):
            agreed += 1
        else:
            inputs = (", ".join(f"{k} {fmt(o.cur[k])}" for k in ("net_income", "operating_cash_flow",
                                                                 "investing_cash_flow", "total_assets"))
                      if o.cur else "no FY row")
            lines.append(f"    {t} FY{o.fy}: stored {fmt(q.get('sloan_ratio'))} oracle {fmt(s)}; {inputs}")
    gate("Sloan ratio within 0.1 percentage points", checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)}", failures)
    for line in lines:
        print(line)


def check_predictability(d: Data, mine: Dict[str, Oracle], differ: set, failures: List[str]) -> Dict[str, Num]:
    print("== predictability: 1-5 stars from FY t-9..t (EPS for EBITDA at banks and insurers)")
    checked = agreed = 0
    lines = []
    stars_of: Dict[str, Num] = {}
    near = []
    for t in d.tickers:
        o, q = mine[t], d.qs.get(t) or {}
        stars: Num = None
        r2: Dict[str, float] = {}
        slopes: Dict[str, float] = {}
        why = "no fiscal years"
        if o.fy is not None:
            stars, r2, slopes, why = predictability(o.ps, o.fy, o.sector in FINANCIALS)
        stars_of[t] = stars
        stored_r2 = d.stored_json(t, "predictability_r2")
        bad = []
        checked += 1
        if stars == q.get("predictability_stars"):
            agreed += 1
        else:
            bad.append("stars")
        if r2:
            for name, value in r2.items():
                checked += 1
                if same(value, stored_r2.get(name), R2_TOL, False):
                    agreed += 1
                else:
                    bad.append(f"R2 {name}")
            if why == "fit":
                edge = 2.0 * (1.0 + 4.0 * min(r2.values()))
                if abs(edge - round(edge)) < 1e-6:
                    near.append(f"{t} ({edge / 2:.8f})")
        if bad:
            window = range(o.fy - PREDICTABILITY_YEARS + 1, o.fy + 1) if o.fy is not None else range(0)
            second = "eps" if o.sector in FINANCIALS else "ebitda_ps"
            values = "; ".join(f"FY{y} {fmt((o.ps.get(y) or {}).get('revenue_ps'))}, "
                               f"{fmt((o.ps.get(y) or {}).get(second))}" for y in window)
            lines.append(f"    {t} ({o.sector}): stars stored {fmt(q.get('predictability_stars'))} oracle "
                         f"{fmt(stars)} ({why}); R2 stored {stored_r2} oracle "
                         f"{ {k: round(v, 6) for k, v in r2.items()} }; slopes "
                         f"{ {k: round(v, 6) for k, v in slopes.items()} }; disagree on {', '.join(bad)}"
                         + downstream(differ, d, t, list(window), ("revenue_ps", second)))
            lines.append(f"        revenue_ps, {second}: {values}")
    gate("predictability stars exact, R-squared within 0.01", checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)}", failures)
    for line in lines:
        print(line)
    print(f"    fits at a half-star boundary (float rounding could decide them): {', '.join(near) or 'none'}")
    return stars_of


def percentile(value: float, group: List[float], lower_better: bool, ties: str) -> Fraction:
    """The share of *group* at or below *value* (at or above it when lower is better), exactly. With *ties*
    'mid', a tie takes the middle of its run instead of its top (a sensitivity check only)."""
    n = len(group)
    if ties == "top":
        k = sum(1 for x in group if (x >= value if lower_better else x <= value))
        return Fraction(k, n)
    worse = sum(1 for x in group if (x > value if lower_better else x < value))
    equal = sum(1 for x in group if x == value)
    return Fraction(2 * worse + equal + 1, 2 * n)


def oracle_ranks(d: Data, ties: str = "top") -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, list]]]:
    """Every company's five ranks, composite and Magic Formula position range, from the stored components."""
    comps = {t: json.loads(q.get("components") or "{}") for t, q in d.qs.items()}
    sector = {t: d.sector.get(t, "general") for t in d.qs}
    out: Dict[str, Dict[str, Any]] = {t: {} for t in d.qs}
    detail: Dict[str, Dict[str, list]] = {t: {} for t in d.qs}
    for rank in RANKS:
        names = sorted({n for c in comps.values() for n in (c.get(rank) or {})})
        pcts: Dict[str, List[Fraction]] = {t: [] for t in d.qs}
        for name in names:
            values = {t: float(c[rank][name]) for t, c in comps.items()
                      if (c.get(rank) or {}).get(name) is not None}
            universe = list(values.values())
            by_class: Dict[str, List[float]] = {}
            for t, v in values.items():
                by_class.setdefault(sector[t], []).append(v)
            for t, v in values.items():
                own = by_class[sector[t]]
                group, where = (own, sector[t]) if len(own) >= MIN_PEERS else (universe, "universe")
                p = percentile(v, group, name in LOWER_IS_BETTER, ties)
                pcts[t].append(p)
                detail[t].setdefault(rank, []).append((name, v, where, len(group), p))
        need = 1 if rank in ("valuation", "momentum") else 2  # a reading: those two have one component each
        for t in d.qs:
            if len(pcts[t]) >= need:
                mean = sum(pcts[t], Fraction(0)) / len(pcts[t])
                out[t][f"rank_{rank}"] = max(1, math.ceil(10 * mean))
                out[t][f"mean_{rank}"] = mean
            else:
                out[t][f"rank_{rank}"] = None
    for t in d.qs:
        given = [out[t][f"rank_{r}"] for r in RANKS if out[t][f"rank_{r}"] is not None]
        out[t]["composite_score"] = Fraction(10 * sum(given), len(given)) if len(given) >= MIN_RANKS else None
    eligible = {t: (float(q["earnings_yield"]), float(q["return_on_capital"])) for t, q in d.qs.items()
                if sector[t] not in NO_MAGIC and q.get("earnings_yield") is not None
                and q.get("return_on_capital") is not None}
    ey = {t: 1 + sum(1 for u in eligible.values() if u[0] > v[0]) for t, v in eligible.items()}
    roc = {t: 1 + sum(1 for u in eligible.values() if u[1] > v[1]) for t, v in eligible.items()}
    total = {t: ey[t] + roc[t] for t in eligible}
    for t in d.qs:
        if t in total:
            low = 1 + sum(1 for s in total.values() if s < total[t])
            high = sum(1 for s in total.values() if s <= total[t])
            out[t]["magic"] = (low, high, total[t], ey[t], roc[t])
        else:
            out[t]["magic"] = None
    out["_ties"] = {"ey": len(eligible) - len({v[0] for v in eligible.values()}),
                    "roc": len(eligible) - len({v[1] for v in eligible.values()}),
                    "eligible": len(eligible)}
    return out, detail


def check_ranks(d: Data, failures: List[str]) -> Dict[str, Dict[str, Any]]:
    print("== ranks, composite and Magic Formula, from the stored components")
    print("    percentile = share of the group at or below the value (at or above, lower-is-better), exact "
          "fractions; group = sector_class with >= 10 valued, else the universe")
    ours, detail = oracle_ranks(d)
    ties = ours.pop("_ties")
    checked = agreed = 0
    lines = []
    for t in sorted(d.qs):
        q, o = d.qs[t], ours[t]
        for rank in RANKS:
            col = f"rank_{rank}"
            checked += 1
            if o[col] == q.get(col):
                agreed += 1
                continue
            mean = o.get(f"mean_{rank}")
            lines.append(f"    {t} ({d.sector.get(t)}) {col}: stored {fmt(q.get(col))} oracle {fmt(o[col])}"
                         + ("" if mean is None else f" (mean {float(mean):.6f})"))
            for name, v, where, n, p in detail[t].get(rank, []):
                lines.append(f"        {name} {fmt(v)}: {where} n={n} p={p} ({float(p):.4f})")
        checked += 1
        mine_c, stored_c = o["composite_score"], q.get("composite_score")
        if (mine_c is None and stored_c is None) or (mine_c is not None and stored_c is not None
                                                     and abs(float(mine_c) - stored_c) <= COMPOSITE_TOL):
            agreed += 1
        else:
            lines.append(f"    {t} composite_score: stored {fmt(stored_c)} oracle "
                         f"{fmt(None if mine_c is None else float(mine_c))}; ranks "
                         f"{[o[f'rank_{r}'] for r in RANKS]}")
        checked += 1
        magic, stored_m = o["magic"], q.get("magic_formula_rank")
        if (magic is None and stored_m is None) or (magic is not None and stored_m is not None
                                                    and magic[0] <= stored_m <= magic[1]):
            agreed += 1
        else:
            lines.append(f"    {t} ({d.sector.get(t)}) magic_formula_rank: stored {fmt(stored_m)} oracle "
                         f"{'N/A' if magic is None else f'{magic[0]}..{magic[1]}'}"
                         + (f" (sum {magic[2]} = EY rank {magic[3]} + ROC rank {magic[4]})" if magic else "")
                         + f"; EY {fmt(q.get('earnings_yield'))}, ROC {fmt(q.get('return_on_capital'))}")
    gate("ranks, composite and Magic Formula rank", checked > 0 and agreed == checked,
         f"agree {share(agreed, checked)}", failures)
    for line in lines:
        print(line)
    tied_sums = Counter(o["magic"][2] for o in ours.values() if o.get("magic"))
    print(f"    Magic Formula: {ties['eligible']} eligible; exact ties in earnings yield {ties['ey']}, in return on "
          f"capital {ties['roc']}; sums shared by several companies: "
          f"{sum(1 for c in tied_sums.values() if c > 1)}; stored ranks equal to the raw sum (the literal reading): "
          f"{sum(1 for t, o in ours.items() if o.get('magic') and d.qs[t].get('magic_formula_rank') == o['magic'][2])}")
    # Sensitivity: the same ranks with ties at the middle of their run.
    mid, _ = oracle_ranks(d, ties="mid")
    mid.pop("_ties")
    moved = [(t, r, ours[t][f"rank_{r}"], mid[t][f"rank_{r}"]) for t in sorted(d.qs) for r in RANKS
             if ours[t][f"rank_{r}"] != mid[t][f"rank_{r}"]]
    by_rank = Counter(r for _, r, _, _ in moved)
    print(f"    sensitivity (reported): with ties at the middle of their run, {len(moved)} of {len(d.qs) * 5} rank "
          f"values would change ({', '.join(f'{r} {n}' for r, n in sorted(by_rank.items())) or 'none'}): "
          + ", ".join(f"{t} {r} {a}->{b}" for t, r, a, b in moved[:40]) + (" ..." if len(moved) > 40 else ""))
    edges = [f"{t} {r}" for t in sorted(d.qs) for r in RANKS
             if ours[t].get(f"mean_{r}") is not None and (10 * ours[t][f"mean_{r}"]).denominator == 1]
    print(f"    rank means exactly on a decile boundary (exact here, where float rounding could tip a rank): "
          f"{len(edges)}{' (' + ', '.join(edges) + ')' if edges else ''}")
    # The stored components against the spec's table (names only).
    extra = sorted({f"{r}.{n}" for q in d.qs.values() for r, c in json.loads(q.get("components") or "{}").items()
                    for n in (c or {}) if n not in SPEC_COMPONENTS.get(r, set())})
    print(f"    stored component names outside the spec's table: {', '.join(extra) or 'none'}")
    return ours


def sic4(value: Any) -> Optional[str]:
    """A stored SIC code as this oracle reads one: four ASCII digits, else none (a code stored otherwise is the
    store's fault, and shows as a disagreement)."""
    text = value.strip() if isinstance(value, str) else ""
    return text if len(text) == 4 and all("0" <= ch <= "9" for ch in text) else None


def oracle_industries(d: Data) -> Dict[str, Dict[str, Any]]:
    """Every scored company's industry group, label and size, and its five ranks and composite within the group, from
    companies.sic and the stored components: exact fractions, one company at a time."""
    code = {t: sic4(d.sic.get(t)) for t in d.qs}
    comps = {t: json.loads(q.get("components") or "{}") for t, q in d.qs.items()}

    def sharing(prefix: str) -> List[str]:
        return sorted(t for t, c in code.items() if c is not None and c[:len(prefix)] == prefix)

    out: Dict[str, Dict[str, Any]] = {}
    for t in sorted(d.qs):
        c = code[t]
        group = None if c is None else next((p for p in (c[:3], c[:2]) if len(sharing(p)) >= MIN_INDUSTRY), None)
        if group is None:
            out[t] = {col: None for col in INDUSTRY_GROUP_COLUMNS + INDUSTRY_RANK_COLUMNS}
            continue
        members = sharing(group)
        counts = Counter(d.sic_description.get(m) for m in members if d.sic_description.get(m))
        named = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:LABEL_TOP]
        label = f"SIC {group}: " + "; ".join(text for text, _ in named) if named else f"SIC {group}"
        mine: Dict[str, Any] = {"industry_group": group, "industry_label": label, "industry_size": len(members),
                                "detail": {}}
        given = []
        for rank in RANKS:
            ps: List[Tuple[str, float, int, Fraction]] = []
            for name, value in (comps[t].get(rank) or {}).items():
                if value is None:
                    continue
                peers = [float(comps[m][rank][name]) for m in members
                         if (comps[m].get(rank) or {}).get(name) is not None]
                if len(peers) >= MIN_INDUSTRY:
                    ps.append((name, float(value), len(peers),
                               percentile(float(value), peers, name in LOWER_IS_BETTER, "top")))
            need = 1 if rank in ("valuation", "momentum") else 2  # as the universe's ranks
            mean = sum((p for *_, p in ps), Fraction(0)) / len(ps) if len(ps) >= need else None
            mine[f"ind_rank_{rank}"] = None if mean is None else max(1, math.ceil(10 * mean))
            mine["detail"][rank] = (ps, mean)
            if mean is not None:
                given.append(mine[f"ind_rank_{rank}"])
        mine["ind_composite"] = Fraction(10 * sum(given), len(given)) if len(given) >= MIN_RANKS else None
        out[t] = mine
    return out


def check_industries(d: Data, failures: List[str]) -> None:
    print("== industry groups and ranks (Milestone 5), from companies.sic and the stored components")
    print(f"    group = the 3-digit SIC prefix when >= {MIN_INDUSTRY} scored companies share it, else the 2-digit one "
          f"when >= {MIN_INDUSTRY} do; members = every scored company whose code starts with it; a component's "
          f"percentile within the members with it, when >= {MIN_INDUSTRY} have it; exact fractions")
    absent = [c for c in INDUSTRY_GROUP_COLUMNS + INDUSTRY_RANK_COLUMNS if c not in d.qs_columns]
    if absent:
        for name in ("industry groups, labels and sizes", "industry ranks and composite",
                     "the companies without industry ranks"):
            gate(name, False, f"quality_scores has no {', '.join(absent)}: ranked before Milestone 5", failures)
        return
    ours = oracle_industries(d)
    checked = agreed = 0
    lines = []
    for t in sorted(d.qs):
        q, o = d.qs[t], ours[t]
        for col in INDUSTRY_GROUP_COLUMNS:
            checked += 1
            if o[col] == q.get(col):
                agreed += 1
            else:
                lines.append(f"    {t} (SIC {d.sic.get(t)}) {col}: stored {fmt(q.get(col))} oracle {fmt(o[col])}")
    gate("industry groups, labels and sizes", checked > 0 and agreed == checked, f"agree {share(agreed, checked)}",
         failures)
    for line in lines:
        print(line)
    checked = agreed = 0
    lines = []
    for t in sorted(d.qs):
        q, o = d.qs[t], ours[t]
        for rank in RANKS:
            col = f"ind_rank_{rank}"
            checked += 1
            if o[col] == q.get(col):
                agreed += 1
                continue
            ps, mean = (o.get("detail") or {}).get(rank, ([], None))
            lines.append(f"    {t} (SIC {d.sic.get(t)}, group {o['industry_group']}) {col}: stored {fmt(q.get(col))} "
                         f"oracle {fmt(o[col])}" + ("" if mean is None else f" (mean {float(mean):.6f})"))
            for name, v, n, p in ps:
                lines.append(f"        {name} {fmt(v)}: n={n} p={p} ({float(p):.4f})")
        checked += 1
        mine_c, stored_c = o["ind_composite"], q.get("ind_composite")
        if (mine_c is None and stored_c is None) or (mine_c is not None and stored_c is not None
                                                     and abs(float(mine_c) - stored_c) <= COMPOSITE_TOL):
            agreed += 1
        else:
            lines.append(f"    {t} ind_composite: stored {fmt(stored_c)} oracle "
                         f"{fmt(None if mine_c is None else float(mine_c))}; ranks "
                         f"{[o[f'ind_rank_{r}'] for r in RANKS]}")
    gate("industry ranks and composite", checked > 0 and agreed == checked, f"agree {share(agreed, checked)}",
         failures)
    for line in lines:
        print(line)
    stored_none = {t for t, q in d.qs.items() if all(q.get(f"ind_rank_{r}") is None for r in RANKS)}
    ours_none = {t for t, o in ours.items() if all(o[f"ind_rank_{r}"] is None for r in RANKS)}
    gate("the companies without industry ranks", stored_none == ours_none,
         f"stored {len(stored_none)}, oracle {len(ours_none)}"
         + (f"; only stored: {', '.join(sorted(stored_none - ours_none)) or '-'}, only oracle: "
            f"{', '.join(sorted(ours_none - stored_none)) or '-'}" if stored_none != ours_none else ", the same"),
         failures)
    # Reported: the groups, and why the others have none.
    groups: Dict[str, List[str]] = {}
    for t, o in ours.items():
        if o["industry_group"] is not None:
            groups.setdefault(o["industry_group"], []).append(t)
    for group in sorted(groups):
        first = ours[groups[group][0]]
        members = sorted(t for t in d.qs if (sic4(d.sic.get(t)) or "").startswith(group))
        print(f"    group {group}: {first['industry_size']} members, {len(groups[group])} ranked in it; "
              f"{first['industry_label']}; members {', '.join(members)}")
    no_code = sorted(t for t in d.qs if sic4(d.sic.get(t)) is None)
    small = sorted(t for t in d.qs if t not in no_code and ours[t]["industry_group"] is None)
    print(f"    without a group: {len(no_code) + len(small)} ({len(no_code)} without a code"
          + (f": {', '.join(no_code)}" if no_code else "") + f"; {len(small)} in an industry under {MIN_INDUSTRY}"
          + (f": {', '.join(f'{t} {d.sic.get(t)}' for t in small)}" if small else "") + ")")
    with_rank = sorted(t for t, o in ours.items() if o["industry_group"] is not None and t not in ours_none)
    print(f"    with a group: {sum(len(v) for v in groups.values())}; with an industry rank: {len(with_rank)}; with an "
          f"industry composite: {sum(1 for o in ours.values() if o['ind_composite'] is not None)}")
    edges = [f"{t} {r}" for t, o in sorted(ours.items()) for r in RANKS
             if (o.get("detail") or {}).get(r, ([], None))[1] is not None
             and (10 * o["detail"][r][1]).denominator == 1]
    print(f"    industry rank means exactly on a decile boundary (where float rounding could tip a rank): "
          f"{len(edges)}{' (' + ', '.join(edges[:40]) + (' ...' if len(edges) > 40 else '') + ')' if edges else ''}")


# ---------------------------------------------------------------------------------------------------------------
# Reported, not gated


def histogram(values: List[Any]) -> str:
    c = Counter("N/A" if v is None else v for v in values)
    keys = sorted((k for k in c if k != "N/A"), key=lambda k: (isinstance(k, str), k)) + (["N/A"] if "N/A" in c
                                                                                           else [])
    return ", ".join(f"{fmt(k) if not isinstance(k, str) else k}: {c[k]}" for k in keys)


def distributions(d: Data, mine: Dict[str, Oracle], stars_of: Dict[str, Num]) -> None:
    print("== real-data distributions (reported)")
    qs = d.qs
    tickers = sorted(qs)
    print(f"    universe: {len(tickers)} companies; sector classes: "
          f"{', '.join(f'{k} {v}' for k, v in sorted(Counter(d.sector.get(t) for t in tickers).items()))}")
    f_scores = [qs[t].get("piotroski_f") for t in tickers]
    print(f"    Piotroski F (stored): {histogram(f_scores)}")
    print(f"    Piotroski tested (stored): {histogram([qs[t].get('piotroski_tested') for t in tickers])}")
    print(f"    Altman zones (stored): {histogram([qs[t].get('altman_zone') for t in tickers])}")
    ours = []
    for t in tickers:
        o = mine[t]
        r = (None if o.sector in NO_ALTMAN or o.cur is None
             else altman(o.cur, (d.snap.get(t) or {}).get("market_cap")))
        ours.append(r[1] if r else None)
    print(f"    Altman zones (oracle): {histogram(ours)}")
    ms = [qs[t].get("beneish_m") for t in tickers]
    flagged = sorted(t for t in tickers if qs[t].get("beneish_m") is not None and qs[t]["beneish_m"] > BENEISH_FLAG)
    print(f"    Beneish M (stored): {sum(1 for m in ms if m is not None)} valued, {sum(1 for m in ms if m is None)} "
          f"N/A; flagged (M > {BENEISH_FLAG}): {len(flagged)} ({', '.join(flagged) or 'none'})")
    print(f"    predictability stars (stored): {histogram([qs[t].get('predictability_stars') for t in tickers])}")
    print(f"    predictability stars (oracle): {histogram([stars_of.get(t) for t in tickers])}")
    comp = sorted(qs[t]["composite_score"] for t in tickers if qs[t].get("composite_score") is not None)
    if comp:
        q1, q2, q3 = statistics.quantiles(comp, n=4, method="inclusive")
        tens = Counter(min(int(c // 10) * 10, 90) for c in comp)
        print(f"    composite (stored): {len(comp)} valued, {len(tickers) - len(comp)} N/A; min {comp[0]:g}, "
              f"quartiles {q1:g} / {q2:g} / {q3:g} (inclusive), max {comp[-1]:g}")
        print("    composite by tens: " + ", ".join(
            f"[{k},{k + 10}{']' if k == 90 else ')'}: {tens.get(k, 0)}" for k in range(0, 100, 10)))
    for rank in RANKS:
        print(f"    rank_{rank} (stored): {histogram([qs[t].get(f'rank_{rank}') for t in tickers])}")


def severe_signs(d: Data, mine: Dict[str, Oracle]) -> None:
    print("== severe warning signs, each traced to the raw tables (reported)")
    severe = [s for s in d.signs if s["severity"] == "severe"]
    print(f"    warning_signs rows: {len(d.signs)}; severe: {len(severe)} "
          f"({', '.join(f'{k} {v}' for k, v in sorted(Counter(s['code'] for s in severe).items()))})")
    expected: Dict[Tuple[str, str], str] = {}
    for t in d.tickers:
        o = mine[t]
        if o.cur is None:
            continue
        a = None if o.sector in NO_ALTMAN else altman(o.cur, (d.snap.get(t) or {}).get("market_cap"))
        if a and a[0] < ALTMAN_DISTRESS:
            expected[(t, "altman_distress")] = f"Z {a[0]:.4f}"
        if o.sector not in NO_BENEISH and o.prev is not None:
            m = beneish(o.cur, o.prev)[0]
            if m is not None and m > BENEISH_FLAG:
                expected[(t, "beneish_flag")] = f"M {m:.4f}"
        s = sloan(o.cur)
        if s is not None and abs(s) > SLOAN_SEVERE:
            expected[(t, "sloan_high")] = f"Sloan {s:.4f}"
        cov = (d.ma.get(t, {}).get(o.fy) or {}).get("interest_coverage")
        if cov is not None and cov < COVERAGE_SEVERE:
            expected[(t, "interest_coverage_low")] = f"coverage {cov:.4f}"
    for s in severe:
        t, code = s["ticker"], s["code"]
        o = mine[t]
        c, p = o.cur, o.prev
        print(f"    {t} ({o.sector}, FY{o.fy}) {code}: stored value {fmt(s['value'])}, threshold "
              f"{fmt(s['threshold'])}; \"{s['message']}\"")
        if c is None:
            print("        no fiscal year stored")
            continue
        if code == "altman_distress":
            mve = (d.snap.get(t) or {}).get("market_cap")
            a = altman(c, mve)
            inputs = ", ".join(f"{k} {fmt(c[k])}" for k in ("current_assets", "current_liabilities",
                                                            "retained_earnings", "operating_income", "revenue",
                                                            "total_assets", "total_liabilities"))
            print(f"        oracle Z {fmt(a[0] if a else None)}: "
                  + (", ".join(f"{k} {v:.4f}" for k, v in a[2].items()) if a else "inputs missing")
                  + f"; MVE {fmt(mve)} ({(d.snap.get(t) or {}).get('collected_at')}), {inputs}")
        elif code == "beneish_flag" and p is not None:
            m, v, neutral, missing = beneish(c, p)
            print(f"        oracle M {fmt(m)}: " + ", ".join(
                f"{name} {fmt(v.get(name))} (x{w:+.3f} = {fmt(None if v.get(name) is None else w * v[name])})"
                for name, w in BENEISH_WEIGHTS) + f"; neutral {neutral or '-'}, missing {missing or '-'}")
            print(f"        t / t-1: {beneish_inputs(c, p)}")
        elif code == "sloan_high":
            print(f"        oracle Sloan {fmt(sloan(c))}: net_income {fmt(c['net_income'])}, operating_cash_flow "
                  f"{fmt(c['operating_cash_flow'])}, investing_cash_flow {fmt(c['investing_cash_flow'])}, "
                  f"total_assets {fmt(c['total_assets'])}")
        elif code == "interest_coverage_low":
            ma = d.ma.get(t, {}).get(o.fy) or {}
            print(f"        metrics_annual interest_coverage {fmt(ma.get('interest_coverage'))}, ebit "
                  f"{fmt(ma.get('ebit'))}; financials_annual operating_income {fmt(c['operating_income'])}, "
                  f"interest_expense {fmt(c['interest_expense'])}; recomputed ebit / interest "
                  f"{fmt(div(ma.get('ebit'), c['interest_expense']))}, operating income / interest "
                  f"{fmt(div(c['operating_income'], c['interest_expense']))}")
        else:
            print("        (no trace rule for this code)")
    counts = Counter(s["ticker"] for s in d.signs)
    off = [t for t, q in sorted(d.qs.items()) if (q.get("warning_count") or 0) != counts.get(t, 0)]
    print(f"    quality_scores.warning_count equal to the ticker's warning_signs rows: "
          f"{share(len(d.qs) - len(off), len(d.qs))}{' (off: ' + ', '.join(off) + ')' if off else ''}")
    stored = {(s["ticker"], s["code"]) for s in severe}
    unexplained = sorted(stored - set(expected))
    absent = sorted(set(expected) - stored)
    print(f"    severe signs the oracle's own figures fire: {len(expected)}; stored and fired by them: "
          f"{len(stored & set(expected))}; stored but not fired by them: "
          f"{', '.join(f'{t} {c}' for t, c in unexplained) or 'none'}; fired by them but not stored: "
          f"{', '.join(f'{t} {c} ({expected[(t, c)]})' for t, c in absent) or 'none'}")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--db", required=True)
    args = p.parse_args(argv)
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    failures: List[str] = []
    GATES.clear()
    d = Data(db)
    mine = {t: Oracle(d, t) for t in d.tickers}
    print(f"database {args.db}: {len(d.tickers)} companies, {len(d.qs)} quality_scores rows, {len(d.ps)} "
          f"per_share_annual rows, {len(d.signs)} warning_signs rows")

    check_fiscal_year(d, mine, failures)
    differ = check_per_share(d, mine, failures)
    check_cagrs(d, mine, differ, failures)
    check_piotroski(d, mine, failures)
    check_altman(d, mine, failures)
    check_beneish(d, mine, failures)
    check_sloan(d, mine, failures)
    stars_of = check_predictability(d, mine, differ, failures)
    check_ranks(d, failures)
    check_industries(d, failures)
    distributions(d, mine, stars_of)
    severe_signs(d, mine)

    print(f"gates: {len(GATES) - len(failures)} of {len(GATES)} pass; "
          + ("FAILED: " + "; ".join(failures) if failures else "all gates pass"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

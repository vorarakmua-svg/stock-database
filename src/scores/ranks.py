"""The five percentile ranks, the composite and the Magic Formula rank, across the universe; and momentum.

Each component becomes a percentile within the company's sector when the sector has MIN_SECTOR_PEERS
companies with it, and within the whole universe otherwise. It is oriented so that higher is better. A rank
is the mean of its components' percentiles as a decile, 1-10. The composite is 10 x the mean rank (equal
weights by default, Q11). See docs/superpowers/specs/2026-10-21-quality-scores-design.md.

The same ranks within the company's SIC industry group (Milestone 5, ``rank_industries``), where the group has
MIN_INDUSTRY_PEERS companies with a component, are kept beside them
(docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and industries").
"""

import math
import statistics
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..universe.industries import group_members
from .fundamentals import Fundamentals
from .scores import Scores
from .signs import HistoryContext

RANKS: Tuple[str, ...] = ("strength", "profitability", "growth", "valuation", "momentum")
#: The components each rank needs before it's given: valuation and momentum have one each.
REQUIRED: Dict[str, int] = {"strength": 2, "profitability": 2, "growth": 2, "valuation": 1, "momentum": 1}
LOWER_IS_BETTER = frozenset({"debt_to_revenue", "price_to_value", "pe_pct_rank"})
MIN_SECTOR_PEERS = 10
#: The members of an industry group with a component that its industry percentile needs (the spec's 10): fewer, and
#: the component is left out of the group's industry ranks.
MIN_INDUSTRY_PEERS = 10
MIN_RANKS = 4
MAGIC_EXCLUDED = ("bank", "insurance", "utility", "reit")
COVERAGE_CAP = 100.0
CASH_TO_DEBT_CAP = 10.0
BETA_FLOOR = 0.5
_FINANCIALS = ("bank", "insurance")


def _months_before(day: date, months: int) -> date:
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = m + 1
    last = (date(y + (month == 12), month % 12 + 1, 1) - date(y, month, 1)).days
    return date(y, month, min(day.day, last))


def momentum(bars: Sequence[Tuple[str, float]], beta: Optional[float]) -> Optional[float]:
    """((P[-1m] / P[-12m] - 1) + (P[-1m] / P[-6m] - 1)) / 2 / max(beta, 0.5): the 12-1 and 6-1 month returns
    from the closes (day, close, ascending), over beta (1 when unknown). None without a year of bars."""
    if not bars:
        return None
    last = date.fromisoformat(str(bars[-1][0])[:10])

    def close_on(months: int) -> Optional[float]:
        target = _months_before(last, months).isoformat()
        prior = [c for d, c in bars if str(d)[:10] <= target]
        return prior[-1] if prior else None

    p1, p6, p12 = close_on(1), close_on(6), close_on(12)
    if not p1 or not p6 or not p12:
        return None
    b = 1.0 if beta is None else max(beta, BETA_FLOOR)
    return ((p1 / p12 - 1.0) + (p1 / p6 - 1.0)) / 2.0 / b


def components(f: Fundamentals, s: Scores, growth: Dict[str, Optional[float]], history: HistoryContext,
               momentum_value: Optional[float]) -> Dict[str, Dict[str, Optional[float]]]:
    """Each rank's components for one company, as raw values (oriented later)."""
    rows = f.rows[-10:]
    latest = rows[-1].figures if rows else {}

    def div(a: Optional[float], b: Optional[float]) -> Optional[float]:
        return None if a is None or b is None or b == 0 else a / b

    coverage = latest.get("interest_coverage")
    debt = latest.get("total_debt")
    cash = latest.get("cash_and_equivalents")
    cash_to_debt = (CASH_TO_DEBT_CAP if debt == 0 and cash else div(cash, debt))
    margins = [r.figures.get("operating_margin") for r in rows]
    known = [m for m in margins if m is not None]
    slope = None
    if len(rows) >= 6:
        now, then = margins[-1], margins[-6]
        if now is not None and then is not None:
            slope = (now - then) / 5.0
    roics = [v for v in (r.figures.get("roic") for r in rows) if v is not None]
    incomes = [v for v in (r.figures.get("net_income") for r in rows) if v is not None]
    financial = f.sector_class in _FINANCIALS
    valuation: Dict[str, Optional[float]] = (
        {"price_to_value": history.price_to_value} if history.price_to_value is not None
        else {"pe_pct_rank": history.pe_pct_rank})
    return {
        "strength": {
            "interest_coverage": None if coverage is None else min(coverage, COVERAGE_CAP),
            "altman_z": s.altman_z,
            "equity_to_assets": div(latest.get("total_equity"), latest.get("total_assets")),
            "cash_to_debt": None if cash_to_debt is None else min(cash_to_debt, CASH_TO_DEBT_CAP),
            "debt_to_revenue": div(debt, latest.get("revenue")),
        },
        "profitability": {
            "operating_margin": latest.get("operating_margin"),
            "operating_margin_10y_mean": statistics.fmean(known) if len(known) >= 5 else None,
            "operating_margin_5y_slope": slope,
            "piotroski_share": div(s.piotroski_f, s.piotroski_tested),
            "predictability_stars": s.predictability_stars,
            "roic_10y_median": statistics.median(roics) if len(roics) >= 5 else None,
            "profitable_years": float(sum(1 for v in incomes if v > 0)) if len(incomes) >= 5 else None,
        },
        "growth": {
            "rev_ps_cagr_3y": growth.get("rev_ps_cagr_3y"),
            "rev_ps_cagr_5y": growth.get("rev_ps_cagr_5y"),
            ("eps_cagr_5y" if financial else "ebitda_ps_cagr_5y"):
                growth.get("eps_cagr_5y" if financial else "ebitda_ps_cagr_5y"),
        },
        "valuation": valuation,
        "momentum": {"momentum": momentum_value},
    }


def percentiles(values: Dict[str, float], lower_better: bool) -> Dict[str, float]:
    """Each key's share of the values at or below its own (at or above, when lower is better): in (0, 1]."""
    data = list(values.values())
    n = len(data)
    if lower_better:
        return {k: sum(1 for x in data if x >= v) / n for k, v in values.items()}
    return {k: sum(1 for x in data if x <= v) / n for k, v in values.items()}


def rank_universe(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The ranks, the composite and the Magic Formula rank for every row: each row is a ticker, its sector
    class, its components (``components``), its earnings yield and its return on capital."""
    out: Dict[str, Dict[str, Any]] = {row["ticker"]: {} for row in rows}
    sector_of = {row["ticker"]: row.get("sector_class") or "general" for row in rows}
    for rank in RANKS:
        names = sorted({name for row in rows for name in (row["components"].get(rank) or {})})
        pcts: Dict[str, List[float]] = {row["ticker"]: [] for row in rows}
        for name in names:
            values = {row["ticker"]: float(v) for row in rows
                      if (v := (row["components"].get(rank) or {}).get(name)) is not None}
            if not values:
                continue
            lower = name in LOWER_IS_BETTER
            universe = percentiles(values, lower)
            by_sector: Dict[str, Dict[str, float]] = {}
            for ticker, value in values.items():
                by_sector.setdefault(sector_of[ticker], {})[ticker] = value
            for sector, group in by_sector.items():
                ranked = percentiles(group, lower) if len(group) >= MIN_SECTOR_PEERS else universe
                for ticker in group:
                    pcts[ticker].append(ranked[ticker])
        for row in rows:
            ticker = row["ticker"]
            defined = sum(1 for v in (row["components"].get(rank) or {}).values() if v is not None)
            enough = defined >= REQUIRED[rank]
            mean = statistics.fmean(pcts[ticker]) if pcts[ticker] else None
            out[ticker][f"rank_{rank}"] = max(1, math.ceil(10 * mean - 1e-9)) if mean is not None and enough else None
    for ticker, result in out.items():
        given = [result[f"rank_{r}"] for r in RANKS if result.get(f"rank_{r}") is not None]
        result["composite_score"] = 10.0 * statistics.fmean(given) if len(given) >= MIN_RANKS else None
    eligible = [row for row in rows if sector_of[row["ticker"]] not in MAGIC_EXCLUDED
                and row.get("earnings_yield") is not None and row.get("return_on_capital") is not None]
    by_ey = sorted(eligible, key=lambda r: -r["earnings_yield"])
    by_roc = sorted(eligible, key=lambda r: -r["return_on_capital"])
    ey_rank = {r["ticker"]: i for i, r in enumerate(by_ey, 1)}
    roc_rank = {r["ticker"]: i for i, r in enumerate(by_roc, 1)}
    order = sorted(eligible, key=lambda r: (ey_rank[r["ticker"]] + roc_rank[r["ticker"]], -r["earnings_yield"]))
    magic = {r["ticker"]: i for i, r in enumerate(order, 1)}
    for ticker, result in out.items():
        result["magic_formula_rank"] = magic.get(ticker)
    return out


def rank_industries(rows: Sequence[Dict[str, Any]], groups: Dict[str, Optional[str]]) -> Dict[str, Dict[str, Any]]:
    """The five ranks and their composite within each row's industry group (*groups*:
    ``industries.industry_groups``), beside ``rank_universe``'s, which they leave as they are
    (docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and industries"). Each row is a
    ticker, its SIC code ("sic") and its components (``components``).

    A component's percentile (``percentiles``, LOWER_IS_BETTER's oriented) is the row's among its group's members
    with that component, every row whose code starts with the group (``industries.group_members``: a 2-digit
    group's take in its 3-digit groups'), when MIN_INDUSTRY_PEERS of them have it; else the component is left out of
    the industry rank. A rank is the decile of its components' mean percentile, given REQUIRED of them so ranked;
    the composite is 10 x the mean of the ranks given, MIN_RANKS of them or more. A row without a group gets none:
    ``ind_rank_<rank>`` and ``ind_composite`` None."""
    codes = {row["ticker"]: row.get("sic") for row in rows}
    members = {group: group_members(codes, group) for group in set(groups.values()) if group is not None}
    out: Dict[str, Dict[str, Any]] = {row["ticker"]: {} for row in rows}
    for rank in RANKS:
        names = sorted({name for row in rows for name in (row["components"].get(rank) or {})})
        pcts: Dict[str, List[float]] = {row["ticker"]: [] for row in rows}
        for name in names:
            values = {row["ticker"]: float(v) for row in rows
                      if (v := (row["components"].get(rank) or {}).get(name)) is not None}
            for group, tickers in members.items():
                peers = {ticker: values[ticker] for ticker in tickers if ticker in values}
                if len(peers) < MIN_INDUSTRY_PEERS:
                    continue
                ranked = percentiles(peers, name in LOWER_IS_BETTER)
                for ticker in peers:
                    if groups.get(ticker) == group:
                        pcts[ticker].append(ranked[ticker])
        for ticker, given in pcts.items():
            out[ticker][f"ind_rank_{rank}"] = (max(1, math.ceil(10 * statistics.fmean(given) - 1e-9))
                                              if len(given) >= REQUIRED[rank] else None)
    for result in out.values():
        ranked_ranks = [result[f"ind_rank_{r}"] for r in RANKS if result[f"ind_rank_{r}"] is not None]
        result["ind_composite"] = 10.0 * statistics.fmean(ranked_ranks) if len(ranked_ranks) >= MIN_RANKS else None
    return out

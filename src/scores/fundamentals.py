"""Annual fundamentals for the quality scores: each fiscal year's figures, its per-share series on today's
split basis, and their compound growth rates.

The share counts and EPS come from ``valuation.inputs.load_inputs``, which puts every year on today's
split basis, so a per-share series doesn't jump at a split. Totals (revenue, EBITDA, equity) need no
adjusting. See docs/superpowers/specs/2026-10-21-quality-scores-design.md.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..parsers.calculated_metrics import CalculatedMetrics
from ..valuation.inputs import load_inputs

#: Eleven fiscal years: a 10-year CAGR needs the year ten years back.
YEARS_KEPT = 11
#: A count from net income / diluted EPS needs EPS of at least this much: EPS is filed to the cent.
MIN_EPS_FOR_COUNT = 0.10

#: The statement figures each year carries, from ``financials_annual``.
ANNUAL_FIELDS: Tuple[str, ...] = (
    "revenue", "cost_of_revenue", "gross_profit", "operating_income", "sga_expense", "interest_expense",
    "income_tax_expense", "pretax_income", "net_income", "total_assets", "current_assets",
    "current_liabilities", "total_liabilities", "long_term_debt", "short_term_debt", "commercial_paper",
    "cash_and_equivalents", "short_term_investments", "accounts_receivable", "inventory", "ppe_net",
    "goodwill", "retained_earnings", "total_equity", "operating_cash_flow", "investing_cash_flow", "capex",
    "depreciation_amortization", "dividends_paid", "shares_outstanding", "weighted_avg_shares_diluted",
)
#: The debt tags behind ``metrics_annual``'s total debt.
DEBT_FIELDS: Tuple[str, ...] = ("long_term_debt", "short_term_debt", "commercial_paper")
#: Banks and insurers keep their debt as filed: theirs has no zero to read (KKR, a bank here, tags neither its
#: debt nor its interest).
DEBT_AS_FILED: Tuple[str, ...] = ("bank", "insurance")
#: The computed figures each year carries, from ``metrics_annual``.
METRIC_FIELDS: Tuple[str, ...] = ("ebitda", "ebit", "free_cash_flow", "roic", "operating_margin",
                                  "interest_coverage", "total_debt", "net_debt")

#: Each per-share series and the total it divides by today's split-basis shares (EPS is the filed EPS,
#: on the same basis).
PER_SHARE: Dict[str, str] = {"revenue_ps": "revenue", "ebitda_ps": "ebitda", "fcf_ps": "free_cash_flow",
                             "book_ps": "total_equity"}
#: The CAGRs stored: (series, years).
GROWTH: Tuple[Tuple[str, int], ...] = (
    ("rev_ps", 3), ("rev_ps", 5), ("rev_ps", 10), ("eps", 3), ("eps", 5), ("eps", 10),
    ("ebitda_ps", 3), ("ebitda_ps", 5), ("ebitda_ps", 10), ("fcf_ps", 5), ("book_ps", 5),
)


@dataclass
class AnnualRow:
    """One fiscal year: its totals, and its share count and EPS on today's split basis. ``debt_unknown`` when
    it paid interest on debt no mapped tag holds (``read_debt``)."""

    fiscal_year: int
    period_end: Optional[str]
    shares: Optional[float]
    eps: Optional[float]
    figures: Dict[str, Optional[float]] = field(default_factory=dict)
    debt_unknown: bool = False

    def per_share(self, key: str) -> Optional[float]:
        """A total over the year's share count, or None."""
        value = self.figures.get(key)
        if value is None or not self.shares or self.shares <= 0:
            return None
        return value / self.shares


@dataclass
class Fundamentals:
    """A company's latest YEARS_KEPT fiscal years, oldest first."""

    ticker: str
    sector_class: str = "general"
    rows: List[AnnualRow] = field(default_factory=list)

    def series(self, name: str) -> List[Tuple[int, Optional[float]]]:
        """A per-share series by name (``revenue_ps``, ``eps``, ``ebitda_ps``, ``fcf_ps``, ``book_ps``), or a
        figure's own series by its field name."""
        if name == "eps":
            return [(r.fiscal_year, r.eps) for r in self.rows]
        if name in PER_SHARE:
            return [(r.fiscal_year, r.per_share(PER_SHARE[name])) for r in self.rows]
        return [(r.fiscal_year, r.figures.get(name)) for r in self.rows]


def cagr(series: Sequence[Tuple[int, Optional[float]]], years: int) -> Optional[float]:
    """(latest / start) ** (1 / years) - 1, the start exactly *years* fiscal years before the latest. Both ends
    must be positive; otherwise None."""
    if not series:
        return None
    by_year = dict(series)
    last_year = max(by_year)
    last, first = by_year.get(last_year), by_year.get(last_year - years)
    if last is None or first is None or last <= 0 or first <= 0:
        return None
    return float((last / first) ** (1.0 / years) - 1.0)


def _rows(conn: sqlite3.Connection, sql: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
    cur = conn.execute(sql, tuple(params))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def load_fundamentals(conn: sqlite3.Connection, ticker: str) -> Fundamentals:
    """The company's latest YEARS_KEPT fiscal years, each with its figures and its split-basis shares and EPS."""
    company = _rows(conn, "SELECT sector_class FROM companies WHERE ticker = ?", (ticker,))
    f = Fundamentals(ticker=ticker, sector_class=(company[0]["sector_class"] if company else None) or "general")
    if not company:
        return f
    records = load_inputs(conn, ticker, max_years=YEARS_KEPT).fy_records
    annual = {r["fiscal_year"]: r for r in _rows(
        conn, "SELECT fiscal_year, " + ", ".join(ANNUAL_FIELDS) + " FROM financials_annual WHERE ticker = ?",
        (ticker,))}
    metrics = {r["fiscal_year"]: r for r in _rows(
        conn, "SELECT fiscal_year, " + ", ".join(METRIC_FIELDS) + " FROM metrics_annual WHERE ticker = ?",
        (ticker,))}
    for rec in records:
        figures: Dict[str, Optional[float]] = {}
        for name in ANNUAL_FIELDS:
            figures[name] = (annual.get(rec.fiscal_year) or {}).get(name)
        for name in METRIC_FIELDS:
            figures[name] = (metrics.get(rec.fiscal_year) or {}).get(name)
        debt_unknown = read_debt(figures, f.sector_class)
        if rec.private_count:
            # A year before the listing on the private count, which only the listing's jump put on the public one:
            # a merger around the listing jumps the same way (DELL's EMC), so the year has no per-share figures;
            # its totals stay (BUG-188).
            f.rows.append(AnnualRow(fiscal_year=rec.fiscal_year, period_end=rec.period_end, shares=None, eps=None,
                                    figures=figures, debt_unknown=debt_unknown))
            continue
        f.rows.append(AnnualRow(fiscal_year=rec.fiscal_year, period_end=rec.period_end,
                                shares=_count_on_eps_basis(rec.shares, rec.eps_diluted, figures),
                                eps=rec.eps_diluted, figures=figures, debt_unknown=debt_unknown))
    return f


def read_debt(figures: Dict[str, Optional[float]], sector_class: str) -> bool:
    """Settle a year's debt where no debt tag holds a positive amount; True when it's unknown (BUG-189, BUG-190).

    Interest paid with no debt tagged is debt the mapping doesn't read (PGR files its $8.4B under
    DebtLongtermAndShorttermCombinedAmount), so total debt, net debt and ROIC become unknown. Neither, outside
    banks and insurers, is no debt (ISRG, ANET, PANW, PLTR and VRTX tag none): total debt 0, net debt less the
    cash and short-term investments, and ROIC on equity less cash, as ``metrics_annual`` computes them. A
    missing interest figure alone says nothing: filers with debt often tag their interest under a name not
    mapped (AAPL, MSFT). The stored metrics stay as they are, for the screener and the history.
    """
    tags = [figures.get(name) for name in DEBT_FIELDS + ("total_debt",)]
    if any(v is not None and v > 0 for v in tags):
        return False
    if (figures.get("interest_expense") or 0.0) > 0:
        figures.update(total_debt=None, net_debt=None, roic=None)
        return True
    if sector_class in DEBT_AS_FILED:
        return False
    cash = (figures.get("cash_and_equivalents") or 0.0) + (figures.get("short_term_investments") or 0.0)
    figures["total_debt"], figures["net_debt"] = 0.0, -cash
    if figures.get("roic") is None:
        figures["roic"] = CalculatedMetrics().calculate_all({**figures, "long_term_debt": 0.0},
                                                            sector=sector_class)["roic"]
    return False


def _count_on_eps_basis(shares: Optional[float], eps: Optional[float],
                        figures: Dict[str, Optional[float]]) -> Optional[float]:
    """The year's share count on its EPS's split basis.

    ``load_inputs`` folds splits by the count that matches the EPS (the weighted diluted count, else net
    income / EPS), but its ``shares`` falls back to the cover's shares outstanding, which stays as filed.
    GOOGL's FY2020 cover reads 0.675B while its EPS was restated for the 2022 20:1 split (BUG-173). So
    without a weighted count, net income over the (normalized) EPS gives the count on today's basis.
    """
    if figures.get("weighted_avg_shares_diluted") is not None:
        return shares
    ni = figures.get("net_income")
    if ni is not None and eps is not None and abs(eps) >= MIN_EPS_FOR_COUNT:
        return abs(ni / eps)
    return shares


def per_share_rows(f: Fundamentals) -> List[Dict[str, Any]]:
    """The ``per_share_annual`` rows: each year's per-share series."""
    return [{"fiscal_year": r.fiscal_year, "eps": r.eps,
             **{name: r.per_share(total) for name, total in PER_SHARE.items()}} for r in f.rows]


def growth(f: Fundamentals) -> Dict[str, Optional[float]]:
    """The stored CAGRs, keyed ``<series>_cagr_<years>y`` (``rev_ps`` is revenue per share)."""
    names = {"rev_ps": "revenue_ps", "eps": "eps", "ebitda_ps": "ebitda_ps", "fcf_ps": "fcf_ps",
             "book_ps": "book_ps"}
    return {f"{key}_cagr_{years}y": cagr(f.series(names[key]), years) for key, years in GROWTH}

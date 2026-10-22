"""Computing and storing the quality scores: each ticker's after its valuations and history, then the rank pass
across the whole universe. docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

from ..exporters.sqlite_store import SQLiteStore
from .fundamentals import growth, load_fundamentals, per_share_rows
from .ranks import components, momentum, rank_universe
from .scores import Market, compute_scores
from .signs import HistoryContext, Sign, warning_signs

#: The closes momentum reads: a year back and a little more.
MOMENTUM_DAYS = 400


def _rows(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
    try:
        cur = conn.execute(sql, params)
    except sqlite3.OperationalError:  # a table from after this database (valuation_ranges)
        return []
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _market(conn: sqlite3.Connection, ticker: str) -> Market:
    # The latest snapshot with a price: a run whose quote failed stores one without price, market cap or beta
    # (BUG-187, as BUG-176 for the history).
    snap = _rows(conn, "SELECT * FROM market_snapshots WHERE ticker = ? AND current_price IS NOT NULL "
                 "ORDER BY collected_at DESC LIMIT 1", (ticker,))
    s = snap[0] if snap else {}
    return Market(market_cap=s.get("market_cap"), price=s.get("current_price"),
                  risk_free_rate=s.get("risk_free_rate"), beta=s.get("beta"))


def _history(conn: sqlite3.Connection, ticker: str, price: Any) -> HistoryContext:
    ranges = {r["metric"]: r for r in _rows(
        conn, "SELECT metric, applicable, current_value, pct_rank FROM valuation_ranges WHERE ticker = ? "
        "AND metric IN ('fair_value', 'pe')", (ticker,))}
    fv, pe = ranges.get("fair_value") or {}, ranges.get("pe") or {}
    value = fv.get("current_value") if fv.get("applicable") else None
    return HistoryContext(price_to_value=price / value if price and value else None,
                          pe_pct_rank=pe.get("pct_rank") if pe.get("applicable") else None)


def _bars(conn: sqlite3.Connection, ticker: str) -> List[Tuple[str, float]]:
    rows = _rows(conn, "SELECT date, close FROM price_bars WHERE ticker = ? AND close IS NOT NULL AND date >= "
                 "date((SELECT MAX(date) FROM price_bars WHERE ticker = ?), ?) ORDER BY date",
                 (ticker, ticker, f"-{MOMENTUM_DAYS} days"))
    return [(str(r["date"])[:10], float(r["close"])) for r in rows]


def compute_ticker(conn: sqlite3.Connection, ticker: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Sign]]:
    """One company's ``quality_scores`` row (without its ranks), its per-share rows and its warning signs."""
    f = load_fundamentals(conn, ticker)
    market = _market(conn, ticker)
    s = compute_scores(f, market)
    g = growth(f)
    history = _history(conn, ticker, market.price)
    signs = warning_signs(f, s, history)
    mom = momentum(_bars(conn, ticker), market.beta)
    row: Dict[str, Any] = {
        "fiscal_year": s.fiscal_year,
        **g,
        "piotroski_f": s.piotroski_f,
        "piotroski_tested": s.piotroski_tested,
        "piotroski_tests": json.dumps(s.piotroski_tests),
        "altman_z": s.altman_z,
        "altman_zone": s.altman_zone,
        "beneish_m": s.beneish_m,
        "beneish_variables": json.dumps(s.beneish_variables),
        "sloan_ratio": s.sloan_ratio,
        "roic": s.roic,
        "wacc": s.wacc,
        "roic_minus_wacc": s.roic_minus_wacc,
        "predictability_stars": s.predictability_stars,
        "predictability_r2": json.dumps(s.predictability_r2),
        "ncav_per_share": s.ncav_per_share,
        "earnings_yield": s.earnings_yield,
        "return_on_capital": s.return_on_capital,
        "debt_to_ebitda": s.debt_to_ebitda,
        "momentum": mom,
        "components": json.dumps(components(f, s, g, history, mom)),
        "warning_count": len(signs),
        "na_reasons": json.dumps(s.na_reasons),
    }
    return row, per_share_rows(f), signs


def store_ticker(conn: sqlite3.Connection, store: SQLiteStore, ticker: str, computed_at: str) -> None:
    """Compute one company's scores and replace its stored ones."""
    row, per_share, signs = compute_ticker(conn, ticker)
    store.export_quality_scores(ticker, row, per_share, signs, computed_at)


def rank_all(db_path: Union[str, Path], ranked_at: str) -> int:
    """Rank every stored company against the universe (its sector where it has 10 peers), and store the ranks,
    the composite and the Magic Formula rank. Returns the number ranked."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = _rows(conn, "SELECT q.ticker, q.components, q.earnings_yield, q.return_on_capital, c.sector_class "
                     "FROM quality_scores q LEFT JOIN companies c ON c.ticker = q.ticker", ())
    finally:
        conn.close()
    for row in rows:
        row["components"] = json.loads(row["components"] or "{}")
    ranks = rank_universe(rows)
    SQLiteStore(db_path=Path(db_path)).update_quality_ranks(ranks, ranked_at)
    return len(ranks)

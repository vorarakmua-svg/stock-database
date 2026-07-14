"""Valuation engine: run every model for a ticker, summarize, verdict.

``run_valuations`` produces exactly one ``ValuationResult`` per model (five
rows) so "not applicable" is always distinguishable from "never computed".
``intrinsic_summary`` collapses the applicable models into per-ticker medians
(stored — price-independent); ``verdict``/``upside_pct`` compare those medians
to a live price at read time.
"""

import logging
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ..exporters.sqlite_store import SQLiteStore
from .inputs import ValuationInputs, load_inputs
from .models import (
    ValuationResult,
    value_dcf,
    value_ddm,
    value_graham,
    value_lynch,
    value_multiples,
)

_MODEL_FNS: List[Tuple[str, Callable[[ValuationInputs], ValuationResult]]] = [
    ("dcf", value_dcf),
    ("ddm", value_ddm),
    ("graham", value_graham),
    ("lynch", value_lynch),
    ("multiples", value_multiples),
]

VERDICT_LABELS: Dict[Optional[str], str] = {
    "cheap": "Looks cheap",
    "fair": "Fairly valued",
    "expensive": "Looks expensive",
    None: "Not valued",
}


def run_valuations(inputs: ValuationInputs,
                   logger: Optional[logging.Logger] = None) -> List[ValuationResult]:
    """All five models for one ticker. A model crash becomes an N/A row."""
    log = logger or logging.getLogger(__name__)
    results: List[ValuationResult] = []
    for name, fn in _MODEL_FNS:
        try:
            results.append(fn(inputs))
        except Exception as e:  # never abort the run for one model
            log.warning(f"{inputs.ticker}: {name} valuation failed: {e}")
            results.append(ValuationResult(
                model=name, applicable=False, na_reason=f"internal error: {e}"))
    return results


def intrinsic_summary(results: List[ValuationResult]) -> Dict[str, Any]:
    """Cross-model medians of bear/base/bull over applicable models."""
    app = [r for r in results if r.applicable]
    if not app:
        return {"n_applicable": 0, "median_bear": None,
                "median_base": None, "median_bull": None}
    return {
        "n_applicable": len(app),
        "median_bear": statistics.median([v for v in [r.value_bear for r in app] if v is not None]),
        "median_base": statistics.median([v for v in [r.value_base for r in app] if v is not None]),
        "median_bull": statistics.median([v for v in [r.value_bull for r in app] if v is not None]),
    }


def verdict(median_bear: Optional[float], median_bull: Optional[float],
            price: Optional[float]) -> Optional[str]:
    """Where the live price sits vs the median fair-value range."""
    if median_bear is None or median_bull is None or price is None or price <= 0:
        return None
    if price < median_bear:
        return "cheap"
    if price > median_bull:
        return "expensive"
    return "fair"


def upside_pct(median_base: Optional[float],
               price: Optional[float]) -> Optional[float]:
    """(median base fair value - price) / price."""
    if median_base is None or price is None or price <= 0:
        return None
    return (median_base - price) / price


def compute_and_store(db_path: Union[str, Path],
                      tickers: Optional[List[str]] = None,
                      logger: Optional[logging.Logger] = None) -> int:
    """Compute and persist valuations for *tickers* (default: every company).

    Per-ticker failures are logged and skipped — a bad ticker never aborts
    the batch. Returns the number of tickers successfully stored.
    """
    log = logger or logging.getLogger(__name__)
    store = SQLiteStore(db_path=Path(db_path), logger=log)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if tickers is None:
            try:
                tickers = [r["ticker"] for r in conn.execute(
                    "SELECT ticker FROM companies ORDER BY ticker").fetchall()]
            except sqlite3.OperationalError as e:
                log.warning(f"Cannot list companies for valuation: {e}")
                return 0
        computed_at = datetime.now(timezone.utc).isoformat()
        stored = 0
        for ticker in tickers:
            try:
                inputs = load_inputs(conn, ticker)
                results = run_valuations(inputs, logger=log)
                summary = intrinsic_summary(results)
                store.export_valuations(ticker, results, summary, computed_at)
                stored += 1
            except Exception as e:
                log.warning(f"Valuation failed for {ticker}: {e}")
        return stored
    finally:
        conn.close()

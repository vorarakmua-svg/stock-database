"""Point-in-time metrics: the fundamental ratio suite computed on as-of-date financials.

A pure-read composition layer. ``AsOfReader`` (see ``query/asof.py``) supplies each
annual period as it was known on a date ``D`` (the latest filing made on or before ``D``);
``CalculatedMetrics`` (see ``parsers/calculated_metrics.py``) computes the ratios. So the
metrics for a year as of ``D`` are ``calculate_all(as_of_annual(ticker, fy, D), sector=...)``
— the same engine the pipeline uses, fed the as-of financials instead of the latest-restated
ones, with no look-ahead.

Only the **fundamental** ratios are produced (profitability, returns, margins, capital
structure, coverage, efficiency, and the bank/insurer/REIT sector ratios). Valuation/EV
ratios are intentionally absent: they need the share price as of ``D`` and the project stores
no historical per-date price series. (``calculate_all`` is called without ``market_data``,
so those keys never appear.)
"""

import logging
from pathlib import Path
from typing import Any, Optional, Union

from ..parsers.calculated_metrics import CalculatedMetrics
from .asof import AsOfReader


class PointInTimeMetrics:
    """Compute the fundamental ratio suite on point-in-time (as-of-date) financials."""

    def __init__(self, reader: AsOfReader,
                 calculator: Optional[CalculatedMetrics] = None,
                 logger: Optional[logging.Logger] = None) -> None:
        self.reader = reader
        self.calculator = calculator or CalculatedMetrics()
        self.logger = logger or logging.getLogger(__name__)

    @classmethod
    def from_path(cls, db_path: Union[str, Path]) -> "PointInTimeMetrics":
        """Build an instance over a DB path (constructs its own read-only AsOfReader)."""
        return cls(AsOfReader(db_path))

    def close(self) -> None:
        self.reader.close()

    def __enter__(self) -> "PointInTimeMetrics":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _sector(self, ticker: str) -> Optional[str]:
        """The company's ``sector_class`` from the companies table, or ``None``."""
        cur = self.reader.conn.execute(
            "SELECT sector_class FROM companies WHERE ticker = ?", (ticker,)
        )
        row = cur.fetchone()
        return row["sector_class"] if row is not None else None

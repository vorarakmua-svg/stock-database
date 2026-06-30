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
from typing import Any, Dict, Optional, Union

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

    def metrics_as_of(
        self, ticker: str, fiscal_year: int, as_of_date: Any, sector: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """The fundamental ratio suite for ``(ticker, fiscal_year)`` as known on ``as_of_date``.

        Resolves the as-of financials via ``AsOfReader`` and runs the standard calculator on
        them. Returns ``None`` if the year had not been filed yet as of that date. ``sector``
        defaults to the company's ``sector_class`` (auto-read); pass a value to override.
        """
        period = self.reader.as_of_annual(ticker, fiscal_year, as_of_date)
        if period is None:
            return None
        sec = sector if sector is not None else self._sector(ticker)
        return self.calculator.calculate_all(period, sector=sec)

    def metric_as_of(
        self, ticker: str, fiscal_year: int, name: str, as_of_date: Any,
        sector: Optional[str] = None
    ) -> Any:
        """A single ratio's value as known on ``as_of_date`` (or ``None``)."""
        metrics = self.metrics_as_of(ticker, fiscal_year, as_of_date, sector=sector)
        return metrics.get(name) if metrics is not None else None

    def metrics_history_as_of(
        self, ticker: str, as_of_date: Any, years_back: Optional[int] = None,
        sector: Optional[str] = None
    ) -> Dict[int, Dict[str, Any]]:
        """Fundamental ratios for every fiscal year known as of ``as_of_date``.

        Each year uses its as-of financials (latest vintage filed <= the date). Keyed by
        fiscal_year, newest first; ``years_back`` trims to the most recent N. A per-year
        calculator failure is captured as ``{"error": ...}`` rather than aborting the batch.
        """
        periods = self.reader.history_as_of(ticker, as_of_date, years_back=years_back)
        if not periods:
            return {}
        sec = sector if sector is not None else self._sector(ticker)
        result: Dict[int, Dict[str, Any]] = {}
        for fy, period in periods.items():
            try:
                result[fy] = self.calculator.calculate_all(period, sector=sec)
            except Exception as e:  # noqa: BLE001 - mirror calculate_historical: never abort the batch
                self.logger.warning("PIT metrics error for %s FY%s: %s", ticker, fy, e)
                result[fy] = {"error": str(e)}
        return result

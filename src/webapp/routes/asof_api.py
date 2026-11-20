"""Point-in-time (as-of-date) JSON API routes.

Wraps AsOfReader and PointInTimeMetrics — no as-of resolution logic here.
All date parameters are ISO YYYY-MM-DD strings. A ticker is read upper-cased and stripped, as
the collection stores it. Each as-of route answers **404** for a ticker whose as-of data isn't
built (collected before the snapshots) or that no collection holds, saying which, rather than
reading it as a company with nothing filed by the date.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...query.asof import AsOfReader
from ...query.pit_metrics import PointInTimeMetrics
from ..dependencies import get_asof_reader, get_pit_metrics, get_reader
from ..repository import Reader

router = APIRouter(prefix="/api/asof", tags=["as-of"])


def _ticker(ticker: str) -> str:
    """*ticker* as the collection stores it."""
    return ticker.upper().strip()


def unread_reason(reader: AsOfReader, ticker: str) -> Optional[str]:
    """Why *ticker*'s as-of view reads nothing on any date (it is unknown, or its as-of data isn't
    built), or None when its as-of data is built."""
    if reader.has_snapshots(ticker):
        return None
    if not reader.has_company(ticker):
        return f"{ticker} is unknown: no company {ticker} has been collected"
    return f"The as-of data for {ticker} isn't built yet: re-collecting {ticker} builds it"


def _raise_if_unread(reader: AsOfReader, ticker: str) -> None:
    reason = unread_reason(reader, ticker)
    if reason is not None:
        raise HTTPException(status_code=404, detail=reason)


@router.get("/{ticker}/annual")
def annual_as_of(
    ticker: str,
    fiscal_year: int,
    date: str,
    reader: AsOfReader = Depends(get_asof_reader),
) -> Dict[str, Any]:
    """Annual period for (ticker, fiscal_year) as known on *date*.

    The annual record as known on *date*; ``carried_from`` names the earlier
    filing of each figure the year's latest filing didn't give.
    **404** if the year had not been filed as of that date, the ticker's
    as-of data isn't built (collected before the snapshots), or the ticker is unknown.
    """
    ticker = _ticker(ticker)
    result = reader.as_of_annual(ticker, fiscal_year, date)
    if result is None:
        _raise_if_unread(reader, ticker)
        raise HTTPException(
            status_code=404,
            detail=f"{ticker} FY{fiscal_year} had not been filed as of {date}",
        )
    return result


@router.get("/{ticker}/history")
def history_as_of(
    ticker: str,
    date: str,
    years: Optional[int] = None,
    reader: AsOfReader = Depends(get_asof_reader),
) -> Dict[str, Any]:
    """Every fiscal year for *ticker* known as of *date*, keyed by fiscal_year.

    ``years`` trims to the N most recent. Returns ``{}`` if nothing filed yet;
    **404** if the ticker's as-of data isn't built or the ticker is unknown.
    """
    ticker = _ticker(ticker)
    result = reader.history_as_of(ticker, date, years_back=years)
    if not result:
        _raise_if_unread(reader, ticker)
    # JSON keys must be strings; fiscal_year ints → string keys
    return {str(k): v for k, v in result.items()}


@router.get("/{ticker}/metrics")
def metrics_as_of(
    ticker: str,
    fiscal_year: int,
    date: str,
    sector: Optional[str] = None,
    pit: PointInTimeMetrics = Depends(get_pit_metrics),
) -> Dict[str, Any]:
    """Fundamental ratio suite for (ticker, fiscal_year) as known on *date*.

    **404** if the year had not been filed as of that date, the ticker's as-of
    data isn't built, or the ticker is unknown.
    """
    ticker = _ticker(ticker)
    result = pit.metrics_as_of(ticker, fiscal_year, date, sector=sector)
    if result is None:
        _raise_if_unread(pit.reader, ticker)
        raise HTTPException(
            status_code=404,
            detail=f"{ticker} FY{fiscal_year} metrics not available as of {date}",
        )
    return result


@router.get("/{ticker}/metrics/history")
def metrics_history_as_of(
    ticker: str,
    date: str,
    years: Optional[int] = None,
    sector: Optional[str] = None,
    pit: PointInTimeMetrics = Depends(get_pit_metrics),
) -> Dict[str, Any]:
    """Fundamental ratios for every fiscal year known as of *date*.

    Keyed by fiscal_year string. ``years`` trims to the N most recent. ``{}`` if
    nothing filed yet; **404** if the ticker's as-of data isn't built or the
    ticker is unknown.
    """
    ticker = _ticker(ticker)
    result = pit.metrics_history_as_of(ticker, date, years_back=years, sector=sector)
    if not result:
        _raise_if_unread(pit.reader, ticker)
    return {str(k): v for k, v in result.items()}


@router.get("/{ticker}/vintages")
def vintages(
    ticker: str,
    fiscal_year: Optional[int] = None,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """All filing vintages for *ticker*, optionally filtered to *fiscal_year*.

    Ordered by fiscal_year DESC then filed_date ASC.
    """
    return r.vintages(_ticker(ticker), fiscal_year=fiscal_year)

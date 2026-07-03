"""Screener & compare JSON API routes.

All database access goes through Depends(get_reader); no SQL here.
build_screen_query / parse_screen_params handle injection safety.
ValueError from any Reader method → HTTP 400.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from ..dependencies import get_reader
from ..repository import Reader
from ..schemas import ScreenRequest
from ..screener import DEFAULT_COMPARE_METRICS, MetricFilter, ScreenSpec, parse_screen_params

router = APIRouter(prefix="/api", tags=["screener"])


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


@router.post("/screen")
def post_screen(
    body: ScreenRequest,
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Screen companies via a POST body (supports all ops including 'between').

    Returns ``{"columns", "items", "total"}``.
    """
    try:
        spec = ScreenSpec(
            filters=[
                MetricFilter(
                    field=f.field,
                    op=f.op,
                    value=f.value,
                    value2=f.value2,
                )
                for f in body.filters
            ],
            sector=body.sector,
            sort=body.sort,
            sort_dir=body.sort_dir,
            limit=body.limit,
            offset=body.offset,
        )
        return r.screen(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/screen")
def get_screen(
    request: Request,
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Screen companies via query-string shorthand.

    Keys of the form ``<field>_<op>`` (op ∈ gte/lte/gt/lt/eq/ne) become
    filters.  Reserved keys: ``sector``, ``sort``, ``sort_dir``, ``limit``,
    ``offset``.  Returns ``{"columns", "items", "total"}``.
    """
    try:
        spec = parse_screen_params(dict(request.query_params))
        return r.screen(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


@router.get("/compare")
def get_compare(
    tickers: str,
    metrics: Optional[str] = None,
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Side-by-side metric comparison.

    ``tickers``: comma-separated ticker symbols.
    ``metrics``: comma-separated metric names (defaults to a headline set).
    Returns ``{"tickers", "rows"}``.
    """
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="'tickers' must not be empty.")

    if metrics:
        metric_list = [m.strip() for m in metrics.split(",") if m.strip()]
    else:
        metric_list = DEFAULT_COMPARE_METRICS

    try:
        return r.compare(ticker_list, metric_list)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------------------
# Sectors
# ---------------------------------------------------------------------------


@router.get("/sectors/aggregates")
def get_sectors_aggregates(
    year: Optional[int] = None,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Per-sector summary with company counts and headline metric medians."""
    return r.sector_aggregates(fiscal_year=year)


@router.get("/sectors/{sector}/medians")
def get_sector_medians(
    sector: str,
    year: Optional[int] = None,
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Per-metric median across all companies in *sector*.

    Returns only columns that have at least one non-null value.
    """
    return r.sector_medians(sector_class=sector, fiscal_year=year)

"""Companies API router.

All endpoints delegate to the Reader via Depends(get_reader) — no SQL here.
Route ordering: /search and /sectors are declared BEFORE /{ticker} so FastAPI
does not misinterpret them as a ticker path parameter.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_reader
from ..repository import Reader
from ..schemas import CompanyListResponse, CompanySummary, SearchHit, SeriesPoint
from ..screener import DEFAULT_COMPARE_METRICS

router = APIRouter(prefix="/api/companies", tags=["companies"])


# ---------------------------------------------------------------------------
# Static routes — must come before /{ticker}
# ---------------------------------------------------------------------------


@router.get("/search", response_model=List[SearchHit])
def search_companies(
    q: str,
    limit: int = 10,
    r: Reader = Depends(get_reader),
) -> List[SearchHit]:
    """Autocomplete: ticker/company_name prefix search."""
    rows = r.search_companies(q, limit)
    return [
        SearchHit(
            ticker=row["ticker"],
            company_name=row.get("company_name"),
            sector_class=row.get("sector_class"),
        )
        for row in rows
    ]


@router.get("/sectors", response_model=List[str])
def list_sectors(r: Reader = Depends(get_reader)) -> List[str]:
    """Distinct non-null sector_class values, sorted."""
    return r.distinct_sectors()


@router.get("", response_model=CompanyListResponse)
def list_companies(
    sector: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    r: Reader = Depends(get_reader),
) -> CompanyListResponse:
    """Paginated list of companies with optional sector/search filters."""
    rows = r.list_companies(sector_class=sector, search=q, limit=limit, offset=offset)
    total = r.count_companies(sector_class=sector, search=q)
    items = [
        CompanySummary(
            ticker=row["ticker"],
            company_name=row.get("company_name"),
            sector_class=row.get("sector_class"),
            sector=row.get("sector"),
            industry=row.get("industry"),
            country=row.get("country"),
        )
        for row in rows
    ]
    return CompanyListResponse(items=items, total=total)


# ---------------------------------------------------------------------------
# /{ticker}  — overview (the only route that 404s for unknown ticker)
# ---------------------------------------------------------------------------


@router.get("/{ticker}", response_model=Dict[str, Any])
def company_overview(
    ticker: str,
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Compound overview for a single company; 404 if ticker is unknown."""
    result = r.company_overview(ticker)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Company not found: {ticker}")
    return result


# ---------------------------------------------------------------------------
# /{ticker}/financials/*
# ---------------------------------------------------------------------------


@router.get("/{ticker}/financials/annual", response_model=List[Dict[str, Any]])
def annual_financials(
    ticker: str,
    years: Optional[int] = None,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Annual financials newest-first; optionally limited to N years."""
    return r.annual_financials(ticker, years_back=years)


@router.get("/{ticker}/financials/quarterly", response_model=List[Dict[str, Any]])
def quarterly_financials(
    ticker: str,
    quarters: Optional[int] = None,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Quarterly financials newest-first; optionally limited to N quarters."""
    return r.quarterly_financials(ticker, quarters_back=quarters)


@router.get("/{ticker}/financials/ttm", response_model=List[Dict[str, Any]])
def ttm_financials(
    ticker: str,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """TTM financials newest period_end first."""
    return r.ttm_financials(ticker)


# ---------------------------------------------------------------------------
# /{ticker}/metrics  and  /{ticker}/metrics/{metric}
# ---------------------------------------------------------------------------


@router.get("/{ticker}/metrics", response_model=List[Dict[str, Any]])
def annual_metrics(
    ticker: str,
    years: Optional[int] = None,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Annual metrics newest-first; optionally limited to N years."""
    return r.annual_metrics(ticker, years_back=years)


@router.get("/{ticker}/metrics/{metric}", response_model=List[SeriesPoint])
def metric_series(
    ticker: str,
    metric: str,
    r: Reader = Depends(get_reader),
) -> List[SeriesPoint]:
    """Time-series for a single metric column (ascending by fiscal_year).

    Returns 404 if ``metric`` is not in the whitelisted column set.
    """
    try:
        rows = r.metric_series(ticker, metric)
    except ValueError:
        raise HTTPException(
            status_code=404, detail=f"unknown metric '{metric}'"
        )
    return [SeriesPoint(fiscal_year=row["fiscal_year"], value=row.get("value")) for row in rows]


# ---------------------------------------------------------------------------
# /{ticker}/snapshots
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /{ticker}/peers
# ---------------------------------------------------------------------------


@router.get("/{ticker}/peers", response_model=Dict[str, Any])
def company_peers(
    ticker: str,
    metrics: Optional[str] = None,
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Peer benchmarking: company vs sector median for the given metrics.

    ``metrics`` is a comma-separated list of metric column names; defaults to
    ``DEFAULT_COMPARE_METRICS``.  Returns 404 if the ticker is unknown or has
    no ``sector_class``; 400 if a metric is not in the whitelisted column set.
    """
    # Pre-check ticker exists and has a sector_class
    company = r.get_company(ticker)
    if company is None or company.get("sector_class") is None:
        raise HTTPException(
            status_code=404,
            detail=f"Company not found or has no sector: {ticker}",
        )

    metric_list: List[str] = (
        [m.strip() for m in metrics.split(",") if m.strip()]
        if metrics
        else DEFAULT_COMPARE_METRICS
    )

    try:
        result = r.peer_comparison(ticker, metric_list)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# /{ticker}/snapshots
# ---------------------------------------------------------------------------


@router.get("/{ticker}/snapshots", response_model=List[Dict[str, Any]])
def snapshot_history(
    ticker: str,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """All market snapshots for a ticker, ascending by collected_at."""
    return r.snapshot_history(ticker)

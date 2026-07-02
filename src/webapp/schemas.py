"""Pydantic response schemas for the stock-database web API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class CompanySummary(BaseModel):
    """Lightweight row returned in the companies list."""

    ticker: str
    company_name: Optional[str] = None
    sector_class: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None


class CompanyListResponse(BaseModel):
    """Paginated list of companies with untruncated total count."""

    items: List[CompanySummary]
    total: int


class SearchHit(BaseModel):
    """Autocomplete result from /search."""

    ticker: str
    company_name: Optional[str] = None
    sector_class: Optional[str] = None


class SeriesPoint(BaseModel):
    """Single (fiscal_year, value) point in a metric or financial time-series."""

    fiscal_year: int
    value: Optional[float] = None


class MetricFilterIn(BaseModel):
    """A single filter predicate in a screen request body."""

    field: str
    op: str
    value: float
    value2: Optional[float] = None


class ScreenRequest(BaseModel):
    """Request body for POST /api/screen."""

    filters: List[MetricFilterIn] = []
    sector: Optional[str] = None
    sort: Optional[str] = None
    sort_dir: str = "desc"
    limit: int = 100
    offset: int = 0


class JobRequest(BaseModel):
    """Request body for POST /api/collection/jobs."""

    tickers: List[str]
    years_back: Optional[int] = None
    include_yahoo: bool = True
    include_sec: bool = True

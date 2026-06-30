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

"""Pydantic response schemas for the stock-database web API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

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


# ---------------------------------------------------------------------------
# Terminal workstation: quote / bars / analyst / earnings / dividends / holders
# ---------------------------------------------------------------------------


class QuoteOut(BaseModel):
    """Latest quote for a ticker: core price fields + computed change stats."""

    ticker: Optional[str] = None
    collected_at: Optional[str] = None
    current_price: Optional[float] = None
    previous_close: Optional[float] = None
    open: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    volume: Optional[float] = None
    avg_volume: Optional[float] = None
    market_cap: Optional[float] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    pe_trailing: Optional[float] = None
    pe_forward: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None


class BarOut(BaseModel):
    """Single daily OHLCV price bar."""

    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None


class AnalystOut(BaseModel):
    """Latest analyst estimates/recommendation snapshot for a ticker."""

    ticker: Optional[str] = None
    collected_at: Optional[str] = None
    target_price_low: Optional[float] = None
    target_price_mean: Optional[float] = None
    target_price_median: Optional[float] = None
    target_price_high: Optional[float] = None
    recommendation: Optional[str] = None
    recommendation_mean: Optional[float] = None
    number_of_analysts: Optional[int] = None
    earnings_date: Optional[str] = None
    forward_eps: Optional[float] = None
    forward_pe: Optional[float] = None
    earnings_growth: Optional[float] = None
    revenue_growth: Optional[float] = None
    upside_potential: Optional[float] = None


class EarningsRow(BaseModel):
    """One quarter's earnings-surprise record (estimate vs. actual EPS)."""

    quarter: str
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    surprise_pct: Optional[float] = None


class DividendEvent(BaseModel):
    """A single dividend payment."""

    date: str
    amount: Optional[float] = None


class SplitEvent(BaseModel):
    """A single stock split event."""

    date: str
    ratio: Optional[float] = None


class HolderRow(BaseModel):
    """One institutional or mutual-fund holder record."""

    holder: str
    shares: Optional[float] = None
    date_reported: Optional[str] = None
    pct_held: Optional[float] = None
    value: Optional[float] = None


class InsiderRow(BaseModel):
    """One insider (Form-4-derived) transaction record."""

    insider: str
    start_date: str
    text: str
    position: Optional[str] = None
    shares: Optional[float] = None
    value: Optional[float] = None
    ownership: Optional[str] = None


class ProfileOut(BaseModel):
    """Company profile: the ``companies`` row plus its officer roster."""

    company: Dict[str, Any]
    officers: List[Dict[str, Any]] = []

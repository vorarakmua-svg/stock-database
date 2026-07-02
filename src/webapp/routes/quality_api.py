"""Data-quality & coverage JSON API for the stock-database web app.

All data access goes through Depends(get_reader); no SQL here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from ..dependencies import get_reader
from ..repository import Reader

router = APIRouter(prefix="/api/quality", tags=["quality"])


@router.get("/runs")
def get_collection_runs(
    ticker: Optional[str] = None,
    limit: int = 200,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Collection-run provenance rows, newest first. Optional ticker filter."""
    return r.collection_runs(ticker=ticker, limit=limit)


@router.get("/latest")
def get_latest_collection_runs(
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Most-recent collection-run row per ticker, ordered by ticker."""
    return r.latest_collection_runs()


@router.get("/coverage")
def get_coverage(
    sector: Optional[str] = None,
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Sector-level coverage and per-field fill rates.

    Returns ``{"by_sector": [...], "field_fill_rates": {...}}``.
    Optional ``?sector=`` narrows the fill-rate computation to one sector.
    """
    return {
        "by_sector": r.coverage_by_sector(),
        "field_fill_rates": r.field_fill_rates(sector_class=sector),
    }


@router.get("/unmapped")
def get_unmapped_facts(
    limit: int = 200,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Unmapped XBRL facts, most-recent collected_at first."""
    return r.unmapped_facts(limit=limit)


@router.get("/unmapped/top")
def get_unmapped_top(
    limit: int = 50,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Top unmapped tags by company count."""
    return r.unmapped_top(limit=limit)


@router.get("/freshness")
def get_data_freshness(
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """High-level data-freshness summary (latest update, n_tickers, table counts)."""
    return r.data_freshness()

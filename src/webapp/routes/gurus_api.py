"""``/api/gurus`` JSON router (Milestone 3): the gurus at their latest quarters, the aggregated portfolio (routed
before ``/{cik}``) and one guru's quarter, the data of the guru pages (``webapp.gurus``;
docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_reader
from ..gurus import guru_list, guru_portfolio, guru_view
from ..repository import Reader

router = APIRouter(prefix="/api/gurus", tags=["gurus"])


@router.get("")
def gurus(r: Reader = Depends(get_reader)) -> List[Dict[str, Any]]:
    """Each guru with a stored quarter, at its latest, the largest total value first (``gurus.guru_list``)."""
    return guru_list(r)


@router.get("/portfolio")
def portfolio(limit: int = Query(200, ge=1, le=10000), r: Reader = Depends(get_reader)) -> List[Dict[str, Any]]:
    """The gurus' aggregated portfolio over their latest quarters, the *limit* most held (``gurus.guru_portfolio``)."""
    return guru_portfolio(r, limit)


@router.get("/{cik}")
def guru(cik: str, period: Optional[str] = None, r: Reader = Depends(get_reader)) -> Dict[str, Any]:
    """One guru's quarter, its latest unless *period* names another (``gurus.guru_view``); 404 for a guru or quarter
    not stored."""
    view = guru_view(r, cik, period)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No 13F quarter stored for guru {cik}"
                                                    + (f" at {period}" if period else ""))
    return view

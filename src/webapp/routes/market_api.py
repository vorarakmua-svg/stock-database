"""``GET /api/market`` JSON router (Milestone 4): the market page's data, ``webapp.market.market_view``: the Buffett
indicator against its long-run trend, Shiller's CAPE against its history and the Treasury yield curve with its 10-year
spreads, with each section's sources (docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md, "The market
views"). 200 without the data: each section is then unavailable, its reason naming the command that collects it."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from ..dependencies import get_reader
from ..market import market_view
from ..repository import Reader

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("")
def market(r: Reader = Depends(get_reader)) -> Dict[str, Any]:
    """The market views (``market.market_view``), computed from the stored series at each request."""
    return market_view(r)

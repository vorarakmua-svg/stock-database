"""The gurus' pages (Milestone 3): the list at ``/gurus``, the aggregated portfolio at ``/gurus/portfolio`` (routed
before ``/gurus/{cik}``) and each guru's quarter at ``/gurus/{cik}`` (``?period=`` for an earlier one), over the
views of ``webapp.gurus`` (docs/superpowers/specs/2026-10-22-sec-ownership-design.md). Reuses the ``templates`` of
``routes.pages``; the formatting of a position is shared with the HDS tab's Gurus section."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..dependencies import get_reader
from ..formatting import fmt_count, fmt_money, fmt_pct
from ..gurus import CHANGE_GROUPS, guru_list, guru_portfolio, guru_view
from ..repository import Reader
from .pages import templates

router = APIRouter()

#: Each change's label and pill colour.
ACTION_LABELS: Dict[str, str] = {"new": "New", "added": "Added", "reduced": "Reduced", "sold_out": "Sold out",
                                 "unchanged": "Unchanged"}
_PILLS: Dict[str, str] = {"new": "up", "added": "up", "reduced": "down", "sold_out": "down",
                          "unchanged": "verdict-none"}
#: A filing's index on EDGAR.
_EDGAR_FILING = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"


def change_pill(action: Optional[str], change_pct: Optional[float]) -> Optional[Dict[str, Optional[str]]]:
    """A position's change as a pill: its label, with the change in shares (*pct*, signed) for an addition or a
    reduction, and its colour. None without the quarter before."""
    if action is None:
        return None
    label = ACTION_LABELS.get(action, action)
    pct = (f"{'+' if change_pct > 0 else ''}{fmt_pct(change_pct)}"
           if action in ("added", "reduced") and change_pct is not None else None)
    return {"label": f"{label} {pct}" if pct else label, "pct": pct, "pill": _PILLS.get(action, "verdict-none")}


def position_cells(p: Dict[str, Any]) -> Dict[str, Any]:
    """A position's cells, formatted: a position sold out has no shares, value or weight, only what it held before."""
    sold = p["action"] == "sold_out"
    before = f"{fmt_count(p['shares_before'])} shares, {fmt_money(p['value_before'])}" if sold else None
    return {"security": p["security"] or "—", "ticker": p["ticker"], "ours": p["ours"], "put_call": p["put_call"],
            "issuer": p["issuer"] or "—", "title_of_class": p["title_of_class"] or "—",
            "principal": p["share_type"] == "PRN", "shares": fmt_count(p["shares"]), "value": fmt_money(p["value"]),
            "weight": fmt_pct(p["weight"]), "change": change_pill(p["action"], p["change_pct"]), "before": before}


def holder_rows(holdings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The HDS tab's Gurus rows (``gurus.gurus_holding``): each guru, its quarter and its position, formatted."""
    return [{**position_cells(h), "cik": h["cik"], "manager": h["manager"] or h["cik"], "period": h["period"],
             "complete": h["complete"]} for h in holdings]


@router.get("/gurus", response_class=HTMLResponse)
def gurus_page(request: Request, r: Reader = Depends(get_reader)) -> Any:
    """The gurus, each at its latest quarter, the largest first, with a note on what a 13F holds."""
    gurus = [{**g, "name": g["manager"] or g["cik"], "filers": ", ".join(g["filers"]) or "—",
              "value": fmt_money(g["total_value"]),
              "new": "—" if g["new"] is None else g["new"], "sold_out": "—" if g["sold_out"] is None else g["sold_out"]}
             for g in guru_list(r)]
    return templates.TemplateResponse(request, "gurus.html", {"request": request, "gurus": gurus})


@router.get("/gurus/portfolio", response_class=HTMLResponse)
def guru_portfolio_page(request: Request, r: Reader = Depends(get_reader)) -> Any:
    """The gurus' aggregated portfolio over their latest quarters."""
    rows = [{**s, "shares": fmt_count(s["shares"]), "value": fmt_money(s["value"])} for s in guru_portfolio(r)]
    return templates.TemplateResponse(request, "guru_portfolio.html", {"request": request, "rows": rows})


@router.get("/gurus/{cik}", response_class=HTMLResponse)
def guru_page(cik: str, request: Request, period: Optional[str] = None, r: Reader = Depends(get_reader)) -> Any:
    """One guru's quarter: its holdings, the changes from the quarter before and its filings. 404 for a guru or
    quarter not stored."""
    view = guru_view(r, cik, period)
    if view is None:
        raise HTTPException(status_code=404, detail=f"No 13F quarter stored for guru {cik}"
                                                    + (f" at {period}" if period else ""))
    changes = None if view["changes"] is None else [
        {"label": ACTION_LABELS[group], "pill": _PILLS[group],
         "rows": [position_cells(p) for p in view["changes"][group]]} for group in CHANGE_GROUPS]
    filings = [{**f, "href": (_EDGAR_FILING.format(cik=int(f["filer_cik"]), folder=f["accession"].replace("-", ""))
                              if (f["filer_cik"] or "").isdigit() else None)} for f in view["filings"]]
    guru = {**view, "name": view["manager"] or view["cik"], "value": fmt_money(view["total_value"]),
            "positions": [position_cells(p) for p in view["positions"]],
            "shares_count": sum(p["put_call"] is None for p in view["positions"]),
            "options_count": sum(p["put_call"] is not None for p in view["positions"])}
    return templates.TemplateResponse(request, "guru.html", {"request": request, "g": guru, "changes": changes,
                                                             "filings": filings})

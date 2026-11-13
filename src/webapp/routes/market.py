"""The market page (Milestone 4) at ``/market``: the Buffett indicator, Shiller's CAPE and the Treasury yield curve,
each with its headline figures, its chart, its sources and its method note, over ``webapp.market.market_view`` (the
data of ``GET /api/market``; docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md, "The market views").
A section without its data says how to collect it. ``renderMarket`` (static/app.js) draws the charts from the page's
inline JSON config. Reuses the ``templates`` of ``routes.pages``."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ..dependencies import get_reader
from ..formatting import fmt_pct
from ..market import COLLECT_COMMAND, MATURITIES, market_view
from ..repository import Reader
from .pages import templates

router = APIRouter()

#: How each section's source line names its latest date, and how much of the date it shows.
_LATEST: Dict[str, Tuple[str, int]] = {"buffett": ("latest quarter", 10), "cape": ("latest month", 7),
                                       "yield_curve": ("latest day", 10)}


def _percent(x: Optional[float]) -> str:
    """A figure already in percent, as the Buffett ratio: 255.04 -> "255.0%"."""
    return "—" if x is None else f"{x:.1f}%"


def _yield(x: Optional[float]) -> str:
    """A yield in percent, as Treasury.gov publishes it: "4.69%"."""
    return "—" if x is None else f"{x:.2f}%"


def _points(x: Optional[float]) -> str:
    """A spread or a change in percentage points, signed: "+0.31 pp"."""
    return "—" if x is None else f"{x:+.2f} pp"


def _one(x: Optional[float]) -> str:
    """A CAPE: one decimal."""
    return "—" if x is None else f"{x:.1f}"


def _month(day: Optional[str]) -> str:
    return day[:7] if day else "—"


def _quarter(day: str) -> str:
    """FRED dates a quarter by its first day: "2026-04-01" is "Q2 2026". Another date stays as it is."""
    if day[7:] == "-01" and day[5:7] in ("01", "04", "07", "10"):
        return f"Q{(int(day[5:7]) - 1) // 3 + 1} {day[:4]}"
    return day


def _empty(section: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """An unavailable section's message, its collect command apart for the template to set in <code>."""
    reason, command = section["reason"], section.get("collect")
    if command and command in reason:
        before, _, after = reason.partition(command)
        return {"before": before, "command": command, "after": after}
    return {"before": reason, "command": None, "after": ""}


def _buffett(b: Dict[str, Any]) -> Dict[str, Any]:
    if not b["available"]:
        return {"available": False, "empty": _empty(b), "method": b["method"]}
    zone = b["zone"]
    return {"available": True, "zone": zone[:1].upper() + zone[1:], "pill": b["pill"], "ratio": _percent(b["ratio"]),
            "quarter": _quarter(b["date"]), "trend": _percent(b["trend"]), "z": f"{b['z']:+.2f} sd",
            "percentile": fmt_pct(b["percentile"]), "growth": fmt_pct(b["growth"]), "since": b["first_date"][:4],
            "method": b["method"]}


def _cape(c: Dict[str, Any]) -> Dict[str, Any]:
    if not c["available"]:
        return {"available": False, "empty": _empty(c), "method": c["method"]}
    return {"available": True, "month": _month(c["date"]), "value": _one(c["value"]), "since": c["first_date"][:4],
            "mean": _one(c["mean"]), "median": _one(c["median"]), "min": _one(c["min"]),
            "min_month": _month(c["min_date"]), "max": _one(c["max"]), "max_month": _month(c["max_date"]),
            "percentile": fmt_pct(c["percentile"]), "method": c["method"]}


def _episode(episode: Optional[Dict[str, Any]], days: int) -> str:
    """A spread's latest sustained inversion: its first and last inverted day and its inverted days; "none" for a
    spread without one, "—" for a spread without days."""
    if episode is None:
        return "none" if days else "—"
    return f"{episode['first']} to {episode['last']}, {episode['days']} inverted days"


def _curve(y: Dict[str, Any]) -> Dict[str, Any]:
    if not y["available"]:
        return {"available": False, "empty": _empty(y), "method": y["method"]}
    year_ago = y["year_ago"]
    now = {p["maturity"]: p["value"] for p in y["curve"]}
    then = {p["maturity"]: p["value"] for p in year_ago["curve"]} if year_ago else {}
    rows = [{"label": label, "now": _yield(now.get(m)), "then": _yield(then.get(m)),
             "change": _points(now[m] - then[m]) if m in now and m in then else "—"}
            for m, label in MATURITIES if m in now or m in then]
    spreads = [{"label": s["label"], "date": s["latest_date"] or "—", "value": _points(s["latest_value"]),
                "inverted": s["inverted"], "share": fmt_pct(s["inverted_share"]),
                "since": (s["first_date"] or "")[:4], "episode": _episode(s["latest_episode"], s["days"]),
                "last_inverted": s["last_inverted_day"] or ("never" if s["days"] else "—")} for s in y["spreads"]]
    return {"available": True, "date": y["date"], "year_ago": year_ago["date"] if year_ago else None, "rows": rows,
            "spreads": spreads, "method": y["method"]}


def _sources(sources: Dict[str, Any]) -> Dict[str, Any]:
    """Each section's source line: its sources' names, links and fetch days, and its latest date."""
    out: Dict[str, Any] = {}
    for section, s in sources.items():
        label, width = _LATEST[section]
        out[section] = {"latest_label": label, "latest": (s["latest"] or "")[:width] or "—",
                        "fetches": [{"name": f["name"], "url": f["url"],
                                     "fetched": str(f["fetched_at"])[:10] if f["fetched_at"] else None}
                                    for f in s["fetches"]]}
    return out


def _trace(day: str, curve: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"date": day, "labels": [p["label"] for p in curve], "values": [p["value"] for p in curve]}


def _chart_config(view: Dict[str, Any]) -> Dict[str, Any]:
    """``renderMarket``'s config: each available section's series (None for one that isn't). The curve's maturities
    are those either day has, shortest first."""
    b, c, y = view["buffett"], view["cape"], view["yield_curve"]
    curve: Optional[Dict[str, Any]] = None
    spreads: Optional[List[Dict[str, Any]]] = None
    if y["available"]:
        then = y["year_ago"]
        either = {p["maturity"] for p in y["curve"]} | ({p["maturity"] for p in then["curve"]} if then else set())
        curve = {"labels": [label for m, label in MATURITIES if m in either], "today": _trace(y["date"], y["curve"]),
                 "year_ago": _trace(then["date"], then["curve"]) if then else None}
        spreads = [{"label": s["label"], "dates": s["dates"], "values": s["values"]} for s in y["spreads"]]
    return {
        "buffett": dict(b["series"]) if b["available"] else None,
        "cape": ({"dates": c["series"]["dates"], "values": c["series"]["values"], "mean": c["mean"],
                  "median": c["median"]} if c["available"] else None),
        "curve": curve,
        "spreads": spreads,
    }


@router.get("/market", response_class=HTMLResponse)
def market_page(request: Request, r: Reader = Depends(get_reader)) -> Any:
    """The market page: the three sections, each available one with its figures and chart, the others with the
    command that collects them."""
    view = market_view(r)
    return templates.TemplateResponse(
        request,
        "market.html",
        {
            "request": request,
            "available": view["available"],
            "command": COLLECT_COMMAND,
            "buffett": _buffett(view["buffett"]),
            "cape": _cape(view["cape"]),
            "curve": _curve(view["yield_curve"]),
            "sources": _sources(view["sources"]),
            # The XSS defense of the other inline configs: "</" can't close the <script>.
            "cfg_json": json.dumps(_chart_config(view)).replace("</", "<\\/") if view["available"] else None,
        },
    )

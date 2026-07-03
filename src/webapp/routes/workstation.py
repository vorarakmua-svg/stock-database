"""The per-stock workstation page (``/stocks/{ticker}``) and its function tabs.

Reuses the ``templates`` instance from ``routes.pages`` (single Jinja2Templates
environment for the whole app — see that module's docstring). Tabs not yet
implemented here (FA/ERN/STAT/HP/DVD/HDS/INS) still render as buttons in the
tab bar; clicking them 404s until their tasks land.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..dependencies import get_reader
from ..formatting import fmt_money, fmt_mult, fmt_pct, fmt_price, fmt_raw2
from ..repository import Reader
from .pages import templates
from .stocks_api import VALID_RANGES

router = APIRouter()

# (label, key) for every function tab in the workstation tab bar. Order matters —
# it's the order the buttons render in.
TABS: List[Tuple[str, str]] = [
    ("DES", "des"),
    ("GP", "gp"),
    ("FA", "fa"),
    ("ERN", "ern"),
    ("STAT", "stat"),
    ("HP", "hp"),
    ("DVD", "dvd"),
    ("HDS", "hds"),
    ("INS", "ins"),
]
_TAB_KEYS = frozenset(key for _, key in TABS)

_GP_RANGES: List[str] = ["1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"]
_GP_INDICATORS: List[Tuple[str, str]] = [
    ("ma50", "MA50"),
    ("ma200", "MA200"),
    ("rsi", "RSI"),
    ("macd", "MACD"),
]
_BENCHMARK_TICKER = "^GSPC"
_BENCHMARK_LABEL = "S&P 500"


# ---------------------------------------------------------------------------
# Page: /stocks/{ticker}
# ---------------------------------------------------------------------------


@router.get("/stocks/{ticker}", response_class=HTMLResponse)
def stock_page(
    ticker: str,
    request: Request,
    tab: str = "des",
    r: Reader = Depends(get_reader),
) -> Any:
    """The workstation shell: header strip + tab bar. 404 if *ticker* is unknown.

    ``?tab=`` picks which tab fires on page load (``hx-trigger="load"``); an
    unrecognized value falls back to ``des`` rather than erroring.
    """
    company = r.get_company(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    active_tab = tab if tab in _TAB_KEYS else "des"
    return templates.TemplateResponse(
        request,
        "stock.html",
        {
            "request": request,
            "company": company,
            "ticker": ticker,
            "tabs": TABS,
            "active_tab": active_tab,
        },
    )


# ---------------------------------------------------------------------------
# DES fragment
# ---------------------------------------------------------------------------


def _range_marker_pct(
    price: Optional[float], low: Optional[float], high: Optional[float]
) -> Optional[float]:
    """Position (0..100) of *price* within [*low*, *high*] for the 52-wk marker.

    ``None`` if any input is missing or the range is degenerate (high == low).
    """
    if price is None or low is None or high is None or high == low:
        return None
    pct = (price - low) / (high - low) * 100.0
    return max(0.0, min(100.0, pct))


@router.get("/ui/stocks/{ticker}/des", response_class=HTMLResponse)
def des_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """DES panel: quote header, summary grid, description, officers, next earnings."""
    profile = r.profile(ticker)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    quote = r.quote(ticker)
    analyst = r.analyst_snapshot(ticker)

    change = quote.get("change") if quote else None
    change_class = "up" if (change is not None and change >= 0) else "down"

    fifty_two_low = quote.get("fifty_two_week_low") if quote else None
    fifty_two_high = quote.get("fifty_two_week_high") if quote else None
    current_price = quote.get("current_price") if quote else None
    marker_pct = _range_marker_pct(current_price, fifty_two_low, fifty_two_high)

    summary: Dict[str, str] = {
        "open": fmt_price(quote.get("open") if quote else None),
        "day_low": fmt_price(quote.get("day_low") if quote else None),
        "day_high": fmt_price(quote.get("day_high") if quote else None),
        "fifty_two_low": fmt_price(fifty_two_low),
        "fifty_two_high": fmt_price(fifty_two_high),
        "volume": fmt_money(quote.get("volume") if quote else None),
        "avg_volume": fmt_money(quote.get("avg_volume") if quote else None),
        "market_cap": fmt_money(quote.get("market_cap") if quote else None),
        "pe_trailing": fmt_mult(quote.get("pe_trailing") if quote else None),
        "pe_forward": fmt_mult(quote.get("pe_forward") if quote else None),
        "eps": fmt_raw2(quote.get("eps_trailing") if quote else None),
        "beta": fmt_raw2(quote.get("beta") if quote else None),
        "dividend_yield": fmt_pct(quote.get("dividend_yield") if quote else None),
        "ex_dividend_date": (quote.get("ex_dividend_date") if quote else None) or "—",
    }

    return templates.TemplateResponse(
        request,
        "fragments/des.html",
        {
            "request": request,
            "ticker": ticker,
            "company": profile["company"],
            "officers": profile["officers"],
            "quote": quote,
            "price_fmt": fmt_price(current_price),
            "change_fmt": fmt_price(change) if change is not None else "—",
            "change_pct_fmt": fmt_pct(quote.get("change_pct") if quote else None),
            "change_class": change_class,
            "post_market_fmt": (
                fmt_price(quote.get("post_market_price")) if quote else None
            ),
            "has_post_market": bool(quote and quote.get("post_market_price") is not None),
            "collected_at": (quote.get("collected_at") if quote else None),
            "marker_pct": marker_pct,
            "summary": summary,
            "description": profile["company"].get("description"),
            "earnings_date": analyst.get("earnings_date") if analyst else None,
        },
    )


# ---------------------------------------------------------------------------
# GP fragment
# ---------------------------------------------------------------------------


def _split_csv_params(values: List[str]) -> List[str]:
    """Flatten HTMX/query-string values that may be comma-joined or repeated.

    Accepts either ``["ma50,rsi"]`` (one comma-joined value) or
    ``["ma50", "rsi"]`` (repeated query params) — both are common depending on
    whether the request came from a single-value link or a multi-select/
    multi-checkbox form submission.
    """
    result: List[str] = []
    for value in values:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


@router.get("/ui/stocks/{ticker}/gp", response_class=HTMLResponse)
def gp_fragment(
    ticker: str,
    request: Request,
    range: str = "1Y",  # noqa: A002 - matches the public query-param name
    type: str = "line",  # noqa: A002 - matches the public query-param name
    r: Reader = Depends(get_reader),
) -> Any:
    """GP panel: range/type/indicator/compare controls + the ``renderGP`` chart hook."""
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    resolved_range = range if range in VALID_RANGES else "1Y"
    chart_type = type if type in ("line", "candle") else "line"

    indicators = _split_csv_params(request.query_params.getlist("ind"))
    compare = _split_csv_params(request.query_params.getlist("compare"))

    companies = r.list_companies(limit=1000)
    compare_options = [
        (c["ticker"], c.get("company_name") or c["ticker"])
        for c in companies
        if c["ticker"] != ticker
    ]

    cfg = {
        "ticker": ticker,
        "range": resolved_range,
        "chartType": chart_type,
        "indicators": indicators,
        "compare": compare,
    }

    return templates.TemplateResponse(
        request,
        "fragments/gp.html",
        {
            "request": request,
            "ticker": ticker,
            "range": resolved_range,
            "range_options": _GP_RANGES,
            "chart_type": chart_type,
            "indicator_options": _GP_INDICATORS,
            "indicators": indicators,
            "compare": compare,
            "compare_options": compare_options,
            "benchmark_ticker": _BENCHMARK_TICKER,
            "benchmark_label": _BENCHMARK_LABEL,
            "cfg_json": json.dumps(cfg),
        },
    )

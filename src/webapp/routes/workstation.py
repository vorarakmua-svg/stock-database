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
from ..formatting import fmt_money, fmt_mult, fmt_pct, fmt_price, fmt_raw2, fmt_value
from ..repository import Reader
from .pages import _STATEMENT_ROWS, _build_statement_display, templates
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
    if change is None:
        change_class = "flat"
    else:
        change_class = "up" if change >= 0 else "down"

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
            "change_fmt": fmt_price(change),
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

    raw_indicators = _split_csv_params(request.query_params.getlist("ind"))
    raw_compare = _split_csv_params(request.query_params.getlist("compare"))

    companies = r.list_companies(limit=1000)
    compare_options = [
        (c["ticker"], c.get("company_name") or c["ticker"])
        for c in companies
        if c["ticker"] != ticker
    ]

    # Whitelist: only known indicator keys / known comparison tickers may flow
    # into cfg (and from there into the inline <script> below). Unknown tokens
    # are silently dropped rather than rejected — this is a reflected-input
    # surface (query params rendered back into HTML/JSON), so anything not on
    # the allowed set must never reach the template.
    _valid_indicator_keys = frozenset(key for key, _ in _GP_INDICATORS)
    _valid_compare_tickers = frozenset(
        [_BENCHMARK_TICKER] + [t for t, _ in compare_options]
    )
    indicators = [i for i in raw_indicators if i in _valid_indicator_keys]
    compare = [c for c in raw_compare if c in _valid_compare_tickers]

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
            # Defense in depth: json.dumps doesn't escape "</", so a literal
            # "</script>" inside a whitelisted-but-still-attacker-influenced
            # string (e.g. a ticker name) could break out of the inline
            # <script> block in gp.html. The escaped sequence is still valid
            # JSON and inert once parsed.
            "cfg_json": json.dumps(cfg).replace("</", "<\\/"),
        },
    )


# ---------------------------------------------------------------------------
# FA fragment
# ---------------------------------------------------------------------------


@router.get("/ui/stocks/{ticker}/fa", response_class=HTMLResponse)
def fa_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """FA panel: inner period tabs + the annual statement table, pre-rendered.

    Reuses ``pages._build_statement_display`` — the exact helper backing
    ``/ui/companies/{ticker}/statements`` — to build the initial (annual)
    table server-side so it's present on first render, not only after an
    HTMX-driven follow-up request. Clicking a period tab swaps ``#fa-statements``
    via that SAME existing fragment endpoint; there is no second copy of the
    statement-building logic.
    """
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    rows_data = r.annual_financials(ticker)
    columns, display_rows = _build_statement_display(
        rows_data, "fiscal_year", _STATEMENT_ROWS
    )
    return templates.TemplateResponse(
        request,
        "fragments/fa.html",
        {
            "request": request,
            "ticker": ticker,
            "columns": columns,
            "rows": display_rows,
        },
    )


# ---------------------------------------------------------------------------
# STAT fragment
# ---------------------------------------------------------------------------

# (label, key, kind) — kind "raw2" is handled as fmt_raw2 (bare 2dp, no
# suffix); everything else dispatches through fmt_value. Share counts use
# "money" (same treatment _STATEMENT_ROWS in pages.py already gives
# shares_outstanding) for consistency across the app rather than introducing
# a new unformatted-thousands-separator kind.
_STAT_PROFITABILITY: List[Tuple[str, str, str]] = [
    ("Gross margin", "gross_margin", "pct"),
    ("Operating margin", "operating_margin", "pct"),
    ("Net margin", "net_margin", "pct"),
    ("EBITDA margin", "ebitda_margin", "pct"),
    ("ROA", "roa", "pct"),
    ("ROE", "roe", "pct"),
    ("ROIC", "roic", "pct"),
]

_STAT_LEVERAGE: List[Tuple[str, str, str]] = [
    ("Debt/Equity", "debt_to_equity", "mult"),
    ("Current ratio", "current_ratio", "mult"),
    ("Quick ratio", "quick_ratio", "mult"),
    ("Interest coverage", "interest_coverage", "mult"),
    ("Debt/EBITDA", "debt_to_ebitda", "mult"),
]

_STAT_VALUATION: List[Tuple[str, str, str]] = [
    ("P/E (trailing)", "pe_trailing", "mult"),
    ("P/E (forward)", "pe_forward", "mult"),
    ("PEG ratio", "peg_ratio", "mult"),
    ("Price/Sales", "price_to_sales", "mult"),
    ("Price/Book", "price_to_book", "mult"),
    ("EV/EBITDA", "ev_to_ebitda", "mult"),
]

_STAT_SHARES: List[Tuple[str, str, str]] = [
    ("Shares outstanding", "shares_outstanding", "money"),
    ("Float shares", "float_shares", "money"),
    ("Shares short", "shares_short", "money"),
    ("Short ratio (days to cover)", "short_ratio", "raw2"),
    ("Short % of float", "short_percent_of_float", "pct"),
    ("Insider %", "insider_percent", "pct"),
    ("Institutional %", "institutional_percent", "pct"),
]


def _build_stat_rows(
    source: Dict[str, Any], spec: List[Tuple[str, str, str]]
) -> List[Dict[str, str]]:
    """Format a fixed set of (label, key, kind) rows from *source*.

    Unlike the multi-period statement table, a dense single-value grid never
    omits a label just because its value is missing — every row always
    renders, with ``fmt_value``/``fmt_raw2`` turning ``None`` into "—".
    """
    rows: List[Dict[str, str]] = []
    for label, key, kind in spec:
        value = source.get(key)
        formatted = fmt_raw2(value) if kind == "raw2" else fmt_value(value, kind)
        rows.append({"label": label, "value": formatted})
    return rows


@router.get("/ui/stocks/{ticker}/stat", response_class=HTMLResponse)
def stat_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """STAT panel: dense profitability/leverage/valuation/share-stats grid."""
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    quote = r.quote(ticker) or {}
    metrics_rows = r.annual_metrics(ticker, years_back=1)
    latest_metrics = metrics_rows[0] if metrics_rows else {}
    # Disjoint field names between market_snapshots and metrics_annual, so
    # merge order doesn't matter — this just lets every row spec below read
    # from one dict regardless of which table its key actually lives in.
    merged: Dict[str, Any] = {**quote, **latest_metrics}

    return templates.TemplateResponse(
        request,
        "fragments/stat.html",
        {
            "request": request,
            "ticker": ticker,
            "profitability": _build_stat_rows(merged, _STAT_PROFITABILITY),
            "leverage": _build_stat_rows(merged, _STAT_LEVERAGE),
            "valuation": _build_stat_rows(merged, _STAT_VALUATION),
            "shares": _build_stat_rows(merged, _STAT_SHARES),
        },
    )


# ---------------------------------------------------------------------------
# ERN fragment
# ---------------------------------------------------------------------------


@router.get("/ui/stocks/{ticker}/ern", response_class=HTMLResponse)
def ern_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """ERN panel: earnings-surprise history + renderERN chart, analyst
    target range / recommendation gauge / growth / next earnings date.
    """
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    history = r.earnings_history(ticker)
    analyst = r.analyst_snapshot(ticker)
    quote = r.quote(ticker)

    earnings_rows: List[Dict[str, str]] = []
    for row in history:
        surprise = row.get("surprise_pct")
        if surprise is None:
            surprise_class = "flat"
            surprise_fmt = "—"
        else:
            surprise_class = "up" if surprise >= 0 else "down"
            surprise_fmt = f"{surprise:+.1f}%"
        earnings_rows.append(
            {
                "quarter": str(row.get("quarter", "")),
                "estimate": fmt_raw2(row.get("eps_estimate")),
                "actual": fmt_raw2(row.get("eps_actual")),
                "surprise": surprise_fmt,
                "surprise_class": surprise_class,
            }
        )

    # earnings_history stores surprise_pct as an already-scaled percentage
    # (e.g. -8.7 meaning -8.7%), unlike the 0..1 fractions fmt_pct expects
    # elsewhere — hence the bespoke f"{:+.1f}%" formatting above instead of
    # fmt_pct.
    ern_cfg = {
        "quarters": [row.get("quarter") for row in history],
        "estimates": [row.get("eps_estimate") for row in history],
        "actuals": [row.get("eps_actual") for row in history],
    }

    target_low = analyst.get("target_price_low") if analyst else None
    target_mean = analyst.get("target_price_mean") if analyst else None
    target_median = analyst.get("target_price_median") if analyst else None
    target_high = analyst.get("target_price_high") if analyst else None
    current_price = quote.get("current_price") if quote else None
    recommendation_mean = analyst.get("recommendation_mean") if analyst else None
    number_of_analysts = analyst.get("number_of_analysts") if analyst else None

    return templates.TemplateResponse(
        request,
        "fragments/ern.html",
        {
            "request": request,
            "ticker": ticker,
            "earnings_rows": earnings_rows,
            # Same XSS defense-in-depth as gp_fragment's cfg_json: json.dumps
            # doesn't escape "</", so any string value winding up in here
            # (there is none from user input today — only DB-derived numbers
            # and dates — but this keeps the inline-<script> pattern uniform
            # and safe against future fields) gets the closing tag escaped.
            "ern_cfg_json": json.dumps(ern_cfg).replace("</", "<\\/"),
            "target_low_fmt": fmt_price(target_low),
            "target_mean_fmt": fmt_price(target_mean),
            "target_median_fmt": fmt_price(target_median),
            "target_high_fmt": fmt_price(target_high),
            "price_marker_pct": _range_marker_pct(current_price, target_low, target_high),
            "mean_marker_pct": _range_marker_pct(target_mean, target_low, target_high),
            "recommendation_mean_fmt": fmt_raw2(recommendation_mean),
            "gauge_marker_pct": _range_marker_pct(recommendation_mean, 1.0, 5.0),
            "number_of_analysts": number_of_analysts,
            "earnings_growth_fmt": fmt_pct(analyst.get("earnings_growth") if analyst else None),
            "revenue_growth_fmt": fmt_pct(analyst.get("revenue_growth") if analyst else None),
            "earnings_date": (analyst.get("earnings_date") if analyst else None),
        },
    )

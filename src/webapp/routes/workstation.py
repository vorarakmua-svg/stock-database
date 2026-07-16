"""The per-stock workstation page (``/stocks/{ticker}``) and its function tabs.

Reuses the ``templates`` instance from ``routes.pages`` (single Jinja2Templates
environment for the whole app — see that module's docstring). Tabs not yet
implemented here (FA/ERN/STAT/HP/DVD/HDS/INS) still render as buttons in the
tab bar; clicking them 404s until their tasks land.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...valuation.assumptions import DISCOUNT_SPREAD, GROWTH_CAP
from ...valuation.engine import VERDICT_LABELS, owner_earnings_verdict, upside_pct, verdict
from ...valuation.models import ValuationResult, dcf_per_share
from ..dependencies import get_job_manager, get_reader, get_settings
from ..formatting import fmt_money, fmt_mult, fmt_pct, fmt_price, fmt_raw2, fmt_value
from ..jobs import CollectionJobManager
from ..repository import Reader
from ..settings import WebSettings
from .pages import _STATEMENT_ROWS, _build_statement_display, templates
from .stocks_api import VALID_RANGES, resolve_range_start

router = APIRouter()

#: A string that could plausibly be a ticker symbol. Anything else 404s rather
#: than being sent to SEC/Yahoo.
_TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,5}$")

# (label, code, key) for every function tab in the workstation tab bar. Order
# matters — it's the order the buttons render in. `label` is the friendly name
# shown first, `code` is the terminal-style abbreviation shown in a `.tab-code`
# span alongside it (e.g. "Overview DES"), `key` is the fragment-route key.
TABS: List[Tuple[str, str, str]] = [
    ("Overview", "DES", "des"),
    ("Chart", "GP", "gp"),
    ("Financials", "FA", "fa"),
    ("Earnings", "ERN", "ern"),
    ("Statistics", "STAT", "stat"),
    ("History", "HP", "hp"),
    ("Dividends", "DVD", "dvd"),
    ("Holders", "HDS", "hds"),
    ("Insiders", "INS", "ins"),
    ("Valuation", "VAL", "val"),
]
_TAB_KEYS = frozenset(key for _, _, key in TABS)

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
# UPDATE DATA fragment: submit a single-ticker collection job
# ---------------------------------------------------------------------------


@router.post("/ui/stocks/{ticker}/update", response_class=HTMLResponse)
def update_ticker_fragment(
    ticker: str,
    request: Request,
    manager: CollectionJobManager = Depends(get_job_manager),
    settings: WebSettings = Depends(get_settings),
) -> Any:
    """Submit a single-ticker collection job and return the status fragment.

    Deliberately does NOT require the ticker to exist in the database — this
    same route fetches brand-new tickers (see the unknown-ticker page). The
    job runner recomputes valuations after export, so one click updates
    everything.
    """
    if not settings.allow_collection:
        return HTMLResponse("<p>Collection is disabled.</p>", status_code=409)
    symbol = ticker.strip().upper()
    if not _TICKER_RE.match(symbol):
        raise HTTPException(status_code=404, detail=f"Not a ticker: {ticker}")
    job_id = manager.submit(
        tickers=[symbol], years_back=None, include_yahoo=True, include_sec=True,
    )
    job = manager.get(job_id)
    return templates.TemplateResponse(
        request,
        "fragments/job_status.html",
        {"request": request, "job": job, "terminal": False},
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
    settings: WebSettings = Depends(get_settings),
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

    val_summary = r.valuation_summary(ticker)
    val_verdict = verdict(
        val_summary.get("median_bear") if val_summary else None,
        val_summary.get("median_bull") if val_summary else None,
        current_price,
    )

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
            "allow_quote_refresh": settings.allow_quote_refresh,
            "val_verdict": val_verdict,
            "val_verdict_label": VERDICT_LABELS[val_verdict],
            "val_upside_fmt": fmt_pct(upside_pct(
                val_summary.get("median_base") if val_summary else None,
                current_price,
            )),
        },
    )


# ---------------------------------------------------------------------------
# Quote-refresh poll fragment (Task 10)
# ---------------------------------------------------------------------------


@router.get("/ui/stocks/{ticker}/refresh-status/{job_id}", response_class=HTMLResponse)
def refresh_status_fragment(
    ticker: str,
    job_id: str,
    request: Request,
    manager: CollectionJobManager = Depends(get_job_manager),
) -> Any:
    """Quote-refresh poll fragment for the DES REFRESH button.

    Polled (via ``htmx.ajax`` kicked off from the button's response handler,
    then self-polling) until the job reaches a terminal state; ``done``
    triggers a DES panel reload, ``error`` shows the failure message. See
    ``templates/fragments/refresh_status.html`` for exactly how polling stops.
    """
    job = manager.get(job_id)
    if job is None:
        return HTMLResponse(
            '<div id="refresh-status">Refresh job not found.</div>', status_code=404
        )
    terminal = job.state in ("done", "error")
    return templates.TemplateResponse(
        request,
        "fragments/refresh_status.html",
        {"request": request, "ticker": ticker, "job": job, "terminal": terminal},
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


# ---------------------------------------------------------------------------
# HP fragment
# ---------------------------------------------------------------------------


@router.get("/ui/stocks/{ticker}/hp", response_class=HTMLResponse)
def hp_fragment(
    ticker: str,
    request: Request,
    range: str = "1Y",  # noqa: A002 - matches the public query-param name
    r: Reader = Depends(get_reader),
) -> Any:
    """HP panel: OHLCV table newest-first + range buttons + CSV download link.

    Reuses ``stocks_api.resolve_range_start`` for range->start-date resolution
    (no duplicate range math) and validates ``range`` against the same
    ``VALID_RANGES`` set the GP route uses — an unknown value silently falls
    back to ``1Y`` rather than being echoed back raw or erroring.
    """
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    resolved_range = range if range in VALID_RANGES else "1Y"
    start = resolve_range_start(resolved_range)
    start_iso = start.isoformat() if start is not None else None
    bars = r.price_bars(ticker, start=start_iso)

    # price_bars() is ascending by date; the HP table reads newest-first.
    rows = [
        {
            "date": b["date"],
            "open": fmt_price(b.get("open")),
            "high": fmt_price(b.get("high")),
            "low": fmt_price(b.get("low")),
            "close": fmt_price(b.get("close")),
            # Volume is a share count, not a dollar amount, but fmt_money's
            # B/M/K-suffixed formatting is the same treatment DES/STAT already
            # give share counts (e.g. DES's summary.volume) — kept consistent
            # here rather than introducing a new unformatted-thousands kind.
            "volume": fmt_money(b.get("volume")),
        }
        for b in reversed(bars)
    ]

    return templates.TemplateResponse(
        request,
        "fragments/hp.html",
        {
            "request": request,
            "ticker": ticker,
            "range": resolved_range,
            "range_options": _GP_RANGES,
            "rows": rows,
            "csv_href": f"/api/export/stock/{ticker}/bars.csv?range={resolved_range}",
        },
    )


# ---------------------------------------------------------------------------
# DVD fragment
# ---------------------------------------------------------------------------


def _annual_dividend_sums(events: List[Dict[str, Any]]) -> Dict[int, float]:
    """Sum dividend ``amount`` by calendar year from ascending ``dividend_events``.

    Mirrors the year-bucketing step in ``yahoo_handler._get_dividend_history``
    (``dividends.resample('YE').sum()``), computed directly from event rows
    already read from the DB rather than a full daily-indexed series.

    ``resample('YE')`` produces one bin per calendar year across the FULL
    span from the first to the last dividend payment — years with no
    payments (a lapsed/suspended dividend) still appear as a ``0.0`` bin.
    To match that exactly, every year in the inclusive range
    ``min(year)..max(year)`` is present in the returned dict, defaulting to
    ``0.0`` when no event fell in that year. Without this fill, a gap year
    would simply be absent, shrinking the apparent span and diverging from
    the handler's CAGR/consistency math.
    """
    sums: Dict[int, float] = {}
    for event in events:
        raw_date = event.get("date")
        amount = event.get("amount")
        if not raw_date or amount is None:
            continue
        year = int(str(raw_date)[:4])
        sums[year] = sums.get(year, 0.0) + float(amount)
    if not sums:
        return sums
    min_year, max_year = min(sums), max(sums)
    return {year: sums.get(year, 0.0) for year in range(min_year, max_year + 1)}


def _dividend_cagr(annual_sums: Dict[int, float]) -> Optional[float]:
    """Dividend CAGR, using the SAME formula as ``_get_dividend_history``.

    ``(last_positive_year / first_positive_year) ** (1 / years) - 1``, where
    ``years = len(annual_sums) - 1`` (the handler's divisor — the number of
    years spanned by the annual series, not the count of positive years) and
    first/last are the amounts of the first/last calendar years with a
    positive sum. ``None`` if fewer than 2 years of data, there are no
    positive years, or the guard (``first_year > 0 and years > 0``) fails.
    """
    if len(annual_sums) < 2:
        return None
    positive_years = sorted(year for year, amount in annual_sums.items() if amount > 0)
    if not positive_years:
        return None
    first_year_amount = annual_sums[positive_years[0]]
    last_year_amount = annual_sums[positive_years[-1]]
    years = len(annual_sums) - 1
    if first_year_amount > 0 and years > 0:
        return (last_year_amount / first_year_amount) ** (1 / years) - 1
    return None


def _dividend_consistency(annual_sums: Dict[int, float]) -> Optional[float]:
    """Share of consecutive positive-dividend years that did not decrease.

    Mirrors ``_get_dividend_history``'s ``dividend_consistency``: among
    calendar years with a positive sum (chronological order), the fraction
    where ``amount[i] >= amount[i - 1]``. ``None`` if fewer than 2 years of
    data overall, or fewer than 2 positive years.
    """
    if len(annual_sums) < 2:
        return None
    positive_years = sorted(year for year, amount in annual_sums.items() if amount > 0)
    annual_list = [annual_sums[year] for year in positive_years]
    if len(annual_list) <= 1:
        return None
    increases = sum(
        1 for i in range(1, len(annual_list)) if annual_list[i] >= annual_list[i - 1]
    )
    return increases / (len(annual_list) - 1)


def _fmt_split_ratio(ratio: Optional[float]) -> str:
    """Format a split ratio as e.g. ``2.00 : 1``; ``None`` -> "—"."""
    if ratio is None:
        return "—"
    return f"{ratio:.2f} : 1"


@router.get("/ui/stocks/{ticker}/dvd", response_class=HTMLResponse)
def dvd_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """DVD panel: dividend stats header, annual bar chart, payments + splits tables.

    CAGR/consistency are computed here from ``dividend_events`` with the SAME
    formulas ``yahoo_handler._get_dividend_history`` uses on the raw yfinance
    series (see ``_dividend_cagr``/``_dividend_consistency`` above) — re-derived
    from what's already in the DB, not re-fetched from Yahoo.
    """
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    quote = r.quote(ticker) or {}
    events = r.dividend_events(ticker)
    raw_splits = r.split_events(ticker)

    annual_sums = _annual_dividend_sums(events)
    cagr = _dividend_cagr(annual_sums)
    consistency = _dividend_consistency(annual_sums)
    years = sorted(annual_sums)

    payment_rows = [
        {"date": e.get("date", ""), "amount": fmt_price(e.get("amount"))}
        for e in reversed(events)
    ]
    split_rows = [
        {"date": s.get("date", ""), "ratio": _fmt_split_ratio(s.get("ratio"))}
        for s in reversed(raw_splits)
    ]

    dvd_cfg = {
        "years": [str(y) for y in years],
        "amounts": [annual_sums[y] for y in years],
    }

    return templates.TemplateResponse(
        request,
        "fragments/dvd.html",
        {
            "request": request,
            "ticker": ticker,
            "dividend_rate_fmt": fmt_price(quote.get("dividend_rate")),
            "dividend_yield_fmt": fmt_pct(quote.get("dividend_yield")),
            "payout_ratio_fmt": fmt_pct(quote.get("payout_ratio")),
            "ex_dividend_date": quote.get("ex_dividend_date") or "—",
            "cagr_fmt": fmt_pct(cagr),
            "consistency_fmt": fmt_pct(consistency),
            "payment_rows": payment_rows,
            "split_rows": split_rows,
            # Same XSS defense-in-depth as gp_fragment's cfg_json/ern_fragment's
            # ern_cfg_json — escape "</" so a literal "</script>" can't break
            # out of the inline <script> block, even though today's inputs
            # here are DB-derived numbers/year strings only.
            "dvd_cfg_json": json.dumps(dvd_cfg).replace("</", "<\\/"),
            "csv_href": f"/api/export/stock/{ticker}/dividends.csv",
        },
    )


# ---------------------------------------------------------------------------
# HDS fragment
# ---------------------------------------------------------------------------


def _build_holder_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Format holder rows: holder, shares, % held, value, date reported."""
    return [
        {
            "holder": h.get("holder", ""),
            "shares": fmt_money(h.get("shares")),
            "pct_held": fmt_pct(h.get("pct_held")),
            "value": fmt_money(h.get("value")),
            "date_reported": h.get("date_reported") or "—",
        }
        for h in rows
    ]


@router.get("/ui/stocks/{ticker}/hds", response_class=HTMLResponse)
def hds_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """HDS panel: institutional + mutual-fund holder tables."""
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    institutional = _build_holder_rows(r.holders(ticker, "institutional"))
    mutualfund = _build_holder_rows(r.holders(ticker, "mutualfund"))

    return templates.TemplateResponse(
        request,
        "fragments/hds.html",
        {
            "request": request,
            "ticker": ticker,
            "institutional": institutional,
            "mutualfund": mutualfund,
        },
    )


# ---------------------------------------------------------------------------
# INS fragment
# ---------------------------------------------------------------------------

_INS_BUY_KEYWORDS = ("buy", "purchase")
_INS_SELL_KEYWORDS = ("sale", "sell")


def _insider_tint_class(text: str) -> str:
    """Buy/sell tint for an insider transaction's free-text description.

    Case-insensitive keyword match: "Buy"/"Purchase" -> ``up`` (green),
    "Sale"/"Sell" -> ``down`` (red), anything else -> ``flat`` (neutral) —
    reusing the same up/down/flat classes the rest of the app uses for
    signed values (see app.css's ``.up``/``.down``/``.flat``).
    """
    lowered = text.lower()
    if any(keyword in lowered for keyword in _INS_BUY_KEYWORDS):
        return "up"
    if any(keyword in lowered for keyword in _INS_SELL_KEYWORDS):
        return "down"
    return "flat"


@router.get("/ui/stocks/{ticker}/ins", response_class=HTMLResponse)
def ins_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """INS panel: insider transaction table, tinted green/red by buy/sell keyword."""
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    transactions = r.insider_transactions(ticker)
    rows = [
        {
            "date": t.get("start_date", ""),
            "insider": t.get("insider", ""),
            "position": t.get("position") or "—",
            "text": t.get("text", ""),
            "shares": fmt_money(t.get("shares")),
            "value": fmt_money(t.get("value")),
            "tint_class": _insider_tint_class(t.get("text") or ""),
        }
        for t in transactions
    ]

    return templates.TemplateResponse(
        request,
        "fragments/ins.html",
        {
            "request": request,
            "ticker": ticker,
            "rows": rows,
        },
    )


# ---------------------------------------------------------------------------
# VAL fragment
# ---------------------------------------------------------------------------


def _dcf_sensitivity(assumptions: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Growth x discount grid of DCF per-share values from stored assumptions.

    Returns None when the stored assumptions lack the required inputs
    (pre-valuation rows, or a hand-edited DB).
    """
    try:
        fcf = float(assumptions["fcf_basis"])
        shares = float(assumptions["shares_outstanding"])
        g0 = float(assumptions["growth_base"])
        d0 = float(assumptions["discount_base"])
    except (KeyError, TypeError, ValueError):
        return None
    growth_offsets = [-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03]
    growths = sorted({min(max(g0 + off, 0.0), GROWTH_CAP) for off in growth_offsets})
    discounts = [d0 - DISCOUNT_SPREAD, d0, d0 + DISCOUNT_SPREAD]
    rows = []
    for d in discounts:
        rows.append({
            "discount": d,
            "values": [dcf_per_share(fcf, shares, g, d) for g in growths],
        })
    return {"growths": growths, "rows": rows}


@router.get("/ui/stocks/{ticker}/val", response_class=HTMLResponse)
def val_fragment(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """VAL panel: fair-value ranges vs price, per-model detail, DCF sensitivity."""
    company = r.get_company(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    rows = r.valuations(ticker)
    summary = r.valuation_summary(ticker)
    quote = r.quote(ticker)
    price = quote.get("current_price") if quote else None

    model_labels = {"dcf": "DCF", "ddm": "Dividend Discount", "graham": "Graham Number",
                    "lynch": "Peter Lynch", "multiples": "Multiples Band",
                    "owner_earnings": "Owner Earnings"}
    applicable = []
    not_applicable = []
    sensitivity = None
    for row in rows:
        try:
            assumptions = json.loads(row.get("assumptions") or "{}")
        except ValueError:
            assumptions = {}
        entry = {
            "model": row["model"],
            "label": model_labels.get(row["model"], row["model"]),
            "na_reason": row.get("na_reason"),
            "bear": row.get("value_bear"),
            "base": row.get("value_base"),
            "bull": row.get("value_bull"),
            "bear_fmt": fmt_price(row.get("value_bear")),
            "base_fmt": fmt_price(row.get("value_base")),
            "bull_fmt": fmt_price(row.get("value_bull")),
            "basis_fy": row.get("basis_fiscal_year"),
            "assumptions": assumptions,
        }
        if row["applicable"]:
            applicable.append(entry)
            if row["model"] == "dcf":
                sensitivity = _dcf_sensitivity(assumptions)
        else:
            not_applicable.append(entry)

    # Owner earnings is a separate lens (Treasury-discounted, not CAPM-discounted)
    # and must never appear as a sixth bar in the five-model chart/table below.
    oe = next((e for e in applicable + not_applicable
               if e["model"] == "owner_earnings"), None)
    applicable = [e for e in applicable if e["model"] != "owner_earnings"]
    not_applicable = [e for e in not_applicable if e["model"] != "owner_earnings"]

    oe_verdict = None
    oe_ctx = None
    if oe is not None:
        if oe["base"] is not None:
            oe_result = ValuationResult(
                model="owner_earnings", applicable=True, value_base=oe["base"],
                assumptions=oe["assumptions"],
            )
            oe_verdict = owner_earnings_verdict(oe_result, price)
        oe_ctx = {
            "na_reason": oe["na_reason"],
            "bear_fmt": oe["bear_fmt"],
            "base_fmt": oe["base_fmt"],
            "bull_fmt": oe["bull_fmt"],
            "buy_below_fmt": fmt_price(oe["assumptions"].get("buy_below")),
            "assumptions": oe["assumptions"],
            "verdict": oe_verdict,
            "verdict_label": VERDICT_LABELS[oe_verdict],
            # The method gap is the insight: same company, two philosophies.
            "dcf_base_fmt": next(
                (e["base_fmt"] for e in applicable if e["model"] == "dcf"), "—"),
        }

    v = verdict(
        summary.get("median_bear") if summary else None,
        summary.get("median_bull") if summary else None,
        price,
    )
    upside = upside_pct(summary.get("median_base") if summary else None, price)

    val_cfg = {
        "models": [{"label": e["label"], "bear": e["bear"], "base": e["base"],
                    "bull": e["bull"]} for e in applicable],
        "price": price,
    }
    computed_at = rows[0].get("computed_at") if rows else None
    return templates.TemplateResponse(
        request,
        "fragments/val.html",
        {
            "request": request,
            "ticker": ticker,
            "has_rows": bool(rows),
            "applicable": applicable,
            "not_applicable": not_applicable,
            "verdict": v,
            "verdict_label": VERDICT_LABELS[v],
            "upside_fmt": fmt_pct(upside),
            "price_fmt": fmt_price(price),
            "summary": summary,
            "median_base_fmt": fmt_price(summary.get("median_base") if summary else None),
            "sensitivity": sensitivity,
            "oe": oe_ctx,
            "computed_at": computed_at,
            # Same XSS defense-in-depth as gp_fragment's cfg_json/ern_fragment's
            # ern_cfg_json/dvd_fragment's dvd_cfg_json: escape "</" so a literal
            # "</script>" can't break out of the inline <script> block. Today's
            # inputs here are DB-derived numbers and a fixed model-label map,
            # but this keeps the inline-<script> pattern uniform and safe
            # against future fields.
            "val_cfg_json": json.dumps(val_cfg).replace("</", "<\\/"),
        },
    )

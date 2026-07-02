"""HTML page routes for the stock-database web UI.

All data access goes through Depends(get_reader); no SQL here.
Formatting is delegated to webapp.formatting — never done inline in templates.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ...exporters.sqlite_store import _METRIC_COLUMNS
from ...query.asof import AsOfReader
from ...query.pit_metrics import PointInTimeMetrics
from ..dependencies import get_asof_reader, get_pit_metrics, get_reader
from ..formatting import fmt_money, fmt_mult, fmt_pct, fmt_price, fmt_raw2, fmt_value
from ..repository import Reader
from ..screener import DEFAULT_COMPARE_METRICS, METRIC_KINDS, parse_screen_params

router = APIRouter()

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)
templates.env.globals.update(
    fmt_money=fmt_money,
    fmt_mult=fmt_mult,
    fmt_pct=fmt_pct,
    fmt_price=fmt_price,
)

# ---------------------------------------------------------------------------
# Display spec — curated line-item lists (verbatim from brief)
# ---------------------------------------------------------------------------

# Statement line items: (label, key, kind)
# kind "eps_raw" is handled as raw 2dp; everything else is "money".
_STATEMENT_ROWS: List[tuple[str, str, str]] = [
    ("Revenue", "revenue", "money"),
    ("Gross profit", "gross_profit", "money"),
    ("Operating income", "operating_income", "money"),
    ("Net income", "net_income", "money"),
    ("EPS (diluted)", "eps_diluted", "eps_raw"),
    ("Total assets", "total_assets", "money"),
    ("Total liabilities", "total_liabilities", "money"),
    ("Total equity", "total_equity", "money"),
    ("Cash & equivalents", "cash_and_equivalents", "money"),
    ("Long-term debt", "long_term_debt", "money"),
    ("Operating cash flow", "operating_cash_flow", "money"),
    ("CapEx", "capex", "money"),
    ("Dividends paid", "dividends_paid", "money"),
    ("Shares outstanding", "shares_outstanding", "money"),
]

# Metrics rows: (label, key, kind)
_METRIC_ROWS: List[tuple[str, str, str]] = [
    ("Gross margin", "gross_margin", "pct"),
    ("Operating margin", "operating_margin", "pct"),
    ("Net margin", "net_margin", "pct"),
    ("EBITDA margin", "ebitda_margin", "pct"),
    ("FCF margin", "fcf_margin", "pct"),
    ("ROIC", "roic", "pct"),
    ("ROA", "roa", "pct"),
    ("ROE", "roe", "pct"),
    ("EBITDA", "ebitda", "money"),
    ("Free cash flow", "free_cash_flow", "money"),
    ("Net debt", "net_debt", "money"),
    ("Debt/EBITDA", "debt_to_ebitda", "mult"),
    ("Interest coverage", "interest_coverage", "mult"),
]

# Metric options for the chart <select>
METRIC_OPTIONS: List[tuple[str, str]] = [
    ("ROIC", "roic"),
    ("ROE", "roe"),
    ("ROA", "roa"),
    ("Net margin", "net_margin"),
    ("Gross margin", "gross_margin"),
    ("Operating margin", "operating_margin"),
    ("EBITDA margin", "ebitda_margin"),
    ("FCF margin", "fcf_margin"),
]

# Friendly labels for the screener metric <select>.
# Unlisted columns fall back to the raw key name.
_SCREENER_LABELS: Dict[str, str] = {
    "ebitda": "EBITDA",
    "ebit": "EBIT",
    "nopat": "NOPAT",
    "free_cash_flow": "Free cash flow",
    "fcf_margin": "FCF margin",
    "levered_fcf": "Levered FCF",
    "net_debt": "Net debt",
    "total_debt": "Total debt",
    "working_capital": "Working capital",
    "invested_capital": "Invested capital",
    "roic": "ROIC",
    "roa": "ROA",
    "roe": "ROE",
    "interest_coverage": "Interest coverage",
    "debt_to_ebitda": "Debt / EBITDA",
    "asset_turnover": "Asset turnover",
    "inventory_turnover": "Inventory turnover",
    "receivables_turnover": "Receivables turnover",
    "gross_margin": "Gross margin",
    "operating_margin": "Operating margin",
    "net_margin": "Net margin",
    "ebitda_margin": "EBITDA margin",
    "net_interest_margin": "Net interest margin",
    "efficiency_ratio": "Efficiency ratio",
    "loan_to_deposit": "Loan-to-deposit",
    "loss_ratio": "Loss ratio",
    "combined_ratio": "Combined ratio",
    "ffo": "FFO",
    "affo": "AFFO",
    "ffo_per_share": "FFO per share",
    "ffo_payout": "FFO payout",
}

# Build list of (label, key) for template selects
SCREENER_METRIC_OPTIONS: List[tuple[str, str]] = [
    (_SCREENER_LABELS.get(col, col), col) for col in _METRIC_COLUMNS
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_statement_display(
    rows_data: List[Dict[str, Any]],
    column_key: str,
    row_spec: List[tuple[str, str, str]],
) -> tuple[List[str], List[Dict[str, Any]]]:
    """Build (columns, display_rows) for a transposed statement table.

    columns: list of period labels (newest first).
    display_rows: [{label, values: [formatted str per column]}].
    Rows where every value is None/missing across all periods are omitted.
    """
    columns: List[str] = [str(r.get(column_key, "")) for r in rows_data]

    display_rows: List[Dict[str, Any]] = []
    for label, key, kind in row_spec:
        raw_values: List[Optional[float]] = [
            r.get(key) for r in rows_data
        ]
        # Skip row if all values are absent
        if all(v is None for v in raw_values):
            continue
        if kind == "eps_raw":
            formatted = [fmt_raw2(v) for v in raw_values]
        else:
            formatted = [fmt_value(v, kind) for v in raw_values]
        display_rows.append({"label": label, "vals": formatted})
    return columns, display_rows


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """Dashboard / home page."""
    company_count = r.count_companies()
    sectors = r.distinct_sectors()
    freshness = r.data_freshness()
    by_sector = r.coverage_by_sector()
    latest_runs = r.latest_collection_runs()
    unmapped_top = r.unmapped_top(limit=10)
    fill_rates = r.field_fill_rates()

    # Pick the 15 most-populated canonical fields for the bar chart
    sorted_rates = sorted(fill_rates.items(), key=lambda kv: kv[1], reverse=True)
    chart_fields = [f for f, _ in sorted_rates[:15]]
    chart_values = [fill_rates[f] for f in chart_fields]
    chart_pct = [fmt_pct(v) for v in chart_values]

    fill_chart_json = json.dumps(
        {"labels": chart_fields, "values": chart_values, "pct": chart_pct}
    )

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "company_count": company_count,
            "sectors": sectors,
            "freshness": freshness,
            "by_sector": by_sector,
            "latest_runs": latest_runs,
            "unmapped_top": unmapped_top,
            "fill_chart_json": fill_chart_json,
        },
    )


@router.get("/quality", response_class=HTMLResponse)
def quality_page(
    request: Request,
    sector: Optional[str] = None,
    r: Reader = Depends(get_reader),
) -> Any:
    """Quality drill-down page: full fill-rate table, latest runs, unmapped list."""
    sectors = r.distinct_sectors()
    fill_rates = r.field_fill_rates(sector_class=sector)
    latest_runs = r.latest_collection_runs()
    unmapped_top = r.unmapped_top(limit=50)
    freshness = r.data_freshness()

    # Sort fill_rates: highest fill rate first
    fill_items = sorted(fill_rates.items(), key=lambda kv: kv[1], reverse=True)

    return templates.TemplateResponse(
        request,
        "quality.html",
        {
            "request": request,
            "sectors": sectors,
            "selected_sector": sector,
            "fill_items": fill_items,
            "latest_runs": latest_runs,
            "unmapped_top": unmapped_top,
            "freshness": freshness,
            "fmt_pct": fmt_pct,
        },
    )


@router.get("/companies", response_class=HTMLResponse)
def companies_list(
    request: Request,
    sector: Optional[str] = None,
    r: Reader = Depends(get_reader),
) -> Any:
    """Companies list page with optional sector filter."""
    companies = r.list_companies(sector_class=sector, limit=1000)
    sectors = r.distinct_sectors()
    return templates.TemplateResponse(
        request,
        "companies.html",
        {
            "request": request,
            "companies": companies,
            "sectors": sectors,
            "selected_sector": sector,
        },
    )


@router.get("/companies/{ticker}", response_class=HTMLResponse)
def company_page(
    ticker: str,
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """Single-company deep-dive page."""
    overview = r.company_overview(ticker)
    if overview is None:
        raise HTTPException(status_code=404, detail=f"Company not found: {ticker}")
    return templates.TemplateResponse(
        request,
        "company.html",
        {
            "request": request,
            "overview": overview,
            "metric_options": METRIC_OPTIONS,
            # Convenience shortcut used in template URLs
            "ticker": ticker,
        },
    )


# ---------------------------------------------------------------------------
# Fragment routes
# ---------------------------------------------------------------------------


@router.get("/ui/search", response_class=HTMLResponse)
def search_fragment(
    request: Request,
    q: str = "",
    r: Reader = Depends(get_reader),
) -> Any:
    """Autocomplete search results fragment."""
    if len(q.strip()) < 1:
        return templates.TemplateResponse(
            request,
            "fragments/search_results.html",
            {"request": request, "hits": []},
        )
    hits = r.search_companies(q, 8)
    return templates.TemplateResponse(
        request,
        "fragments/search_results.html",
        {"request": request, "hits": hits},
    )


@router.get("/ui/companies/{ticker}/statements", response_class=HTMLResponse)
def statements_fragment(
    ticker: str,
    request: Request,
    period: str = "annual",
    r: Reader = Depends(get_reader),
) -> Any:
    """HTMX fragment: transposed financial-statement table."""
    if period == "quarterly":
        rows_data = r.quarterly_financials(ticker)
        column_key = "period_end"
        row_spec = _STATEMENT_ROWS
    elif period == "ttm":
        rows_data = r.ttm_financials(ticker)
        column_key = "period_end"
        row_spec = _STATEMENT_ROWS
    elif period == "metrics":
        rows_data = r.annual_metrics(ticker)
        column_key = "fiscal_year"
        row_spec = _METRIC_ROWS
    else:
        # annual (default + unknown)
        rows_data = r.annual_financials(ticker)
        column_key = "fiscal_year"
        row_spec = _STATEMENT_ROWS

    columns, display_rows = _build_statement_display(rows_data, column_key, row_spec)
    return templates.TemplateResponse(
        request,
        "fragments/statements.html",
        {"request": request, "columns": columns, "rows": display_rows},
    )


@router.get("/ui/companies/{ticker}/metric-chart", response_class=HTMLResponse)
def metric_chart_fragment(
    ticker: str,
    request: Request,
    metric: str = "roic",
    r: Reader = Depends(get_reader),
) -> Any:
    """HTMX fragment: Plotly metric-chart."""
    try:
        series = r.metric_series(ticker, metric)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "fragments/metric_chart.html",
            {"request": request, "metric": metric, "error": str(exc)},
        )

    # Find a human label from METRIC_OPTIONS
    label_map = {key: lbl for lbl, key in METRIC_OPTIONS}
    label = label_map.get(metric, metric)

    return templates.TemplateResponse(
        request,
        "fragments/metric_chart.html",
        {
            "request": request,
            "metric": metric,
            "label": label,
            "series_json": json.dumps(series),
            "error": None,
        },
    )


# ---------------------------------------------------------------------------
# As-of explorer page + fragments
# ---------------------------------------------------------------------------

# Curated line items for the as-of result view
_ASOF_FINANCIALS_ROWS: List[tuple[str, str, str]] = [
    ("Revenue", "revenue", "money"),
    ("Net income", "net_income", "money"),
    ("Total assets", "total_assets", "money"),
    ("Total equity", "total_equity", "money"),
    ("Operating cash flow", "operating_cash_flow", "money"),
]

_ASOF_RATIO_ROWS: List[tuple[str, str, str]] = [
    ("ROIC", "roic", "pct"),
    ("ROE", "roe", "pct"),
    ("Net margin", "net_margin", "pct"),
    ("Debt/EBITDA", "debt_to_ebitda", "mult"),
]

# Curated line items displayed in the vintages side-by-side table
_VINTAGES_ROWS: List[tuple[str, str]] = [
    ("Revenue", "revenue"),
    ("Net income", "net_income"),
    ("Total assets", "total_assets"),
    ("Total equity", "total_equity"),
]


@router.get("/asof", response_class=HTMLResponse)
def asof_page(request: Request) -> Any:
    """As-of explorer page (form only; results load via HTMX)."""
    return templates.TemplateResponse(
        request,
        "asof.html",
        {"request": request},
    )


@router.get("/ui/asof/result", response_class=HTMLResponse)
def asof_result_fragment(
    request: Request,
    ticker: str,
    fiscal_year: int,
    date: str,
    asof_reader: AsOfReader = Depends(get_asof_reader),
    pit: PointInTimeMetrics = Depends(get_pit_metrics),
) -> Any:
    """HTMX fragment: as-of financials + ratios for one (ticker, fiscal_year, date)."""
    filing = asof_reader.as_of_annual(ticker, fiscal_year, date)
    ratios: Optional[Dict[str, Any]] = None
    if filing is not None:
        ratios = pit.metrics_as_of(ticker, fiscal_year, date)

    # Build curated rows
    fin_rows: List[Dict[str, Any]] = []
    if filing is not None:
        for label, key, kind in _ASOF_FINANCIALS_ROWS:
            val = filing.get(key)
            fin_rows.append({"label": label, "value": fmt_value(val, kind)})

    ratio_rows: List[Dict[str, Any]] = []
    if ratios is not None:
        for label, key, kind in _ASOF_RATIO_ROWS:
            val = ratios.get(key)
            ratio_rows.append({"label": label, "value": fmt_value(val, kind)})

    return templates.TemplateResponse(
        request,
        "fragments/asof_result.html",
        {
            "request": request,
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "date": date,
            "filing": filing,
            "fin_rows": fin_rows,
            "ratio_rows": ratio_rows,
        },
    )


@router.get("/ui/asof/vintages", response_class=HTMLResponse)
def asof_vintages_fragment(
    request: Request,
    ticker: str,
    fiscal_year: Optional[int] = None,
    r: Reader = Depends(get_reader),
) -> Any:
    """HTMX fragment: side-by-side vintage table for one (ticker, fiscal_year)."""
    rows = r.vintages(ticker, fiscal_year=fiscal_year)

    # Build (row_label, [value per filing]) structure
    columns: List[str] = [
        f"{v['filed_date']} ({v.get('form', '')})" for v in rows
    ]
    display_rows: List[Dict[str, Any]] = []
    for label, key in _VINTAGES_ROWS:
        values = [fmt_money(v.get(key)) for v in rows]
        display_rows.append({"label": label, "vals": values})

    return templates.TemplateResponse(
        request,
        "fragments/asof_vintages.html",
        {
            "request": request,
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "vintages": rows,
            "columns": columns,
            "rows": display_rows,
        },
    )


# ---------------------------------------------------------------------------
# Screener page & fragments
# ---------------------------------------------------------------------------

@router.get("/screener", response_class=HTMLResponse)
def screener_page(
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """Screener page with filter form and HTMX result target."""
    sectors = r.distinct_sectors()
    return templates.TemplateResponse(
        request,
        "screener.html",
        {
            "request": request,
            "sectors": sectors,
            "metric_options": SCREENER_METRIC_OPTIONS,
        },
    )


@router.get("/ui/screen", response_class=HTMLResponse)
def screen_fragment(
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """HTMX fragment: screener result table.

    Accepts the same GET shorthand as GET /api/screen.
    """
    try:
        spec = parse_screen_params(dict(request.query_params))
        result = r.screen(spec)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "fragments/screener_results.html",
            {
                "request": request,
                "error": str(exc),
                "items": [],
                "total": 0,
                "columns": [],
                "sort": None,
                "sort_dir": "desc",
                "base_params": "",
            },
        )

    # Build base_params: current query string without sort/sort_dir so that
    # sortable column-header links can append their own sort/sort_dir.
    base_items = [
        (k, v) for k, v in request.query_params.multi_items()
        if k not in ("sort", "sort_dir")
    ]
    base_params = urlencode(base_items)

    return templates.TemplateResponse(
        request,
        "fragments/screener_results.html",
        {
            "request": request,
            "error": None,
            "items": result["items"],
            "total": result["total"],
            "columns": result["columns"],
            "sort": spec.sort,
            "sort_dir": spec.sort_dir,
            "base_params": base_params,
        },
    )


@router.get("/ui/companies/{ticker}/peers", response_class=HTMLResponse)
def peers_fragment(
    ticker: str,
    request: Request,
    metrics: Optional[str] = None,
    r: Reader = Depends(get_reader),
) -> Any:
    """HTMX fragment: peer benchmarking table for a company."""
    metric_list: List[str] = (
        [m.strip() for m in metrics.split(",") if m.strip()]
        if metrics
        else DEFAULT_COMPARE_METRICS
    )

    company = r.get_company(ticker)
    if company is None or company.get("sector_class") is None:
        return templates.TemplateResponse(
            request,
            "fragments/peers.html",
            {
                "request": request,
                "error": f"No sector data for {ticker}.",
                "peers": None,
                "rows": [],
            },
        )

    try:
        peers = r.peer_comparison(ticker, metric_list)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "fragments/peers.html",
            {
                "request": request,
                "error": str(exc),
                "peers": None,
                "rows": [],
            },
        )

    # Pre-format each metric row
    formatted_rows: List[Dict[str, Any]] = []
    for m in metric_list:
        kind = METRIC_KINDS.get(m, "raw")
        company_val = peers["company"].get(m)
        median_val = peers["sector_median"].get(m)
        delta: Optional[float] = (
            (company_val - median_val)
            if company_val is not None and median_val is not None
            else None
        )
        formatted_rows.append(
            {
                "label": m,
                "company_fmt": fmt_value(company_val, kind),
                "median_fmt": fmt_value(median_val, kind),
                "delta_fmt": fmt_value(delta, kind),
            }
        )

    return templates.TemplateResponse(
        request,
        "fragments/peers.html",
        {
            "request": request,
            "error": None,
            "peers": peers,
            "rows": formatted_rows,
        },
    )


@router.get("/ui/compare", response_class=HTMLResponse)
def compare_fragment(
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """HTMX fragment: side-by-side metric comparison table."""
    tickers_raw = request.query_params.get("tickers", "")
    tickers = [t.strip() for t in tickers_raw.split(",") if t.strip()]

    metrics_raw = request.query_params.get("metrics", "")
    if metrics_raw:
        metrics = [m.strip() for m in metrics_raw.split(",") if m.strip()]
    else:
        metrics = DEFAULT_COMPARE_METRICS

    if not tickers:
        return templates.TemplateResponse(
            request,
            "fragments/compare_table.html",
            {
                "request": request,
                "error": "No tickers specified.",
                "tickers": [],
                "rows": [],
            },
        )

    try:
        result = r.compare(tickers, metrics)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "fragments/compare_table.html",
            {
                "request": request,
                "error": str(exc),
                "tickers": [],
                "rows": [],
            },
        )
    # Pre-format each cell so the template renders plain strings.
    formatted_rows: List[Dict[str, Any]] = []
    for row in result["rows"]:
        metric = row["metric"]
        kind = METRIC_KINDS.get(metric, "raw")
        formatted_values = {
            t: fmt_value(v, kind) for t, v in row["values"].items()
        }
        formatted_rows.append({"metric": metric, "values": formatted_values})

    return templates.TemplateResponse(
        request,
        "fragments/compare_table.html",
        {
            "request": request,
            "error": None,
            "tickers": result["tickers"],
            "rows": formatted_rows,
        },
    )

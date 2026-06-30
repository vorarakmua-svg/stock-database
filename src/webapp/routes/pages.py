"""HTML page routes for the stock-database web UI.

All data access goes through Depends(get_reader); no SQL here.
Formatting is delegated to webapp.formatting — never done inline in templates.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..dependencies import get_reader
from ..formatting import fmt_raw2, fmt_value
from ..repository import Reader

router = APIRouter()

templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
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
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "company_count": company_count, "sectors": sectors},
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
            "fragments/search_results.html",
            {"request": request, "hits": []},
        )
    hits = r.search_companies(q, 8)
    return templates.TemplateResponse(
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
            "fragments/metric_chart.html",
            {"request": request, "metric": metric, "error": str(exc)},
        )

    # Find a human label from METRIC_OPTIONS
    label_map = {key: lbl for lbl, key in METRIC_OPTIONS}
    label = label_map.get(metric, metric)

    return templates.TemplateResponse(
        "fragments/metric_chart.html",
        {
            "request": request,
            "metric": metric,
            "label": label,
            "series_json": json.dumps(series),
            "error": None,
        },
    )

"""CSV export endpoints for the stock-database web app.

All endpoints use the stdlib ``csv`` module (no new heavy dependencies) and
return a ``StreamingResponse`` / ``Response`` with ``media_type="text/csv"``
and a ``Content-Disposition: attachment`` header so browsers trigger a
file download.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from ..dependencies import get_reader
from ..repository import Reader
from ..screener import parse_screen_params

router = APIRouter(prefix="/api/export", tags=["export"])


def _rows_to_csv(rows: List[Dict[str, Any]]) -> str:
    """Serialize a list of dicts to a CSV string (stdlib csv, header from first row)."""
    if not rows:
        return ""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


@router.get("/company/{ticker}/annual.csv")
def export_annual(
    ticker: str,
    r: Reader = Depends(get_reader),
) -> Response:
    """Download annual financials for *ticker* as a CSV attachment."""
    rows = r.annual_financials(ticker)
    content = _rows_to_csv(rows)
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{ticker}_annual.csv"',
        },
    )


@router.get("/company/{ticker}/metrics.csv")
def export_metrics(
    ticker: str,
    r: Reader = Depends(get_reader),
) -> Response:
    """Download annual metrics for *ticker* as a CSV attachment."""
    rows = r.annual_metrics(ticker)
    content = _rows_to_csv(rows)
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{ticker}_metrics.csv"',
        },
    )


@router.get("/screen.csv")
def export_screen(
    request: Request,
    r: Reader = Depends(get_reader),
) -> Response:
    """Download screener results as a CSV attachment.

    Accepts the same GET shorthand as ``GET /api/screen``.
    Raises 400 for invalid metric columns or filter values.
    """
    try:
        spec = parse_screen_params(dict(request.query_params))
        result = r.screen(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    items: List[Dict[str, Any]] = result["items"]
    columns: List[str] = result["columns"]

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(items)
    content = out.getvalue()

    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="screen.csv"',
        },
    )

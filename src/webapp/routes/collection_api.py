"""Collection trigger API and UI fragment routes.

JSON API (under /api/collection/):
  POST /api/collection/jobs     — submit a collection job
  GET  /api/collection/jobs     — list all jobs
  GET  /api/collection/jobs/{id} — poll one job

UI routes:
  GET  /collect                         — collection page
  POST /ui/collection/start             — HTMX fragment: submit + return status
  GET  /ui/collection/jobs/{job_id}     — HTMX fragment: poll status
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..dependencies import get_job_manager, get_settings
from ..jobs import CollectionJobManager
from ..schemas import JobRequest
from ..settings import WebSettings

router = APIRouter(tags=["collection"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@router.post("/api/collection/jobs", status_code=202)
def submit_job(
    body: JobRequest,
    manager: CollectionJobManager = Depends(get_job_manager),
    settings: WebSettings = Depends(get_settings),
) -> JSONResponse:
    """Submit a collection job.

    Returns 409 if collection is disabled, 400 if tickers list is empty,
    otherwise 202 with the initial job status.
    """
    if not settings.allow_collection:
        raise HTTPException(status_code=409, detail="collection is disabled")
    if not body.tickers:
        raise HTTPException(status_code=400, detail="tickers list must not be empty")

    job_id = manager.submit(
        tickers=body.tickers,
        years_back=body.years_back,
        include_yahoo=body.include_yahoo,
        include_sec=body.include_sec,
    )
    job = manager.get(job_id)
    assert job is not None  # just submitted
    return JSONResponse(status_code=202, content=job.to_dict())


@router.get("/api/collection/jobs")
def list_jobs(
    manager: CollectionJobManager = Depends(get_job_manager),
) -> List[Dict[str, Any]]:
    """List all collection jobs, newest first."""
    return [j.to_dict() for j in manager.list()]


@router.get("/api/collection/jobs/{job_id}")
def get_job(
    job_id: str,
    manager: CollectionJobManager = Depends(get_job_manager),
) -> Dict[str, Any]:
    """Return the current status of a job, or 404 if unknown."""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    return job.to_dict()


# ---------------------------------------------------------------------------
# UI page
# ---------------------------------------------------------------------------

@router.get("/collect", response_class=HTMLResponse)
def collect_page(
    request: Request,
    settings: WebSettings = Depends(get_settings),
) -> Any:
    """Data-collection page. Shows a form when enabled, a note when disabled."""
    return templates.TemplateResponse(
        "collect.html",
        {
            "request": request,
            "allow_collection": settings.allow_collection,
        },
    )


# ---------------------------------------------------------------------------
# HTMX fragment: submit
# ---------------------------------------------------------------------------

@router.post("/ui/collection/start", response_class=HTMLResponse)
async def collection_start_fragment(
    request: Request,
    manager: CollectionJobManager = Depends(get_job_manager),
    settings: WebSettings = Depends(get_settings),
) -> Any:
    """HTMX fragment: parse form data, submit job, return status fragment."""
    if not settings.allow_collection:
        return HTMLResponse("<p>Collection is disabled.</p>", status_code=409)

    form = await request.form()
    raw_tickers = str(form.get("tickers", ""))
    tickers = [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]
    if not tickers:
        return HTMLResponse(
            '<p class="error">Please enter at least one ticker.</p>',
            status_code=400,
        )

    years_back_str = str(form.get("years_back", "") or "")
    years_back = int(years_back_str) if years_back_str.isdigit() else None
    include_yahoo = form.get("include_yahoo") == "on"
    include_sec = form.get("include_sec") == "on"

    job_id = manager.submit(
        tickers=tickers,
        years_back=years_back,
        include_yahoo=include_yahoo,
        include_sec=include_sec,
    )
    job = manager.get(job_id)
    assert job is not None

    return templates.TemplateResponse(
        "fragments/job_status.html",
        {"request": request, "job": job, "terminal": False},
    )


# ---------------------------------------------------------------------------
# HTMX fragment: poll
# ---------------------------------------------------------------------------

@router.get("/ui/collection/jobs/{job_id}", response_class=HTMLResponse)
def job_status_fragment(
    job_id: str,
    request: Request,
    manager: CollectionJobManager = Depends(get_job_manager),
) -> Any:
    """HTMX fragment: current job status (polled every 2s until terminal)."""
    job = manager.get(job_id)
    if job is None:
        return HTMLResponse("<p>Job not found.</p>", status_code=404)
    terminal = job.state in ("done", "error")
    return templates.TemplateResponse(
        "fragments/job_status.html",
        {"request": request, "job": job, "terminal": terminal},
    )

"""FastAPI dependency providers for the web app."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from fastapi import Depends, HTTPException, Request

from ..query.asof import AsOfReader
from ..query.pit_metrics import PointInTimeMetrics
from .jobs import CollectionJobManager
from .repository import Reader
from .settings import WebSettings


def get_settings(request: Request) -> WebSettings:
    """Return the WebSettings stored on the application state."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_reader(settings: WebSettings = Depends(get_settings)) -> Iterator[Reader]:
    """FastAPI dependency providing a Reader, closing it on teardown.

    Raises HTTP 503 if the database file does not exist (e.g. first-run before
    a collection run has populated it). Routes inject this via ``Depends``.
    """
    if not Path(settings.db_path).exists():
        raise HTTPException(
            status_code=503,
            detail=f"Database not available: {settings.db_path}",
        )
    r = Reader(settings.db_path)
    try:
        yield r
    finally:
        r.close()


def get_asof_reader(settings: WebSettings = Depends(get_settings)) -> Iterator[AsOfReader]:
    """FastAPI dependency providing an AsOfReader, closing it on teardown.

    Raises HTTP 503 if the database file does not exist.
    """
    if not Path(settings.db_path).exists():
        raise HTTPException(
            status_code=503,
            detail=f"Database not available: {settings.db_path}",
        )
    r = AsOfReader(settings.db_path)
    try:
        yield r
    finally:
        r.close()


def get_job_manager(request: Request) -> CollectionJobManager:
    """Return the CollectionJobManager stored on the application state."""
    return request.app.state.job_manager  # type: ignore[no-any-return]


def get_pit_metrics(settings: WebSettings = Depends(get_settings)) -> Iterator[PointInTimeMetrics]:
    """FastAPI dependency providing a PointInTimeMetrics, closing it on teardown.

    Raises HTTP 503 if the database file does not exist.
    """
    if not Path(settings.db_path).exists():
        raise HTTPException(
            status_code=503,
            detail=f"Database not available: {settings.db_path}",
        )
    pit = PointInTimeMetrics.from_path(settings.db_path)
    try:
        yield pit
    finally:
        pit.close()

"""FastAPI application factory for the stock-web interface."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .jobs import CollectionJobManager
from .routes import asof_api, collection_api, companies, export_api, pages, quality_api, screener_api
from .routes.pages import templates
from .settings import WebSettings, default_settings

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    db_path: Optional[Union[str, Path]] = None,
    settings: Optional[WebSettings] = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Override the database path. Takes precedence over *settings*.
        settings: Pre-built WebSettings instance. Defaults to ``default_settings()``.

    Returns:
        Configured FastAPI application.
    """
    if settings is None:
        settings = default_settings()

    if db_path is not None:
        settings.db_path = Path(db_path)

    app = FastAPI(title="Stock Database Web API", version="0.1.0")
    app.state.settings = settings
    app.state.job_manager = CollectionJobManager(db_path=settings.db_path)

    # Mount static files (must exist before the app handles requests)
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Routers — page routes first so they take precedence over /api paths
    app.include_router(pages.router)
    app.include_router(companies.router)
    app.include_router(asof_api.router)
    app.include_router(screener_api.router)
    app.include_router(quality_api.router)
    app.include_router(collection_api.router)
    app.include_router(export_api.router)

    # ---------------------------------------------------------------------------
    # Error handlers: JSON for /api/* routes, HTML for browser routes
    # ---------------------------------------------------------------------------

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> Any:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
            )
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "status_code": exc.status_code,
                "message": exc.detail or "An error occurred.",
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(
        request: Request, exc: Exception
    ) -> Any:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error."},
            )
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "request": request,
                "status_code": 500,
                "message": "Internal server error.",
            },
            status_code=500,
        )

    @app.on_event("shutdown")
    def _shutdown_job_manager() -> None:
        app.state.job_manager.shutdown()

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        """Health-check endpoint — always returns 200 even if the DB is absent."""
        s: WebSettings = app.state.settings
        return {
            "status": "ok",
            "db_path": str(s.db_path),
            "db_exists": Path(s.db_path).exists(),
        }

    return app

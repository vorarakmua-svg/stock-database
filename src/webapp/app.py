"""FastAPI application factory for the stock-web interface."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import asof_api, companies, pages, quality_api, screener_api
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

    # Mount static files (must exist before the app handles requests)
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Routers — page routes first so they take precedence over /api paths
    app.include_router(pages.router)
    app.include_router(companies.router)
    app.include_router(asof_api.router)
    app.include_router(screener_api.router)
    app.include_router(quality_api.router)

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

"""FastAPI application factory for the stock-web interface."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from fastapi import FastAPI

from .settings import WebSettings, default_settings


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

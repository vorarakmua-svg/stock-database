"""FastAPI dependency providers for the web app."""
from __future__ import annotations

from fastapi import Request

from .settings import WebSettings


def get_settings(request: Request) -> WebSettings:
    """Return the WebSettings stored on the application state."""
    return request.app.state.settings  # type: ignore[no-any-return]

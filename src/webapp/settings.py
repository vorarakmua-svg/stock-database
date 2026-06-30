"""WebSettings — runtime configuration for the web app."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import StorageConfig


def _default_db_path() -> Path:
    return StorageConfig().database_path


@dataclass
class WebSettings:
    """Configuration for the stock-web FastAPI application."""

    db_path: Path = field(default_factory=_default_db_path)
    host: str = "127.0.0.1"
    port: int = 8000
    allow_collection: bool = False


def default_settings() -> WebSettings:
    """Return a WebSettings instance with default values."""
    return WebSettings()

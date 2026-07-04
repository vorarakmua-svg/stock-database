"""WebSettings — runtime configuration for the web app."""
from __future__ import annotations

import os
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
    allow_quote_refresh: bool = True

    @classmethod
    def from_env(cls) -> WebSettings:
        """Build WebSettings from environment variables.

        Recognised variables (all optional; defaults apply when absent):
          STOCK_WEB_DB_PATH             — path to the SQLite database
          STOCK_WEB_HOST                — bind host (default ``"127.0.0.1"``)
          STOCK_WEB_PORT                — bind port as integer (default ``8000``)
          STOCK_WEB_ALLOW_COLLECTION    — truthy values ``1``, ``true``, ``yes``,
                                          ``on`` (case-insensitive) enable full
                                          collection; default ``False`` when unset.
          STOCK_WEB_ALLOW_QUOTE_REFRESH — same truthy set enables the lightweight
                                          on-demand quote refresh; unlike
                                          STOCK_WEB_ALLOW_COLLECTION, this
                                          defaults to ``True`` when unset (quote
                                          refresh is low-risk/low-cost, so it is
                                          opt-out rather than opt-in).
        """
        db_path_env = os.environ.get("STOCK_WEB_DB_PATH")
        db_path: Path = Path(db_path_env) if db_path_env else _default_db_path()

        host: str = os.environ.get("STOCK_WEB_HOST", "127.0.0.1")

        port_env = os.environ.get("STOCK_WEB_PORT")
        port: int = int(port_env) if port_env else 8000

        truthy = {"1", "true", "yes", "on"}
        allow_raw = os.environ.get("STOCK_WEB_ALLOW_COLLECTION", "").strip().lower()
        allow_collection: bool = allow_raw in truthy

        quote_refresh_env = os.environ.get("STOCK_WEB_ALLOW_QUOTE_REFRESH")
        allow_quote_refresh: bool = (
            True if quote_refresh_env is None else quote_refresh_env.strip().lower() in truthy
        )

        return cls(
            db_path=db_path,
            host=host,
            port=port,
            allow_collection=allow_collection,
            allow_quote_refresh=allow_quote_refresh,
        )


def default_settings() -> WebSettings:
    """Return a WebSettings instance with default values."""
    return WebSettings()

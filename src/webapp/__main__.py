"""Entry point for running the stock-web server via ``python -m src.webapp``."""
from __future__ import annotations

import uvicorn

from .app import create_app
from .settings import default_settings


def main() -> None:
    """Build the app and start uvicorn."""
    settings = default_settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

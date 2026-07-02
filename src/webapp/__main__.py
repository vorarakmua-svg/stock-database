"""Entry point for running the stock-web server via ``python -m src.webapp``."""
from __future__ import annotations

import uvicorn

from .app import create_app
from .settings import WebSettings


def main() -> None:
    """Build the app and start uvicorn.

    All overrides are read from environment variables:
      STOCK_WEB_DB_PATH, STOCK_WEB_HOST, STOCK_WEB_PORT, STOCK_WEB_ALLOW_COLLECTION
    """
    settings = WebSettings.from_env()
    uvicorn.run(create_app(settings=settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

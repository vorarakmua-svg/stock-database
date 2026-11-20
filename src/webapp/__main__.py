"""Entry point for running the stock-web server via ``python -m src.webapp``."""
from __future__ import annotations

import argparse
from typing import List, Optional

import uvicorn

from .app import create_app
from .settings import WebSettings

_DESCRIPTION = """Serve the stock-database web workstation.

Every setting is an environment variable; the command takes no arguments:
  STOCK_WEB_DB_PATH              the SQLite database (default data/output/stock.db)
  STOCK_WEB_HOST                 the address to bind (default 127.0.0.1)
  STOCK_WEB_PORT                 the port (default 8000)
  STOCK_WEB_ALLOW_COLLECTION     0 hides UPDATE DATA and refuses collection jobs
  STOCK_WEB_ALLOW_QUOTE_REFRESH  0 disables the quote refresh
"""


def main(argv: Optional[List[str]] = None) -> None:
    """Build the app and start uvicorn.

    All overrides are read from environment variables (``--help`` lists them and starts nothing, BUG-046); an
    unknown argument is refused rather than ignored.
    """
    parser = argparse.ArgumentParser(prog="stock-web", description=_DESCRIPTION,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    settings = WebSettings.from_env()
    uvicorn.run(create_app(settings=settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

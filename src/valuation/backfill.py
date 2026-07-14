"""Backfill valuations for tickers already in the SQLite store.

Usage:
    python -m src.valuation.backfill                  # every company, default DB
    python -m src.valuation.backfill --db path.db     # explicit DB
    python -m src.valuation.backfill AAPL MSFT        # only these tickers

Reads only what collection already stored — no network fetches.
"""

import argparse
import logging
import sys
from typing import List, Optional

from ..config import StorageConfig
from .engine import compute_and_store


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute and store valuations from existing collected data."
    )
    parser.add_argument("tickers", nargs="*",
                        help="Tickers to value (default: every company in the DB)")
    parser.add_argument("--db", default=None,
                        help="SQLite DB path (default: the standard store location)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db_path = args.db or str(StorageConfig().database_path)
    tickers = args.tickers or None
    stored = compute_and_store(db_path, tickers=tickers)
    print(f"Valuations stored for {stored} ticker{'s' if stored != 1 else ''} "
          f"in {db_path}")
    return 0 if stored > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

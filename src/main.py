"""
Stock Data Collection System - CLI Entry Point

Usage:
    python -m src.main AAPL MSFT GOOGL
    python -m src.main AAPL --output-dir ./my_data
    python -m src.main AAPL MSFT --no-sec --formats json
    python -m src.main AAPL --years 10 --verbose
    python -m src.main --tickers-file universe/core.txt
    python -m src.main --tickers-file universe/sp500-2026-10-25.csv --skip-existing
    python -m src.main refresh [--steps LIST] [--force] [--db PATH] [--output-dir DIR] [--cache-dir DIR]

The tickers are the positional ones, then each --tickers-file's (a .txt, a ticker a line, or a .csv's ticker or
Symbol column; ``src/universe/tickers.py``), in Yahoo's form (BRK.B as BRK-B), each once, in order.
--skip-existing leaves out the tickers already in the database the run writes to (--db, else
<output-dir>/output/stock.db). Exit 1 when no ticker remains to collect
(docs/superpowers/specs/2026-10-25-universe-automation-design.md, "The universe").

``refresh`` as the first argument runs ``stock-data refresh`` (``src/refresh.py``) with the rest: no ticker is called
REFRESH (the same spec, "stock-data refresh").
"""

import argparse
import logging
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import List, Optional, Sequence, Set

from .config import AppConfig, SECConfig, StorageConfig, YahooConfig
from .exporters.sqlite_store import read_only_uri
from .fetchers.stock_data_fetcher import StockDataFetcher
from .universe.tickers import read_tickers_file, unique_tickers


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    return logging.getLogger("stock_fetcher")


def resolve_tickers(tickers: Sequence[str], files: Sequence[Path] = ()) -> List[str]:
    """The tickers to collect: the positional *tickers* (a blank one left out), then each of *files*'
    (``read_tickers_file``), in Yahoo's form, each once, in order. Raises ValueError for one that isn't a ticker or a
    file that isn't a tickers file, OSError for a file that can't be read."""
    named = [ticker for ticker in tickers if ticker.strip()]
    for path in files:
        named += read_tickers_file(path)
    return unique_tickers(named)


def stored_tickers(db_path: Path) -> Set[str]:
    """The tickers in the database's companies table, read through a read-only connection: none without a database
    (none is created), or with one that has no companies table yet."""
    if not Path(db_path).is_file():
        return set()
    with closing(sqlite3.connect(read_only_uri(db_path), uri=True)) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'companies'").fetchone() is None:
            return set()
        return {row[0] for row in conn.execute("SELECT ticker FROM companies")}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch US stock fundamentals from Yahoo Finance and SEC EDGAR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Fetch data for multiple tickers
    python -m src.main AAPL MSFT GOOGL TSLA

    # Fetch with custom output directory
    python -m src.main AAPL --output-dir ./stock_data

    # Skip SEC data (Yahoo only)
    python -m src.main AAPL --no-sec

    # Fetch more historical data
    python -m src.main AAPL --years 10

    # JSON only output
    python -m src.main AAPL --formats json

    # Verbose logging
    python -m src.main AAPL -v

    # A list of tickers (repeatable, beside positional tickers)
    python -m src.main --tickers-file universe/core.txt

    # The S&P 500's members not yet in the database
    python -m src.main --tickers-file universe/sp500-2026-10-25.csv --skip-existing
        """
    )

    parser.add_argument(
        "tickers",
        nargs="*",
        help="Stock ticker symbols (e.g., AAPL MSFT GOOGL); optional with --tickers-file"
    )

    parser.add_argument(
        "--tickers-file",
        type=Path,
        action="append",
        metavar="PATH",
        help="A file of tickers to collect (repeatable): a .txt (a ticker a line, '#' comments) or a .csv (its "
             "ticker or Symbol column); added after the positional tickers, each ticker once"
    )

    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Leave out the tickers already in the database (--db, else <output-dir>/output/stock.db)"
    )

    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path("data"),
        help="Base output directory (default: ./data)"
    )

    parser.add_argument(
        "--formats", "-f",
        nargs="+",
        choices=["json", "csv", "sqlite"],
        default=["json", "csv", "sqlite"],
        help="Output formats (default: json csv sqlite)"
    )

    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite database path (default: <output-dir>/output/stock.db)"
    )

    parser.add_argument(
        "--years",
        type=int,
        default=None,
        help="Limit historical financial data to the most recent N years "
             "(default: all available from SEC)"
    )

    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=4,
        help="Number of tickers to fetch concurrently (default: 4, 1 = sequential)"
    )

    parser.add_argument(
        "--sec-user-agent",
        type=str,
        default="StockDataCollector admin@example.com",
        help="SEC EDGAR User-Agent (format: 'Company email@domain.com')"
    )

    parser.add_argument(
        "--no-yahoo",
        action="store_true",
        help="Skip Yahoo Finance data"
    )

    parser.add_argument(
        "--no-sec",
        action="store_true",
        help="Skip SEC EDGAR data"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point: ``refresh`` as the first argument runs ``stock-data refresh`` with the rest."""
    given = list(sys.argv[1:] if argv is None else argv)
    if given[:1] == ["refresh"]:
        from . import refresh
        return refresh.main(given[1:])
    args = parse_args(given)

    # Setup logging
    logger = setup_logging(args.verbose)

    # Validate inputs: the positional tickers and the files', in Yahoo's form, each once
    try:
        tickers = resolve_tickers(args.tickers, args.tickers_file or [])
    except (OSError, ValueError) as e:
        logger.error(f"Cannot read the tickers: {e}")
        return 1
    if not tickers:
        logger.error("No tickers given: name some, or a --tickers-file that lists some")
        return 1

    if args.no_yahoo and args.no_sec:
        logger.error("Cannot skip both Yahoo and SEC data sources")
        return 1

    storage = StorageConfig(base_dir=args.output_dir, db_path=args.db)
    if args.skip_existing:
        db_path = storage.database_path
        try:
            existing = stored_tickers(db_path)
        except sqlite3.Error as e:
            logger.error(f"Cannot read the companies in {db_path}: {e}")
            return 1
        new = [t for t in tickers if t not in existing]
        logger.info(f"--skip-existing: {len(tickers) - len(new)} of {len(tickers)} tickers already in {db_path} "
                    f"left out, {len(new)} to collect")
        if not new:
            logger.error(f"All {len(tickers)} tickers given are already in the database ({db_path}): nothing to "
                         "collect (--skip-existing)")
            return 1
        tickers = new

    logger.info("Stock Data Fetcher starting")
    logger.info(f"Tickers: {', '.join(tickers)}")
    logger.info(f"Output directory: {args.output_dir}")
    logger.info(f"Formats: {', '.join(args.formats)}")
    logger.info(f"Years of data: {args.years or 'all available'}")
    logger.info(f"Yahoo Finance: {'disabled' if args.no_yahoo else 'enabled'}")
    logger.info(f"SEC EDGAR: {'disabled' if args.no_sec else 'enabled'}")

    # Build configuration
    config = AppConfig(
        sec=SECConfig(user_agent=args.sec_user_agent),
        yahoo=YahooConfig(),
        storage=storage,
        output_formats=args.formats,
        max_workers=args.workers,
    )

    # Run fetcher
    try:
        with StockDataFetcher(config=config, logger=logger) as fetcher:
            summary = fetcher.fetch_and_export(
                tickers=tickers,
                include_yahoo=not args.no_yahoo,
                include_sec=not args.no_sec,
                years_back=args.years,
                formats=args.formats
            )

        # Print summary
        print("\n" + "=" * 60)
        print("FETCH COMPLETE")
        print("=" * 60)
        print(f"Tickers requested: {summary['tickers_requested']}")
        print(f"Successfully fetched: {summary['successful']}")
        print(f"With warnings: {summary['with_warnings']}")
        print(f"With errors: {summary['with_errors']}")
        print(f"Duration: {summary['duration_seconds']:.1f} seconds")
        print()
        print("Output files:")
        for format_type, paths in summary['export_paths'].items():
            for path in paths:
                print(f"  [{format_type.upper()}] {path}")
        print("=" * 60)

        return 0

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())

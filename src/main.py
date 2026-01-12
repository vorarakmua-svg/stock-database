"""
Stock Data Collection System - CLI Entry Point

Usage:
    python -m src.main AAPL MSFT GOOGL
    python -m src.main AAPL --output-dir ./my_data
    python -m src.main AAPL MSFT --no-sec --formats json
    python -m src.main AAPL --years 10 --verbose
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from .config import AppConfig, SECConfig, YahooConfig, StorageConfig
from .fetchers.stock_data_fetcher import StockDataFetcher


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    return logging.getLogger("stock_fetcher")


def parse_args() -> argparse.Namespace:
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
        """
    )

    parser.add_argument(
        "tickers",
        nargs="+",
        help="Stock ticker symbols (e.g., AAPL MSFT GOOGL)"
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
        choices=["json", "csv"],
        default=["json", "csv"],
        help="Output formats (default: json csv)"
    )

    parser.add_argument(
        "--years",
        type=int,
        default=10,
        help="Years of historical financial data (default: 10, max available from SEC)"
    )

    parser.add_argument(
        "--sec-user-agent",
        type=str,
        default="StockDataCollector user@example.com",
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

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Setup logging
    logger = setup_logging(args.verbose)

    # Validate inputs
    if not args.tickers:
        logger.error("No tickers provided")
        return 1

    if args.no_yahoo and args.no_sec:
        logger.error("Cannot skip both Yahoo and SEC data sources")
        return 1

    # Clean tickers
    tickers = [t.upper().strip() for t in args.tickers if t.strip()]

    logger.info(f"Stock Data Fetcher starting")
    logger.info(f"Tickers: {', '.join(tickers)}")
    logger.info(f"Output directory: {args.output_dir}")
    logger.info(f"Formats: {', '.join(args.formats)}")
    logger.info(f"Years of data: {args.years}")
    logger.info(f"Yahoo Finance: {'disabled' if args.no_yahoo else 'enabled'}")
    logger.info(f"SEC EDGAR: {'disabled' if args.no_sec else 'enabled'}")

    # Build configuration
    config = AppConfig(
        sec=SECConfig(user_agent=args.sec_user_agent),
        yahoo=YahooConfig(),
        storage=StorageConfig(base_dir=args.output_dir),
        output_formats=args.formats,
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

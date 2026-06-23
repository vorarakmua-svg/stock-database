"""Canonical-field coverage report across a cross-sector company universe.

Run:
    python coverage_report.py                 # curated ~40-ticker basket
    python coverage_report.py JPM PGR PLD     # specific tickers
    python coverage_report.py --user-agent "Me me@example.com"

Measures, per canonical field and per sector, how often it is reported / derived /
empty — the empirical proof of standardization across all companies. Uses the SEC
payload cache, so re-runs are fast.
"""

import argparse
import logging
import sys

from src.analysis.coverage import analyze, basket_tickers, format_summary, summarize
from src.fetchers.sec_handler import SECHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonical-field coverage report")
    parser.add_argument("tickers", nargs="*", help="Tickers (default: curated basket)")
    parser.add_argument("--user-agent", default="StockDataCollector admin@example.com",
                        help="SEC User-Agent (format: 'Company email@domain.com')")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING if not args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    logger = logging.getLogger("coverage")

    tickers = [t.upper() for t in args.tickers] or basket_tickers()
    handler = SECHandler(user_agent=args.user_agent, logger=logger)

    coverages = []
    try:
        for i, ticker in enumerate(tickers, 1):
            cik = handler.ticker_to_cik(ticker)
            if not cik:
                print(f"[{i}/{len(tickers)}] {ticker}: CIK not found", file=sys.stderr)
                continue
            try:
                facts = handler.get_company_facts(cik)
                subs = handler.get_submissions(cik)
            except Exception as e:
                print(f"[{i}/{len(tickers)}] {ticker}: fetch error {e}", file=sys.stderr)
                continue
            cov = analyze(ticker, facts or {}, subs)
            coverages.append(cov)
            print(f"[{i}/{len(tickers)}] {ticker:6} {cov.sector:10} FY{cov.fiscal_year}",
                  file=sys.stderr)
    finally:
        handler.close()

    if not coverages:
        print("No companies analyzed.")
        return 1

    print(format_summary(summarize(coverages)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

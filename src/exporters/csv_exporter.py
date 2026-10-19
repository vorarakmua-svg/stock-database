"""CSV file exporter for stock data summaries."""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models.stock_data import StockData


class CSVExporter:
    """
    Export stock data summaries to CSV files.

    Creates a summary CSV with key metrics from all stocks.
    """

    def __init__(
        self,
        output_dir: Path,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize CSV exporter.

        Args:
            output_dir: Directory for output CSV files
            logger: Optional logger instance
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)

    def export(
        self,
        data: List[StockData],
        filename: str = "summary.csv"
    ) -> Optional[Path]:
        """
        Export stock data summaries to CSV, keeping tickers from earlier runs.

        A ticker in ``data`` replaces its existing row; rows for other tickers
        are kept, so the file summarizes every ticker collected into this
        output directory, not just the latest run.

        Args:
            data: List of StockData objects
            filename: Output filename

        Returns:
            Path to created file
        """
        if not data:
            self.logger.warning("No data to export to CSV")
            return None

        try:
            filepath = self.output_dir / filename

            # This run's values replace the earlier ones for the same tickers, but a value it
            # didn't get (empty after a failed fetch) keeps the earlier one, as the JSON does.
            existing_columns, existing_rows = self._read_existing(filepath)
            new_rows = {
                summary["ticker"]: self._clean_for_csv(summary)
                for summary in (stock.to_summary() for stock in data)
            }
            rows = {row["ticker"]: row for row in existing_rows}
            for ticker, new_row in new_rows.items():
                rows[ticker] = {**rows.get(ticker, {}), **{k: v for k, v in new_row.items() if v != ""}}

            # Determine all columns (union of all keys)
            all_columns = set(existing_columns)
            for row in new_rows.values():
                all_columns.update(row.keys())

            # Sort columns for consistent output
            # Put key columns first
            priority_columns = [
                "ticker", "cik", "company_name", "sector", "industry",
                "current_price", "market_cap", "pe_trailing", "pe_forward",
                "eps_trailing", "dividend_yield", "beta",
                "total_revenue", "net_income", "total_cash", "total_debt",
                "profit_margin", "return_on_equity",
            ]

            # Build ordered column list
            columns = []
            for col in priority_columns:
                if col in all_columns:
                    columns.append(col)
                    all_columns.discard(col)

            # Add remaining columns sorted alphabetically
            columns.extend(sorted(all_columns))

            self._write_atomically(filepath, columns, [rows[ticker] for ticker in sorted(rows)])

            self.logger.info(f"Exported {len(data)} stocks to {filepath} ({len(rows)} in total)")
            return filepath

        except Exception as e:
            self.logger.error(f"Error exporting to CSV: {e}")
            return None

    def _read_existing(self, filepath: Path) -> Tuple[List[str], List[Dict[str, str]]]:
        """Columns and rows of an earlier export, so a run adds to it instead of replacing it.

        The file holds every earlier run, so nothing here may lose it. ``utf-8-sig``
        reads past the byte-order mark Excel adds when it saves "CSV UTF-8"; a file
        that still can't be parsed (an Excel ANSI save) is moved aside for the user
        instead of being overwritten, and any other error propagates with the file
        left as it is.
        """
        if not filepath.exists():
            return [], []
        try:
            with open(filepath, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                return list(reader.fieldnames or []), [row for row in reader if row.get("ticker")]
        except (UnicodeDecodeError, csv.Error) as e:
            kept = filepath.with_name(f"{filepath.stem}.unreadable-{datetime.now():%Y%m%d-%H%M%S}{filepath.suffix}")
            filepath.replace(kept)
            self.logger.warning(
                f"Could not read existing {filepath.name} ({e}); kept it as {kept.name} and wrote this run's data only"
            )
            return [], []

    def _write_atomically(self, filepath: Path, columns: List[str], rows: List[Dict[str, Any]]) -> None:
        """Write to a temporary file and swap it in, so a failure mid-write leaves the old file whole."""
        tmp = filepath.with_name(filepath.name + ".tmp")
        try:
            with open(tmp, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)
            os.replace(tmp, filepath)
        finally:
            tmp.unlink(missing_ok=True)

    def _clean_for_csv(self, data: dict) -> dict:
        """Clean values for CSV output."""
        cleaned = {}
        for key, value in data.items():
            if value is None:
                cleaned[key] = ""
            elif isinstance(value, float):
                # Format floats nicely
                if abs(value) >= 1e9:
                    cleaned[key] = f"{value:.2e}"
                elif abs(value) >= 1000:
                    cleaned[key] = f"{value:.2f}"
                elif abs(value) >= 1:
                    cleaned[key] = f"{value:.4f}"
                else:
                    cleaned[key] = f"{value:.6f}"
            elif isinstance(value, (list, dict)):
                cleaned[key] = str(value)
            else:
                cleaned[key] = value
        return cleaned

    def export_financial_history(
        self,
        data: List[StockData],
        filename: str = "financial_history.csv"
    ) -> Optional[Path]:
        """
        Export historical financial data to CSV, keeping earlier runs' rows.

        Creates a time-series CSV with annual financials. Like the JSON merge,
        a ticker-year in ``data`` replaces the same ticker-year; all other rows
        (other tickers, older years) are kept.

        Args:
            data: List of StockData objects
            filename: Output filename

        Returns:
            Path to created file
        """
        try:
            filepath = self.output_dir / filename

            rows = []
            for stock in data:
                if not stock.financials_annual:
                    continue

                for year, financials in stock.financials_annual.items():
                    row = {
                        "ticker": stock.ticker,
                        "fiscal_year": year,
                        # Skip internal/provenance keys (e.g. _source_tags)
                        **{k: v for k, v in financials.items() if not k.startswith("_")}
                    }
                    rows.append(row)

            if not rows:
                self.logger.warning("No financial history to export")
                return None

            # This run's ticker-years replace the same ticker-years from earlier runs
            existing_columns, existing_rows = self._read_existing(filepath)
            merged = {(row["ticker"], row.get("fiscal_year", "")): row for row in existing_rows}
            for row in rows:
                merged[(str(row["ticker"]), str(row["fiscal_year"]))] = self._clean_for_csv(row)

            # Get all columns
            all_columns = set(existing_columns)
            for row in rows:
                all_columns.update(row.keys())

            # Sort columns
            priority = ["ticker", "fiscal_year"]
            columns = priority + sorted(all_columns - set(priority))

            self._write_atomically(filepath, columns, [merged[key] for key in sorted(merged)])

            self.logger.info(f"Exported financial history to {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"Error exporting financial history: {e}")
            return None

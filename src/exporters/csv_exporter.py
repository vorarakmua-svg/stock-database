"""CSV file exporter for stock data summaries."""

import csv
import logging
from pathlib import Path
from typing import List, Optional

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
        Export stock data summaries to CSV.

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

            # Get summaries
            summaries = [stock.to_summary() for stock in data]

            # Determine all columns (union of all keys)
            all_columns = set()
            for summary in summaries:
                all_columns.update(summary.keys())

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

            # Write CSV
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
                writer.writeheader()
                for summary in summaries:
                    # Clean values for CSV
                    cleaned = self._clean_for_csv(summary)
                    writer.writerow(cleaned)

            self.logger.info(f"Exported {len(data)} stocks to {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"Error exporting to CSV: {e}")
            return None

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
        Export historical financial data to CSV.

        Creates a time-series CSV with annual financials.

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

            # Get all columns
            all_columns = set()
            for row in rows:
                all_columns.update(row.keys())

            # Sort columns
            priority = ["ticker", "fiscal_year"]
            columns = priority + sorted(all_columns - set(priority))

            # Write CSV
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
                writer.writeheader()
                for row in rows:
                    writer.writerow(self._clean_for_csv(row))

            self.logger.info(f"Exported financial history to {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"Error exporting financial history: {e}")
            return None

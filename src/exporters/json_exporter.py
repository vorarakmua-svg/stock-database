"""JSON file exporter for stock data."""

import json
import logging
from pathlib import Path
from typing import List, Union, Optional
from datetime import datetime

from ..models.stock_data import StockData


class JSONExporter:
    """
    Export stock data to JSON files.

    Creates individual JSON files for each ticker with full data.
    """

    def __init__(
        self,
        output_dir: Path,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize JSON exporter.

        Args:
            output_dir: Directory for output JSON files
            logger: Optional logger instance
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)

    def export(
        self,
        data: Union[StockData, List[StockData]],
        filename: Optional[str] = None
    ) -> List[Path]:
        """
        Export stock data to JSON file(s).

        Args:
            data: Single StockData or list of StockData objects
            filename: Optional custom filename (used only for single export)

        Returns:
            List of paths to created files
        """
        if isinstance(data, StockData):
            data = [data]

        paths = []
        for stock in data:
            path = self._export_single(stock, filename if len(data) == 1 else None)
            if path:
                paths.append(path)

        return paths

    def _export_single(
        self,
        stock: StockData,
        filename: Optional[str] = None
    ) -> Optional[Path]:
        """Export a single StockData to JSON."""
        try:
            # Generate filename from ticker if not provided
            if filename:
                filepath = self.output_dir / filename
            else:
                filepath = self.output_dir / f"{stock.ticker.upper()}.json"

            # Ensure .json extension
            if not filepath.suffix:
                filepath = filepath.with_suffix(".json")

            # Convert to dict and write
            data_dict = stock.to_dict()

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data_dict, f, indent=2, default=str, ensure_ascii=False)

            self.logger.info(f"Exported {stock.ticker} to {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"Error exporting {stock.ticker} to JSON: {e}")
            return None

    def export_combined(
        self,
        data: List[StockData],
        filename: str = "all_stocks.json"
    ) -> Optional[Path]:
        """
        Export all stocks to a single combined JSON file.

        Args:
            data: List of StockData objects
            filename: Output filename

        Returns:
            Path to created file
        """
        try:
            filepath = self.output_dir / filename

            combined = {
                "exported_at": datetime.now().isoformat(),
                "count": len(data),
                "stocks": {
                    stock.ticker: stock.to_dict()
                    for stock in data
                }
            }

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(combined, f, indent=2, default=str, ensure_ascii=False)

            self.logger.info(f"Exported {len(data)} stocks to {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"Error exporting combined JSON: {e}")
            return None

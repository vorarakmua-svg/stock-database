"""JSON file exporter for stock data."""

import json
import logging
from pathlib import Path
from typing import List, Union, Optional, Dict, Any
from datetime import datetime

from ..models.stock_data import StockData


class JSONExporter:
    """
    Export stock data to JSON files.

    Creates individual JSON files for each ticker with full data.
    Supports merging new data with existing data to preserve history.
    """

    def __init__(
        self,
        output_dir: Path,
        logger: Optional[logging.Logger] = None,
        merge_mode: bool = True
    ):
        """
        Initialize JSON exporter.

        Args:
            output_dir: Directory for output JSON files
            logger: Optional logger instance
            merge_mode: If True, merge with existing data instead of overwriting
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger(__name__)
        self.merge_mode = merge_mode

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

            # Convert to dict
            new_data = stock.to_dict()

            # Merge with existing data if merge_mode is enabled
            if self.merge_mode and filepath.exists():
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                    new_data = self._merge_data(existing_data, new_data)
                    self.logger.info(f"Merged new data with existing {stock.ticker} data")
                except Exception as e:
                    self.logger.warning(f"Could not load existing data for merge: {e}")

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, indent=2, default=str, ensure_ascii=False)

            self.logger.info(f"Exported {stock.ticker} to {filepath}")
            return filepath

        except Exception as e:
            self.logger.error(f"Error exporting {stock.ticker} to JSON: {e}")
            return None

    def _merge_data(
        self,
        existing: Dict[str, Any],
        new: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge new data with existing data, preserving historical records.

        Merge strategy:
        - financials_annual: Keep all years from both (union)
        - financials_quarterly: Keep all quarters from both (union)
        - insider_transactions: Merge and deduplicate
        - price_history: Keep both old and new, store history
        - market_data, valuation, shareholders: Use latest (new)
        - calculated_metrics: Use latest (new)
        - company_info: Use latest but preserve missing fields
        """
        merged = new.copy()

        # Merge annual financials (keep all years)
        if existing.get("financials_annual") and new.get("financials_annual"):
            merged_annual = existing["financials_annual"].copy()
            merged_annual.update(new["financials_annual"])  # New overwrites same year
            merged["financials_annual"] = merged_annual
            self.logger.debug(f"Merged annual financials: {len(merged_annual)} years")

        # Merge quarterly financials (keep all quarters)
        if existing.get("financials_quarterly") and new.get("financials_quarterly"):
            merged_quarterly = existing["financials_quarterly"].copy()
            merged_quarterly.update(new["financials_quarterly"])
            merged["financials_quarterly"] = merged_quarterly
            self.logger.debug(f"Merged quarterly financials: {len(merged_quarterly)} quarters")

        # Merge insider transactions (deduplicate by date+name+shares)
        if existing.get("insider_transactions") and new.get("insider_transactions"):
            merged["insider_transactions"] = self._merge_transactions(
                existing["insider_transactions"],
                new["insider_transactions"]
            )

        # Preserve price history over time
        if existing.get("price_history") and new.get("price_history"):
            # Store previous snapshots in history
            history_snapshots = existing.get("price_history_snapshots", [])
            old_snapshot = {
                "collected_at": existing.get("collected_at"),
                **{k: v for k, v in existing["price_history"].items()
                   if k not in ["annual_returns"]}
            }
            history_snapshots.append(old_snapshot)
            # Keep last 10 snapshots
            merged["price_history_snapshots"] = history_snapshots[-10:]

        # Merge company info (use new but fill gaps from old)
        if existing.get("company_info") and new.get("company_info"):
            merged_info = existing["company_info"].copy()
            # Update with new values (new takes precedence)
            for k, v in new["company_info"].items():
                if v is not None:
                    merged_info[k] = v
            merged["company_info"] = merged_info

        # Track data collection history
        collection_history = existing.get("collection_history", [])
        collection_history.append({
            "collected_at": new.get("collected_at"),
            "data_sources": new.get("data_sources", []),
        })
        # Keep last 50 collection records
        merged["collection_history"] = collection_history[-50:]

        # Record first collected date
        if existing.get("first_collected_at"):
            merged["first_collected_at"] = existing["first_collected_at"]
        else:
            merged["first_collected_at"] = existing.get("collected_at")

        return merged

    def _merge_transactions(
        self,
        existing: List[Dict],
        new: List[Dict]
    ) -> List[Dict]:
        """Merge insider transactions, removing duplicates."""
        # Create unique key for each transaction
        def tx_key(tx):
            return (
                tx.get("transaction_date", ""),
                tx.get("reporting_owner", ""),
                tx.get("shares", 0),
                tx.get("transaction_type", "")
            )

        seen = set()
        merged = []

        # Add new transactions first (higher priority)
        for tx in new:
            key = tx_key(tx)
            if key not in seen:
                seen.add(key)
                merged.append(tx)

        # Add existing transactions that aren't duplicates
        for tx in existing:
            key = tx_key(tx)
            if key not in seen:
                seen.add(key)
                merged.append(tx)

        # Sort by date descending
        merged.sort(key=lambda x: x.get("transaction_date", ""), reverse=True)

        return merged

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

"""JSON file exporter for stock data."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..models.stock_data import PRICED_METRICS, QUOTE_SECTIONS, StockData, fill, got_price
from ..parsers.calculated_metrics import CalculatedMetrics
from .record_scope import absent

#: A run's own diagnostics: always the latest run's when merging, even when empty.
_RUN_FIELDS = frozenset({"collected_at", "data_sources", "errors", "warnings"})
#: The merge's own bookkeeping: never data a run kept.
_BOOKKEEPING = frozenset({"collection_history", "first_collected_at", "price_history_snapshots",
                          "section_collected_at"})
#: The SEC record's sections a run that can't read the predecessor's filings only fills (BUG-179, BUG-278).
_FILLED_SECTIONS = ("financials_annual", "financials_quarterly", "financials_ttm")


def _is_empty(value: Any) -> bool:
    """Nothing the run got: no value, or a handler's failure alone, ``{"error": ...}`` (BUG-118)."""
    return value is None or value == "" or value == {} or value == [] or (
        isinstance(value, dict) and value.keys() == {"error"})


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
                    new_data = self._merge_data(self._without_absent(existing_data, stock), new_data,
                                                fill_only=bool(stock.predecessor_error))
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

    @staticmethod
    def _without_absent(existing: Dict[str, Any], stock: StockData) -> Dict[str, Any]:
        """*existing* without the periods and unmapped tags *stock*'s run no longer gives and speaks for, as the
        database removes them (``record_scope.absent``: a complete read of a filer with an annual record, within its
        years_back, a part whose building step raised kept), so the merge keeps only what the run still gives
        (BUG-380)."""
        kept = dict(existing)
        for section in ("financials_annual", "financials_quarterly", "financials_ttm"):
            periods = existing.get(section)
            if isinstance(periods, dict) and periods:
                gone = set(absent(stock, section, periods))
                kept[section] = {key: value for key, value in periods.items() if key not in gone}
        facts = existing.get("unmapped_facts")
        if isinstance(facts, list) and facts:
            gone = set(absent(stock, "unmapped_facts", {fact.get("tag") for fact in facts}))
            kept["unmapped_facts"] = [fact for fact in facts if fact.get("tag") not in gone]
        return kept

    def _merge_data(
        self,
        existing: Dict[str, Any],
        new: Dict[str, Any],
        fill_only: bool = False
    ) -> Dict[str, Any]:
        """
        Merge new data with existing data, preserving historical records.

        Merge strategy:
        - financials_annual: Keep all years from both (union)
        - financials_quarterly: Keep all quarters from both (union)
        (``_export_single`` first takes out of *existing* the periods and unmapped tags a run that speaks for
        the ticker's record no longer gives, as the database does: BUG-380)
        - insider_transactions: Merge and deduplicate
        - price_history: Keep both old and new, store history
        - market_data, valuation, shareholders: Use latest (new)
        - calculated_metrics: Use latest (new)
        - company_info: Use latest but preserve missing fields
        - a section the new run didn't get keeps the earlier one, and ``section_collected_at`` says when
          that was collected (BUG-030); a run without a price keeps the quote and the metrics priced on
          it, as the database's latest snapshot does (BUG-038)
        - *fill_only* (a run that couldn't read its predecessor's filings): the SEC record's periods and
          unmapped facts only fill what's stored, each year's metrics computed on the year as merged, as
          the database does (BUG-179, BUG-278)
        """
        merged = new.copy()
        kept_at: Dict[str, Any] = {}

        def collected_at(key: str) -> Any:
            """When the existing file's *key* was collected: its own run's time, which a kept one carries."""
            return (existing.get("section_collected_at") or {}).get(key) or existing.get("collected_at")

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

        # Merge trailing-twelve-month series (keep all period-ends)
        if existing.get("financials_ttm") and new.get("financials_ttm"):
            merged_ttm = existing["financials_ttm"].copy()
            merged_ttm.update(new["financials_ttm"])
            merged["financials_ttm"] = merged_ttm

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
                "collected_at": collected_at("price_history"),
                **{k: v for k, v in existing["price_history"].items()
                   if k not in ["annual_returns"]}
            }
            history_snapshots.append(old_snapshot)
            # Keep last 10 snapshots
            merged["price_history_snapshots"] = history_snapshots[-10:]

        # Merge company info (use new but fill gaps from old)
        if existing.get("company_info") and new.get("company_info"):
            merged_info = existing["company_info"].copy()
            # Update with new values (new takes precedence); an empty string is no value, as the CSV and SQLite
            # read it (BUG-055)
            for k, v in new["company_info"].items():
                if v is not None and v != "":
                    merged_info[k] = v
            merged["company_info"] = merged_info

        # A run that couldn't read its predecessor's filings speaks for nothing stored: its periods
        # and unmapped facts only fill, and each year's metrics are the year's as merged (BUG-278).
        if fill_only:
            self._fill_sec_record(existing, new, merged)

        # A run without a price has no quote: the earlier quote stays, with the metrics priced on
        # its market cap, as the database keeps its latest snapshot (BUG-038).
        if not got_price(new.get("market_data")):
            for key in QUOTE_SECTIONS:
                if not _is_empty(existing.get(key)):
                    merged[key] = existing[key]
                    kept_at[key] = collected_at(key)
            old_metrics = existing.get("calculated_metrics") or {}
            priced = {k: old_metrics[k] for k in PRICED_METRICS if k in old_metrics}
            if priced and not _is_empty(merged.get("calculated_metrics")):
                merged["calculated_metrics"] = {**merged["calculated_metrics"], **priced}
                kept_at.update({f"calculated_metrics.{k}": collected_at(f"calculated_metrics.{k}") for k in priced})

        # A section the new run didn't get (a failed or partial fetch) keeps the earlier data
        # instead of being emptied, and so does the quality report describing kept financials,
        # each with the time it was collected (BUG-030). Only the run's own diagnostics are
        # always the new run's.
        for key, old in existing.items():
            if key not in _RUN_FIELDS and not _is_empty(old) and _is_empty(merged.get(key)):
                merged[key] = old
                if key not in _BOOKKEEPING:
                    kept_at[key] = collected_at(key)
        if not new.get("financials_annual") and existing.get("financials_annual") and existing.get("data_quality"):
            merged["data_quality"] = existing["data_quality"]
            kept_at["data_quality"] = collected_at("data_quality")
        if kept_at:
            merged["section_collected_at"] = kept_at
        else:
            merged.pop("section_collected_at", None)

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

    def _fill_sec_record(self, existing: Dict[str, Any], new: Dict[str, Any], merged: Dict[str, Any]) -> None:
        """Merge a run that couldn't read its predecessor's filings the way the database takes it
        (``SQLiteStore._write_stock``, BUG-179): its periods and unmapped facts only fill the stored
        ones, and each year it gives has its metrics computed on the year as merged (BUG-278)."""
        for section in _FILLED_SECTIONS:
            if existing.get(section) and new.get(section):
                merged[section] = fill(existing[section], new[section])
        if existing.get("unmapped_facts") and new.get("unmapped_facts"):
            facts = {fact.get("tag"): fact for fact in existing["unmapped_facts"]}
            for fact in new["unmapped_facts"]:
                tag = fact.get("tag")
                facts[tag] = fill(facts[tag], fact) if tag in facts else fact
            merged["unmapped_facts"] = list(facts.values())
        metrics = new.get("calculated_metrics") or {}
        annual = merged.get("financials_annual") or {}
        if metrics.get("historical"):
            calculator = CalculatedMetrics(self.logger)
            historical = dict((existing.get("calculated_metrics") or {}).get("historical") or {})
            for year in metrics["historical"]:
                historical[year] = calculator.calculate_historical(
                    {year: annual.get(year) or {}}, sector=new.get("sector_class"))[year]
            merged["calculated_metrics"] = {**metrics, "historical": historical}

    def _merge_transactions(
        self,
        existing: List[Dict],
        new: List[Dict]
    ) -> List[Dict]:
        """Merge insider (Form 4) filings, removing duplicates.

        Each record is a Form 4 filing as returned by
        ``SECHandler.get_insider_transactions`` (fields from SEC submissions:
        ``accessionNumber``, ``filingDate``, ``form``, ...). The accession number
        uniquely identifies a filing; we fall back to (filingDate, form) when it
        is absent.
        """
        def tx_key(tx):
            return (
                tx.get("accessionNumber") or "",
                tx.get("filingDate") or "",
                tx.get("form") or "",
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

        # Sort by filing date descending
        merged.sort(key=lambda x: x.get("filingDate", ""), reverse=True)

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

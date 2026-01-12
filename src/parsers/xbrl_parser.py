"""XBRL data parser for SEC EDGAR financial data."""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from ..mappings.xbrl_tags import XBRL_TAG_MAPPING, XBRL_SIMPLE_MAPPING, PRIORITY_TAGS


class XBRLParser:
    """
    Parser for XBRL financial data from SEC EDGAR Company Facts API.

    Converts raw XBRL data to structured, human-readable format.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize XBRL parser.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self.tag_mapping = XBRL_TAG_MAPPING
        self.simple_mapping = XBRL_SIMPLE_MAPPING

    def parse_company_facts(self, facts: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse raw company facts into structured data.

        Args:
            facts: Raw company facts from SEC EDGAR API

        Returns:
            Structured dictionary with parsed financial data
        """
        if not facts:
            return {}

        result = {
            "cik": facts.get("cik"),
            "entity_name": facts.get("entityName"),
            "parsed_at": datetime.now().isoformat(),
        }

        # Parse US-GAAP facts
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if us_gaap:
            result["us_gaap"] = self._parse_taxonomy(us_gaap)

        # Parse DEI (Document and Entity Information)
        dei = facts.get("facts", {}).get("dei", {})
        if dei:
            result["dei"] = self._parse_taxonomy(dei)

        return result

    def _parse_taxonomy(self, taxonomy_data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a taxonomy (us-gaap or dei) section."""
        parsed = {}

        for tag, tag_data in taxonomy_data.items():
            # Get human-readable name if available
            simple_name = self.simple_mapping.get(tag, tag)

            parsed[tag] = {
                "label": tag_data.get("label", tag),
                "description": tag_data.get("description", ""),
                "simple_name": simple_name,
                "values": self._parse_units(tag_data.get("units", {})),
            }

        return parsed

    def _parse_units(self, units: Dict[str, List]) -> Dict[str, List[Dict]]:
        """Parse unit-based values (USD, shares, etc.)."""
        parsed = {}

        for unit_type, values in units.items():
            parsed[unit_type] = [
                {
                    "value": v.get("val"),
                    "start": v.get("start"),
                    "end": v.get("end"),
                    "filed": v.get("filed"),
                    "form": v.get("form"),
                    "fiscal_year": v.get("fy"),
                    "fiscal_period": v.get("fp"),
                    "accession": v.get("accn"),
                    "frame": v.get("frame"),
                }
                for v in values
            ]

        return parsed

    def extract_annual_financials(
        self,
        facts: Dict[str, Any],
        years_back: int = 5,
        extract_all: bool = True
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract annual financial statements from company facts.

        Args:
            facts: Raw company facts
            years_back: Number of fiscal years to extract
            extract_all: If True, extract ALL tags (not just mapped ones)

        Returns:
            Dictionary organized by fiscal year
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        # Collect all 10-K data points
        annual_data = {}

        # Determine which tags to extract
        tags_to_extract = us_gaap.keys() if extract_all else PRIORITY_TAGS

        for tag in tags_to_extract:
            tag_data = us_gaap.get(tag, {})
            if not tag_data:
                continue

            # Get USD values first, then shares, then per-share
            units = tag_data.get("units", {})
            values = units.get("USD", []) or units.get("shares", []) or units.get("USD/shares", [])

            for entry in values:
                form = entry.get("form", "")
                if form not in ["10-K", "10-K/A"]:
                    continue

                fy = entry.get("fy")
                if not fy:
                    continue

                # Initialize year entry
                if fy not in annual_data:
                    annual_data[fy] = {
                        "fiscal_year": fy,
                        "filed_date": entry.get("filed"),
                        "period_end": entry.get("end"),
                        "form": form,
                    }

                # Add the metric with simple name if mapped, otherwise use raw tag
                simple_name = self.simple_mapping.get(tag, tag)

                # Only add if not already present (first value wins for same field)
                if simple_name not in annual_data[fy]:
                    annual_data[fy][simple_name] = entry.get("val")

        # Sort and limit years
        sorted_years = sorted(annual_data.keys(), reverse=True)[:years_back]
        return {str(y): annual_data[y] for y in sorted_years}

    def extract_quarterly_financials(
        self,
        facts: Dict[str, Any],
        quarters_back: int = 12,
        extract_all: bool = True
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract quarterly financial statements from company facts.

        Args:
            facts: Raw company facts
            quarters_back: Number of quarters to extract
            extract_all: If True, extract ALL tags (not just mapped ones)

        Returns:
            Dictionary organized by period end date
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        quarterly_data = {}

        # Determine which tags to extract
        tags_to_extract = us_gaap.keys() if extract_all else PRIORITY_TAGS

        for tag in tags_to_extract:
            tag_data = us_gaap.get(tag, {})
            if not tag_data:
                continue

            units = tag_data.get("units", {})
            values = units.get("USD", []) or units.get("shares", []) or units.get("USD/shares", [])

            for entry in values:
                form = entry.get("form", "")
                if form not in ["10-Q", "10-Q/A"]:
                    continue

                period_end = entry.get("end")
                if not period_end:
                    continue

                if period_end not in quarterly_data:
                    quarterly_data[period_end] = {
                        "period_end": period_end,
                        "filed_date": entry.get("filed"),
                        "fiscal_year": entry.get("fy"),
                        "fiscal_period": entry.get("fp"),
                        "form": form,
                    }

                simple_name = self.simple_mapping.get(tag, tag)
                # Only add if not already present
                if simple_name not in quarterly_data[period_end]:
                    quarterly_data[period_end][simple_name] = entry.get("val")

        # Sort and limit
        sorted_periods = sorted(quarterly_data.keys(), reverse=True)[:quarters_back]
        return {p: quarterly_data[p] for p in sorted_periods}

    def get_latest_metrics(
        self,
        facts: Dict[str, Any],
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Get the most recent value for each financial metric.

        Args:
            facts: Raw company facts
            tags: List of tags to extract (defaults to PRIORITY_TAGS)

        Returns:
            Dictionary with latest values for each metric
        """
        tags = tags or PRIORITY_TAGS
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        latest = {}

        for tag in tags:
            tag_data = us_gaap.get(tag, {})
            if not tag_data:
                continue

            units = tag_data.get("units", {})
            values = units.get("USD", []) or units.get("shares", []) or units.get("USD/shares", [])

            if not values:
                continue

            # Find the most recent filed value
            sorted_values = sorted(
                values,
                key=lambda x: x.get("filed", ""),
                reverse=True
            )

            if sorted_values:
                most_recent = sorted_values[0]
                simple_name = self.simple_mapping.get(tag, tag)
                latest[simple_name] = {
                    "value": most_recent.get("val"),
                    "period_end": most_recent.get("end"),
                    "filed_date": most_recent.get("filed"),
                    "form": most_recent.get("form"),
                }

        return latest

    def build_balance_sheet(
        self,
        facts: Dict[str, Any],
        fiscal_year: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Build a structured balance sheet from company facts.

        Args:
            facts: Raw company facts
            fiscal_year: Specific fiscal year (None for latest)

        Returns:
            Structured balance sheet dictionary
        """
        balance_sheet_tags = [
            tag for tag, info in self.tag_mapping.items()
            if info.get("category") == "balance_sheet"
        ]

        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        balance_sheet = {"assets": {}, "liabilities": {}, "equity": {}}

        for tag in balance_sheet_tags:
            tag_data = us_gaap.get(tag, {})
            if not tag_data:
                continue

            info = self.tag_mapping.get(tag, {})
            subcategory = info.get("subcategory", "other")
            simple_name = info.get("simple_name", tag)

            units = tag_data.get("units", {})
            values = units.get("USD", [])

            if not values:
                continue

            # Filter by fiscal year if specified
            if fiscal_year:
                values = [v for v in values if v.get("fy") == fiscal_year]

            # Get most recent 10-K value
            annual_values = [v for v in values if v.get("form") in ["10-K", "10-K/A"]]
            if annual_values:
                latest = max(annual_values, key=lambda x: x.get("filed", ""))
                balance_sheet[subcategory][simple_name] = latest.get("val")

        return balance_sheet

    def build_income_statement(
        self,
        facts: Dict[str, Any],
        fiscal_year: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Build a structured income statement from company facts.

        Args:
            facts: Raw company facts
            fiscal_year: Specific fiscal year (None for latest)

        Returns:
            Structured income statement dictionary
        """
        income_tags = [
            tag for tag, info in self.tag_mapping.items()
            if info.get("category") == "income_statement"
        ]

        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        income_statement = {}

        for tag in income_tags:
            tag_data = us_gaap.get(tag, {})
            if not tag_data:
                continue

            info = self.tag_mapping.get(tag, {})
            simple_name = info.get("simple_name", tag)

            units = tag_data.get("units", {})
            values = units.get("USD", []) or units.get("USD/shares", [])

            if not values:
                continue

            if fiscal_year:
                values = [v for v in values if v.get("fy") == fiscal_year]

            annual_values = [v for v in values if v.get("form") in ["10-K", "10-K/A"]]
            if annual_values:
                latest = max(annual_values, key=lambda x: x.get("filed", ""))
                income_statement[simple_name] = latest.get("val")

        return income_statement

    def build_cash_flow(
        self,
        facts: Dict[str, Any],
        fiscal_year: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Build a structured cash flow statement from company facts.

        Args:
            facts: Raw company facts
            fiscal_year: Specific fiscal year (None for latest)

        Returns:
            Structured cash flow statement dictionary
        """
        cash_flow_tags = [
            tag for tag, info in self.tag_mapping.items()
            if info.get("category") == "cash_flow"
        ]

        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        cash_flow = {}

        for tag in cash_flow_tags:
            tag_data = us_gaap.get(tag, {})
            if not tag_data:
                continue

            info = self.tag_mapping.get(tag, {})
            simple_name = info.get("simple_name", tag)

            units = tag_data.get("units", {})
            values = units.get("USD", [])

            if not values:
                continue

            if fiscal_year:
                values = [v for v in values if v.get("fy") == fiscal_year]

            annual_values = [v for v in values if v.get("form") in ["10-K", "10-K/A"]]
            if annual_values:
                latest = max(annual_values, key=lambda x: x.get("filed", ""))
                cash_flow[simple_name] = latest.get("val")

        return cash_flow

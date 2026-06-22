"""XBRL data parser for SEC EDGAR financial data."""

import logging
import re
from typing import Dict, Any, Optional
from datetime import date, datetime

from ..mappings.xbrl_tags import XBRL_SIMPLE_MAPPING, PRIORITY_TAGS

# Duration bounds (in days) used to distinguish annual vs quarterly periods.
# A "full year" span is ~365 days; a quarter is ~91 days. Bounds are generous
# to absorb 52/53-week fiscal calendars.
_FULL_YEAR_MIN_DAYS = 350
_FULL_YEAR_MAX_DAYS = 380
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100

_FRAME_YEAR_RE = re.compile(r"CY(\d{4})")


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
        self.simple_mapping = XBRL_SIMPLE_MAPPING

    # ========== Period helpers ==========

    @staticmethod
    def _parse_iso_date(value: Optional[str]) -> Optional[date]:
        """Parse an ISO ``YYYY-MM-DD`` string into a date, or None."""
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def _period_year(self, entry: Dict[str, Any]) -> Optional[int]:
        """Determine the fiscal year a fact *covers*.

        In SEC ``companyfacts`` each fact carries the ``fy`` of the filing it was
        reported in, not the period it represents - so a 10-K's comparative years
        all share the filing's ``fy``. We therefore derive the year from the fact
        itself: the ``frame`` (e.g. ``CY2024``) when present, otherwise the ``end``
        date's year.
        """
        frame = entry.get("frame")
        if frame:
            match = _FRAME_YEAR_RE.search(frame)
            if match:
                return int(match.group(1))

        end = self._parse_iso_date(entry.get("end"))
        if end is not None:
            return end.year
        return None

    def _span_days(self, entry: Dict[str, Any]) -> Optional[int]:
        """Number of days the fact spans, or None for instant facts (no ``start``)."""
        start = self._parse_iso_date(entry.get("start"))
        end = self._parse_iso_date(entry.get("end"))
        if start is None or end is None:
            return None
        return (end - start).days

    def _is_full_year(self, entry: Dict[str, Any]) -> bool:
        """True for instant facts and for duration facts spanning ~one year.

        Instant facts (balance sheet - no ``start``) always belong to their period
        end year. Duration facts (income/cash flow) are kept only when they cover a
        full year, which excludes the quarterly sub-periods also present in 10-Ks.
        """
        span = self._span_days(entry)
        if span is None:
            return True  # instant fact
        return _FULL_YEAR_MIN_DAYS <= span <= _FULL_YEAR_MAX_DAYS

    def _is_quarter(self, entry: Dict[str, Any]) -> bool:
        """True for instant facts and for duration facts spanning ~one quarter."""
        span = self._span_days(entry)
        if span is None:
            return True  # instant fact
        return _QUARTER_MIN_DAYS <= span <= _QUARTER_MAX_DAYS

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

        # Collect all 10-K data points, keyed by the fiscal year each fact covers.
        annual_data: Dict[int, Dict[str, Any]] = {}
        # Track the "filed" date behind each stored value so a later (restated)
        # filing supersedes an earlier one, per (year, field).
        field_filed: Dict[int, Dict[str, str]] = {}

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

                # Reject quarterly sub-periods reported inside the 10-K.
                if not self._is_full_year(entry):
                    continue

                year = self._period_year(entry)
                if year is None:
                    continue

                # Initialize / refresh year-level metadata (latest filing wins).
                meta = annual_data.setdefault(year, {"fiscal_year": year})
                field_filed.setdefault(year, {})
                filed = entry.get("filed") or ""
                if filed >= meta.get("_meta_filed", ""):
                    meta["_meta_filed"] = filed
                    meta["filed_date"] = entry.get("filed")
                    meta["period_end"] = entry.get("end")
                    meta["form"] = form

                # Add the metric with simple name if mapped, otherwise use raw tag.
                simple_name = self.simple_mapping.get(tag, tag)

                # Most-recently-filed value wins for the same (year, field).
                if filed >= field_filed[year].get(simple_name, ""):
                    annual_data[year][simple_name] = entry.get("val")
                    field_filed[year][simple_name] = filed

        # Drop internal bookkeeping, sort and limit years.
        for meta in annual_data.values():
            meta.pop("_meta_filed", None)
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

        quarterly_data: Dict[str, Dict[str, Any]] = {}
        # Track the "filed" date behind each stored value per (period, field).
        field_filed: Dict[str, Dict[str, str]] = {}

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

                # Reject full-year (or other non-quarterly) spans inside the 10-Q.
                if not self._is_quarter(entry):
                    continue

                period_end = entry.get("end")
                if not period_end:
                    continue

                meta = quarterly_data.setdefault(period_end, {"period_end": period_end})
                field_filed.setdefault(period_end, {})
                filed = entry.get("filed") or ""
                if filed >= meta.get("_meta_filed", ""):
                    meta["_meta_filed"] = filed
                    meta["filed_date"] = entry.get("filed")
                    meta["fiscal_year"] = entry.get("fy")
                    meta["fiscal_period"] = entry.get("fp")
                    meta["form"] = form

                simple_name = self.simple_mapping.get(tag, tag)
                # Most-recently-filed value wins for the same (period, field).
                if filed >= field_filed[period_end].get(simple_name, ""):
                    quarterly_data[period_end][simple_name] = entry.get("val")
                    field_filed[period_end][simple_name] = filed

        # Drop internal bookkeeping, sort and limit.
        for meta in quarterly_data.values():
            meta.pop("_meta_filed", None)
        sorted_periods = sorted(quarterly_data.keys(), reverse=True)[:quarters_back]
        return {p: quarterly_data[p] for p in sorted_periods}

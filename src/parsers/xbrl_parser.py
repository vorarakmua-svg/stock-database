"""XBRL data parser for SEC EDGAR financial data."""

import logging
import re
from datetime import date, datetime
from typing import Any, Dict, Optional

from ..mappings.canonical import CANONICAL_FIELDS, SIGN_ABS

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
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract standardized annual (10-K) financials, keyed by fiscal year.

        Each period dict uses canonical keys (see ``mappings/canonical.py``) so the
        same concept is comparable across companies regardless of which XBRL tag a
        filer used. A ``_source_tags`` map records the tag each value came from.

        Args:
            facts: Raw company facts
            years_back: Number of fiscal years to keep (most recent first)

        Returns:
            Dictionary organized by fiscal year (string keys).
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        data = self._resolve_canonical(
            us_gaap,
            form_set={"10-K", "10-K/A"},
            valid_fn=self._is_full_year,
            period_key_fn=self._period_year,
            quarterly=False,
        )

        sorted_years = sorted(data.keys(), reverse=True)[:years_back]
        return {str(y): data[y] for y in sorted_years}

    def extract_quarterly_financials(
        self,
        facts: Dict[str, Any],
        quarters_back: int = 12,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract standardized quarterly (10-Q) financials, keyed by period-end date.

        Uses the same canonical resolution as :meth:`extract_annual_financials`.

        Args:
            facts: Raw company facts
            quarters_back: Number of quarters to keep (most recent first)

        Returns:
            Dictionary organized by period end date.
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        data = self._resolve_canonical(
            us_gaap,
            form_set={"10-Q", "10-Q/A"},
            valid_fn=self._is_quarter,
            period_key_fn=lambda e: e.get("end"),
            quarterly=True,
        )

        sorted_periods = sorted(data.keys(), reverse=True)[:quarters_back]
        return {p: data[p] for p in sorted_periods}

    def _resolve_canonical(
        self,
        us_gaap: Dict[str, Any],
        form_set: set,
        valid_fn,
        period_key_fn,
        quarterly: bool,
    ) -> Dict[Any, Dict[str, Any]]:
        """Resolve every canonical field for every period from raw us-gaap facts.

        For each canonical field, candidate tags are tried in priority order; the
        highest-priority tag with data for a period wins, and within a tag the
        most-recently-filed value wins (so restatements supersede originals).
        """
        data: Dict[Any, Dict[str, Any]] = {}
        # (period, canonical_key) -> (tag_priority, filed). Tracks the basis for the
        # currently stored value so a better candidate can replace it.
        best: Dict[Any, tuple] = {}
        # period -> filed date of the entry that set period-level metadata.
        meta_filed: Dict[Any, str] = {}

        for field in CANONICAL_FIELDS:
            unit_key = field.xbrl_unit
            for priority, tag in enumerate(field.tags):
                tag_data = us_gaap.get(tag)
                if not tag_data:
                    continue

                for entry in tag_data.get("units", {}).get(unit_key, []):
                    if entry.get("form", "") not in form_set:
                        continue
                    if not valid_fn(entry):
                        continue

                    period = period_key_fn(entry)
                    if period is None:
                        continue

                    filed = entry.get("filed") or ""
                    bkey = (period, field.key)
                    cur = best.get(bkey)
                    # Keep the existing value unless this entry is from a
                    # higher-priority tag, or the same tag filed at least as late.
                    if cur is not None and not (
                        priority < cur[0] or (priority == cur[0] and filed >= cur[1])
                    ):
                        continue
                    best[bkey] = (priority, filed)

                    value = entry.get("val")
                    if field.sign == SIGN_ABS and isinstance(value, (int, float)):
                        value = abs(value)

                    period_dict = data.setdefault(
                        period, self._init_period_meta(period, quarterly)
                    )
                    period_dict[field.key] = value
                    period_dict.setdefault("_source_tags", {})[field.key] = tag

                    # Period-level metadata follows the latest-filed contributing entry.
                    if filed >= meta_filed.get(period, ""):
                        meta_filed[period] = filed
                        period_dict["filed_date"] = entry.get("filed")
                        period_dict["period_end"] = entry.get("end")
                        period_dict["form"] = entry.get("form")
                        if quarterly:
                            period_dict["fiscal_year"] = entry.get("fy")
                            period_dict["fiscal_period"] = entry.get("fp")

        return data

    @staticmethod
    def _init_period_meta(period: Any, quarterly: bool) -> Dict[str, Any]:
        """Seed a period dict with its identifying metadata."""
        if quarterly:
            return {"period_end": period}
        return {"fiscal_year": period}

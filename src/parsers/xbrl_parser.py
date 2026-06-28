"""XBRL data parser for SEC EDGAR financial data."""

import logging
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from ..mappings.canonical import CANONICAL_FIELDS, DURATION, INSTANT, SIGN_ABS

# Duration bounds (in days) used to distinguish annual vs quarterly periods.
# A "full year" span is ~365 days; a quarter is ~91 days. Bounds are generous
# to absorb 52/53-week fiscal calendars.
_FULL_YEAR_MIN_DAYS = 350
_FULL_YEAR_MAX_DAYS = 380
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100

# Forms that carry quarterly and annual statement facts.
_QUARTERLY_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}

_FRAME_YEAR_RE = re.compile(r"CY(\d{4})")
_FRAME_QUARTER_RE = re.compile(r"^CY(\d{4})Q(\d)")


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
        """Determine the fiscal year a fact *covers*, from the period-**end** year.

        In SEC ``companyfacts`` each fact carries the ``fy`` of the *filing*, not the
        period it represents, so we must derive the year from the fact itself. We use
        the ``end`` date's year because it is consistent across income-statement
        (duration) and balance-sheet (instant) facts of the same fiscal year — both
        end on the fiscal-year-end date. The ``frame`` (e.g. ``CY2025``) is *not*
        used as the primary signal: SEC assigns frames by calendar year, so a
        non-December fiscal year (e.g. a retailer ending Jan 31) would split its
        income statement (``CY2025``) from its balance sheet into different buckets.
        ``frame`` is only a fallback when ``end`` is missing.
        """
        end = self._parse_iso_date(entry.get("end"))
        if end is not None:
            return end.year

        frame = entry.get("frame")
        if frame:
            match = _FRAME_YEAR_RE.search(frame)
            if match:
                return int(match.group(1))
        return None

    def _fiscal_year_from_end(self, end_iso: Optional[str]) -> Optional[int]:
        """Fiscal year a period belongs to, from its period-end date.

        A period's fiscal year is its period-end calendar year, except for a 52/53-week
        filer whose year-end lands in early January (month == 1, day <= 7): that belongs
        to the prior (December) fiscal year. January-end retailers (day 28-31) keep the
        end year. SEC's ``fy`` field is deliberately NOT used — filers mis-stamp it (e.g.
        HON tagged its FY2021 facts as fy=2020).
        """
        end = self._parse_iso_date(end_iso)
        if end is None:
            return None
        if end.month == 1 and end.day <= 7:
            return end.year - 1
        return end.year

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
        years_back: Optional[int] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract standardized annual (10-K) financials, keyed by fiscal year.

        Each period dict uses canonical keys (see ``mappings/canonical.py``) so the
        same concept is comparable across companies regardless of which XBRL tag a
        filer used. A ``_source_tags`` map records the tag each value came from.

        Args:
            facts: Raw company facts
            years_back: Number of fiscal years to keep (most recent first).
                ``None`` (default) keeps all available history.

        Returns:
            Dictionary organized by fiscal year (string keys).
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        def annual_fy(entry: Dict[str, Any]) -> Optional[int]:
            return self._fiscal_year_from_end(entry.get("end")) or self._period_year(entry)

        data = self._resolve_canonical(
            us_gaap,
            form_set={"10-K", "10-K/A"},
            valid_fn=self._is_full_year,
            period_key_fn=annual_fy,
            quarterly=False,
        )

        sorted_years = sorted(data.keys(), reverse=True)
        if years_back:
            sorted_years = sorted_years[:years_back]
        return {str(y): data[y] for y in sorted_years}

    def extract_quarterly_financials(
        self,
        facts: Dict[str, Any],
        quarters_back: Optional[int] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract standardized **discrete** quarterly financials, keyed by period-end.

        Income statements and cash-flow statements are reported cumulative-to-date in
        10-Qs (and full-year in the 10-K). We recover true 3-month figures by
        differencing the cumulative ladder anchored at the fiscal-year start
        (``discrete[k] = YTD[k] - YTD[k-1]``), which also yields Q4
        (annual - 9-month YTD). Balance-sheet (instant) facts are captured as
        per-quarter checkpoints. See :meth:`_collect_discrete_flows` /
        :meth:`_collect_instant_checkpoints`.

        Args:
            facts: Raw company facts
            quarters_back: Number of quarters to keep (most recent first).
                ``None`` (default) keeps all available history.

        Returns:
            Dictionary organized by period end date.
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap:
            return {}

        periods: Dict[str, Dict[str, Any]] = {}
        frames: Dict[str, set] = {}

        self._collect_instant_checkpoints(us_gaap, periods, frames)
        self._collect_discrete_flows(us_gaap, periods, frames)
        self._apply_quarter_calendar(periods, frames)

        sorted_ends = sorted(periods.keys(), reverse=True)
        if quarters_back:
            sorted_ends = sorted_ends[:quarters_back]
        return {e: periods[e] for e in sorted_ends}

    def _collect_instant_checkpoints(
        self, us_gaap: Dict[str, Any], periods: Dict[str, Dict[str, Any]],
        frames: Dict[str, set],
    ) -> None:
        """Balance-sheet (instant) values at every quarter-end (10-Q and 10-K)."""
        best: Dict[Tuple[str, str], Tuple[int, str]] = {}
        for field in CANONICAL_FIELDS:
            if field.kind != INSTANT:
                continue
            for priority, tag in enumerate(field.tags):
                for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in _QUARTERLY_FORMS:
                        continue
                    if entry.get("start"):  # instant facts have no start
                        continue
                    end = entry.get("end")
                    if not end:
                        continue
                    if entry.get("frame"):
                        frames.setdefault(end, set()).add(entry["frame"])
                    filed = entry.get("filed") or ""
                    bkey = (end, field.key)
                    cur = best.get(bkey)
                    if cur is not None and not (
                        priority < cur[0] or (priority == cur[0] and filed >= cur[1])
                    ):
                        continue
                    best[bkey] = (priority, filed)
                    pd_ = periods.setdefault(end, {"period_end": end})
                    pd_[field.key] = entry.get("val")
                    pd_.setdefault("_source_tags", {})[field.key] = tag

    def _collect_discrete_flows(
        self, us_gaap: Dict[str, Any], periods: Dict[str, Dict[str, Any]],
        frames: Dict[str, set],
    ) -> None:
        """Discrete quarterly flows via cumulative-ladder differencing.

        For each flow (duration) field, dedup facts per (start, end), then group by
        the fiscal-period ``start``. Within each cumulative ladder (a start group with
        >= 2 points) difference consecutive year-to-date values into 3-month figures,
        guarding that each increment spans ~one quarter so missing rungs don't produce
        multi-quarter blobs.
        """
        for field in CANONICAL_FIELDS:
            if field.kind != DURATION:
                continue
            # Dedup per (start, end): higher-priority tag, then most-recently-filed.
            chosen: Dict[Tuple[str, str], Tuple[int, str, Any]] = {}
            for priority, tag in enumerate(field.tags):
                for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in _QUARTERLY_FORMS:
                        continue
                    start, end = entry.get("start"), entry.get("end")
                    if not start or not end:
                        continue
                    span = self._span(start, end)
                    if span is None or span > _FULL_YEAR_MAX_DAYS:
                        continue  # ignore multi-year facts
                    if entry.get("frame"):
                        frames.setdefault(end, set()).add(entry["frame"])
                    filed = entry.get("filed") or ""
                    key = (start, end)
                    cur = chosen.get(key)
                    if cur is not None and not (
                        priority < cur[0] or (priority == cur[0] and filed >= cur[1])
                    ):
                        continue
                    chosen[key] = (priority, filed, entry.get("val"))

            # Group by fiscal-period start -> cumulative ladders.
            ladders: Dict[str, List[Tuple[str, Any]]] = defaultdict(list)
            for (start, end), (_p, _f, val) in chosen.items():
                ladders[start].append((end, val))

            for start, points in ladders.items():
                if len(points) < 2:
                    continue  # not a cumulative ladder (isolated 3-month fact)
                points.sort()
                # The fiscal year this ladder belongs to, from its latest end (date rule).
                ladder_fy = self._fiscal_year_from_end(points[-1][0])
                prev_end, prev_val = start, 0.0
                for end, val in points:
                    if not isinstance(val, (int, float)):
                        prev_end, prev_val = end, val
                        continue
                    inc_span = self._span(prev_end, end)
                    if inc_span is None or not (_QUARTER_MIN_DAYS <= inc_span <= _QUARTER_MAX_DAYS):
                        prev_end, prev_val = end, val  # gap: reset baseline
                        continue
                    discrete = val - (prev_val if isinstance(prev_val, (int, float)) else 0.0)
                    if field.sign == SIGN_ABS:
                        discrete = abs(discrete)
                    fq = self._fiscal_quarter_from_span(self._span(start, end))
                    pd_ = periods.setdefault(end, {"period_end": end})
                    if field.key not in pd_:
                        pd_[field.key] = discrete
                        pd_.setdefault("_source_tags", {})[field.key] = "derived:ytd_diff"
                    pd_.setdefault("fiscal_quarter", fq)
                    pd_.setdefault("fiscal_year", ladder_fy)
                    prev_end, prev_val = end, val

    def _apply_quarter_calendar(
        self, periods: Dict[str, Dict[str, Any]], frames: Dict[str, set],
    ) -> None:
        """Attach calendar_year/calendar_quarter (+frame) to each quarter-end."""
        for end, pd_ in periods.items():
            year, quarter, chosen = self._calendar_from_frames(frames.get(end, set()),
                                                               quarterly=True)
            if year is None:
                year = self._calendar_year_from_end(end)
            if quarter is None:
                quarter = self._calendar_quarter_from_end(end)
            pd_["calendar_year"] = year
            pd_["calendar_quarter"] = quarter
            if chosen:
                pd_["frame"] = chosen

    @staticmethod
    def _fiscal_quarter_from_span(cum_span: Optional[int]) -> Optional[int]:
        """Fiscal quarter (1-4) from a cumulative period's day span (~91/182/273/365)."""
        if cum_span is None:
            return None
        return max(1, min(4, round(cum_span / 91.31)))

    def _calendar_quarter_from_end(self, end: Optional[str]) -> Optional[int]:
        """Fallback calendar quarter from a period-end month (used when no frame)."""
        end_date = self._parse_iso_date(end)
        if end_date is None:
            return None
        return (end_date.month - 1) // 3 + 1

    def _span(self, start: Optional[str], end: Optional[str]) -> Optional[int]:
        """Day count between two ISO dates, or None."""
        s, e = self._parse_iso_date(start), self._parse_iso_date(end)
        if s is None or e is None:
            return None
        return (e - s).days

    def _assign_if_better(
        self,
        data: Dict[Any, Dict[str, Any]],
        best: Dict[Any, Tuple[int, str]],
        period: Any,
        seed: Dict[str, Any],
        field: Any,
        priority: int,
        tag: str,
        entry: Dict[str, Any],
    ) -> bool:
        """Store ``entry``'s value for ``field`` under ``period`` iff it beats the
        current best (higher-priority tag, or the same tag filed at least as late).
        Seeds a new period dict from ``seed``. Returns True when it won, so the caller
        can run any period-level metadata update."""
        filed = entry.get("filed") or ""
        bkey = (period, field.key)
        cur = best.get(bkey)
        if cur is not None and not (
            priority < cur[0] or (priority == cur[0] and filed >= cur[1])
        ):
            return False
        best[bkey] = (priority, filed)
        value = entry.get("val")
        if field.sign == SIGN_ABS and isinstance(value, (int, float)):
            value = abs(value)
        period_dict = data.setdefault(period, dict(seed))
        period_dict[field.key] = value
        period_dict.setdefault("_source_tags", {})[field.key] = tag
        return True

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
        # period -> set of SEC calendar `frame` strings seen across all of the
        # period's facts (the authoritative calendar mapping; see _apply_calendar).
        period_frames: Dict[Any, set] = {}

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

                    # Capture the SEC frame from every contributing fact (it may be
                    # present only on a comparative instance, not the one that wins).
                    frame = entry.get("frame")
                    if frame:
                        period_frames.setdefault(period, set()).add(frame)

                    seed = self._init_period_meta(period, quarterly)
                    if not self._assign_if_better(
                        data, best, period, seed, field, priority, tag, entry
                    ):
                        continue

                    # Period-level metadata follows the latest-filed contributing entry.
                    period_dict = data[period]
                    filed = entry.get("filed") or ""
                    if filed >= meta_filed.get(period, ""):
                        meta_filed[period] = filed
                        period_dict["filed_date"] = entry.get("filed")
                        period_dict["period_end"] = entry.get("end")
                        period_dict["form"] = entry.get("form")
                        period_dict["fiscal_period"] = entry.get("fp")
                        if quarterly:
                            period_dict["fiscal_year"] = entry.get("fy")

        self._apply_calendar(data, period_frames, quarterly)
        return data

    @staticmethod
    def _init_period_meta(period: Any, quarterly: bool) -> Dict[str, Any]:
        """Seed a period dict with its identifying metadata."""
        if quarterly:
            return {"period_end": period}
        return {"fiscal_year": period}

    def _apply_calendar(self, data: Dict[Any, Dict[str, Any]],
                        period_frames: Dict[Any, set], quarterly: bool) -> None:
        """Set SEC-authoritative ``calendar_year`` (+quarter, +frame) on each period.

        Uses the SEC ``frame`` when present (e.g. a June-fiscal-year-end maps to its
        calendar year, a January FYE to the prior calendar year), falling back to the
        period-end month rule for the rare period with no frame on any instance.
        """
        for period, period_dict in data.items():
            year, quarter, chosen = self._calendar_from_frames(
                period_frames.get(period, set()), quarterly
            )
            if year is None:
                year = self._calendar_year_from_end(period_dict.get("period_end"))
            period_dict["calendar_year"] = year
            if chosen:
                period_dict["frame"] = chosen
            if quarterly:
                period_dict["calendar_quarter"] = quarter

    @staticmethod
    def _parse_frame(frame: str):
        """Parse an SEC frame like ``CY2024`` / ``CY2024Q4I`` -> (year, quarter|None)."""
        match = re.match(r"^CY(\d{4})(?:Q(\d))?I?$", frame or "")
        if not match:
            return None, None
        year = int(match.group(1))
        quarter = int(match.group(2)) if match.group(2) else None
        return year, quarter

    def _calendar_from_frames(self, frames: set, quarterly: bool):
        """Choose the best frame for a period -> (year, quarter, frame_string).

        For annual periods prefer a full-year frame (``CY2024``); for quarterly prefer
        a frame carrying a quarter (``CY2024Q4I``). Returns (None, None, None) if no
        parseable frame is available.
        """
        parsed = []
        for frame in frames:
            year, quarter = self._parse_frame(frame)
            if year is not None:
                parsed.append((year, quarter, frame))
        if not parsed:
            return None, None, None
        if quarterly:
            with_q = [p for p in parsed if p[1] is not None]
            return (with_q or parsed)[0]
        full_year = [p for p in parsed if p[1] is None]
        return (full_year or parsed)[0]

    def _calendar_year_from_end(self, end: Optional[str]):
        """Fallback calendar-year rule (replicates SEC's convention).

        A period ending in Jun-Dec maps to its end year; Jan-May maps to the prior
        year (i.e. the calendar year holding most of the 12-month period).
        """
        end_date = self._parse_iso_date(end)
        if end_date is None:
            return None
        return end_date.year if end_date.month >= 6 else end_date.year - 1

"""XBRL data parser for SEC EDGAR financial data."""

import logging
import math
import re
import statistics
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..mappings.canonical import CANONICAL_FIELDS, DURATION, INSTANT, SIGN_ABS

# Duration bounds (in days) used to distinguish annual vs quarterly periods.
# A "full year" span is ~365 days; a quarter is ~91 days. Bounds are generous
# to absorb 52/53-week fiscal calendars.
_FULL_YEAR_MIN_DAYS = 350
_FULL_YEAR_MAX_DAYS = 380
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100

# Forms that carry quarterly and annual statement facts. A foreign issuer's annual
# report (20-F, 40-F) counts as annual; its 6-K interim reports aren't read.
_QUARTERLY_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
#: Forms of filers reporting as foreign private issuers.
_FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A", "6-K"}
_FRAME_YEAR_RE = re.compile(r"CY(\d{4})")
_FRAME_QUARTER_RE = re.compile(r"^CY(\d{4})Q(\d)")
#: Fields reported in dollars: a filer reporting in another currency resolves none.
_DOLLAR_FIELDS = frozenset(f.key for f in CANONICAL_FIELDS if f.xbrl_unit.startswith("USD"))


def _in_dollars(periods: Iterable[Dict[str, Any]]) -> bool:
    """Whether any period holds a dollar figure, not just share counts."""
    return any(_DOLLAR_FIELDS & period.keys() for period in periods)


def _reporting_currency(us_gaap: Dict[str, Any]) -> Optional[str]:
    """The currency most of the filer's monetary facts are in.

    A filer reporting in another currency also tags a US$ convenience
    translation of some figures (TSM: 4,624 TWD facts beside 1,010 USD ones),
    so a few USD facts don't make a dollar record.
    """
    counts: Dict[str, int] = defaultdict(int)
    for tag in us_gaap.values():
        for unit, entries in tag.get("units", {}).items():
            if re.fullmatch(r"[A-Z]{3}", unit):
                counts[unit] += len(entries)
    return max(counts, key=lambda unit: counts[unit]) if counts else None


def _foreign_currency(us_gaap: Dict[str, Any]) -> bool:
    """Whether the filer reports in a currency other than dollars."""
    return _reporting_currency(us_gaap) not in (None, "USD")


#: A later filing's figure for a year can be a tagging error of the figure the earlier
#: filings agree on: its negative, a zero, or it times 10^3 or more (STX's FY2024 10-K put
#: FY2022's 2.77 dividend a share at 2,770,000; BUG-061). x10 and x100 are left alone, as
#: a 10:1 split restates by exactly that.
_MIN_SCALE_ERROR = 3
#: Years on each side whose figures decide between the later filing and the earlier ones.
_NEIGHBOUR_YEARS = 2
#: Figures within this fraction of each other agree (filings round differently).
_AGREE = 0.005
#: A year's cash-flow sections, which its net change in cash checks.
_CASH_SECTIONS = ("operating_cash_flow", "investing_cash_flow", "financing_cash_flow")
#: Totals no company reports as zero (BAC's 2011 10-K put FY2008's total assets at 0).
#: A later zero for a line item can be a restatement: T-Mobile's history replaced
#: MetroPCS's after the reverse merger, and Prologis's FY2018 lease liability was off the
#: balance sheet before ASC 842.
_NEVER_ZERO = frozenset({"total_assets", "current_assets", "total_liabilities",
                         "current_liabilities", "total_equity"})


def _agree(a: float, b: float) -> bool:
    return abs(a - b) <= _AGREE * max(abs(a), abs(b))


def _tagging_error(earlier: float, latest: float, key: str) -> Optional[str]:
    """What a later filing's figure for *key* is of the one the earlier filings agree
    on, when it can be a tagging error: "sign flip", "zero" (of a total) or
    "scale x10^k"; else None."""
    if earlier == 0 or _agree(earlier, latest):
        return None
    if latest == 0:
        return "zero" if key in _NEVER_ZERO else None
    if _agree(earlier, -latest):
        return "sign flip"
    ratio = latest / earlier
    if ratio > 0:
        k = round(math.log10(ratio))
        if abs(k) >= _MIN_SCALE_ERROR and _agree(ratio, 10.0 ** k):
            return f"scale x10^{k}"
    return None


def _years_side_with_earlier(kind: str, earlier: float, latest: float,
                             neighbours: List[float]) -> bool:
    """Whether the years around a conflicting figure side with the earlier filings.

    For a sign flip, every one of them has the earlier figure's sign; for a zero or a
    scale, the earlier figure is within a factor of ten of their median and the later
    one isn't. Fewer than two years decide nothing.
    """
    if len(neighbours) < 2:
        return False
    if kind == "sign flip":
        return all(v != 0 and (v > 0) == (earlier > 0) for v in neighbours)
    if kind == "zero" and 0 in neighbours:
        return False  # the figure is zero in other years too
    logs = [math.log10(abs(v)) for v in neighbours if v != 0]
    if len(logs) < 2:
        return False
    mid = statistics.median(logs)
    return abs(math.log10(abs(earlier)) - mid) <= 1 and (
        latest == 0 or abs(math.log10(abs(latest)) - mid) > 1)


def _cash_adds_up(sections: List[float], period: Dict[str, Any]) -> bool:
    """Whether a year's cash-flow sections sum to its net change in cash, within 1% of
    the largest, with or without the FX effect and discontinued operations (filers
    differ on which the sections include)."""
    fx = period.get("fx_effect_on_cash") or 0.0
    discontinued = period.get("cash_from_discontinued_operations") or 0.0
    scale = max(abs(s) for s in sections) or 1.0
    return any(abs(sum(sections) + extra - period["net_change_in_cash"]) <= 0.01 * scale
               for extra in (0.0, fx, discontinued, fx + discontinued))


def explain_no_annual_financials(facts: Optional[Dict[str, Any]],
                                 first_filing: Optional[str] = None) -> str:
    """Why a filer's SEC data yields no annual financials, for the run's warnings.

    An empty record otherwise reads like a short history: XOM's new holding
    company had filed only 10-Qs, and TSM reports in IFRS. ``first_filing`` is
    the date its filings are listed from, when that is known.
    """
    taxonomies = (facts or {}).get("facts", {})

    def forms(taxonomy: str) -> List[str]:
        return sorted({entry["form"] for tag in taxonomies.get(taxonomy, {}).values()
                       for unit in tag.get("units", {}).values() for entry in unit if entry.get("form")})

    if not taxonomies.get("us-gaap"):
        if taxonomies.get("ifrs-full"):
            return f"it reports in IFRS ({', '.join(forms('ifrs-full'))}), which isn't mapped"
        return "SEC has no XBRL financial statements for it"
    us_gaap_forms = set(forms("us-gaap"))
    if not us_gaap_forms & _ANNUAL_FORMS:
        report = "20-F" if us_gaap_forms & _FOREIGN_FORMS else "10-K"
        since = f" (its filings listed from {first_filing})" if first_filing else ""
        return f"it has filed no annual report ({report}) yet{since}"
    if _foreign_currency(taxonomies["us-gaap"]):
        return (f"it reports in {_reporting_currency(taxonomies['us-gaap'])} ({', '.join(sorted(us_gaap_forms))}), "
                "which isn't converted to dollars")
    return "its annual-report facts give no full fiscal year of a mapped concept"


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

    @staticmethod
    def _matches_kind(field: Any, entry: Dict[str, Any]) -> bool:
        """A flow (duration) field needs a fact that covers a period.

        ``_is_full_year`` accepts instant facts for the balance sheet, but under a
        duration tag an instant is a single event, not the year's value: BAC tags
        each quarterly dividend declaration that way, and the last one (0.24)
        outranked the full-year 0.92 filed under the fallback tag.
        """
        return field.kind != DURATION or bool(entry.get("start"))

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
        filer used. A ``_source_tags`` map records the tag each value came from, and
        a ``_repairs`` list any later filing's tagging error put right (see
        ``_restore_agreed_figures``).

        Args:
            facts: Raw company facts
            years_back: Number of fiscal years to keep (most recent first).
                ``None`` (default) keeps all available history.

        Returns:
            Dictionary organized by fiscal year (string keys).
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap or _foreign_currency(us_gaap):
            return {}

        data = self._annual_periods(us_gaap)
        if not _in_dollars(data.values()):
            return {}  # share counts alone aren't a record; the run explains the gap

        sorted_years = sorted(data.keys(), reverse=True)
        if years_back:
            sorted_years = sorted_years[:years_back]
        return {str(y): data[y] for y in sorted_years}

    def extract_annual_vintages(
        self,
        facts: Dict[str, Any],
        years_back: Optional[int] = None,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Every filing's view of each annual period: ``{fiscal_year: {accn: period}}``.

        Unlike :meth:`extract_annual_financials` (which collapses to the latest-filed
        value per period), this keeps one period dict per *filing* (accession), so a
        restated year retains both its original and restated vintages. Reads the
        already-fetched ``companyfacts`` (every historical instance carries its own
        ``accn``/``filed``); no extra network. Annual reports (10-K, 20-F, 40-F) only.
        """
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        if not us_gaap or _foreign_currency(us_gaap):
            return {}

        data: Dict[Any, Dict[str, Any]] = {}
        best: Dict[Any, Tuple[int, str]] = {}
        period_frames: Dict[Any, set] = {}

        for field in CANONICAL_FIELDS:
            for priority, tag in enumerate(field.tags):
                tag_data = us_gaap.get(tag)
                if not tag_data:
                    continue
                for entry in tag_data.get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in _ANNUAL_FORMS:
                        continue
                    if not self._matches_kind(field, entry) or not self._is_full_year(entry):
                        continue
                    fy = self._fiscal_year_from_end(entry.get("end")) or self._period_year(entry)
                    accn = entry.get("accn")
                    if fy is None or not accn:
                        continue
                    period = (fy, accn)
                    frame = entry.get("frame")
                    if frame:
                        period_frames.setdefault(period, set()).add(frame)
                    seed = {
                        "fiscal_year": fy, "accn": accn,
                        "filed_date": entry.get("filed"), "period_end": entry.get("end"),
                        "form": entry.get("form"),
                    }
                    self._assign_if_better(data, best, period, seed, field,
                                           priority, tag, entry)

        self._apply_calendar(data, period_frames, quarterly=False)
        if not _in_dollars(data.values()):
            return {}

        nested: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for (fy, accn), period_dict in data.items():
            nested.setdefault(str(fy), {})[accn] = period_dict
        if years_back:
            keep = sorted(nested.keys(), reverse=True)[:years_back]
            nested = {y: nested[y] for y in keep}
        return nested

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
        if not us_gaap or _foreign_currency(us_gaap):
            return {}

        periods: Dict[str, Dict[str, Any]] = {}
        frames: Dict[str, set] = {}
        # A year-end figure the annual record puts back (see _restore_agreed_figures) is
        # read the same here: {(field, period end): (the later filing's figure, the one kept)}.
        restored = {(r["field"], r["period_end"]): (r["dropped"], r["kept"])
                    for period in self._annual_periods(us_gaap).values() for r in period.get("_repairs", [])}

        self._collect_instant_checkpoints(us_gaap, periods, frames, restored)
        self._collect_discrete_flows(us_gaap, periods, frames, restored)
        self._apply_quarter_calendar(periods, frames)

        sorted_ends = sorted(periods.keys(), reverse=True)
        if quarters_back:
            sorted_ends = sorted_ends[:quarters_back]
        return {e: periods[e] for e in sorted_ends}

    def _collect_instant_checkpoints(
        self, us_gaap: Dict[str, Any], periods: Dict[str, Dict[str, Any]],
        frames: Dict[str, set], restored: Dict[Tuple[str, str], Tuple[float, float]],
    ) -> None:
        """Balance-sheet (instant) values at every quarter-end (10-Q and 10-K), with the
        year-end figures the annual record puts back (*restored*)."""
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
        for (key, end), (dropped, kept) in restored.items():
            value = periods.get(end, {}).get(key)
            if isinstance(value, (int, float)) and _agree(value, dropped):
                periods[end][key] = kept

    def _collect_discrete_flows(
        self, us_gaap: Dict[str, Any], periods: Dict[str, Dict[str, Any]],
        frames: Dict[str, set], restored: Dict[Tuple[str, str], Tuple[float, float]],
    ) -> None:
        """Discrete quarterly flows via cumulative-ladder differencing.

        For each flow (duration) field, dedup facts per (start, end), then group by
        the fiscal-period ``start``. Within each cumulative ladder (a start group with
        >= 2 points) difference consecutive year-to-date values into 3-month figures,
        guarding that each increment spans ~one quarter so missing rungs don't produce
        multi-quarter blobs. A full-year rung reads the figure the annual record puts
        back (*restored*), so the fourth quarter is the restored year less nine months.
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
            for (start, end), (priority, filed, val) in list(chosen.items()):
                fix = restored.get((field.key, end))
                span = self._span(start, end)
                if fix is None or not isinstance(val, (int, float)) or span is None or span < _FULL_YEAR_MIN_DAYS:
                    continue
                dropped, kept = fix  # magnitudes for a SIGN_ABS field; the rung keeps its sign
                if field.sign == SIGN_ABS and _agree(abs(val), dropped):
                    chosen[(start, end)] = (priority, filed, math.copysign(kept, val))
                elif field.sign != SIGN_ABS and _agree(val, dropped):
                    chosen[(start, end)] = (priority, filed, kept)

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
                    if not self._matches_kind(field, entry) or not valid_fn(entry):
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

    def _annual_fy(self, entry: Dict[str, Any]) -> Optional[int]:
        """The fiscal year an annual report's fact covers (see ``_fiscal_year_from_end``)."""
        return self._fiscal_year_from_end(entry.get("end")) or self._period_year(entry)

    def _annual_periods(self, us_gaap: Dict[str, Any]) -> Dict[Any, Dict[str, Any]]:
        """Every fiscal year's figures from the annual reports, keyed by fiscal year,
        with a later filing's tagging errors put right."""
        data = self._resolve_canonical(
            us_gaap,
            form_set=_ANNUAL_FORMS,
            valid_fn=self._is_full_year,
            period_key_fn=self._annual_fy,
            quarterly=False,
        )
        self._restore_agreed_figures(us_gaap, data, self._annual_fy)
        return data

    def _annual_filings(self, us_gaap: Dict[str, Any], field: Any, tag: str,
                        period_key_fn) -> Dict[Any, List[Tuple[str, float, str]]]:
        """Each annual report's figure for *field* under *tag*, as ``_resolve_canonical``
        reads them: ``{period: [(filed, value, end), ...]}`` in filing order."""
        filings: Dict[Any, List[Tuple[str, float, str]]] = defaultdict(list)
        for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
            value = entry.get("val")
            if entry.get("form", "") not in _ANNUAL_FORMS or not isinstance(value, (int, float)):
                continue
            if not self._matches_kind(field, entry) or not self._is_full_year(entry):
                continue
            period = period_key_fn(entry)
            if period is not None:
                filings[period].append((entry.get("filed") or "",
                                        float(abs(value) if field.sign == SIGN_ABS else value),
                                        entry.get("end") or ""))
        for values in filings.values():
            values.sort(key=lambda filing: filing[0])
        return filings

    def _restore_agreed_figures(self, us_gaap: Dict[str, Any],
                                data: Dict[Any, Dict[str, Any]], period_key_fn) -> None:
        """Put back the figure the earlier filings agree on where a later filing's
        comparative reads as a tagging error of it (see ``_tagging_error``), and record
        each in the period's ``_repairs``.

        The latest filing wins otherwise, as a restatement should. A year's own net
        change in cash decides for its cash-flow sections (AMD's 2024 10-K flipped
        FY2021's investing and financing cash flows, and the year no longer added up);
        the years around it decide the rest, so a later filing that corrects an earlier
        error keeps its figure (BRK-B's FY2010 share count, LLY's FY2008 D&A).
        """
        conflicts: List[Tuple[Any, str, str, float, float, str, str]] = []
        for field in CANONICAL_FIELDS:
            years_by_tag: Dict[str, List[Any]] = defaultdict(list)
            for fy, period in data.items():
                if isinstance(period.get(field.key), (int, float)):
                    years_by_tag[period["_source_tags"][field.key]].append(fy)
            for tag, years in years_by_tag.items():
                filings = self._annual_filings(us_gaap, field, tag, period_key_fn)
                for fy in years:
                    history = filings.get(fy, [])
                    latest = float(data[fy][field.key])
                    filed, _, end = history[-1] if history else ("", 0.0, "")
                    earlier = [value for when, value, _ in history if when < filed]
                    if not earlier or not all(_agree(value, latest) for when, value, _ in history
                                              if when == filed):
                        continue
                    if not all(_agree(value, earlier[-1]) for value in earlier):
                        continue  # the earlier filings disagree among themselves
                    kind = _tagging_error(earlier[-1], latest, field.key)
                    if kind:
                        conflicts.append((fy, field.key, kind, earlier[-1], latest, filed, end))

        agreed_cash: Dict[Any, Dict[str, float]] = defaultdict(dict)
        for fy, key, _, earlier_value, *_ in conflicts:
            if key in _CASH_SECTIONS:
                agreed_cash[fy][key] = earlier_value
        restored = []
        for conflict in conflicts:
            fy, key, kind, earlier_value, latest, *_ = conflict
            period = data[fy]
            sections = [period.get(k) for k in _CASH_SECTIONS]
            if key in _CASH_SECTIONS and all(isinstance(v, (int, float))
                                             for v in sections + [period.get("net_change_in_cash")]):
                agreed = [agreed_cash[fy].get(k, v) for k, v in zip(_CASH_SECTIONS, sections)]
                if not _cash_adds_up(sections, period) and _cash_adds_up(agreed, period):
                    restored.append(conflict)
                continue
            neighbours = [data[y][key] for y in range(fy - _NEIGHBOUR_YEARS, fy + _NEIGHBOUR_YEARS + 1)
                          if y != fy and isinstance(data.get(y, {}).get(key), (int, float))]
            if _years_side_with_earlier(kind, earlier_value, latest, neighbours):
                restored.append(conflict)
        for fy, key, kind, earlier_value, latest, filed, end in restored:
            data[fy][key] = earlier_value
            data[fy].setdefault("_repairs", []).append({"field": key, "kind": kind, "kept": earlier_value,
                                                        "dropped": latest, "filed": filed, "period_end": end})

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

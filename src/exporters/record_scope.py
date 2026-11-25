"""What a collection run speaks for in the SEC record it stores: one rule for every output.

A run that parsed the filer's annual record from a complete read of its filings replaces the record: a period or an
unmapped tag its parse no longer gives goes from the SQLite database (BUG-344), the JSON file and
financial_history.csv alike (BUG-380). A run that read part of the filings, gave no annual record, or limited its
years removes nothing it can't speak for.
"""

import json
from typing import Any, Callable, Dict, Hashable, Iterable, List, Optional, Set, Tuple

from ..models.stock_data import StockData


def incomplete(stock: StockData) -> Optional[str]:
    """What a run's SEC read lacked, as ``collection_runs.incomplete`` keeps it (JSON): ``report``, the latest report
    its submissions listed whose facts its companyfacts lacked, the SEC's lag (BUG-251), unless the run read it and
    every earlier report they lacked from the filings' own XBRL (``recovered``, Q17); ``predecessor``, why its
    predecessor's filings couldn't be read (BUG-279); ``submissions``, why its submissions request failed, so that it
    couldn't look for such a report (BUG-282). None for a complete run. ``stock-data refresh``'s SEC step collects a
    company again whose latest collection with the SEC lacked any."""
    lacked: Dict[str, Any] = {}
    if stock.missing_report and not stock.missing_report.get("recovered"):
        lacked["report"] = stock.missing_report
    if stock.predecessor_error:
        lacked["predecessor"] = stock.predecessor_error
    if stock.submissions_error:
        lacked["submissions"] = stock.submissions_error
    return json.dumps(lacked) if lacked else None


def complete_sec_read(stock: StockData) -> bool:
    """Whether a run read the whole of its filer's SEC record: nothing ``incomplete`` names (the latest report its
    companyfacts lacked, its predecessor's filings, its submissions) and no SEC error (a request or a step of the read
    that failed). Only such a run removes the stored periods and unmapped tags its parse no longer gives (BUG-344)."""
    return incomplete(stock) is None and not any(str(error).startswith("SEC EDGAR") for error in stock.errors)


def speaks_for_record(stock: StockData) -> bool:
    """Whether *stock*'s run replaces the ticker's stored SEC record: it parsed the filer's annual record (a filer
    with no 10-K yet gives a few quarters only, which can't speak for what a predecessor filed) from a complete read
    of its filings (``complete_sec_read``)."""
    return bool(stock.financials_annual) and complete_sec_read(stock)


def _year(key: Any) -> Any:
    """A fiscal year as an int, whichever way an output keeps it (2025, "2025"); any other key as it is."""
    text = str(key)
    return int(text) if text.isdigit() else key


def _years(stock: StockData) -> Set[Any]:
    return {_year(fy) for fy in stock.financials_annual if str(fy).isdigit()}


#: Each part of the SEC record a run replaces: the keys the run gives, how a stored key compares with them, the
#: building step whose failure (``StockData.unbuilt``) leaves the part as stored, and whether a ``years_back`` run
#: speaks for it from its earliest key on only (the unmapped facts, each tag's latest, are all the run's whatever the
#: limit).
RECORD_PARTS: Dict[str, Tuple[Callable[[StockData], Set[Any]], Callable[[Any], Any], Optional[str], bool]] = {
    "financials_annual": (_years, _year, None, True),
    "metrics_annual": (_years, _year, "metrics", True),
    "financials_quarterly": (lambda s: {str(k) for k in s.financials_quarterly or {}}, str, "quarterly", True),
    "financials_ttm": (lambda s: {str(k) for k in s.financials_ttm or {}}, str, "quarterly", True),
    "unmapped_facts": (lambda s: {fact.get("tag") for fact in s.unmapped_facts or []}, lambda k: k, None, False),
}


def absent(stock: StockData, part: str, stored: Iterable[Hashable]) -> List[Any]:
    """The keys in *stored* (an output's keys of *stock*'s *part*, ``RECORD_PARTS``) that *stock*'s run no longer
    gives and that go, as they are given: none unless the run speaks for the record (``speaks_for_record``), none
    of a part whose building step raised (the run's part of it isn't its record), and with a ``years_back`` none
    before the earliest key the run gives (none when it gives none) (BUG-344, BUG-380)."""
    if not speaks_for_record(stock):
        return []
    given_of, key_of, step, limited = RECORD_PARTS[part]
    if step in stock.unbuilt:
        return []
    given = given_of(stock)
    gone = [key for key in stored if key_of(key) not in given]
    if limited and stock.years_back:
        if not given:
            return []
        floor = min(given)
        gone = [key for key in gone if key_of(key) >= floor]
    return gone

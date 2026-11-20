"""13F (Milestone 3): a manager's quarterly holdings report, parsed from the filing's full submission text
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

The text (``{accession}.txt``) carries every document of the filing under its ``<TYPE>``: the 13F-HR or 13F-HR/A
cover (an ``edgarSubmission``) and the INFORMATION TABLE, each XML between ``<XML>`` and ``</XML>``. Tags are matched
on their local names, as filers' namespaces and prefixes vary (a default namespace, ``ns1:``, ``n1:``). Values are
kept as filed and in dollars: filings made before 2023-01-03 state them in thousands (the SEC's rule goes by the
filing date, not the period), unless the holdings' prices say otherwise (``_multiplier``). The cover's entry and
value totals are kept beside the parsed count and sum, so a filing that disagrees beyond rounding can be flagged and
reported with its numbers. A text without a cover or an information table raises ``ThirteenFError`` for the caller
to skip and report.
"""
from __future__ import annotations

import re
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

#: The first filing day whose values are in dollars; earlier filings state them in thousands.
DOLLARS_FROM = "2023-01-03"
#: The share prices a portfolio's median can take, in dollars. A median outside $1 to $100,000 means the wrong
#: units: Baupost and Duquesne still state thousands on filings made after the SEC's switch to dollars.
PRICE_FLOOR = 1
PRICE_CEILING = 100_000
_DOCUMENT = re.compile(rb"<DOCUMENT>(.*?)</DOCUMENT>", re.S)
_TYPE = re.compile(rb"<TYPE>([^\r\n<]*)")
_XML = re.compile(rb"<XML>(.*?)</XML>", re.S)
_FILED = re.compile(rb"FILED AS OF DATE:\s*(\d{4})(\d{2})(\d{2})")
_PERIOD = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
_TRUE = {"1", "true"}
_FALSE = {"0", "false"}


class ThirteenFError(ValueError):
    """The text isn't a readable 13F holdings report."""


@dataclass
class Holding:
    """One entry of the information table, in the filing's order (*row*): its *value* as filed, *value_usd* in
    dollars, and its shares or principal amount (*share_type* 'SH' or 'PRN')."""

    row: int
    issuer: Optional[str] = None
    title_of_class: Optional[str] = None
    cusip: Optional[str] = None
    value: Optional[Decimal] = None
    value_usd: Optional[float] = None
    shares: Optional[float] = None
    share_type: Optional[str] = None
    put_call: Optional[str] = None
    discretion: Optional[str] = None


@dataclass
class ThirteenF:
    """A 13F-HR or 13F-HR/A: its cover, with the totals it states in the units as filed (*multiplier*, the units
    used, turns them into dollars), and its information table's entries."""

    accession: str
    form: Optional[str] = None
    filed: Optional[str] = None
    period: Optional[str] = None
    report_type: Optional[str] = None
    is_amendment: bool = False
    amendment_type: Optional[str] = None
    entry_total: Optional[int] = None
    value_total: Optional[Decimal] = None
    confidential_omitted: Optional[bool] = None
    multiplier: int = 1
    holdings: List[Holding] = field(default_factory=list)

    @property
    def rows(self) -> int:
        """The entries parsed."""
        return len(self.holdings)

    @property
    def rows_value(self) -> Decimal:
        """The parsed entries' values summed, as filed."""
        return sum((h.value for h in self.holdings if h.value is not None), Decimal(0))

    @property
    def checksum_ok(self) -> bool:
        """The cover's totals agree with the entries, in the units as filed: as many entries as it states, and a
        value total within rounding of their sum. Each entry's value is rounded to the unit, and so is the total, so
        the two can differ by up to half a unit per entry plus half a unit."""
        if self.entry_total is None or self.value_total is None or self.entry_total != self.rows:
            return False
        return abs(self.rows_value - self.value_total) <= Decimal(self.entry_total + 1) / 2

    def dollars(self, value: Optional[Decimal]) -> Optional[float]:
        """A value as filed, in dollars."""
        return None if value is None else float(value * self.multiplier)


def _text(node: Optional[ET.Element], path: str) -> Optional[str]:
    """A field's text, its runs of whitespace (a name broken over lines) made single spaces."""
    found = node.find(path) if node is not None else None
    if found is None or found.text is None:
        return None
    return " ".join(found.text.split()) or None


def _decimal(text: Optional[str]) -> Optional[Decimal]:
    if text is None:
        return None
    try:
        number = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _number(text: Optional[str]) -> Optional[float]:
    number = _decimal(text)
    return None if number is None else float(number)


def _flag(text: Optional[str]) -> Optional[bool]:
    if text is None:
        return None
    lowered = text.lower()
    return True if lowered in _TRUE else False if lowered in _FALSE else None


def _period(text: Optional[str]) -> Optional[str]:
    """The cover's MM-DD-YYYY as an ISO date."""
    match = _PERIOD.fullmatch(text or "")
    return f"{match.group(3)}-{match.group(1)}-{match.group(2)}" if match else None


def documents(text: bytes) -> List[Tuple[str, Optional[bytes]]]:
    """The filing's documents in its order: each one's TYPE and its XML (None for a document without one)."""
    found = []
    for body in _DOCUMENT.findall(text):
        kind = _TYPE.search(body)
        xml = _XML.search(body)
        found.append((kind.group(1).decode("ascii", "replace").strip() if kind else "", xml.group(1) if xml else None))
    return found


def _root(xml: bytes, accession: str, what: str, tag: str) -> ET.Element:
    """A document's XML root, every tag reduced to its local name."""
    try:
        root = ET.fromstring(xml.strip())
    except ET.ParseError as exc:
        raise ThirteenFError(f"{accession}: the {what} isn't well-formed XML ({exc})") from exc
    for node in root.iter():
        node.tag = node.tag.rpartition("}")[2]
    if root.tag != tag:
        raise ThirteenFError(f"{accession}: the {what} isn't an {tag} (<{root.tag}>)")
    return root


def _holding(node: ET.Element, row: int) -> Holding:
    cusip = _text(node, "cusip")
    return Holding(
        row=row,
        issuer=_text(node, "nameOfIssuer"),
        title_of_class=_text(node, "titleOfClass"),
        cusip=cusip.upper() if cusip else None,
        value=_decimal(_text(node, "value")),
        shares=_number(_text(node, "shrsOrPrnAmt/sshPrnamt")),
        share_type=_text(node, "shrsOrPrnAmt/sshPrnamtType"),
        put_call=_text(node, "putCall"),
        discretion=_text(node, "investmentDiscretion"),
    )


def _possible(price: Decimal) -> bool:
    return PRICE_FLOOR <= price <= PRICE_CEILING


def _multiplier(holdings: List[Holding], by_date: int) -> int:
    """The units the holdings' prices imply. The filing date's rule (*by_date*) holds unless the median price of the
    share rows it gives is impossible and 1,000 times more or less is possible: under $1 a share, the values are in
    thousands; over $100,000, in dollars. Without a share row (with shares) the date's rule holds."""
    prices = [h.value * by_date / Decimal(h.shares) for h in holdings
              if h.share_type == "SH" and h.value is not None and h.shares is not None and h.shares > 0]
    if not prices:
        return by_date
    median = statistics.median(prices)
    if median < PRICE_FLOOR and _possible(median * 1000):
        return 1000
    if median > PRICE_CEILING and _possible(median / 1000):
        return 1
    return by_date


def parse_13f(text: bytes, accession: str) -> ThirteenF:
    """Parse one 13F-HR or 13F-HR/A from its full submission text (``{accession}.txt``)."""
    docs = documents(text)
    cover = next((xml for kind, xml in docs if kind.upper().startswith("13F") and xml is not None), None)
    table = next((xml for kind, xml in docs if kind.upper() == "INFORMATION TABLE" and xml is not None), None)
    if cover is None:
        raise ThirteenFError(f"{accession}: no 13F cover document")
    root = _root(cover, accession, "cover", "edgarSubmission")
    if table is None:
        raise ThirteenFError(f"{accession}: no INFORMATION TABLE document")
    entries = _root(table, accession, "information table", "informationTable")
    filed = _FILED.search(text)
    if filed is None:
        raise ThirteenFError(f"{accession}: no filing date (FILED AS OF DATE), so the values' units are unknown")
    day = b"-".join(filed.groups()).decode("ascii")
    holdings = [_holding(node, row) for row, node in enumerate(entries.findall("infoTable"))]
    multiplier = _multiplier(holdings, 1000 if day < DOLLARS_FROM else 1)
    for holding in holdings:
        holding.value_usd = None if holding.value is None else float(holding.value * multiplier)
    entry_total = _decimal(_text(root, "formData/summaryPage/tableEntryTotal"))
    return ThirteenF(
        accession=accession,
        form=_text(root, "headerData/submissionType"),
        filed=day,
        period=_period(_text(root, "formData/coverPage/reportCalendarOrQuarter")
                       or _text(root, "headerData/filerInfo/periodOfReport")),
        report_type=_text(root, "formData/coverPage/reportType"),
        is_amendment=_flag(_text(root, "formData/coverPage/isAmendment")) is True,
        amendment_type=_text(root, "formData/coverPage/amendmentInfo/amendmentType"),
        entry_total=None if entry_total is None else int(entry_total),
        value_total=_decimal(_text(root, "formData/summaryPage/tableValueTotal")),
        confidential_omitted=_flag(_text(root, "formData/summaryPage/isConfidentialOmitted")),
        multiplier=multiplier,
        holdings=holdings,
    )

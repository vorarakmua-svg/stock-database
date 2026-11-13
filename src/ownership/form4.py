"""Form 4 (Milestone 3): an insider's ownership filing, parsed from the SEC's ``ownershipDocument`` XML
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

Only what the filing states is kept. A field given as a footnote alone (an RSU's price) is None, with the
footnote's id kept, never guessed. Holding rows that record no transaction are left out. The documents come
from SEC EDGAR; one that isn't a readable ownership document raises ``Form4Error`` for the caller to skip and
report.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional

#: The schema's booleans: 1/0 in most filers' documents, true/false in others' (AAPL).
_TRUE = {"1", "true"}
_FALSE = {"0", "false"}


class Form4Error(ValueError):
    """The document isn't a readable ownership document."""


@dataclass
class Owner:
    """A reporting owner and their relationship to the issuer."""

    cik: Optional[str]
    name: Optional[str]
    is_director: bool = False
    is_officer: bool = False
    officer_title: Optional[str] = None
    is_ten_percent: bool = False
    is_other: bool = False
    other_text: Optional[str] = None


@dataclass
class Trade:
    """One transaction row of the non-derivative or the derivative table, in the filing's order (*seq*)."""

    table: str
    seq: int
    security_title: Optional[str] = None
    date: Optional[str] = None
    code: Optional[str] = None
    shares: Optional[float] = None
    price: Optional[float] = None
    price_footnotes: List[str] = field(default_factory=list)
    acquired_disposed: Optional[str] = None
    owned_after: Optional[float] = None
    direct_indirect: Optional[str] = None
    nature_of_ownership: Optional[str] = None
    exercise_price: Optional[float] = None
    underlying_title: Optional[str] = None
    underlying_shares: Optional[float] = None


@dataclass
class Form4:
    """A Form 4 or 4/A: its issuer, owners and transactions. An amendment names its original's filing day."""

    accession: str
    form: Optional[str] = None
    period_of_report: Optional[str] = None
    original_filed: Optional[str] = None
    issuer_cik: Optional[str] = None
    issuer_ticker: Optional[str] = None
    aff10b5one: Optional[bool] = None
    owners: List[Owner] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)


def _text(node: Optional[ET.Element], path: str) -> Optional[str]:
    found = node.find(path) if node is not None else None
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _value(node: ET.Element, path: str) -> Optional[str]:
    """A field's value: the schema wraps it as ``<field><value>...</value><footnoteId .../></field>``."""
    return _text(node, f"{path}/value")


def _number(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _flag(text: Optional[str]) -> Optional[bool]:
    if text is None:
        return None
    lowered = text.lower()
    return True if lowered in _TRUE else False if lowered in _FALSE else None


def _day(text: Optional[str]) -> Optional[str]:
    return text[:10] if text else None


def _footnotes(node: ET.Element, path: str) -> List[str]:
    found = node.find(path)
    if found is None:
        return []
    return [ref.get("id") or "" for ref in found.findall("footnoteId")]


def _owner(node: ET.Element) -> Owner:
    relation = node.find("reportingOwnerRelationship")
    return Owner(
        cik=_text(node, "reportingOwnerId/rptOwnerCik"),
        name=_text(node, "reportingOwnerId/rptOwnerName"),
        is_director=bool(_flag(_text(relation, "isDirector"))),
        is_officer=bool(_flag(_text(relation, "isOfficer"))),
        officer_title=_text(relation, "officerTitle"),
        is_ten_percent=bool(_flag(_text(relation, "isTenPercentOwner"))),
        is_other=bool(_flag(_text(relation, "isOther"))),
        other_text=_text(relation, "otherText"),
    )


def _trade(node: ET.Element, table: str, seq: int) -> Trade:
    trade = Trade(
        table=table,
        seq=seq,
        security_title=_value(node, "securityTitle"),
        date=_day(_value(node, "transactionDate")),
        code=_text(node, "transactionCoding/transactionCode"),
        shares=_number(_value(node, "transactionAmounts/transactionShares")),
        price=_number(_value(node, "transactionAmounts/transactionPricePerShare")),
        price_footnotes=_footnotes(node, "transactionAmounts/transactionPricePerShare"),
        acquired_disposed=_value(node, "transactionAmounts/transactionAcquiredDisposedCode"),
        owned_after=_number(_value(node, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
        direct_indirect=_value(node, "ownershipNature/directOrIndirectOwnership"),
        nature_of_ownership=_value(node, "ownershipNature/natureOfOwnership"),
    )
    if table == "derivative":
        trade.exercise_price = _number(_value(node, "conversionOrExercisePrice"))
        trade.underlying_title = _value(node, "underlyingSecurity/underlyingSecurityTitle")
        trade.underlying_shares = _number(_value(node, "underlyingSecurity/underlyingSecurityShares"))
    return trade


def parse_form4(xml: bytes, accession: str) -> Form4:
    """Parse one Form 4 or 4/A document (its raw XML, not the rendered page). Whitespace around it is ignored: an
    XML declaration after a blank line isn't well-formed."""
    try:
        root = ET.fromstring(xml.strip())
    except ET.ParseError as exc:
        raise Form4Error(f"{accession}: not well-formed XML ({exc})") from exc
    if root.tag != "ownershipDocument":
        raise Form4Error(f"{accession}: not an ownership document (<{root.tag}>)")
    filing = Form4(
        accession=accession,
        form=_text(root, "documentType"),
        period_of_report=_day(_text(root, "periodOfReport")),
        original_filed=_day(_text(root, "dateOfOriginalSubmission")),
        issuer_cik=_text(root, "issuer/issuerCik"),
        issuer_ticker=_text(root, "issuer/issuerTradingSymbol"),
        aff10b5one=_flag(_text(root, "aff10b5One")),
        owners=[_owner(node) for node in root.findall("reportingOwner")],
    )
    rows = [(node, "nonderivative") for node in root.findall("nonDerivativeTable/nonDerivativeTransaction")]
    rows += [(node, "derivative") for node in root.findall("derivativeTable/derivativeTransaction")]
    filing.trades = [_trade(node, table, seq) for seq, (node, table) in enumerate(rows)]
    return filing

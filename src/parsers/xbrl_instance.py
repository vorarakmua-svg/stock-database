"""A filing's own XBRL instance, read as the SEC's companyfacts carry a filing's facts (Q17).

The companyfacts API sometimes lacks a report for months after EDGAR lists it (BUG-251: DLR's 10-Q of 2026-07-31,
86 days on; BUG-295: none of Citigroup's 2026 10-Qs). The filing's instance document holds the same facts: EDGAR's
extracted instance (``<primary document>_htm.xml``) for an inline filing, the filed ``.xml`` instance for an older one.
``companyfacts_from_instance`` reads one into companyfacts' shape, so the parser reads it as it would the SEC's:

- Only the facts companyfacts keep: numeric facts of the standard taxonomies (us-gaap, ifrs-full, dei, srt, invest),
  in a context with no segment and no scenario (no dimension), for the filer's own CIK, with a unit and a value (a
  nil fact has none). A
  filer's own concepts, text blocks and dimensional facts are left out, as the SEC leaves them out.
- Values as the instance gives them: EDGAR's extracted instance has an inline fact's scale and sign applied already,
  and a filed instance states the value. As companyfacts give them, at most six decimal places, and an int when
  integral (``_value``).
- Each fact as ``{start?, end, val, accn, fy, fp, form, filed, frame?}`` under its taxonomy, concept and unit (the
  unit's measures by their local names: ``USD``, ``shares``, ``USD/shares``, ``pure``). ``fy`` and ``fp`` are the
  filing's dei:DocumentFiscalYearFocus and DocumentFiscalPeriodFocus on every fact, comparative columns included, as
  companyfacts gives them; ``form`` and ``filed`` are the submissions feed's, passed in.
- ``frame``: the calendar period the SEC's frames give a fact's period (``frame_of``: ``CY2025``, ``CY2026Q1``,
  ``CY2026Q1I``; none for a half year or nine months). companyfacts give it to one fact of a period, the latest filed,
  which a gap's report's is; the parser reads a period's frames from all its facts.
- A fact the instance repeats (an inline document shows a figure in several tables) is kept once; two values for one
  concept, period and unit keep the more precise (``decimals``), as XBRL's duplicate rule reads them.

stdlib only (``xml.etree.ElementTree.iterparse``, clearing as it goes: a bank's instance runs to 18 MB)."""

import io
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple, Union

_XBRLI = "{http://www.xbrl.org/2003/instance}"

#: The standard taxonomies companyfacts carry, by their namespaces' stems (any year's release).
_TAXONOMIES: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"^http://(fasb\.org|xbrl\.us)/us-gaap/"), "us-gaap"),
    (re.compile(r"^https?://xbrl\.ifrs\.org/taxonomy/[^/]+/ifrs-full"), "ifrs-full"),
    (re.compile(r"^http://(xbrl\.sec\.gov|xbrl\.us)/dei/"), "dei"),
    (re.compile(r"^http://fasb\.org/srt/"), "srt"),
    (re.compile(r"^http://xbrl\.sec\.gov/invest/"), "invest"),
)


class InstanceError(ValueError):
    """An instance that can't be read: not XML, or no fact companyfacts would keep."""


def _taxonomy(namespace: str) -> Optional[str]:
    for pattern, name in _TAXONOMIES:
        if pattern.match(namespace):
            return name
    return None


def _split(tag: str) -> Tuple[str, str]:
    """``{namespace}local`` -> (namespace, local)."""
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return namespace, local
    return "", tag


def _measure(text: Optional[str]) -> str:
    """A unit measure by its local name, as companyfacts name units: ``iso4217:USD`` -> ``USD``."""
    return (text or "").strip().rpartition(":")[2]


def _day(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def frame_of(start: Optional[str], end: str) -> Optional[str]:
    """The SEC frame of a fact's period, by its end: ``CY2025`` for a span of 320 to 410 days, the calendar year
    holding its end less 180 days (a fiscal year to 2026-01-25 is CY2025, Microsoft's to 2011-06-30 CY2011);
    ``CY2026Q1`` for a span of 46 to 136 days, the calendar quarter holding its end less 45 days (a quarter to
    2026-04-26 is CY2026Q1, Costco's 12 weeks to 2019-05-12 CY2019Q1, Kroger's 16 to 2020-05-23 CY2020Q2);
    ``CY2026Q1I`` for an instant, or a span under 46 days, the same quarter's (a count of 2010-02-14 is CY2009Q4I).
    None for any other span (a half year, nine months). The rule gives every frame of the 1,013 cached companyfacts
    (2026-11-13): 5.2 million framed facts, us-gaap's 1.16 million annual, 1.46 million quarterly and 2.52 million
    instant among them."""
    last = _day(end)
    first = _day(start) if start else last
    if last is None or first is None:
        return None
    span = (last - first).days
    if span < 0:
        return None
    if 320 <= span <= 410:
        return f"CY{(last - timedelta(days=180)).year}"
    quarter_of = last - timedelta(days=45)
    name = f"CY{quarter_of.year}Q{(quarter_of.month - 1) // 3 + 1}"
    if span < 46:
        return name + "I"
    if span <= 136:
        return name
    return None


def _value(text: Optional[str]) -> Optional[Union[int, float]]:
    """A fact's value as companyfacts give it: at most six decimal places (none of the 1,013 cached files carries more;
    Microsoft's par value of 0.00000625 reads 6e-06), an int when integral."""
    try:
        number = Decimal((text or "").strip())
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    exponent = number.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -6:
        number = number.quantize(Decimal("0.000001"))
    return int(number) if number == number.to_integral_value() else float(number)


def _read_context(element: ET.Element, cik: int, contexts: Dict[str, Tuple[Optional[str], str]]) -> None:
    """Keep *element*, a context, in *contexts* (id -> (start or None, end or instant)) when companyfacts would read
    its facts: no segment and no scenario (no dimension), the filer's own CIK, and an instant or a start and an end
    (never ``forever``)."""
    entity = element.find(_XBRLI + "entity")
    if entity is None or entity.find(_XBRLI + "segment") is not None or element.find(_XBRLI + "scenario") is not None:
        return
    identifier = (entity.findtext(_XBRLI + "identifier") or "").strip()
    if identifier.isdigit() and int(identifier) != cik:
        return
    instant = (element.findtext(f"{_XBRLI}period/{_XBRLI}instant") or "").strip()
    start = (element.findtext(f"{_XBRLI}period/{_XBRLI}startDate") or "").strip()
    end = (element.findtext(f"{_XBRLI}period/{_XBRLI}endDate") or "").strip()
    if instant:
        contexts[element.get("id") or ""] = (None, instant[:10])
    elif start and end:
        # A span of no days is an instant, as companyfacts give it (JPM's acquisition paid on 2025-01-31).
        contexts[element.get("id") or ""] = (None if start[:10] == end[:10] else start[:10], end[:10])


def _unit(element: ET.Element) -> str:
    """A unit as companyfacts name it: its measures' local names, a ratio's as ``numerator/denominator``."""
    divide = element.find(_XBRLI + "divide")
    if divide is None:
        return "*".join(_measure(m.text) for m in element.iter(_XBRLI + "measure"))
    parts = [[_measure(m.text) for m in side.iter(_XBRLI + "measure")]
             for side in (divide.find(_XBRLI + "unitNumerator"), divide.find(_XBRLI + "unitDenominator"))
             if side is not None]
    return "/".join("*".join(p) for p in parts)


def _focus(found: List[Tuple[str, str, str]], contexts: Dict[str, Tuple[Optional[str], str]], concept: str) -> str:
    """The filing's dei *concept* (its fiscal year or period focus): the value given in a non-dimensional context of
    the filer's, else the first given."""
    given = [(context in contexts, text) for name, context, text in found if name == concept and text]
    plain = [text for in_plain, text in given if in_plain]
    return (plain or [text for _, text in given] or [""])[0]


def _precision(decimals: Optional[str]) -> float:
    if decimals is None or decimals.strip().upper() == "INF":
        return float("inf")
    try:
        return float(int(decimals.strip()))
    except ValueError:
        return float("-inf")


def companyfacts_from_instance(content: bytes, cik: Union[str, int], accession: str, form: str,
                               filed: str) -> Dict[str, Any]:
    """The facts companyfacts would carry of a filing, from its XBRL instance *content*: ``{"facts": {taxonomy:
    {concept: {"units": {unit: [entry, ...]}}}}}``, each entry with *accession*, *form* and *filed* (the submissions
    feed's) and the filing's fiscal year and period focus. Raises ``InstanceError`` for a document that isn't XML or
    holds no such fact of CIK *cik*."""
    contexts: Dict[str, Tuple[Optional[str], str]] = {}  # id -> (start, end) of a non-dimensional context of the filer
    units: Dict[str, str] = {}
    raw: List[Tuple[str, str, str, str, str, Optional[str]]] = []  # taxonomy, concept, context, unit, text, decimals
    focus: List[Tuple[str, str, str]] = []  # the dei focus facts: concept, context, text
    own = int(cik)
    try:
        for _, element in ET.iterparse(io.BytesIO(content), events=("end",)):
            tag = element.tag
            if tag == _XBRLI + "context":
                _read_context(element, own, contexts)
            elif tag == _XBRLI + "unit":
                units[element.get("id") or ""] = _unit(element)
            else:
                context_ref = element.get("contextRef")
                if context_ref is None:
                    continue
                namespace, concept = _split(tag)
                taxonomy = _taxonomy(namespace)
                if taxonomy == "dei" and concept in ("DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus"):
                    focus.append((concept, context_ref, (element.text or "").strip()))
                elif taxonomy and element.get("unitRef") is not None:
                    raw.append((taxonomy, concept, context_ref, element.get("unitRef") or "", element.text or "",
                                element.get("decimals")))
            element.clear()  # read: its text and children aren't needed again
    except ET.ParseError as exc:
        raise InstanceError(f"not an XBRL instance ({exc})") from exc

    fy_text, fp_text = _focus(focus, contexts, "DocumentFiscalYearFocus"), _focus(focus, contexts,
                                                                                  "DocumentFiscalPeriodFocus")
    fy: Optional[int] = int(fy_text) if fy_text.isdigit() else None
    fp: Optional[str] = fp_text or None

    kept: Dict[Tuple[str, str, str, Optional[str], str], Tuple[float, Union[int, float]]] = {}
    for taxonomy, concept, context_ref, unit_ref, text, decimals in raw:
        period = contexts.get(context_ref)
        unit = units.get(unit_ref)
        value = _value(text)
        if period is None or not unit or value is None:
            continue
        key = (taxonomy, concept, unit, period[0], period[1])
        precision = _precision(decimals)
        held = kept.get(key)
        if held is None or precision > held[0]:
            kept[key] = (precision, value)

    facts: Dict[str, Dict[str, Any]] = {}
    for (taxonomy, concept, unit, start, end), (_, value) in kept.items():
        entry: Dict[str, Any] = {}
        if start is not None:
            entry["start"] = start
        entry.update({"end": end, "val": value, "accn": accession, "fy": fy, "fp": fp, "form": form, "filed": filed})
        frame = frame_of(start, end)
        if frame:
            entry["frame"] = frame
        units_of = facts.setdefault(taxonomy, {}).setdefault(concept, {"units": {}})["units"]
        units_of.setdefault(unit, []).append(entry)
    if not facts:
        raise InstanceError(f"no fact of CIK {own} the companyfacts would keep")
    for tags in facts.values():
        for body in tags.values():
            for entries in body["units"].values():
                entries.sort(key=lambda e: (e["end"], e.get("start") or ""))
    return {"facts": facts}

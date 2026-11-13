"""Q17: a filing's own XBRL instance read as the SEC's companyfacts carry its facts (src/parsers/xbrl_instance.py)."""

import pytest

from src.parsers.xbrl_instance import InstanceError, companyfacts_from_instance, frame_of

_CIK = "0001297996"
_ACCN = "0001104659-26-089296"

#: Contexts of a second-quarter 10-Q as EDGAR's extracted instance gives them (id -> the context's inner XML).
CONTEXTS = {
    "q2": "<period><startDate>2026-04-01</startDate><endDate>2026-06-30</endDate></period>",
    "h1": "<period><startDate>2026-01-01</startDate><endDate>2026-06-30</endDate></period>",
    "q2_2025": "<period><startDate>2025-04-01</startDate><endDate>2025-06-30</endDate></period>",
    "jun": "<period><instant>2026-06-30</instant></period>",
    "dec": "<period><instant>2025-12-31</instant></period>",
    "cover": "<period><instant>2026-07-24</instant></period>",
}


def instance(facts, contexts=None, segments=(), scenarios=(), entities=None, units=None, focus=("2026", "Q2"),
             focus_context="q2"):
    """A small XBRL instance, as EDGAR extracts one: *contexts* (id -> period XML, CONTEXTS by default), those in
    *segments* with a dimension in their entity and those in *scenarios* with one in a scenario, each the filer's
    unless *entities* (id -> CIK) names another; the units USD,
    shares, USD per share and pure (or *units*, id -> inner XML); the dei focus facts *focus* (fiscal year, period)
    in *focus_context*; and *facts*, each (prefix:concept, context, unit or None, text, extra attributes)."""
    contexts = CONTEXTS if contexts is None else contexts
    units = units or {
        "usd": "<measure>iso4217:USD</measure>",
        "shares": "<measure>xbrli:shares</measure>",
        "usdPerShare": "<divide><unitNumerator><measure>iso4217:USD</measure></unitNumerator>"
                       "<unitDenominator><measure>xbrli:shares</measure></unitDenominator></divide>",
        "pure": "<measure>xbrli:pure</measure>",
    }
    member = ('<xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">dlr:ColocationMember'
              '</xbrldi:explicitMember>')
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:us-gaap="http://fasb.org/us-gaap/2026" '
             'xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:srt="http://fasb.org/srt/2026" '
             'xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full" '
             'xmlns:dlr="http://www.digitalrealty.com/20260630" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
             'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:xbrli="http://www.xbrl.org/2003/instance" '
             'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:link="http://www.xbrl.org/2003/linkbase" '
             'xmlns:xlink="http://www.w3.org/1999/xlink">',
             '<link:schemaRef xlink:href="dlr-20260630.xsd" xlink:type="simple"/>']
    if focus:
        parts += [f'<dei:DocumentFiscalYearFocus contextRef="{focus_context}">{focus[0]}</dei:DocumentFiscalYearFocus>',
                  f'<dei:DocumentFiscalPeriodFocus contextRef="{focus_context}">{focus[1]}'
                  '</dei:DocumentFiscalPeriodFocus>']
    for cid, period in contexts.items():
        segment = f"<segment>{member}</segment>" if cid in segments else ""
        scenario = f"<scenario>{member}</scenario>" if cid in scenarios else ""
        entity = (entities or {}).get(cid, _CIK)
        parts.append(f'<context id="{cid}"><entity><identifier scheme="http://www.sec.gov/CIK">{entity}</identifier>'
                     f'{segment}</entity>{period}{scenario}</context>')
    parts += [f'<unit id="{uid}">{body}</unit>' for uid, body in units.items()]
    for concept, context, unit, text, *extra in facts:
        attributes = f' contextRef="{context}"' + (f' unitRef="{unit}" decimals="-3"' if unit else "")
        attributes += "".join(f' {k}="{v}"' for k, v in (extra[0] if extra else {}).items())
        parts.append(f"<{concept}{attributes}>{text}</{concept}>")
    parts.append("</xbrl>")
    return "\n".join(parts).encode("utf-8")


def read(content, form="10-Q", filed="2026-07-31"):
    return companyfacts_from_instance(content, _CIK, _ACCN, form, filed)["facts"]


def entries(facts, concept, unit="USD", taxonomy="us-gaap"):
    return facts[taxonomy][concept]["units"][unit]


def test_a_quarters_facts_are_read_as_the_companyfacts_carry_them():
    # DLR's 10-Q of 2026-07-31, which its companyfacts lacked for months (BUG-251): each fact under its taxonomy,
    # concept and unit, with the filing's accession, form, filing date and fiscal focus, and the SEC's frame.
    facts = read(instance([("us-gaap:Revenues", "q2", "usd", "1712345000"),
                           ("us-gaap:Revenues", "h1", "usd", "3347518000"),
                           ("us-gaap:Revenues", "q2_2025", "usd", "1492955000"),
                           ("us-gaap:Assets", "jun", "usd", "48601234000"),
                           ("us-gaap:Assets", "dec", "usd", "48731000000")]))
    common = {"accn": _ACCN, "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-07-31"}
    assert entries(facts, "Revenues") == [
        {"start": "2025-04-01", "end": "2025-06-30", "val": 1492955000, **common, "frame": "CY2025Q2"},
        {"start": "2026-01-01", "end": "2026-06-30", "val": 3347518000, **common},
        {"start": "2026-04-01", "end": "2026-06-30", "val": 1712345000, **common, "frame": "CY2026Q2"},
    ]
    assert entries(facts, "Assets") == [
        {"end": "2025-12-31", "val": 48731000000, **common, "frame": "CY2025Q4I"},
        {"end": "2026-06-30", "val": 48601234000, **common, "frame": "CY2026Q2I"},
    ]


def test_a_fact_with_a_dimension_is_left_out():
    # companyfacts keep only facts in a context without a segment or a scenario: a segment's revenue isn't the total.
    facts = read(instance([("us-gaap:Revenues", "q2", "usd", "1712345000"),
                           ("us-gaap:Revenues", "seg", "usd", "1000000000"),
                           ("us-gaap:Revenues", "scen", "usd", "900000000")],
                          contexts={**CONTEXTS, "seg": CONTEXTS["q2_2025"], "scen": CONTEXTS["h1"]},
                          segments=("seg",), scenarios=("scen",)))
    assert [e["val"] for e in entries(facts, "Revenues")] == [1712345000]


def test_another_entitys_fact_is_left_out():
    facts = read(instance([("us-gaap:Revenues", "q2", "usd", "1712345000"),
                           ("us-gaap:Revenues", "other", "usd", "5")],
                          contexts={**CONTEXTS, "other": CONTEXTS["h1"]}, entities={"other": "0001234567"}))
    assert [e["val"] for e in entries(facts, "Revenues")] == [1712345000]


def test_units_are_named_as_the_companyfacts_name_them():
    facts = read(instance([("us-gaap:EarningsPerShareDiluted", "q2", "usdPerShare", "0.71"),
                           ("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding", "q2", "shares", "343456789"),
                           ("us-gaap:EffectiveIncomeTaxRateContinuingOperations", "q2", "pure", "0.125"),
                           ("us-gaap:Revenues", "q2", "usd", "1712345000")]))
    assert {concept: list(body["units"]) for concept, body in facts["us-gaap"].items()} == {
        "EarningsPerShareDiluted": ["USD/shares"], "WeightedAverageNumberOfDilutedSharesOutstanding": ["shares"],
        "EffectiveIncomeTaxRateContinuingOperations": ["pure"], "Revenues": ["USD"]}
    assert entries(facts, "EarningsPerShareDiluted", "USD/shares")[0]["val"] == 0.71
    assert entries(facts, "WeightedAverageNumberOfDilutedSharesOutstanding", "shares")[0]["val"] == 343456789
    assert entries(facts, "EffectiveIncomeTaxRateContinuingOperations", "pure")[0]["val"] == 0.125


def test_only_the_numeric_facts_of_the_standard_taxonomies_are_read():
    # A nil fact, a text fact and the filer's own concept are left out; dei's, srt's and ifrs-full's numeric facts are
    # kept, as companyfacts keep them.
    facts = read(instance([("us-gaap:Revenues", "q2", "usd", "1712345000"),
                           ("us-gaap:OtherNonoperatingIncomeExpense", "q2", "usd", "", {"xsi:nil": "true"}),
                           ("us-gaap:RevenueRecognitionPolicyTextBlock", "q2", None, "Revenue is recognized ..."),
                           ("dei:EntityCentralIndexKey", "q2", None, "0001297996"),  # a number without a unit
                           ("dlr:ColocationRevenue", "q2", "usd", "1000000000"),
                           ("dei:EntityCommonStockSharesOutstanding", "cover", "shares", "341234567"),
                           ("srt:RevenueFromRelatedParties", "q2", "usd", "1000"),
                           ("ifrs-full:Revenue", "q2", "usd", "1712345000")]))
    assert set(facts) == {"us-gaap", "dei", "srt", "ifrs-full"}
    assert set(facts["us-gaap"]) == {"Revenues"}
    assert set(facts["dei"]) == {"EntityCommonStockSharesOutstanding"}
    assert entries(facts, "EntityCommonStockSharesOutstanding", "shares", "dei") == [
        {"end": "2026-07-24", "val": 341234567, "accn": _ACCN, "fy": 2026, "fp": "Q2", "form": "10-Q",
         "filed": "2026-07-31", "frame": "CY2026Q2I"}]


@pytest.mark.parametrize("text, value", [
    ("-48000000", -48000000),     # the sign as the instance gives it (an inline fact's sign applied by EDGAR)
    ("1712345000", 1712345000),   # the scale as given: decimals="-3" doesn't rescale it
    ("1712345000.00", 1712345000),
    ("0.71", 0.71),
    ("0.00000625", 6e-06),        # six decimal places at most, as companyfacts give them (MSFT's par value)
])
def test_a_value_is_read_as_given(text, value):
    got = entries(read(instance([("us-gaap:Revenues", "q2", "usd", text)])), "Revenues")[0]["val"]
    assert got == value and type(got) is type(value)


def test_a_fact_given_twice_is_kept_once():
    # An inline document shows a figure in several tables; two contexts of the same period are one period.
    facts = read(instance([("us-gaap:Revenues", "q2", "usd", "1712345000"),
                           ("us-gaap:Revenues", "q2", "usd", "1712345000"),
                           ("us-gaap:Revenues", "q2b", "usd", "1712345000")],
                          contexts={**CONTEXTS, "q2b": CONTEXTS["q2"]}))
    assert [e["val"] for e in entries(facts, "Revenues")] == [1712345000]


def test_two_values_for_one_fact_keep_the_more_precise():
    facts = read(instance([("us-gaap:Revenues", "q2", "usd", "1712000000", {"decimals": "-6"}),
                           ("us-gaap:Revenues", "q2b", "usd", "1712345000", {"decimals": "-3"}),
                           ("us-gaap:Revenues", "h1", "usd", "3347518000", {"decimals": "INF"}),
                           ("us-gaap:Revenues", "h1b", "usd", "3347000000", {"decimals": "-6"})],
                          contexts={**CONTEXTS, "q2b": CONTEXTS["q2"], "h1b": CONTEXTS["h1"]}).replace(
        b' decimals="-3" decimals=', b" decimals="))
    assert [e["val"] for e in entries(facts, "Revenues")] == [3347518000, 1712345000]


def test_every_fact_carries_the_filings_fiscal_focus():
    # The comparative quarter of 2025 too, as companyfacts give it (fy 2026, fp Q2 on the 10-Q's every fact).
    facts = read(instance([("us-gaap:Revenues", "q2_2025", "usd", "1492955000")], focus=("2027", "Q1")))
    assert {(e["fy"], e["fp"]) for e in entries(facts, "Revenues")} == {(2027, "Q1")}


def test_the_focus_given_without_a_dimension_wins():
    content = instance([("us-gaap:Revenues", "q2", "usd", "1712345000")], focus=("2025", "FY"), focus_context="seg",
                       contexts={**CONTEXTS, "seg": CONTEXTS["q2"]}, segments=("seg",))
    content = content.replace(b"<context id=", b'<dei:DocumentFiscalYearFocus contextRef="q2">2026'
                                               b'</dei:DocumentFiscalYearFocus><dei:DocumentFiscalPeriodFocus '
                                               b'contextRef="q2">Q2</dei:DocumentFiscalPeriodFocus><context id=', 1)
    assert {(e["fy"], e["fp"]) for e in entries(read(content), "Revenues")} == {(2026, "Q2")}


def test_a_filing_without_a_fiscal_focus_gives_none():
    facts = read(instance([("us-gaap:Revenues", "q2", "usd", "1712345000")], focus=None))
    assert {(e["fy"], e["fp"]) for e in entries(facts, "Revenues")} == {(None, None)}


def test_a_span_of_no_days_is_an_instant():
    # As companyfacts give it: JPM's acquisition paid on 2025-01-31 (start = end) has no start.
    facts = read(instance([("us-gaap:PaymentsToAcquireBusinessesGross", "day", "usd", "609000000")],
                          contexts={"day": "<period><startDate>2025-01-31</startDate><endDate>2025-01-31</endDate>"
                                           "</period>"}))
    assert entries(facts, "PaymentsToAcquireBusinessesGross")[0]["end"] == "2025-01-31"
    assert "start" not in entries(facts, "PaymentsToAcquireBusinessesGross")[0]


@pytest.mark.parametrize("start, end, frame", [
    ("2026-01-01", "2026-12-31", "CY2026"),
    ("2025-01-27", "2026-01-25", "CY2025"),  # a year ending in January: the year before
    ("2010-07-01", "2011-06-30", "CY2011"),  # Microsoft's year to June 30: the year it ends in
    ("2010-06-27", "2011-06-25", "CY2010"),  # a 52-week year ending June 25: the year before
    ("2008-12-29", "2010-01-03", "CY2009"),  # 53 weeks to January 3
    ("2026-04-01", "2026-06-30", "CY2026Q2"),
    ("2026-01-26", "2026-04-26", "CY2026Q1"),  # a quarter ending in late April
    ("2019-02-18", "2019-05-12", "CY2019Q1"),  # Costco's 12 weeks to May 12
    ("2020-02-02", "2020-05-23", "CY2020Q2"),  # Kroger's 16 weeks to May 23
    (None, "2026-06-30", "CY2026Q2I"),
    (None, "2010-02-14", "CY2009Q4I"),  # a cover-page count on February 14
    (None, "2013-11-15", "CY2013Q4I"),
    ("2023-01-01", "2023-01-31", "CY2022Q4I"),  # a month: an instant's frame
    ("2026-01-01", "2026-06-30", None),  # a half year
    ("2026-01-01", "2026-09-30", None),  # nine months
    ("2026-06-30", "2026-01-01", None),
])
def test_the_frame_is_the_secs(start, end, frame):
    # The rule gives every frame of the 1,013 cached companyfacts (5.2 million facts); these are its cases.
    assert frame_of(start, end) == frame


@pytest.mark.parametrize("content", [b"<html><body>Not found</body></html", b"{}", b""])
def test_a_document_that_isnt_an_instance_is_an_error(content):
    with pytest.raises(InstanceError):
        read(content)


def test_an_instance_without_a_fact_the_companyfacts_would_keep_is_an_error():
    with pytest.raises(InstanceError, match="no fact of CIK 1297996"):
        read(instance([("dlr:ColocationRevenue", "q2", "usd", "1000000000"),
                       ("us-gaap:Revenues", "seg", "usd", "1000000000")],
                      contexts={**CONTEXTS, "seg": CONTEXTS["q2"]}, segments=("seg",)))

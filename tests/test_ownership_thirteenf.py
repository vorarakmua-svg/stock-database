"""The 13F parser (Milestone 3): real SEC full submission texts, each document found by its TYPE, the values read in
dollars (by the filing date, or by the holdings' prices where the date gives impossible ones) and the cover's totals
checked against the entries, within rounding (docs/superpowers/specs/2026-10-22-sec-ownership-design.md).
"""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from src.ownership import thirteenf
from src.ownership.thirteenf import ThirteenFError, documents, parse_13f

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ownership"
#: 0001709323-22-000005, filed 2022-11-14 for Q3 2022: values in thousands, an ``ns1:`` information table.
HIMALAYA = "himalaya_2022q3_thousands.txt"
#: 0001437749-26-023711, filed 2026-07-16 for Q2 2026: values in dollars, a default-namespace information table.
DJCO = "djco_2026q2_dollars.txt"
#: 0001172661-25-001497, filed 2025-04-16: a 13F-HR/A adding a holding left out of the Q4 2024 report (Hertz).
PERSHING = "pershing_2024q4_new_holdings.txt"
#: 0002043585-26-000022, filed 2026-08-14 for Q2 2026: a value total a dollar under its eight entries' sum.
HIMALAYA_2026 = "himalaya_2026q2_rounding.txt"
#: 0001061768-26-000010, filed 2026-08-13 for Q2 2026: Baupost still states its values in thousands.
BAUPOST = "baupost_2026q2_thousands.txt"


def _text(name):
    return (FIXTURES / name).read_bytes()


def _parse(name=None, accession="0000000000-26-000001", text=None):
    return parse_13f(text if text is not None else _text(name), accession)


def _filed_on(text, day):
    """The text as if the SEC had filed it on *day* (YYYYMMDD)."""
    changed, n = re.subn(rb"FILED AS OF DATE:\s*\d{8}", b"FILED AS OF DATE:\t\t" + day, text)
    assert n == 1
    return changed


def _value_total(name, old, new):
    """The filing with its cover's value total *old* replaced by *new*."""
    text = _text(name)
    assert text.count(b"<tableValueTotal>%d</tableValueTotal>" % old) == 1
    return _parse(text=text.replace(b"<tableValueTotal>%d</tableValueTotal>" % old,
                                    b"<tableValueTotal>%d</tableValueTotal>" % new))


def _one_holding(value, shares, filed=b"20250416"):
    """Pershing's one-entry filing with its holding's value and shares replaced, as if filed on *filed*."""
    text = _filed_on(_text(PERSHING), filed).replace(b"<value>46533105</value>", b"<value>%d</value>" % value)
    return _parse(text=text.replace(b"<sshPrnamt>12713963</sshPrnamt>", b"<sshPrnamt>%d</sshPrnamt>" % shares))


def test_the_cover_of_a_holdings_report():
    f = _parse(DJCO, "0001437749-26-023711")
    assert (f.accession, f.form, f.filed, f.period, f.report_type, f.is_amendment, f.amendment_type) == (
        "0001437749-26-023711", "13F-HR", "2026-07-16", "2026-06-30", "13F HOLDINGS REPORT", False, None)
    assert (f.entry_total, f.value_total, f.confidential_omitted) == (4, Decimal("256664220"), False)


def test_each_entry_in_the_filings_order():
    f = _parse(DJCO)
    assert [(h.row, h.issuer, h.title_of_class, h.cusip, h.shares, h.share_type, h.put_call, h.discretion)
            for h in f.holdings] == [
        (0, "Alibaba Group Holding Ltd", "Sponsored ADS", "01609W102", 195000.0, "SH", None, "SOLE"),
        (1, "Bank of America Corp", "Common Stock", "060505104", 2000000.0, "SH", None, "SOLE"),
        (2, "US Bancorp", "Common Stock", "902973304", 119500.0, "SH", None, "SOLE"),
        (3, "Wells Fargo & Co", "Common Stock", "949746101", 1413000.0, "SH", None, "SOLE"),
    ]


def test_values_filed_from_2023_01_03_are_in_dollars():
    bac = _parse(DJCO).holdings[1]
    assert (bac.value, bac.value_usd) == (Decimal("113960000"), 113960000.0)  # $56.98 a share


def test_values_filed_before_2023_01_03_are_in_thousands():
    f = _parse(HIMALAYA, "0001709323-22-000005")
    assert (f.form, f.filed, f.period, f.entry_total, f.value_total) == (
        "13F-HR", "2022-11-14", "2022-09-30", 6, Decimal("1743552"))
    assert [h.cusip for h in f.holdings] == [
        "02079K107", "02079K305", "037833100", "060505104", "084670702", "595112103"]
    bac = f.holdings[3]
    assert (bac.issuer, bac.value, bac.value_usd, bac.shares) == (
        "BK OF AMERICA CORP", Decimal("440527"), 440527000.0, 14586987.0)  # $30.20 a share


def test_the_units_go_by_the_filing_date_not_the_period():
    # The same Q3 2022 report, its entries principal amounts (no share price to go by), as if filed on the last day
    # of thousands, then on the first day of dollars.
    text = _text(HIMALAYA).replace(b"<ns1:sshPrnamtType>SH</ns1:sshPrnamtType>",
                                   b"<ns1:sshPrnamtType>PRN</ns1:sshPrnamtType>")
    last = _parse(text=_filed_on(text, b"20230102"))
    first = _parse(text=_filed_on(text, b"20230103"))
    assert last.period == first.period == "2022-09-30"
    assert (last.multiplier, first.multiplier) == (1000, 1)
    assert (last.holdings[3].value_usd, first.holdings[3].value_usd) == (440527000.0, 440527.0)


def test_a_filing_made_after_the_switch_that_still_states_thousands():
    # Baupost's Q2 2026 13F-HR: in dollars, as its filing date says, its median share would cost $0.13.
    f = _parse(BAUPOST, "0001061768-26-000010")
    assert (f.filed, f.multiplier) == ("2026-08-13", 1000)
    amazon = next(h for h in f.holdings if h.cusip == "023135106")
    assert (amazon.value, amazon.value_usd, amazon.shares) == (Decimal("892310"), 892310000.0, 3743854.0)  # $238.34
    assert (f.value_total, f.dollars(f.value_total), f.checksum_ok) == (Decimal("5415853"), 5415853000.0, True)


def test_a_filing_made_before_the_switch_that_states_dollars():
    # Li Lu's Q2 2026 report (dollars: its median share $209.23) as if filed in 2022, when that read $209,225.
    f = _parse(text=_filed_on(_text(HIMALAYA_2026), b"20221114"))
    assert f.multiplier == 1
    assert [h.value_usd for h in f.holdings] == [float(h.value) for h in f.holdings]


def test_the_real_filings_keep_the_units_of_their_filing_date():
    assert [_parse(name).multiplier for name in (HIMALAYA, DJCO, PERSHING, HIMALAYA_2026)] == [1000, 1, 1, 1]


def test_without_share_rows_the_filing_date_decides():
    # Baupost's thousands with every entry a principal amount, then with no shares: no share price to go by.
    text = _text(BAUPOST)
    principal = text.replace(b"<ns1:sshPrnamtType>SH</ns1:sshPrnamtType>",
                             b"<ns1:sshPrnamtType>PRN</ns1:sshPrnamtType>")
    assert _parse(text=principal).multiplier == 1
    no_shares = re.sub(rb"<ns1:sshPrnamt>\d+</ns1:sshPrnamt>", b"<ns1:sshPrnamt>0</ns1:sshPrnamt>", text)
    assert _parse(text=no_shares).multiplier == 1


def test_one_odd_row_doesnt_decide_the_units():
    # The median price: Baupost's thousands, its Amazon row's shares cut to 1 (892,310 a share, as filed).
    text = _text(BAUPOST).replace(b"<ns1:sshPrnamt>3743854</ns1:sshPrnamt>", b"<ns1:sshPrnamt>1</ns1:sshPrnamt>")
    assert _parse(text=text).multiplier == 1000


def test_the_price_bounds():
    assert (thirteenf.PRICE_FLOOR, thirteenf.PRICE_CEILING) == (1, 100000)
    # Filed after the switch (dollars by date): a median under $1 that is $1 to $100,000 at 1,000 times is thousands.
    assert [_one_holding(value, shares).multiplier for value, shares in (
        (999, 1000),  # $0.999 a share: $999 in thousands
        (1000, 1000),  # $1: possible as it stands
        (1, 1000),  # $0.001: $1 in thousands
        (1, 1001),  # under $1 either way: the date's rule
    )] == [1000, 1, 1000, 1]
    # Filed before it (thousands by date): a median over $100,000 that is $1 to $100,000 at 1/1,000 is dollars.
    assert [_one_holding(value, shares, b"20221114").multiplier for value, shares in (
        (100, 1),  # $100,000 a share: possible as it stands
        (101, 1),  # $101,000: $101 in dollars
        (100000, 1),  # $100,000,000: $100,000 in dollars
        (100001, 1),  # over $100,000 either way: the date's rule
    )] == [1000, 1, 1, 1000]


def test_the_checksum_holds_on_the_real_filings():
    for name in (HIMALAYA, DJCO, PERSHING, BAUPOST):
        f = _parse(name)
        assert f.checksum_ok, name
        assert (f.entry_total, f.value_total) == (f.rows, f.rows_value), name  # in the units as filed


def test_the_value_total_may_differ_from_the_entries_by_their_rounding():
    # Each entry's value is rounded to the unit, and so is the total: half a unit per entry plus half a unit. One
    # entry (Pershing's Hertz, 46,533,105): a unit either way passes, two don't.
    assert [_value_total(PERSHING, 46533105, total).checksum_ok
            for total in (46533103, 46533104, 46533106, 46533107)] == [False, True, True, False]
    # Six entries summing to 1,743,552: 3.5 units, so 3 pass and 4 don't, either way.
    assert [_value_total(HIMALAYA, 1743552, total).checksum_ok
            for total in (1743548, 1743549, 1743555, 1743556)] == [False, True, True, False]
    f = _value_total(PERSHING, 46533105, 46533104)
    assert (f.value_total, f.rows_value) == (Decimal("46533104"), Decimal("46533105"))  # the figures as filed


def test_a_real_value_total_a_dollar_under_its_entries_passes():
    # Li Lu's Q2 2026 report: eight entries summing to $3,702,921,099 against a stated $3,702,921,098.
    f = _parse(HIMALAYA_2026, "0002043585-26-000022")
    assert (f.entry_total, f.rows, f.value_total, f.rows_value) == (8, 8, Decimal("3702921098"), Decimal("3702921099"))
    assert f.checksum_ok


def test_a_value_beyond_the_rounding_fails_the_checksum():
    # Four entries may differ from the total by (4 + 1) / 2 = 2.5 units; this one, by 3.
    f = _parse(text=_text(DJCO).replace(b"<value>7217800</value>", b"<value>7217803</value>"))
    assert not f.checksum_ok
    assert (f.entry_total, f.rows, f.value_total, f.rows_value) == (4, 4, Decimal("256664220"), Decimal("256664223"))


def test_an_entry_total_that_disagrees_with_the_entries_fails_the_checksum_whatever_the_values():
    text = _text(DJCO).replace(b"<tableEntryTotal>4</tableEntryTotal>", b"<tableEntryTotal>5</tableEntryTotal>")
    f = _parse(text=text)
    assert not f.checksum_ok
    assert (f.entry_total, f.rows) == (5, 4) and f.value_total == f.rows_value  # the values agree exactly
    assert not _parse(text=text.replace(b"<value>7217800</value>", b"<value>7217801</value>")).checksum_ok


def test_a_cover_without_its_totals_fails_the_checksum():
    text = _text(DJCO)
    for total in (b"<tableEntryTotal>4</tableEntryTotal>", b"<tableValueTotal>256664220</tableValueTotal>"):
        f = _parse(text=text.replace(total, b""))
        assert not f.checksum_ok and None in (f.entry_total, f.value_total)


def test_the_values_are_summed_exactly():
    # In floats 0.1 + 0.2 isn't 0.3: the figures are kept as decimals, exactly as filed.
    text = _text(DJCO)
    for old, new in ((b"<value>18716100</value>", b"<value>0.1</value>"),
                     (b"<value>113960000</value>", b"<value>0.2</value>"),
                     (b"<value>7217800</value>", b"<value>0</value>"),
                     (b"<value>116770320</value>", b"<value>0</value>"),
                     (b"<tableValueTotal>256664220</tableValueTotal>", b"<tableValueTotal>0.3</tableValueTotal>")):
        text = text.replace(old, new)
    f = _parse(text=text)
    assert f.rows_value == Decimal("0.3") and f.checksum_ok


def test_an_amendment_adding_new_holdings():
    f = _parse(PERSHING, "0001172661-25-001497")
    assert (f.form, f.filed, f.period, f.report_type, f.is_amendment, f.amendment_type) == (
        "13F-HR/A", "2025-04-16", "2024-12-31", "13F HOLDINGS REPORT", True, "NEW HOLDINGS")
    assert f.confidential_omitted is None  # the cover doesn't say
    (hertz,) = f.holdings
    assert (hertz.issuer, hertz.cusip, hertz.value_usd, hertz.shares, hertz.share_type) == (
        "HERTZ GLOBAL HLDGS INC", "42806J700", 46533105.0, 12713963.0, "SH")


def test_the_period_falls_back_to_the_headers_period_of_report():
    text = _text(DJCO).replace(b"<reportCalendarOrQuarter>06-30-2026</reportCalendarOrQuarter>", b"")
    assert _parse(text=text).period == "2026-06-30"  # headerData's periodOfReport


def test_the_documents_are_split_by_their_type():
    (cover_type, cover), (table_type, table) = documents(_text(HIMALAYA))
    assert (cover_type, table_type) == ("13F-HR", "INFORMATION TABLE")
    assert cover.strip().startswith(b"<?xml") and cover.strip().endswith(b"</edgarSubmission>")
    assert table.strip().endswith(b"</ns1:informationTable>")


def test_each_document_is_found_by_its_type_not_its_place():
    text = _text(DJCO)
    first = text.index(b"<DOCUMENT>")
    second = text.index(b"<DOCUMENT>", first + 1)
    end = text.index(b"</SEC-DOCUMENT>")
    # An exhibit whose XML looks like an information table, then the information table, then the cover.
    exhibit = (b"<DOCUMENT>\n<TYPE>EX-99\n<SEQUENCE>3\n<FILENAME>x.xml\n<TEXT>\n<XML>\n<informationTable><infoTable>"
               b"<cusip>000000000</cusip><value>1</value></infoTable></informationTable>\n</XML>\n</TEXT>\n</DOCUMENT>\n")
    shuffled = text[:first] + exhibit + text[second:end] + text[first:second] + text[end:]
    assert [kind for kind, _ in documents(shuffled)] == ["EX-99", "INFORMATION TABLE", "13F-HR"]
    f = _parse(text=shuffled)
    assert (f.form, f.rows, f.checksum_ok) == ("13F-HR", 4, True)


def test_crlf_line_endings_read_the_same():
    # A checkout under core.autocrlf=true turns the fixtures' LF into CRLF.
    text = _text(HIMALAYA)
    crlf = _parse(text=text.replace(b"\n", b"\r\n"))
    assert crlf == _parse(text=text) and (crlf.rows, crlf.checksum_ok) == (6, True)


def test_text_fields_lose_their_line_breaks_and_a_cusip_is_upper_cased():
    text = _text(DJCO).replace(b"<nameOfIssuer>Bank of America Corp</nameOfIssuer>",
                               b"<nameOfIssuer>Bank of\n  America Corp</nameOfIssuer>")
    text = text.replace(b"<cusip>01609W102</cusip>", b"<cusip> 01609w102 </cusip>")
    f = _parse(text=text)
    assert (f.holdings[0].cusip, f.holdings[1].issuer) == ("01609W102", "Bank of America Corp")


def test_an_option_keeps_its_put_or_call_and_a_principal_amount_its_type():
    text = _text(DJCO).replace(b"</shrsOrPrnAmt>", b"</shrsOrPrnAmt>\n<putCall>Call</putCall>", 1)
    text = text.replace(b"<sshPrnamtType>SH</sshPrnamtType>", b"<sshPrnamtType>PRN</sshPrnamtType>", 1)
    first, second = _parse(text=text).holdings[:2]
    assert (first.put_call, first.share_type) == ("Call", "PRN")
    assert (second.put_call, second.share_type) == (None, "SH")


def test_a_holdings_report_without_its_information_table_is_refused():
    text = _text(DJCO)
    second = text.index(b"<DOCUMENT>", text.index(b"<DOCUMENT>") + 1)
    with pytest.raises(ThirteenFError, match="INFORMATION TABLE"):
        parse_13f(text[:second] + text[text.index(b"</SEC-DOCUMENT>"):], "x")


def test_a_text_that_isnt_a_readable_13f_is_refused():
    with pytest.raises(ThirteenFError):
        parse_13f(b"<html><body>moved</body></html>", "x")  # no cover
    with pytest.raises(ThirteenFError):
        parse_13f(_text(DJCO).replace(b"</edgarSubmission>", b""), "x")  # the cover cut short
    with pytest.raises(ThirteenFError):
        parse_13f(_text(DJCO).replace(b"</informationTable>", b""), "x")  # the table cut short
    with pytest.raises(ThirteenFError, match="filing date"):
        parse_13f(_text(DJCO).replace(b"FILED AS OF DATE:", b""), "x")  # the values' units unknown

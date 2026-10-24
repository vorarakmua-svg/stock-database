"""The Form 4 parser (Milestone 3): real SEC ownership documents, read from their XML
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
from pathlib import Path

import pytest

from src.ownership.form4 import Form4Error, parse_form4

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ownership"


def _parse(name, accession="0000000000-26-000001"):
    return parse_form4((FIXTURES / name).read_bytes(), accession)


def test_a_directors_open_market_purchase():
    f = _parse("pfe_director_purchase.xml", "0001225208-26-006944")
    assert (f.accession, f.form, f.period_of_report, f.issuer_cik, f.issuer_ticker) == (
        "0001225208-26-006944", "4", "2026-08-05", "0000078003", "PFE")
    (owner,) = f.owners
    assert (owner.cik, owner.name, owner.is_director, owner.is_officer, owner.officer_title) == (
        "0001214686", "BLAYLOCK RONALD E", True, False, None)
    (trade,) = f.trades  # the trust's holding row records no transaction
    assert (trade.table, trade.date, trade.code, trade.shares, trade.price, trade.acquired_disposed,
            trade.owned_after, trade.direct_indirect) == (
        "nonderivative", "2026-08-05", "P", 39231.0, 25.46, "A", 66938.0, "D")
    assert f.aff10b5one is False


def test_a_ceo_buying_through_a_trust():
    f = _parse("intc_ceo_purchase.xml")
    (owner,) = f.owners
    assert (owner.is_director, owner.is_officer, owner.officer_title) == (True, True, "CEO")
    (trade,) = f.trades
    assert (trade.code, trade.shares, trade.price, trade.direct_indirect) == ("P", 105263.0, 95.0, "I")


def test_a_sale_under_a_10b5_1_plan_with_true_and_false_flags():
    f = _parse("aapl_planned_sale.xml")
    assert f.aff10b5one is True
    (owner,) = f.owners
    assert (owner.is_director, owner.is_officer, owner.is_ten_percent) == (False, True, False)
    assert owner.officer_title == "SVP, GC and Government Affairs"
    (trade,) = f.trades
    assert (trade.code, trade.shares, trade.price, trade.acquired_disposed, trade.owned_after) == (
        "S", 2399.0, 336.18, "D", 41992.0)


def test_an_exercise_its_withholding_the_sales_a_gift_and_the_derivative_row():
    f = _parse("aapl_exercise_sale_gift.xml")
    assert [(t.table, t.code) for t in f.trades] == [("nonderivative", c) for c in "MFSSSSG"] + [("derivative", "M")]
    assert [t.seq for t in f.trades] == list(range(8))
    exercise = f.trades[0]
    assert exercise.price is None and exercise.price_footnotes == ["F1"]  # an RSU: its price is a footnote
    assert f.trades[6].price == 0.0  # the gift
    derivative = f.trades[-1]
    assert (derivative.security_title, derivative.shares, derivative.exercise_price, derivative.underlying_title,
            derivative.underlying_shares, derivative.owned_after) == (
        "Restricted Stock Unit", 374541.0, None, "Common Stock", 374541.0, 0.0)


def test_a_gift_by_an_officer_with_one_and_zero_flags():
    f = _parse("ba_gift.xml")
    (owner,) = f.owners
    assert (owner.is_director, owner.is_officer, owner.officer_title) == (False, True, "EVP, Pres. & CEO, BGS")
    (trade,) = f.trades
    assert (trade.code, trade.shares, trade.price, trade.owned_after) == ("G", 907.0, 0.0, 41450.495)


def test_an_amendment_names_its_original_filing_day():
    f = _parse("pg_amendment.xml")
    assert (f.form, f.period_of_report, f.original_filed) == ("4/A", "2026-08-20", "2026-08-24")
    assert [t.code for t in f.trades] == ["S"]


def test_a_document_with_two_reporting_owners_keeps_both():
    # Joint filers (a fund and its manager) file one Form 4 together.
    xml = (FIXTURES / "pfe_director_purchase.xml").read_bytes().replace(
        b"</reportingOwner>",
        b"</reportingOwner><reportingOwner><reportingOwnerId><rptOwnerCik>0000000002</rptOwnerCik>"
        b"<rptOwnerName>SECOND OWNER</rptOwnerName></reportingOwnerId><reportingOwnerRelationship>"
        b"<isTenPercentOwner>1</isTenPercentOwner></reportingOwnerRelationship></reportingOwner>", 1)
    f = parse_form4(xml, "x")
    assert [(o.cik, o.is_ten_percent) for o in f.owners] == [("0001214686", False), ("0000000002", True)]


def test_a_company_reporting_as_another_issuers_owner_names_that_issuer():
    # Pfizer's own Form 4 as Cerevel's former 10% owner, listed in Pfizer's submissions feed (0000078003-24-000169).
    f = _parse("pfe_owner_of_cerevel.xml", "0000078003-24-000169")
    assert (f.issuer_cik, f.issuer_ticker) == ("0001805387", "CERE")
    (owner,) = f.owners
    assert (owner.cik, owner.name, owner.is_other, owner.other_text) == (
        "0000078003", "PFIZER INC", True, "Former 10% Owner")
    (trade,) = f.trades
    assert (trade.code, trade.shares, trade.price, trade.owned_after) == ("U", 27349211.0, 45.0, 0.0)


def test_an_amendment_filed_to_add_an_omitted_trade():
    # PFE's 4/A of 2025-03-03 adds a withholding of 2025-02-24 that the Form 4 of 2025-02-25 left out.
    original = _parse("pfe_sar_earnout.xml", "0001225208-25-002397")
    assert (original.form, original.period_of_report, original.original_filed) == ("4", "2025-02-22", None)
    assert [(t.table, t.code, t.date, t.shares, t.price) for t in original.trades] == [
        ("nonderivative", "M", "2025-02-22", 8741.0, 30.17), ("nonderivative", "F", "2025-02-22", 340.0, 26.3),
        ("nonderivative", "F", "2025-02-22", 7798.0, 26.08), ("nonderivative", "F", "2025-02-23", 662.0, 26.3),
        ("derivative", "M", "2025-02-22", 8741.0, 0.0)]
    amendment = _parse("pfe_amendment_adding_a_trade.xml", "0001225208-25-002701")
    assert (amendment.form, amendment.period_of_report, amendment.original_filed) == ("4/A", "2025-02-22",
                                                                                      "2025-02-25")
    assert [(t.code, t.date, t.shares, t.price) for t in amendment.trades] == [("F", "2025-02-24", 403.0, 26.65)]


def test_whitespace_around_the_document_is_ignored():
    xml = (FIXTURES / "pfe_director_purchase.xml").read_bytes()
    f = parse_form4(b"\r\n  \n" + xml + b"\n\n", "x")
    assert (f.issuer_ticker, [t.code for t in f.trades]) == ("PFE", ["P"])


def test_a_document_that_isnt_a_readable_form_4_is_refused():
    with pytest.raises(Form4Error):
        parse_form4(b"<html><body>not an ownership document</body></html>", "x")
    with pytest.raises(Form4Error):
        parse_form4(b"<ownershipDocument><documentType>4", "x")  # cut short

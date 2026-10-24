"""Insider aggregates (Milestone 3): open-market buying and selling over 3, 6 and 12 months, CEO and CFO buys,
cluster buys and planned sales, from the stored Form 4 trades, and the trades each 4/A restates
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
import pytest

from src.ownership.insiders import CODE_MEANINGS, ceo_or_cfo, price_suspect, summarize, supersessions

TODAY = "2026-10-22"


def _trade(date, code, shares, price, owner="A", title=None, plan=None, kind="nonderivative", suspect=None):
    return {"date": date, "code": code, "shares": shares, "price": price, "owner_cik": owner, "owner_name": owner,
            "officer_title": title, "is_ceo_cfo": ceo_or_cfo(title), "aff10b5one": plan, "kind": kind,
            "price_suspect": suspect}


@pytest.mark.parametrize("title, expected", [
    ("CEO", True), ("Chairman & CEO", True), ("President and Chief Executive Officer", True), ("EVP, CFO", True),
    ("Senior Vice President,  CFO", True), ("CFO & Treasurer", True), ("Chief Financial Officer", True),
    ("EVP, Pres. & CEO, BGS", False), ("CEO of Payments", False), ("COO", False), ("SVP & Controller", False),
    (None, False),
])
def test_ceo_or_cfo_names_the_companys_chief_not_a_units(title, expected):
    assert ceo_or_cfo(title) is expected


def test_the_windows_count_open_market_purchases_and_sales_only():
    s = summarize([
        _trade("2026-10-01", "P", 1000, 20.0, owner="A"),
        _trade("2026-06-01", "P", 500, 10.0, owner="B"),
        _trade("2026-09-15", "S", 300, 30.0, owner="C"),
        _trade("2026-09-20", "A", 9999, 0.0, owner="D"),  # a grant: no open-market trade
        _trade("2026-09-20", "M", 50, 5.0, owner="E", kind="derivative"),
        _trade("2025-01-01", "P", 7000, 10.0, owner="F"),  # older than a year
    ], TODAY)
    w3, w12 = s["windows"]["3m"], s["windows"]["12m"]
    assert (w3["bought_shares"], w3["bought_value"], w3["sold_shares"], w3["sold_value"], w3["net_value"]) == (
        1000, 20000.0, 300, 9000.0, 11000.0)
    assert (w3["buyers"], w3["sellers"]) == (1, 1)
    assert (w12["bought_shares"], w12["bought_value"], w12["buyers"], w12["net_value"]) == (1500, 25000.0, 2, 16000.0)


def test_a_trade_without_a_price_counts_its_shares_but_no_value():
    w = summarize([_trade("2026-10-01", "P", 100, None)], TODAY)["windows"]["12m"]
    assert (w["bought_shares"], w["bought_value"]) == (100, 0.0)


def test_ceo_and_cfo_buys_are_listed():
    s = summarize([
        _trade("2026-08-11", "P", 105263, 95.0, owner="TAN", title="CEO"),
        _trade("2026-08-12", "P", 100, 90.0, owner="UNIT", title="EVP, Pres. & CEO, BGS"),
        _trade("2026-08-13", "S", 100, 90.0, owner="CFO", title="EVP, CFO"),
    ], TODAY)
    assert [(b["owner_name"], b["shares"], b["value"]) for b in s["ceo_cfo_buys_12m"]] == [
        ("TAN", 105263, 105263 * 95.0)]


def test_three_insiders_buying_within_30_days_make_a_cluster():
    s = summarize([
        _trade("2026-03-01", "P", 100, 10.0, owner="A"),
        _trade("2026-03-10", "P", 100, 10.0, owner="B"),
        _trade("2026-03-25", "P", 100, 10.0, owner="C"),
        _trade("2026-03-26", "P", 100, 10.0, owner="A"),  # the same insider again
        _trade("2026-07-01", "P", 100, 10.0, owner="D"),  # two buyers later: no cluster
        _trade("2026-07-20", "P", 100, 10.0, owner="E"),
    ], TODAY)
    assert [(c["start"], c["end"], c["buyers"], c["value"]) for c in s["clusters_12m"]] == [
        ("2026-03-01", "2026-03-26", 3, 4000.0)]


def test_buyers_spread_over_more_than_30_days_are_no_cluster():
    s = summarize([_trade("2026-03-01", "P", 1, 1.0, owner="A"), _trade("2026-03-20", "P", 1, 1.0, owner="B"),
                   _trade("2026-04-05", "P", 1, 1.0, owner="C")], TODAY)
    assert s["clusters_12m"] == []


def test_the_share_of_sales_under_a_plan_counts_flagged_filings_only():
    s = summarize([
        _trade("2026-09-01", "S", 300, 10.0, owner="A", plan=1),
        _trade("2026-09-02", "S", 100, 10.0, owner="B", plan=0),
        _trade("2026-09-03", "S", 600, 10.0, owner="C", plan=None),  # an older form without the flag
    ], TODAY)
    assert s["planned_sale_share_12m"] == pytest.approx(0.75)  # 300 of the 400 shares whose filings say
    assert summarize([_trade("2026-09-03", "S", 600, 10.0, plan=None)], TODAY)["planned_sale_share_12m"] is None


@pytest.mark.parametrize("price, close, suspect", [
    (1031414.0, 1025.28, True),  # the Lilly Endowment's 0000316011-25-000073, 2025-11-14: $1,031.414 less its point
    (1032319.0, 1025.28, True),  # the same filing's next sale
    (878401.0, 881.40, True),  # 0001415889-25-005456, 2025-02-24
    (1030.312, 1025.28, False),  # the Endowment's same-day sale, its price as it should be
    (775883.3, 510.08, False),  # BRK-B's 0000919574-25-001778: 2 Class A shares, 1,521 times the B close, a real price
    (1.031414, 1025.28, True),  # a thousandth of it: the point moved the other way
    (1125000.0, 1025.28, True),  # 1,000 times $1,125: within 10% of the close
    (1135000.0, 1025.28, False),  # 1,000 times $1,135: over 10% off
    (0.0, 1025.28, False),  # a gift's price
    (None, 1025.28, None),  # no price: nothing to check
    (1031414.0, None, None),  # no close on or before the trade's date
])
def test_a_price_1000_times_or_a_thousandth_of_the_days_close_lost_its_decimal_point(price, close, suspect):
    assert price_suspect(price, close) is suspect


def test_a_suspect_price_counts_in_shares_only():
    # BUG-220: the Endowment's 55,908 shares sold "at $1,031,414" count as shares, not as $57.7B.
    s = summarize([
        _trade("2026-09-01", "S", 55908, 1031414.0, owner="E", suspect=1),
        _trade("2026-09-01", "S", 949, 1033.155, owner="E", suspect=0),
        _trade("2026-09-02", "P", 100, 1000.0, owner="F"),  # no close to check it against: as filed
    ], TODAY)
    w = s["windows"]["12m"]
    assert (w["sold_shares"], w["bought_shares"]) == (56857, 100)
    assert w["sold_value"] == pytest.approx(949 * 1033.155)
    assert w["net_value"] == pytest.approx(100 * 1000.0 - 949 * 1033.155)


def test_every_code_has_a_meaning():
    assert {"P", "S", "A", "M", "F", "G", "D", "C", "X", "I", "J", "U"} <= set(CODE_MEANINGS)


# ---- amendments: the filing a 4/A amends, and the trades it restates ----------------------------------------------

def _filing_row(accession, filed, form="4", owner="1", period="2026-08-20", original=None):
    return {"accession": accession, "owner_cik": owner, "form": form, "filed_date": filed,
            "period_of_report": period, "original_filed": original}


def _trade_row(accession, seq=0, code="S", day="2026-08-20", shares=100.0, price=10.0, kind="nonderivative",
               title="Common Stock"):
    return {"accession": accession, "seq": seq, "kind": kind, "code": code, "security_title": title, "date": day,
            "shares": shares, "price": price}


#: A Form 4 filed on 2026-08-24 and its 4/A, filed on 2026-08-27 and naming that day as the original's.
ORIGINAL = _filing_row("O", "2026-08-24")
AMENDMENT = _filing_row("A", "2026-08-27", form="4/A", original="2026-08-24")


@pytest.mark.parametrize("restating, restated", [
    (_trade_row("A"), True),  # the same trade again
    (_trade_row("A", code="P"), True),  # the same but for its code: a code corrected (a purchase filed as a sale)
    (_trade_row("A", day="2026-08-19"), True),  # its date corrected: the same shares and price
    (_trade_row("A", shares=90.0), True),  # its shares corrected: the same date
    (_trade_row("A", price=11.0), True),  # its price corrected
    (_trade_row("A", title="common  stock"), True),  # the title's case and spacing aside
    (_trade_row("A", day="2026-08-21", shares=90.0), False),  # another day, other shares: a trade added
    (_trade_row("A", code="P", shares=90.0), False),  # another code and other shares: a trade added
    (_trade_row("A", code="P", day="2026-08-19"), False),  # another code and another day: a trade added
    (_trade_row("A", kind="derivative"), False),  # the other table
    (_trade_row("A", title="Series A Preferred Stock"), False),  # another security
])
def test_an_amendment_restates_a_trade_of_the_same_kind_code_and_security_on_its_day_or_in_its_amount(
        restating, restated):
    amended_by, superseded = supersessions([ORIGINAL, AMENDMENT], [_trade_row("O"), restating])
    assert amended_by == {"O": "A"}  # the filing-level note, restated or not
    assert superseded == ({("O", 0): "A"} if restated else {})


def test_trades_are_matched_one_for_one():
    # Two lots sold the same day at the same price; the amendment restates one: the other stands.
    _, superseded = supersessions([ORIGINAL, AMENDMENT], [_trade_row("O", 0), _trade_row("O", 1), _trade_row("A")])
    assert superseded == {("O", 0): "A"}
    # Each of the amendment's trades restates one: the second, its date corrected, finds the lot left.
    _, superseded = supersessions([ORIGINAL, AMENDMENT], [
        _trade_row("O", 0), _trade_row("O", 1, day="2026-08-21"),
        _trade_row("A", 0), _trade_row("A", 1, day="2026-08-22")])
    assert superseded == {("O", 0): "A", ("O", 1): "A"}


def test_the_same_trade_first_then_the_same_amount_then_the_same_day():
    # The 02-22 withholding of 200 shares is the 02-23 one with its date corrected, not the 02-22 one of 100.
    _, superseded = supersessions([ORIGINAL, AMENDMENT], [
        _trade_row("O", 0, code="F", day="2025-02-22", shares=100.0), _trade_row("O", 1, code="F", day="2025-02-23",
                                                                                 shares=200.0),
        _trade_row("A", 0, code="F", day="2025-02-22", shares=200.0)])
    assert superseded == {("O", 1): "A"}
    # An exact restatement claims its trade before another trade's corrected amount can.
    _, superseded = supersessions([ORIGINAL, AMENDMENT], [
        _trade_row("O", 0, day="2026-08-20", shares=100.0), _trade_row("O", 1, day="2026-08-21", shares=50.0),
        _trade_row("A", 0, day="2026-08-21", shares=100.0), _trade_row("A", 1, day="2026-08-20", shares=100.0)])
    assert superseded == {("O", 0): "A", ("O", 1): "A"}


def test_a_code_correction_comes_right_after_the_exact_matches():
    # A sale and a purchase of 100 at $10 on 08-20: the 4/A's purchase is the purchase again, not the sale corrected.
    _, superseded = supersessions([ORIGINAL, AMENDMENT], [
        _trade_row("O", 0, code="S"), _trade_row("O", 1, code="P"), _trade_row("A", 0, code="P")])
    assert superseded == {("O", 1): "A"}
    # A sale on 08-20 and a purchase on 08-21, alike otherwise: the 4/A's purchase on 08-20 is the sale with its code
    # corrected, before it could be the purchase with its date corrected.
    _, superseded = supersessions([ORIGINAL, AMENDMENT], [
        _trade_row("O", 0, code="S"), _trade_row("O", 1, code="P", day="2026-08-21"), _trade_row("A", 0, code="P")])
    assert superseded == {("O", 0): "A"}


def test_only_the_same_owners_filing_is_amended():
    # Two officers' tax withholdings filed the same day look alike; the 4/A is the first officer's.
    filings = [_filing_row("O-1", "2026-08-24", owner="1"), _filing_row("O-2", "2026-08-24", owner="2"),
               _filing_row("A", "2026-08-27", form="4/A", owner="1", original="2026-08-24")]
    amended_by, superseded = supersessions(filings, [_trade_row("O-1"), _trade_row("O-2"), _trade_row("A")])
    assert (amended_by, superseded) == ({"O-1": "A"}, {("O-1", 0): "A"})


def test_an_amendment_amends_the_filing_of_its_original_filing_day_only():
    # The original of 2026-08-24 isn't stored (before the window, say): a later Form 4 isn't what the 4/A amends.
    filings = [_filing_row("O-2", "2026-08-25"), AMENDMENT]
    assert supersessions(filings, [_trade_row("O-2"), _trade_row("A")]) == ({}, {})


def test_the_period_of_report_chooses_between_the_days_filings():
    # Two Form 4s by one owner on 2026-08-24, for 08-18 and 08-20: the 4/A is for 08-20.
    filings = [_filing_row("O-2", "2026-08-24", period="2026-08-20"),
               _filing_row("O-9", "2026-08-24", period="2026-08-18"),
               _filing_row("A", "2026-08-27", form="4/A", original="2026-08-24", period="2026-08-20")]
    trades = [_trade_row("O-2", day="2026-08-20"), _trade_row("O-9", day="2026-08-18"),
              _trade_row("A", day="2026-08-20")]
    assert supersessions(filings, trades) == ({"O-2": "A"}, {("O-2", 0): "A"})


def test_a_4a_of_a_4a_restates_the_first_amendments_trade():
    filings = [ORIGINAL, AMENDMENT, _filing_row("A-2", "2026-09-02", form="4/A", original="2026-08-27")]
    trades = [_trade_row("O", shares=100.0), _trade_row("A", shares=90.0), _trade_row("A-2", shares=95.0)]
    assert supersessions(filings, trades) == ({"O": "A", "A": "A-2"}, {("O", 0): "A", ("A", 0): "A-2"})


def test_a_second_amendment_of_the_same_original_restates_the_first_ones_trade():
    # Both 4/As name the original's filing day; the second restates the trade the first one restated.
    filings = [ORIGINAL, AMENDMENT, _filing_row("A-2", "2026-09-02", form="4/A", original="2026-08-24")]
    trades = [_trade_row("O", shares=100.0), _trade_row("A", shares=90.0), _trade_row("A-2", shares=95.0)]
    assert supersessions(filings, trades) == ({"O": "A-2"}, {("O", 0): "A", ("A", 0): "A-2"})


def test_an_amendment_filed_the_day_of_its_original_amends_it():
    filings = [_filing_row("Z-0", "2026-08-24"), _filing_row("A-0", "2026-08-24", form="4/A", original="2026-08-24")]
    assert supersessions(filings, [_trade_row("Z-0"), _trade_row("A-0", shares=90.0)]) == (
        {"Z-0": "A-0"}, {("Z-0", 0): "A-0"})

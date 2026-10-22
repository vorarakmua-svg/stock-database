"""Insider aggregates (Milestone 3): open-market buying and selling over 3, 6 and 12 months, CEO and CFO buys,
cluster buys and planned sales, from the stored Form 4 trades
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
import pytest

from src.ownership.insiders import CODE_MEANINGS, ceo_or_cfo, summarize

TODAY = "2026-10-22"


def _trade(date, code, shares, price, owner="A", title=None, plan=None, kind="nonderivative"):
    return {"date": date, "code": code, "shares": shares, "price": price, "owner_cik": owner, "owner_name": owner,
            "officer_title": title, "is_ceo_cfo": ceo_or_cfo(title), "aff10b5one": plan, "kind": kind}


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


def test_every_code_has_a_meaning():
    assert {"P", "S", "A", "M", "F", "G", "D", "C", "X", "I", "J", "U"} <= set(CODE_MEANINGS)

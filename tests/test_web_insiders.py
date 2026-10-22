"""Insiders from the SEC's Form 4 filings on the web (Milestone 3): GET /api/stocks/{ticker}/form4, the INS tab
and the screener's insider fields (docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
import sqlite3
from datetime import date, timedelta

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.ownership.collect import FilingRef
from src.ownership.form4 import Form4, Owner, Trade
from src.webapp.formatting import fmt_money
from src.webapp.repository import Reader
from src.webapp.screener import INSIDER_EXPRS, SCREEN_COLUMNS, MetricFilter, ScreenSpec


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def _filing(accession, filed, owner, title, code, shares, price, when, kind="nonderivative", form="4",
            original=None, plan=None, director=False):
    trade = Trade(table=kind, seq=0, security_title="Common Stock", date=when, code=code, shares=shares, price=price,
                  acquired_disposed="A" if code in ("P", "A") else "D", owned_after=10000.0, direct_indirect="D")
    filing = Form4(accession=accession, form=form, period_of_report=when, original_filed=original,
                   issuer_ticker="AAA", aff10b5one=plan,
                   owners=[Owner(cik=owner, name=f"INSIDER {owner}", is_director=director,
                                 is_officer=title is not None, officer_title=title)],
                   trades=[trade])
    return FilingRef(accession, form, filed, when, "doc.xml"), filing


def _seed(db):
    SQLiteStore(db_path=db).export_insider_filings("AAA", [
        _filing("A-1", _ago(18), "1", "CEO", "P", 1000.0, 50.0, _ago(20)),
        _filing("A-2", _ago(23), "2", None, "P", 500.0, 52.0, _ago(25), director=True),
        _filing("A-3", _ago(28), "3", None, "P", 200.0, 51.0, _ago(30), director=True),
        _filing("A-4", _ago(8), "4", "EVP, CFO", "S", 300.0, 55.0, _ago(10), plan=1),
        _filing("A-5", _ago(38), "4", "EVP, CFO", "S", 999.0, 55.0, _ago(40)),  # amended by A-6
        _filing("A-6", _ago(35), "4", "EVP, CFO", "S", 99.0, 55.0, _ago(40), form="4/A", original=_ago(38)),
        _filing("A-7", _ago(13), "5", "SVP", "A", 10000.0, None, _ago(15), kind="derivative"),
        # A purchase its amendment corrects into a sale: the original counts nowhere.
        _filing("A-8", _ago(48), "7", None, "P", 50.0, 50.0, _ago(50), director=True),
        _filing("A-9", _ago(45), "7", None, "S", 1.0, 55.0, _ago(50), form="4/A", original=_ago(48), director=True),
    ], "2026-10-22T00:00:00+00:00")


def test_the_endpoint_gives_the_sec_trades_and_their_aggregates(client, web_db):
    _seed(web_db)
    body = client.get("/api/stocks/AAA/form4").json()
    assert body["source"] == "sec"
    w = body["summary"]["windows"]["12m"]
    assert (w["bought_shares"], w["bought_value"], w["buyers"]) == (1700.0, 86200.0, 3)
    assert (w["sold_shares"], w["sold_value"], w["sellers"]) == (400.0, 22000.0, 2)  # A-6 and A-9 replaced theirs
    assert [b["owner_name"] for b in body["summary"]["ceo_cfo_buys_12m"]] == ["INSIDER 1"]
    assert [c["buyers"] for c in body["summary"]["clusters_12m"]] == [3]
    assert body["summary"]["planned_sale_share_12m"] == pytest.approx(300.0 / 300.0)
    trades = body["trades"]
    assert [t["accession"] for t in trades] == ["A-4", "A-7", "A-1", "A-2", "A-3", "A-6", "A-9"]  # newest first
    first = trades[0]
    assert (first["code"], first["meaning"], first["owner_name"], first["officer_title"], first["plan"]) == (
        "S", "open-market sale", "INSIDER 4", "EVP, CFO", True)


def test_without_sec_filings_the_endpoint_falls_back_to_yahoo(client):
    body = client.get("/api/stocks/AAA/form4").json()
    assert body["source"] == "yahoo" and body["summary"] is None and body["trades"]  # web_db's 3 Yahoo rows


def test_an_unknown_ticker_is_404(client):
    assert client.get("/api/stocks/ZZZZ/form4").status_code == 404


def test_the_ins_tab_shows_the_sec_trades_and_aggregates(client, web_db):
    _seed(web_db)
    html = client.get("/ui/stocks/AAA/ins").text
    assert "SEC Form 4" in html and "open-market purchase" in html and "INSIDER 1" in html
    assert "Cluster buy" in html and "CEO/CFO buys" in html
    assert fmt_money(86200.0) in html  # the year's open-market buying


def test_the_screener_reads_the_insider_fields(web_db):
    _seed(web_db)
    assert set(INSIDER_EXPRS) <= set(SCREEN_COLUMNS)
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[]))["items"]}
        aaa = items["AAA"]
        assert aaa["ins_net_buy_12m"] == pytest.approx(86200.0 - 22000.0)
        assert (aaa["ins_buyers_12m"], aaa["ins_ceo_cfo_buys_12m"], aaa["ins_cluster_buyers_12m"]) == (3, 1, 3)
        assert items["EEE"]["ins_buyers_12m"] == 0
        cluster = r.screen(ScreenSpec(filters=[MetricFilter(field="ins_cluster_buyers_12m", op="gte", value=3)]))
    assert [row["ticker"] for row in cluster["items"]] == ["AAA"]


def test_a_database_without_the_insider_tables_still_screens(web_db):
    conn = sqlite3.connect(web_db)
    conn.execute("DROP TABLE IF EXISTS insider_trades")
    conn.execute("DROP TABLE IF EXISTS insider_filings")
    conn.commit()
    conn.close()
    with Reader(web_db) as r:
        result = r.screen(ScreenSpec(filters=[], sort="ins_net_buy_12m"))
        assert all(row["ins_net_buy_12m"] is None for row in result["items"])

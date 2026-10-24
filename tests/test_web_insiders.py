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
            original=None, plan=None, director=False, period=None):
    trade = Trade(table=kind, seq=0, security_title="Common Stock", date=when, code=code, shares=shares, price=price,
                  acquired_disposed="A" if code in ("P", "A") else "D", owned_after=10000.0, direct_indirect="D")
    filing = Form4(accession=accession, form=form, period_of_report=period or when, original_filed=original,
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
        # A purchase its amendment corrects into a sale (the same day, shares and price: its code alone corrected):
        # the purchase counts nowhere, the sale stands.
        _filing("A-8", _ago(48), "7", None, "P", 1.0, 55.0, _ago(50), director=True),
        _filing("A-9", _ago(45), "7", None, "S", 1.0, 55.0, _ago(50), form="4/A", original=_ago(48), director=True),
    ], "2026-10-22T00:00:00+00:00")
    # A purchase its amendment restates, its date corrected to before the year: nothing of EEE's within it.
    SQLiteStore(db_path=db).export_insider_filings("EEE", [
        _filing("E-1", _ago(18), "8", None, "P", 10.0, 5.0, _ago(20), director=True),
        _filing("E-2", _ago(15), "8", None, "P", 10.0, 5.0, _ago(400), form="4/A", original=_ago(18), director=True),
    ], "2026-10-22T00:00:00+00:00")


def test_the_endpoint_gives_the_sec_trades_and_their_aggregates(client, web_db):
    _seed(web_db)
    body = client.get("/api/stocks/AAA/form4").json()
    assert body["source"] == "sec"
    w = body["summary"]["windows"]["12m"]
    assert (w["bought_shares"], w["bought_value"], w["buyers"]) == (1700.0, 86200.0, 3)  # A-9 corrected A-8's code
    assert (w["sold_shares"], w["sold_value"], w["sellers"]) == (400.0, 22000.0, 2)  # A-6 restated A-5's 999
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
    assert html.count("(4/A)") == 2  # A-6's and A-9's rows
    assert "a 4/A replaces only the trades it restates" in " ".join(html.split())


def test_the_screener_reads_the_insider_fields(web_db):
    _seed(web_db)
    assert set(INSIDER_EXPRS) <= set(SCREEN_COLUMNS)
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[]))["items"]}
        aaa = items["AAA"]
        assert aaa["ins_net_buy_12m"] == pytest.approx(86200.0 - 22000.0)
        assert (aaa["ins_buyers_12m"], aaa["ins_ceo_cfo_buys_12m"], aaa["ins_cluster_buyers_12m"]) == (3, 1, 3)
        eee = items["EEE"]  # its one purchase in the year restated: it counts nowhere, nor anchors a cluster
        assert (eee["ins_net_buy_12m"], eee["ins_buyers_12m"], eee["ins_cluster_buyers_12m"]) == (None, 0, None)
        cluster = r.screen(ScreenSpec(filters=[MetricFilter(field="ins_cluster_buyers_12m", op="gte", value=3)]))
    assert [row["ticker"] for row in cluster["items"]] == ["AAA"]


def test_an_amendment_adding_a_trade_leaves_its_originals_purchase_counted(client, web_db):
    # As PFE's real 4/As: an amendment for the original's period adding a trade (a sale a day later, of other shares)
    # restates nothing, so the purchase of the filing it amends counts in the aggregates and the screener.
    SQLiteStore(db_path=web_db).export_insider_filings("AAA", [
        _filing("C-1", _ago(28), "9", None, "P", 100.0, 20.0, _ago(30), director=True),
        _filing("C-2", _ago(25), "9", None, "S", 10.0, 30.0, _ago(29), form="4/A", original=_ago(28), director=True,
                period=_ago(30)),
    ], "2026-10-22T00:00:00+00:00")
    w = client.get("/api/stocks/AAA/form4").json()["summary"]["windows"]["12m"]
    assert (w["bought_shares"], w["buyers"], w["sold_shares"], w["sellers"]) == (100.0, 1, 10.0, 1)
    with Reader(web_db) as r:
        aaa = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[]))["items"]}["AAA"]
    assert aaa["ins_net_buy_12m"] == pytest.approx(100 * 20.0 - 10 * 30.0)
    assert (aaa["ins_buyers_12m"], aaa["ins_cluster_buyers_12m"]) == (1, 1)


def test_a_price_off_by_a_dropped_decimal_point_is_shown_as_filed_and_counts_in_shares_only(client, web_db):
    # BUG-220: a price 1,000 times, or a thousandth of, the close that day lost its decimal point. It shows as filed,
    # with a note, and every dollar figure leaves it out: its trade counts in shares only.
    conn = sqlite3.connect(web_db)
    (close,) = conn.execute("SELECT close FROM price_bars WHERE ticker = 'AAA' ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()
    SQLiteStore(db_path=web_db).export_insider_filings("AAA", [
        _filing("D-1", _ago(18), "11", None, "S", 100.0, close * 1000, _ago(20), director=True),
        _filing("D-2", _ago(13), "12", None, "S", 5.0, close / 1000, _ago(15), director=True),
        _filing("D-3", _ago(8), "13", None, "S", 10.0, close, _ago(10), director=True),
        # A derivative's price is its own: unchecked.
        _filing("D-4", _ago(8), "13", None, "M", 50.0, close / 1000, _ago(10), kind="derivative", director=True),
    ], "2026-10-22T00:00:00+00:00")
    body = client.get("/api/stocks/AAA/form4").json()
    w = body["summary"]["windows"]["12m"]
    assert (w["sold_shares"], w["sellers"]) == (115.0, 3)
    assert w["sold_value"] == pytest.approx(10 * close)
    rows = {t["accession"]: t for t in body["trades"]}
    assert (rows["D-1"]["price"], rows["D-1"]["value"], rows["D-1"]["price_suspect"]) == (close * 1000, None, True)
    assert rows["D-1"]["price_note"] == "as filed, about 1,000 times the close that day"
    assert rows["D-2"]["price_note"] == "as filed, about a thousandth of the close that day"
    assert (rows["D-3"]["value"], rows["D-3"]["price_suspect"], rows["D-3"]["price_note"]) == (
        pytest.approx(10 * close), False, None)
    assert (rows["D-4"]["value"], rows["D-4"]["price_suspect"], rows["D-4"]["price_note"]) == (
        pytest.approx(50 * close / 1000), None, None)
    with Reader(web_db) as r:
        aaa = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[]))["items"]}["AAA"]
    assert aaa["ins_net_buy_12m"] == pytest.approx(-10 * close)
    html = client.get("/ui/stocks/AAA/ins").text
    assert "as filed, about 1,000 times the close that day" in html
    assert "as filed, about a thousandth of the close that day" in html


def test_a_suspect_prices_note_without_the_close_names_no_direction(client, web_db):
    # Flagged when stored; the stock's closes gone since: the note says only that it is a decimal point off.
    conn = sqlite3.connect(web_db)
    (close,) = conn.execute("SELECT close FROM price_bars WHERE ticker = 'AAA' ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()
    SQLiteStore(db_path=web_db).export_insider_filings("AAA", [
        _filing("D-1", _ago(18), "11", None, "S", 100.0, close * 1000, _ago(20), director=True)],
        "2026-10-22T00:00:00+00:00")
    conn = sqlite3.connect(web_db)
    conn.execute("DELETE FROM price_bars WHERE ticker = 'AAA'")
    conn.commit()
    conn.close()
    (trade,) = client.get("/api/stocks/AAA/form4").json()["trades"]
    assert (trade["price_suspect"], trade["value"]) == (True, None)
    assert trade["price_note"] == "as filed, 1,000 times off the close that day"


@pytest.mark.parametrize("missing, source", [
    (("superseded_by", "price_suspect"), "yahoo"),  # before the trades' amendment note: no SEC trades read
    (("price_suspect",), "sec"),  # before the price check: the trades read, their prices as filed
])
def test_a_database_whose_trades_predate_their_newer_columns_still_screens(client, web_db, missing, source):
    # insider_trades as older code created it, with rows: no reader fails on it, and the insider fields are NULL.
    _seed(web_db)
    conn = sqlite3.connect(web_db)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(insider_trades)") if row[1] not in missing]
    conn.executescript(  # without DROP COLUMN, which older SQLite lacks
        f"CREATE TABLE old_trades AS SELECT {', '.join(columns)} FROM insider_trades; DROP TABLE insider_trades; "
        "ALTER TABLE old_trades RENAME TO insider_trades;")
    conn.close()
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[], sort="ins_net_buy_12m"))["items"]}
    assert all(items[t][f] is None for t in items for f in INSIDER_EXPRS)
    assert client.get("/api/stocks/AAA/form4").json()["source"] == source
    assert client.get("/ui/stocks/AAA/ins").status_code == 200


def test_a_database_without_the_insider_tables_still_screens(web_db):
    conn = sqlite3.connect(web_db)
    conn.execute("DROP TABLE IF EXISTS insider_trades")
    conn.execute("DROP TABLE IF EXISTS insider_filings")
    conn.commit()
    conn.close()
    with Reader(web_db) as r:
        result = r.screen(ScreenSpec(filters=[], sort="ins_net_buy_12m"))
        assert all(row["ins_net_buy_12m"] is None for row in result["items"])

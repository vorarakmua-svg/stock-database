"""The gurus' 13F holdings on the web (Milestone 3, M3-6): the views over the stored quarters and positions, the guru
pages, the HDS tab's Gurus section, the API and the screener's guru fields
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
import json
import sqlite3
from decimal import Decimal

import pytest

from src.exporters.sqlite_store import _METRIC_COLUMNS, SQLiteStore
from src.ownership.collect import FilingRef, store_guru_positions
from src.ownership.gurus import Guru
from src.ownership.thirteenf import Holding, ThirteenF
from src.webapp.gurus import guru_list, guru_portfolio, guru_view, gurus_holding, has_gurus
from src.webapp.repository import Reader
from src.webapp.screener import (
    GURU_EXPRS,
    HISTORY_EXPRS,
    INSIDER_EXPRS,
    METRIC_KINDS,
    QUALITY_EXPRS,
    SCREEN_COLUMNS,
    SNAPSHOT_SCREEN_COLUMNS,
    VALUATION_EXPRS,
    MetricFilter,
    ScreenSpec,
    build_screen_query,
)

Q1, Q2 = "2026-03-31", "2026-06-30"
NOW = "2026-10-22T00:00:00+00:00"
BUFFETT = Guru("1067983", "BERKSHIRE HATHAWAY INC", "Warren Buffett")
LI_LU = Guru("1709323", "Himalaya Capital Management LLC", "Li Lu")
ACKMAN = Guru("2026053", "PERSHING SQUARE INC.", "Bill Ackman",
              former=(("1336528", "Pershing Square Capital Management, L.P."),))
AAA, BBB, EEE, GOOGL, GOOG = "00000A101", "00000B101", "00000E101", "02079K305", "02079K107"
UNM, XYZ = "99999Z101", "88888Y101"
NAMES = {AAA: "AAA CORP", BBB: "BBB BANK", EEE: "EEE CORP", GOOGL: "ALPHABET INC CL A", GOOG: "ALPHABET INC CL C",
         UNM: "UNMAPPED CO", XYZ: "XYZ HOLDINGS"}


def _filing(accession, filer, period, filed, holdings, entry_total=None):
    rows = [Holding(row=i, cusip=h[0], issuer=NAMES[h[0]], title_of_class="COM", value=Decimal(h[2]),
                    value_usd=float(h[2]), shares=float(h[1]), share_type="SH", put_call=h[3] if len(h) > 3 else None)
            for i, h in enumerate(holdings)]
    parsed = ThirteenF(accession=accession, form="13F-HR", filed=filed, period=period,
                       entry_total=len(rows) if entry_total is None else entry_total,
                       value_total=sum((h.value for h in rows), Decimal(0)), holdings=rows)
    return FilingRef(accession, "13F-HR", filed, period, f"{accession}.txt", filer), parsed


def _seed(db):
    """Three gurus' Q1 and Q2 2026, through the collector's own steps.

    Berkshire adds AAA, buys GOOGL, GOOG and AAA calls (worth more than two of its share positions), sells out of BBB
    and XYZ. Li Lu's Q2 has no quarter before, and holds EEE only in calls.
    Pershing Square cuts AAA by half, buys an unmapped security, sells out of GOOG; its other filer's Q2 report fails
    its checksum."""
    store = SQLiteStore(db_path=db)
    store.export_guru_filings(BUFFETT, [
        _filing("B-Q1", "1067983", Q1, "2026-05-15", [(AAA, 100, 10000), (BBB, 50, 5000), (XYZ, 10, 1000)]),
        _filing("B-Q2", "1067983", Q2, "2026-08-14", [(AAA, 150, 18000), (GOOGL, 20, 4000), (GOOG, 10, 2000),
                                                      (AAA, 5, 3000, "Call")]),
    ], NOW)
    store.export_guru_filings(LI_LU, [
        _filing("H-Q2", "1709323", Q2, "2026-08-13", [(AAA, 30, 3600), (BBB, 10, 1000), (GOOG, 3, 600),
                                                      (EEE, 2, 200, "Call")]),
    ], NOW)
    store.export_guru_filings(ACKMAN, [
        _filing("P-Q1", "1336528", Q1, "2026-05-14", [(AAA, 40, 4000), (GOOG, 5, 1000)]),
        _filing("P-Q2", "2026053", Q2, "2026-08-12", [(AAA, 20, 2400), (UNM, 10, 5000)]),
        _filing("P-Q2-X", "1336528", Q2, "2026-08-12", [(AAA, 1, 1)], entry_total=9),
    ], NOW)
    store.export_cusip_map(
        [{"cusip": c, "ticker": t, "name": NAMES[c], "source": "universe", "mapped_at": NOW}
         for c, t in ((AAA, "AAA"), (BBB, "BBB"), (EEE, "EEE"), (GOOGL, "GOOGL"), (GOOG, "GOOG"))]
        + [{"cusip": c, "ticker": None, "name": NAMES[c], "source": "none", "mapped_at": NOW} for c in (UNM, XYZ)])
    store_guru_positions(db, [BUFFETT, LI_LU, ACKMAN])


def _add_alphabet(db):
    """GOOGL as one of our companies, with a fiscal year the screener reads."""
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO companies (ticker, company_name, sector_class) VALUES ('GOOGL', 'Alphabet Inc.', "
                 "'general')")
    conn.execute("INSERT INTO metrics_annual (ticker, fiscal_year, roic) VALUES ('GOOGL', 2024, 0.3)")
    conn.commit()
    conn.close()


def _drop_guru_tables(db):
    conn = sqlite3.connect(db)
    for table in ("guru_positions", "guru_quarters", "guru_filers", "guru_filings", "guru_holdings", "cusip_map"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    conn.close()


def _short(row):
    return (row["cik"], row.get("ticker"), row.get("put_call"), row.get("shares"), row.get("value"), row.get("action"))


# ---- the views ----------------------------------------------------------------------------------------------------


def test_the_list_gives_each_gurus_latest_quarter_largest_first(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        gurus = guru_list(r)
    assert [(g["cik"], g["manager"], g["period"], g["filed"], g["total_value"]) for g in gurus] == [
        ("1067983", "Warren Buffett", Q2, "2026-08-14", 27000.0), ("2026053", "Bill Ackman", Q2, "2026-08-12", 7400.0),
        ("1709323", "Li Lu", Q2, "2026-08-13", 5400.0)]
    # Positions held (shares and principal amounts; the calls apart), and the quarter's new and sold-out ones.
    assert [(g["positions"], g["new"], g["sold_out"], g["complete"], g["has_prior"]) for g in gurus] == [
        (3, 2, 2, True, True), (2, 1, 1, False, True), (3, None, None, True, False)]
    assert gurus[1]["filers"] == ["PERSHING SQUARE INC.", "Pershing Square Capital Management, L.P."]


def test_a_gurus_view_gives_its_holdings_and_the_quarters_changes(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        view = guru_view(r, "1067983")
        padded = guru_view(r, "0001067983")
    assert (view["cik"], view["manager"], view["filers"], view["period"], view["periods"]) == (
        "1067983", "Warren Buffett", ["BERKSHIRE HATHAWAY INC"], Q2, [Q2, Q1])
    assert (view["total_value"], view["filed"], view["complete"], view["has_prior"], view["accessions"]) == (
        27000.0, "2026-08-14", True, True, ["B-Q2"])
    assert [f["accession"] for f in view["filings"]] == ["B-Q2"] and view["filings"][0]["filer_cik"] == "1067983"
    # Largest first, the options after the shares.
    assert [(p["security"], p["put_call"], p["value"], p["ours"]) for p in view["positions"]] == [
        ("AAA", None, 18000.0, True), ("GOOGL", None, 4000.0, False), ("GOOG", None, 2000.0, False),
        ("AAA", "Call", 3000.0, True)]
    first = view["positions"][0]
    assert (first["weight"], first["action"], first["shares_before"], first["change_pct"], first["cusips"]) == (
        pytest.approx(18000 / 27000), "added", 100.0, 0.5, [AAA])
    changes = {action: [(p["security"], p["put_call"]) for p in rows] for action, rows in view["changes"].items()}
    assert changes == {"new": [("GOOGL", None), ("GOOG", None), ("AAA", "Call")], "added": [("AAA", None)],
                       "reduced": [], "sold_out": [("BBB", None), (XYZ, None)]}
    gone = view["changes"]["sold_out"][0]
    assert (gone["shares"], gone["value"], gone["shares_before"], gone["value_before"], gone["ours"]) == (
        None, None, 50.0, 5000.0, True)
    assert padded == view


def test_another_quarter_of_a_guru_and_one_without_the_quarter_before(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        q1 = guru_view(r, "1067983", Q1)
        ackman = guru_view(r, "2026053")
    assert (q1["period"], q1["has_prior"], q1["changes"]) == (Q1, False, None)
    assert [(p["security"], p["value"], p["action"]) for p in q1["positions"]] == [
        ("AAA", 10000.0, None), ("BBB", 5000.0, None), (XYZ, 1000.0, None)]
    assert ackman["complete"] is False and [p["security"] for p in ackman["positions"]] == [UNM, "AAA"]
    assert [p["security"] for p in ackman["changes"]["reduced"]] == ["AAA"]


def test_an_unknown_guru_or_quarter_has_no_view(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        assert guru_view(r, "9999999") is None
        assert guru_view(r, "1067983", "2020-12-31") is None
        assert guru_view(r, "Berkshire") is None


def test_the_gurus_holding_a_stock_held_first_then_options_then_sold_out(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        aaa = gurus_holding(r, "AAA")
        bbb = gurus_holding(r, "BBB")
        none = gurus_holding(r, "CCC")
        calls_only = gurus_holding(r, "EEE")
    assert [_short(h) for h in aaa] == [
        ("1067983", "AAA", None, 150.0, 18000.0, "added"), ("1709323", "AAA", None, 30.0, 3600.0, None),
        ("2026053", "AAA", None, 20.0, 2400.0, "reduced"), ("1067983", "AAA", "Call", 5.0, 3000.0, "new")]
    first = aaa[0]
    assert (first["manager"], first["period"], first["weight"], first["change_pct"]) == (
        "Warren Buffett", Q2, pytest.approx(18000 / 27000), 0.5)
    assert aaa[2]["change_pct"] == -0.5
    assert [_short(h) for h in bbb] == [("1709323", "BBB", None, 10.0, 1000.0, None),
                                        ("1067983", "BBB", None, None, None, "sold_out")]
    assert (bbb[1]["shares_before"], bbb[1]["value_before"]) == (50.0, 5000.0)
    assert none == []
    assert [_short(h) for h in calls_only] == [("1709323", "EEE", "Call", 2.0, 200.0, None)]


def test_the_gurus_holding_a_stock_hold_its_other_class_too(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        googl = gurus_holding(r, "GOOGL")
        goog = gurus_holding(r, "GOOG")
    assert [_short(h) for h in googl] == [
        ("1067983", "GOOGL", None, 20.0, 4000.0, "new"), ("1067983", "GOOG", None, 10.0, 2000.0, "new"),
        ("1709323", "GOOG", None, 3.0, 600.0, None), ("2026053", "GOOG", None, None, None, "sold_out")]
    assert goog == googl


def test_the_aggregated_portfolio(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        portfolio = guru_portfolio(r)
        top = guru_portfolio(r, limit=2)
    # Shares only, over each guru's latest quarter; sorted by the gurus holding, then the value. XYZ, sold out by its
    # one holder, is held by none.
    assert [(s["security"], s["ticker"], s["issuer"], s["ours"], s["gurus"], s["shares"], s["value"], s["buyers"],
             s["sellers"]) for s in portfolio] == [
        ("AAA", "AAA", "AAA CORP", True, 3, 200.0, 24000.0, 1, 1),
        ("GOOG", "GOOG", "ALPHABET INC CL C", False, 2, 13.0, 2600.0, 1, 1),
        (UNM, None, "UNMAPPED CO", False, 1, 10.0, 5000.0, 1, 0),
        ("GOOGL", "GOOGL", "ALPHABET INC CL A", False, 1, 20.0, 4000.0, 1, 0),
        ("BBB", "BBB", "BBB BANK", True, 1, 10.0, 1000.0, 0, 1)]
    assert top == portfolio[:2]


def test_the_views_without_the_guru_tables_or_their_rows(web_db):
    with Reader(web_db) as r:  # the tables, empty
        assert (guru_list(r), guru_view(r, "1067983"), gurus_holding(r, "AAA"), guru_portfolio(r), has_gurus(r)) == (
            [], None, [], [], False)
    _drop_guru_tables(web_db)
    with Reader(web_db) as r:  # a database from before them
        assert (guru_list(r), guru_view(r, "1067983"), gurus_holding(r, "AAA"), guru_portfolio(r), has_gurus(r)) == (
            [], None, [], [], False)


def test_has_gurus_once_a_quarter_is_stored(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        assert has_gurus(r) is True


# ---- the pages ----------------------------------------------------------------------------------------------------


def test_the_gurus_page_lists_them_with_a_note_on_the_data(client, web_db):
    _seed(web_db)
    response = client.get("/gurus")
    assert response.status_code == 200
    html = response.text
    assert html.index("Warren Buffett") < html.index("Bill Ackman") < html.index("Li Lu")
    assert 'href="/gurus/1067983"' in html and 'href="/gurus/2026053"' in html and 'href="/gurus/portfolio"' in html
    assert "$27.00K" in html and "Incomplete" in html and "Pershing Square Capital Management, L.P." in html
    assert "13F" in html and "45 days" in html and "long" in html


def test_the_gurus_page_without_holdings(client, web_db):
    _drop_guru_tables(web_db)
    response = client.get("/gurus")
    assert response.status_code == 200 and "No 13F holdings collected" in response.text


def test_a_gurus_page_shows_its_holdings_changes_and_filings(client, web_db):
    _seed(web_db)
    response = client.get("/gurus/1067983")
    assert response.status_code == 200
    html = response.text
    assert "Warren Buffett" in html and "BERKSHIRE HATHAWAY INC" in html
    assert 'href="/stocks/AAA"' in html and 'href="/stocks/BBB"' in html  # ours, BBB in the sold-out list
    assert 'href="/stocks/GOOG"' not in html and 'href="/stocks/GOOGL"' not in html  # not ours here
    assert "ALPHABET INC CL C" in html and "Call" in html and "+50.0%" in html and "66.7%" in html
    assert "Sold out" in html and "XYZ HOLDINGS" in html and "(50 shares, $5.00K)" in html  # what BBB held
    assert '<select name="period"' in html
    assert f'<option value="{Q2}" selected>' in html and f'<option value="{Q1}">' in html
    assert "B-Q2" in html and "No prior quarter" not in html and "Incomplete" not in html


def test_a_gurus_earlier_quarter_and_its_notices(client, web_db):
    _seed(web_db)
    q1 = client.get(f"/gurus/1067983?period={Q1}").text
    assert f'<option value="{Q1}" selected>' in q1 and "No prior quarter" in q1 and "$10.00K" in q1
    assert "Incomplete" in client.get("/gurus/2026053").text


def test_an_unknown_guru_or_quarter_is_404(client, web_db):
    _seed(web_db)
    assert client.get("/gurus/9999999").status_code == 404
    assert client.get("/gurus/1067983?period=2020-12-31").status_code == 404
    # A CIK has 10 digits at most: a longer run of digits is no guru's, not a number to read (Python refuses to
    # read one of over 4,300 digits).
    assert client.get("/gurus/" + "1" * 5000).status_code == 404
    assert client.get("/api/gurus/" + "1" * 5000).status_code == 404


def test_the_aggregated_portfolio_page(client, web_db):
    _seed(web_db)
    response = client.get("/gurus/portfolio")
    assert response.status_code == 200
    html = response.text
    order = [html.index(text) for text in ('href="/stocks/AAA"', "ALPHABET INC CL C", "UNMAPPED CO",
                                           "ALPHABET INC CL A", 'href="/stocks/BBB"')]
    assert order == sorted(order) and "XYZ HOLDINGS" not in html and "$24.00K" in html


def test_the_nav_links_the_gurus(client):
    assert '<a href="/gurus" class="nav-link">Gurus</a>' in client.get("/").text


# ---- the HDS tab --------------------------------------------------------------------------------------------------


def test_the_hds_tab_shows_the_gurus_above_yahoos_holders(client, web_db):
    _seed(web_db)
    html = client.get("/ui/stocks/AAA/hds").text
    assert html.index('section-heading">Gurus') < html.index('section-heading">Institutional Holders')
    assert 'href="/gurus/1067983"' in html and "Li Lu" in html and "Bill Ackman" in html
    assert "+50.0%" in html and "-50.0%" in html and "Call" in html and "$18.00K" in html
    assert "other share class" not in html  # AAA has none


def test_the_hds_tab_shows_the_other_class_and_the_sold_out(client, web_db):
    _seed(web_db)
    _add_alphabet(web_db)
    html = client.get("/ui/stocks/GOOGL/hds").text
    gurus = html.split('section-heading">Gurus', 1)[1].split('section-heading">Institutional Holders', 1)[0]
    assert gurus.count('href="/gurus/1067983"') == 2 and "GOOG" in gurus and "Sold out" in gurus
    assert "GOOG too, the other share class" in gurus and "had 5 shares, $1.00K" in gurus  # Pershing's GOOG


def test_the_hds_tab_when_no_guru_holds_the_stock(client, web_db):
    _seed(web_db)
    html = client.get("/ui/stocks/CCC/hds").text
    assert 'section-heading">Gurus' in html and "No guru's latest 13F holds it." in html


def test_the_hds_tab_without_guru_holdings_shows_yahoos_only(client, web_db):
    assert 'section-heading">Gurus' not in client.get("/ui/stocks/AAA/hds").text  # none stored
    _drop_guru_tables(web_db)
    response = client.get("/ui/stocks/AAA/hds")
    assert response.status_code == 200 and 'section-heading">Gurus' not in response.text
    assert "Institutional Holders" in response.text


# ---- the API ------------------------------------------------------------------------------------------------------


def test_the_api_lists_the_gurus(client, web_db):
    _seed(web_db)
    body = client.get("/api/gurus").json()
    assert [g["cik"] for g in body] == ["1067983", "2026053", "1709323"]
    assert set(body[0]) == {"cik", "manager", "filers", "period", "filed", "total_value", "positions", "new",
                            "sold_out", "complete", "has_prior"}


def test_the_api_gives_a_gurus_quarter(client, web_db):
    _seed(web_db)
    body = client.get("/api/gurus/1067983").json()
    assert (body["period"], body["periods"], len(body["positions"])) == (Q2, [Q2, Q1], 4)
    assert set(body["changes"]) == {"new", "added", "reduced", "sold_out"}
    earlier = client.get(f"/api/gurus/1067983?period={Q1}").json()
    assert (earlier["period"], earlier["has_prior"], earlier["changes"]) == (Q1, False, None)
    missing = client.get("/api/gurus/9999999")
    assert missing.status_code == 404 and "detail" in missing.json()
    assert client.get("/api/gurus/1067983?period=2020-12-31").status_code == 404


def test_the_api_gives_the_aggregated_portfolio(client, web_db):
    _seed(web_db)
    body = client.get("/api/gurus/portfolio").json()
    assert [s["security"] for s in body] == ["AAA", "GOOG", UNM, "GOOGL", "BBB"]
    assert [s["security"] for s in client.get("/api/gurus/portfolio?limit=2").json()] == ["AAA", "GOOG"]


def test_the_api_gives_the_gurus_holding_a_stock(client, web_db):
    _seed(web_db)
    body = client.get("/api/stocks/AAA/gurus").json()
    assert (body["ticker"], body["classes"], [g["cik"] for g in body["gurus"]]) == (
        "AAA", ["AAA"], ["1067983", "1709323", "2026053", "1067983"])
    assert client.get("/api/stocks/ZZZZ/gurus").status_code == 404


def test_the_api_without_the_guru_tables(client, web_db):
    _drop_guru_tables(web_db)
    assert client.get("/api/gurus").json() == [] and client.get("/api/gurus/portfolio").json() == []
    assert client.get("/api/gurus/1067983").status_code == 404
    assert client.get("/api/stocks/AAA/gurus").json()["gurus"] == []


# ---- the screener -------------------------------------------------------------------------------------------------


def test_the_guru_fields_are_whitelisted_listed_and_typed():
    assert list(GURU_EXPRS) == ["gurus_holding", "gurus_buying"]
    assert all(f in SCREEN_COLUMNS for f in GURU_EXPRS)
    others = (set(_METRIC_COLUMNS) | set(SNAPSHOT_SCREEN_COLUMNS) | set(VALUATION_EXPRS) | set(HISTORY_EXPRS)
              | set(QUALITY_EXPRS) | set(INSIDER_EXPRS))
    assert not others & set(GURU_EXPRS)
    assert METRIC_KINDS["gurus_holding"] == METRIC_KINDS["gurus_buying"] == "count"
    with pytest.raises(ValueError):
        build_screen_query(ScreenSpec(filters=[MetricFilter(field="gurus_holding; DROP TABLE x", op="gte", value=1)]))


def test_the_screener_reads_the_gurus_holding_and_buying(web_db):
    _seed(web_db)
    _add_alphabet(web_db)
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[]))["items"]}
        held = r.screen(ScreenSpec(filters=[MetricFilter(field="gurus_holding", op="gte", value=2)],
                                   sort="gurus_holding"))
    # AAA: Berkshire (adding), Li Lu and Pershing Square. GOOGL: Berkshire (new, GOOGL and GOOG: once) and Li Lu
    # (GOOG, no quarter before); Pershing Square's GOOG sold out. EEE: none, Li Lu's calls not counted.
    assert {t: (row["gurus_holding"], row["gurus_buying"]) for t, row in items.items()} == {
        "AAA": (3, 1), "GOOGL": (2, 1), "EEE": (0, 0)}
    assert [row["ticker"] for row in held["items"]] == ["AAA", "GOOGL"] and held["total"] == 2


def test_a_database_without_the_guru_tables_still_screens(web_db):
    _drop_guru_tables(web_db)
    with Reader(web_db) as r:
        result = r.screen(ScreenSpec(filters=[], sort="gurus_holding"))
        assert result["total"] == 2 and all(row["gurus_holding"] is None and row["gurus_buying"] is None
                                            for row in result["items"])
        assert r.screen(ScreenSpec(filters=[MetricFilter(field="gurus_buying", op="gte", value=0)]))["total"] == 0


def test_the_screener_offers_the_guru_fields(client, web_db):
    html = client.get("/screener").text
    metrics = json.loads(html.split('id="screener-metrics">', 1)[1].split("</script>", 1)[0])
    groups = {m["key"]: m["group"] for m in metrics}
    assert groups.get("gurus_holding") == groups.get("gurus_buying") == "Gurus (13F)"
    _seed(web_db)
    results = client.get("/ui/screen?sort=gurus_holding&sort_dir=desc").text
    # One column, sorted by the gurus holding, the buyers among them in brackets (AAA's 3, 1 buying).
    assert "sort=gurus_holding" in results and "Gurus (buying)" in results
    assert '3 <span class="text-muted" title="Of them buying' in results and ">(1)</span>" in results

"""Collecting insiders' Form 4 filings and the gurus' 13F filings (Milestone 3): a filer's submissions feed back N
years or quarters, each document fetched once and cached, parsed and stored, without losing what a failed fetch
didn't bring (docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
import json
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.fetchers.rate_limiter import RateLimiter
from src.ownership import collect
from src.ownership.collect import (
    FilingRef,
    Form4Source,
    ThirteenFSource,
    collect_gurus,
    collect_insiders,
    form4_refs,
    map_cusips,
    quarter_start,
    raw_document,
    thirteenf_refs,
)
from src.ownership.form4 import Form4, Owner, Trade, parse_form4
from src.ownership.gurus import GURUS, Guru
from src.ownership.openfigi import JobErrors, Listing, OpenFIGI
from src.ownership.thirteenf import Holding, ThirteenF, ThirteenFError
from src.webapp.repository import Reader

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ownership"
FEED = "https://data.sec.gov/submissions/CIK0000080424.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/80424"
PFE_FEED = "https://data.sec.gov/submissions/CIK0000078003.json"
PFE_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/78003"


def _page(rows):
    """A submissions feed page in the SEC's column layout: (accession, form, filed, report date, document)."""
    keys = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()

    def json(self):
        return json.loads(self.content)


class _SEC:
    """Serves URLs from *routes* (url -> (status, body)) and records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def get_raw(self, url):
        self.requests.append(url)
        status, body = self.routes.get(url, (404, b""))
        return _Response(status, body)


def _original_of_the_amendment():
    # PG's 4/A of 2026-08-27 amends a Form 4 filed on 2026-08-24: the same document as an original.
    xml = (FIXTURES / "pg_amendment.xml").read_text(encoding="utf-8")
    xml = xml.replace("<documentType>4/A</documentType>", "<documentType>4</documentType>")
    start, end = xml.index("<dateOfOriginalSubmission>"), xml.index("</dateOfOriginalSubmission>")
    return (xml[:start] + xml[end + len("</dateOfOriginalSubmission>"):]).encode("utf-8")


def _db(tmp_path, companies=(("PG", "0000080424"),)):
    store = SQLiteStore(db_path=tmp_path / "own.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.executemany("INSERT INTO companies (ticker, cik, sector_class) VALUES (?, ?, 'general')", companies)
    conn.commit()
    conn.close()
    return tmp_path / "own.db"


def _routes(document_status=200):
    page = _page([
        ("0000080424-26-000160", "4/A", "2026-08-27", "2026-08-20", "xslF345X06/form4a.xml"),
        ("0000080424-26-000150", "4", "2026-08-24", "2026-08-20", "xslF345X06/form4.xml"),
        ("0000080424-26-000140", "8-K", "2026-08-20", None, "pg-8k.htm"),
    ])
    return {
        FEED: (200, {"filings": {"recent": page, "files": []}}),
        f"{ARCHIVE}/000008042426000160/form4a.xml": (document_status, (FIXTURES / "pg_amendment.xml").read_bytes()),
        f"{ARCHIVE}/000008042426000150/form4.xml": (document_status, _original_of_the_amendment()),
    }


def test_form4_refs_reads_the_feed_layout_and_keeps_form_4s_since_the_cutoff():
    page = _page([
        ("A-1", "4", "2026-09-01", "2026-08-30", "xslF345X06/a.xml"),
        ("A-2", "8-K", "2026-09-02", None, "x.htm"),
        ("A-3", "4/A", "2023-01-05", "2023-01-01", "xslF345X05/b.xml"),
        ("A-4", "4", "2026-01-02", "2025-12-30", "c.xml"),
    ])
    refs = form4_refs([page], "2024-01-01")
    assert [(r.accession, r.form, r.filed, r.document) for r in refs] == [
        ("A-1", "4", "2026-09-01", "a.xml"), ("A-4", "4", "2026-01-02", "c.xml")]


def test_raw_document_drops_the_rendering_path():
    assert raw_document("xslF345X06/wk-form4_1791925767.xml") == "wk-form4_1791925767.xml"
    assert raw_document("form4.xml") == "form4.xml"


def test_the_source_reads_older_pages_back_to_the_cutoff_only(tmp_path):
    recent = _page([("B-1", "4", "2026-05-01", "2026-04-30", "d.xml")])
    older = _page([("B-0", "4", "2025-03-01", "2025-02-27", "e.xml")])
    sec = _SEC({
        FEED: (200, {"filings": {"recent": recent, "files": [
            {"name": "CIK0000080424-submissions-001.json", "filingFrom": "2025-01-01", "filingTo": "2025-12-31"},
            {"name": "CIK0000080424-submissions-002.json", "filingFrom": "2019-01-01", "filingTo": "2021-12-31"},
        ]}}),
        "https://data.sec.gov/submissions/CIK0000080424-submissions-001.json": (200, older),
    })
    refs = form4_refs(Form4Source(sec, tmp_path).feed_pages("0000080424", "2024-01-01"), "2024-01-01")
    assert [r.accession for r in refs] == ["B-1", "B-0"]
    assert not any(url.endswith("-002.json") for url in sec.requests)  # older than the cutoff: never fetched


def test_each_document_is_fetched_once_then_read_from_the_cache(tmp_path):
    sec = _SEC(_routes())
    ref = form4_refs([_page([("0000080424-26-000160", "4/A", "2026-08-27", "2026-08-20", "form4a.xml")])],
                     "2024-01-01")[0]
    first = Form4Source(sec, tmp_path / "cache").filing("0000080424", ref)
    again = Form4Source(_SEC({}), tmp_path / "cache").filing("0000080424", ref)  # no route: the cache serves
    assert first == again and first.form == "4/A"
    assert len(sec.requests) == 1


def test_collect_stores_each_owner_and_trade_and_the_amendment_supersedes_the_trade_it_restates(tmp_path):
    db = _db(tmp_path)
    summary = collect_insiders(db, ["PG"], 3, Form4Source(_SEC(_routes()), tmp_path / "cache"), today="2026-10-22")
    assert summary["PG"]["stored"] == 2 and summary["PG"]["errors"] == []
    conn = sqlite3.connect(db)
    filings = conn.execute("SELECT accession, form, filed_date, owner_cik, officer_title, superseded_by "
                           "FROM insider_filings ORDER BY accession").fetchall()
    assert filings == [  # the filing-level note: amended by
        ("0000080424-26-000150", "4", "2026-08-24", "0001825382", "Chf Rsch, Dev & Innov Officer",
         "0000080424-26-000160"),
        ("0000080424-26-000160", "4/A", "2026-08-27", "0001825382", "Chf Rsch, Dev & Innov Officer", None),
    ]
    trades = conn.execute("SELECT accession, seq, ticker, kind, date, code, shares, price, superseded_by "
                          "FROM insider_trades ORDER BY accession").fetchall()
    assert trades == [
        ("0000080424-26-000150", 0, "PG", "nonderivative", "2026-08-20", "S", 3053.0, 143.79, "0000080424-26-000160"),
        ("0000080424-26-000160", 0, "PG", "nonderivative", "2026-08-20", "S", 3053.0, 143.79, None)]


def test_a_4a_correcting_the_earliest_date_supersedes_the_trade_it_restates(tmp_path):
    # PG's 4/A was filed "solely to correct the date of the stock sale": the original, here, sold on 2026-08-21, the
    # day its period of report gives, and the 4/A's period is 2026-08-20. The same shares and price make it the trade.
    routes = _routes()
    original = _original_of_the_amendment().replace(b"2026-08-20", b"2026-08-21")
    routes[f"{ARCHIVE}/000008042426000150/form4.xml"] = (200, original)
    db = _db(tmp_path)
    collect_insiders(db, ["PG"], 3, Form4Source(_SEC(routes), tmp_path / "cache"), today="2026-10-22")
    assert _rows(db, "SELECT t.accession, f.period_of_report, t.date, t.superseded_by FROM insider_trades t "
                     "JOIN insider_filings f ON f.accession = t.accession ORDER BY t.accession") == [
        ("0000080424-26-000150", "2026-08-21", "2026-08-21", "0000080424-26-000160"),
        ("0000080424-26-000160", "2026-08-20", "2026-08-20", None)]


def _pfe_pair_routes():
    """PFE's feed with the Form 4 of 2025-02-25 (an officer's SAR earn-out: five trades) and its 4/A of 2025-03-03,
    filed to add the 2025-02-24 withholding the Form 4 left out, both as filed."""
    page = _page([
        ("0001225208-25-002701", "4/A", "2025-03-03", "2025-02-22", "xslF345X05/form4a.xml"),
        ("0001225208-25-002397", "4", "2025-02-25", "2025-02-22", "xslF345X05/form4.xml"),
    ])
    return {
        PFE_FEED: (200, {"filings": {"recent": page, "files": []}}),
        f"{PFE_ARCHIVE}/000122520825002701/form4a.xml": (200, (FIXTURES / "pfe_amendment_adding_a_trade.xml")
                                                          .read_bytes()),
        f"{PFE_ARCHIVE}/000122520825002397/form4.xml": (200, (FIXTURES / "pfe_sar_earnout.xml").read_bytes()),
    }


def test_an_amendment_adding_a_trade_leaves_its_originals_trades_standing(tmp_path):
    db = _db(tmp_path, [("PFE", "0000078003")])
    summary = collect_insiders(db, ["PFE"], 3, Form4Source(_SEC(_pfe_pair_routes()), tmp_path / "cache"),
                               today="2026-10-22")
    assert (summary["PFE"]["stored"], summary["PFE"]["errors"]) == (2, [])
    assert _rows(db, "SELECT accession, seq, kind, code, date, shares, superseded_by FROM insider_trades "
                     "ORDER BY accession, seq") == [
        ("0001225208-25-002397", 0, "nonderivative", "M", "2025-02-22", 8741.0, None),
        ("0001225208-25-002397", 1, "nonderivative", "F", "2025-02-22", 340.0, None),
        ("0001225208-25-002397", 2, "nonderivative", "F", "2025-02-22", 7798.0, None),
        ("0001225208-25-002397", 3, "nonderivative", "F", "2025-02-23", 662.0, None),
        ("0001225208-25-002397", 4, "derivative", "M", "2025-02-22", 8741.0, None),
        ("0001225208-25-002701", 0, "nonderivative", "F", "2025-02-24", 403.0, None)]
    assert _rows(db, "SELECT accession, superseded_by FROM insider_filings ORDER BY accession") == [
        ("0001225208-25-002397", "0001225208-25-002701"), ("0001225208-25-002701", None)]  # amended by, still
    with Reader(db) as r:  # the readers show all six
        assert sorted((t["accession"], t["seq"]) for t in r.sec_insider_trades("PFE")) == [
            ("0001225208-25-002397", seq) for seq in range(5)] + [("0001225208-25-002701", 0)]


def _stored(accession, filed, form="4", original=None, shares=100.0, owner="0000000001"):
    """A stored filing as the collector passes it: one owner, one sale of *shares* at $10 on 2026-08-20."""
    trade = Trade(table="nonderivative", seq=0, security_title="Common Stock", date="2026-08-20", code="S",
                  shares=shares, price=10.0)
    filing = Form4(accession=accession, form=form, period_of_report="2026-08-20", original_filed=original,
                   issuer_cik="0000080424", owners=[Owner(cik=owner, name=f"OWNER {owner}")], trades=[trade])
    return FilingRef(accession, form, filed, "2026-08-20", "doc.xml"), filing


def test_a_4a_of_a_4a_supersedes_the_trade_the_first_one_restated(tmp_path):
    db = tmp_path / "own.db"
    SQLiteStore(db_path=db).export_insider_filings("PG", [
        _stored("F-1", "2026-08-24", shares=100.0),
        _stored("F-2", "2026-08-27", form="4/A", original="2026-08-24", shares=90.0),
        _stored("F-3", "2026-09-02", form="4/A", original="2026-08-27", shares=95.0),
    ], "2026-10-22T00:00:00+00:00")
    assert _rows(db, "SELECT accession, shares, superseded_by FROM insider_trades ORDER BY accession") == [
        ("F-1", 100.0, "F-2"), ("F-2", 90.0, "F-3"), ("F-3", 95.0, None)]
    assert _rows(db, "SELECT accession, superseded_by FROM insider_filings ORDER BY accession") == [
        ("F-1", "F-2"), ("F-2", "F-3"), ("F-3", None)]


def test_an_insider_trades_table_from_before_the_trades_own_note_gets_its_column(tmp_path):
    db = tmp_path / "own.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE insider_trades (accession TEXT NOT NULL, seq INTEGER NOT NULL, ticker TEXT NOT NULL, "
                 "kind TEXT NOT NULL, security_title TEXT, date TEXT, code TEXT, shares REAL, price REAL, "
                 "price_footnotes TEXT, acquired_disposed TEXT, owned_after REAL, direct_indirect TEXT, "
                 "nature_of_ownership TEXT, exercise_price REAL, underlying_title TEXT, underlying_shares REAL, "
                 "PRIMARY KEY (accession, seq))")
    conn.commit()
    conn.close()
    SQLiteStore(db_path=db).export_insider_filings("PG", [
        _stored("F-1", "2026-08-24"), _stored("F-2", "2026-08-27", form="4/A", original="2026-08-24", shares=90.0),
    ], "2026-10-22T00:00:00+00:00")
    assert _rows(db, "SELECT accession, superseded_by FROM insider_trades ORDER BY accession") == [
        ("F-1", "F-2"), ("F-2", None)]


# ---- only the company's own filings, and a successor's predecessor ------------------------------------------------

CERE = "0000078003-24-000169"  # Pfizer's Form 4 as Cerevel's former 10% owner, in Pfizer's feed
PFE_PURCHASE = "0001225208-26-006944"  # a director's purchase of Pfizer stock


def _pfe_routes(purchase=None):
    page = _page([
        (PFE_PURCHASE, "4", "2026-08-07", "2026-08-05", "xslF345X05/form4.xml"),
        (CERE, "4", "2024-08-05", "2024-08-01", "xslF345X05/wf-form4.xml"),
    ])
    return {
        PFE_FEED: (200, {"filings": {"recent": page, "files": []}}),
        f"{PFE_ARCHIVE}/000122520826006944/form4.xml": (
            200, purchase if purchase is not None else (FIXTURES / "pfe_director_purchase.xml").read_bytes()),
        f"{PFE_ARCHIVE}/000007800324000169/wf-form4.xml": (200, (FIXTURES / "pfe_owner_of_cerevel.xml").read_bytes()),
    }


def test_a_filing_of_another_issuer_is_left_out_and_counted(tmp_path):
    db = _db(tmp_path, [("PFE", "0000078003")])
    lines = []
    summary = collect_insiders(db, ["PFE"], 3, Form4Source(_SEC(_pfe_routes()), tmp_path / "cache"),
                               today="2026-10-22", log=lines.append)
    assert summary["PFE"] == {"listed": 2, "stored": 1, "other_issuer": 1, "from_predecessor": 0, "errors": []}
    assert _rows(db, "SELECT DISTINCT accession FROM insider_filings") == [(PFE_PURCHASE,)]
    assert _rows(db, "SELECT DISTINCT accession FROM insider_trades") == [(PFE_PURCHASE,)]
    assert (tmp_path / "cache" / "form4" / f"{CERE}.xml").exists()  # it parsed: cached, not fetched again
    assert len(lines) == 1 and lines[0].isascii() and "1 of other issuers" in lines[0]


def test_an_issuer_cik_is_read_as_a_number(tmp_path):
    purchase = (FIXTURES / "pfe_director_purchase.xml").read_bytes().replace(
        b"<issuerCik>0000078003</issuerCik>", b"<issuerCik>78003</issuerCik>")
    assert b"<issuerCik>78003</issuerCik>" in purchase
    db = _db(tmp_path, [("PFE", "0000078003")])
    summary = collect_insiders(db, ["PFE"], 3, Form4Source(_SEC(_pfe_routes(purchase)), tmp_path / "cache"),
                               today="2026-10-22")
    assert (summary["PFE"]["stored"], summary["PFE"]["other_issuer"]) == (1, 1)


def test_a_filing_naming_no_issuer_is_reported_not_stored(tmp_path):
    purchase = (FIXTURES / "pfe_director_purchase.xml").read_bytes().replace(
        b"<issuerCik>0000078003</issuerCik>", b"")
    assert b"<issuerCik>" not in purchase
    db = _db(tmp_path, [("PFE", "0000078003")])
    summary = collect_insiders(db, ["PFE"], 3, Form4Source(_SEC(_pfe_routes(purchase)), tmp_path / "cache"),
                               today="2026-10-22")["PFE"]
    assert (summary["stored"], summary["other_issuer"]) == (0, 1)  # not another issuer's: unknown
    assert summary["errors"] == [f"{PFE_PURCHASE}: no issuer CIK"]
    assert _rows(db, "SELECT COUNT(*) FROM insider_filings") == [(0,)]


def test_another_issuers_filing_an_earlier_run_stored_is_removed(tmp_path):
    db = _db(tmp_path, [("PFE", "0000078003")])
    cerevel = parse_form4((FIXTURES / "pfe_owner_of_cerevel.xml").read_bytes(), CERE)
    SQLiteStore(db_path=db).export_insider_filings(  # as the code before this check stored it
        "PFE", [(FilingRef(CERE, "4", "2024-08-05", "2024-08-01", "wf-form4.xml"), cerevel)], "2026-10-01T00:00:00")
    collect_insiders(db, ["PFE"], 3, Form4Source(_SEC(_pfe_routes()), tmp_path / "cache"), today="2026-10-22")
    assert _rows(db, "SELECT DISTINCT accession FROM insider_filings") == [(PFE_PURCHASE,)]
    assert _rows(db, "SELECT DISTINCT accession FROM insider_trades") == [(PFE_PURCHASE,)]


def test_the_issuers_own_copy_of_a_filing_left_out_stays(tmp_path):
    # Cerevel's own rows of the filing (were it a company of ours) aren't Pfizer's to remove.
    db = _db(tmp_path, [("PFE", "0000078003"), ("CERE", "0001805387")])
    cerevel = parse_form4((FIXTURES / "pfe_owner_of_cerevel.xml").read_bytes(), CERE)
    SQLiteStore(db_path=db).export_insider_filings(
        "CERE", [(FilingRef(CERE, "4", "2024-08-05", "2024-08-01", "wf-form4.xml"), cerevel)], "2026-10-01T00:00:00")
    collect_insiders(db, ["PFE"], 3, Form4Source(_SEC(_pfe_routes()), tmp_path / "cache"), today="2026-10-22")
    assert _rows(db, "SELECT DISTINCT ticker, accession FROM insider_filings ORDER BY ticker") == [
        ("CERE", CERE), ("PFE", PFE_PURCHASE)]
    assert _rows(db, "SELECT DISTINCT ticker, accession FROM insider_trades ORDER BY ticker") == [
        ("CERE", CERE), ("PFE", PFE_PURCHASE)]


#: ExxonMobil Holdings (XOM since 2026-07) and Exxon Mobil Corp before it (sec_handler.PREDECESSOR_CIKS).
XOM, EXXON = "0002115436", "0000034088"


def _form4(accession, issuer, form="4", day="2026-08-20", owner="0000000001"):
    trade = Trade(table="nonderivative", seq=0, security_title="Common Stock", date=day, code="S", shares=100.0,
                  price=110.0)
    return Form4(accession=accession, form=form, period_of_report=day, issuer_cik=issuer,
                 owners=[Owner(cik=owner, name=f"OWNER {owner}")], trades=[trade])


class _Feeds:
    """A fake Form 4 source: each CIK's feed rows (accession, form, filed, report date), or the error its feed raises,
    and each accession's parsed filing. Records each filing fetched with the CIK it was fetched under."""

    def __init__(self, feeds, filings):
        self.feeds, self.filings = feeds, filings
        self.fetched = []

    def feed_pages(self, cik, since):
        rows = self.feeds.get(cik, [])
        if isinstance(rows, Exception):
            raise rows
        return iter([_page([row + ("xslF345X05/doc.xml",) for row in rows])])

    def filing(self, cik, ref):
        self.fetched.append((cik, ref.accession))
        return self.filings[ref.accession]


#: XOM's feed: its own Form 4s from 2026-07-06 and one as another issuer's owner; Exxon Mobil Corp's: its own before
#: and on the day XOM's start, one as another issuer's owner, and one before the window.
XOM_FEEDS = {
    XOM: [("X-3", "4", "2026-08-03", "2026-07-31"), ("X-2", "4", "2026-07-20", "2026-07-17"),
          ("X-1", "4", "2026-07-06", "2026-07-02")],
    EXXON: [("P-4", "4", "2026-07-06", "2026-07-02"), ("P-3", "4", "2026-07-01", "2026-06-29"),
            ("P-2", "4", "2025-05-05", "2025-05-01"), ("P-1", "4", "2024-01-10", "2024-01-08"),
            ("P-0", "4", "2023-01-10", "2023-01-06")],
}
XOM_FILINGS = {
    "X-3": _form4("X-3", XOM), "X-2": _form4("X-2", "0000000777"), "X-1": _form4("X-1", XOM),
    "P-4": _form4("P-4", EXXON), "P-3": _form4("P-3", EXXON), "P-2": _form4("P-2", "0000000999"),
    "P-1": _form4("P-1", EXXON), "P-0": _form4("P-0", EXXON),
}


def test_a_successor_takes_its_predecessors_form_4s_filed_before_its_own_first(tmp_path):
    db = _db(tmp_path, [("XOM", XOM)])
    source = _Feeds(XOM_FEEDS, XOM_FILINGS)
    summary = collect_insiders(db, ["XOM"], 3, source, today="2026-10-22")
    assert summary["XOM"] == {"listed": 6, "stored": 4, "other_issuer": 2, "from_predecessor": 2, "errors": []}
    assert source.fetched == [(XOM, "X-3"), (XOM, "X-2"), (XOM, "X-1"), (EXXON, "P-3"), (EXXON, "P-2"),
                              (EXXON, "P-1")]  # nothing filed from XOM's first Form 4 on
    assert _rows(db, "SELECT ticker, accession FROM insider_filings ORDER BY accession") == [
        ("XOM", "P-1"), ("XOM", "P-3"), ("XOM", "X-1"), ("XOM", "X-3")]


def test_a_successor_without_a_form_4_of_its_own_takes_all_its_predecessors(tmp_path):
    db = _db(tmp_path, [("XOM", XOM)])
    feeds = {XOM: [("X-2", "4", "2026-07-20", "2026-07-17")], EXXON: XOM_FEEDS[EXXON]}
    summary = collect_insiders(db, ["XOM"], 3, _Feeds(feeds, XOM_FILINGS), today="2026-10-22")
    assert (summary["XOM"]["stored"], summary["XOM"]["from_predecessor"], summary["XOM"]["other_issuer"]) == (3, 3, 2)
    assert _rows(db, "SELECT accession FROM insider_filings ORDER BY accession") == [("P-1",), ("P-3",), ("P-4",)]


def test_a_predecessors_filing_from_the_successors_first_form_4_on_is_removed(tmp_path):
    # An earlier run, before XOM's feed listed its first Form 4, took all of Exxon Mobil Corp's; this one, after it,
    # takes those filed before it.
    db = _db(tmp_path, [("XOM", XOM)])
    collect_insiders(db, ["XOM"], 3, _Feeds({XOM: [], EXXON: XOM_FEEDS[EXXON]}, XOM_FILINGS), today="2026-07-06")
    assert ("P-4",) in _rows(db, "SELECT accession FROM insider_filings")
    collect_insiders(db, ["XOM"], 3, _Feeds(XOM_FEEDS, XOM_FILINGS), today="2026-10-22")
    assert _rows(db, "SELECT DISTINCT accession FROM insider_trades ORDER BY accession") == [
        ("P-1",), ("P-3",), ("X-1",), ("X-3",)]


def test_a_failed_predecessor_feed_is_reported_and_the_successors_own_filings_stored(tmp_path):
    db = _db(tmp_path, [("XOM", XOM)])
    feeds = {XOM: XOM_FEEDS[XOM], EXXON: OSError("CIK0000034088.json: HTTP 503")}
    summary = collect_insiders(db, ["XOM"], 3, _Feeds(feeds, XOM_FILINGS), today="2026-10-22")["XOM"]
    assert (summary["stored"], summary["from_predecessor"]) == (2, 0)
    assert len(summary["errors"]) == 1 and EXXON in summary["errors"][0] and "HTTP 503" in summary["errors"][0]


# ---- one company's failure never ends the run ---------------------------------------------------------------------

def test_a_store_failure_is_reported_for_its_company_and_the_run_goes_on(tmp_path):
    db = _db(tmp_path, [("AAA", "0000000011"), ("BBB", "0000000022")])
    feeds = {"0000000011": [("A-1", "4", "2026-08-03", "2026-07-31")],
             "0000000022": [("B-1", "4", "2026-08-03", "2026-07-31")]}
    collect_insiders(db, ["AAA"], 3, _Feeds({"0000000011": [("A-0", "4", "2026-08-01", "2026-07-30")]},
                                            {"A-0": _form4("A-0", "0000000011")}), today="2026-10-22")
    joint = _form4("A-1", "0000000011")
    joint.owners.append(Owner(cik="0000000001", name="THE SAME OWNER AGAIN"))  # a duplicate owner CIK
    summary = collect_insiders(db, ["AAA", "BBB"], 3, _Feeds(feeds, {"A-1": joint, "B-1": _form4("B-1", "0000000022")}),
                               today="2026-10-22")
    assert (summary["AAA"]["listed"], summary["AAA"]["stored"], len(summary["AAA"]["errors"])) == (1, 0, 1)
    assert summary["AAA"]["errors"][0].startswith("store: IntegrityError: UNIQUE constraint failed")
    assert summary["BBB"] == {"listed": 1, "stored": 1, "other_issuer": 0, "from_predecessor": 0, "errors": []}
    assert _rows(db, "SELECT ticker, accession FROM insider_filings ORDER BY ticker, accession") == [
        ("AAA", "A-0"), ("BBB", "B-1")]  # AAA's earlier filing kept, nothing of its failed batch


def test_an_unexpected_error_is_reported_for_its_company_and_the_run_goes_on(tmp_path):
    db = _db(tmp_path, [("AAA", "0000000011"), ("BBB", "0000000022")])
    feeds = {"0000000011": KeyError("name"), "0000000022": [("B-1", "4", "2026-08-03", "2026-07-31")]}
    summary = collect_insiders(db, ["AAA", "BBB"], 3, _Feeds(feeds, {"B-1": _form4("B-1", "0000000022")}),
                               today="2026-10-22")
    assert summary["AAA"]["stored"] == 0 and summary["AAA"]["errors"] == ["KeyError: 'name'"]
    assert summary["BBB"]["stored"] == 1


def test_a_second_run_adds_nothing_twice(tmp_path):
    db = _db(tmp_path)
    for _ in range(2):
        collect_insiders(db, ["PG"], 3, Form4Source(_SEC(_routes()), tmp_path / "cache"), today="2026-10-22")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM insider_filings").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM insider_trades").fetchone()[0] == 2


def test_a_failed_fetch_keeps_what_was_stored_and_is_reported(tmp_path):
    db = _db(tmp_path)
    collect_insiders(db, ["PG"], 3, Form4Source(_SEC(_routes()), tmp_path / "cache"), today="2026-10-22")
    # A later run without the cache, whose document fetches fail.
    summary = collect_insiders(db, ["PG"], 3, Form4Source(_SEC(_routes(503)), tmp_path / "empty-cache"),
                               today="2026-10-22")
    assert summary["PG"]["stored"] == 0 and len(summary["PG"]["errors"]) == 2
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM insider_filings").fetchone()[0] == 2  # nothing lost


def test_a_document_that_isnt_a_form_4_is_reported_not_stored(tmp_path):
    db = _db(tmp_path)
    routes = _routes()
    routes[f"{ARCHIVE}/000008042426000150/form4.xml"] = (200, b"<html>moved</html>")
    summary = collect_insiders(db, ["PG"], 3, Form4Source(_SEC(routes), tmp_path / "cache"), today="2026-10-22")
    assert summary["PG"]["stored"] == 1
    assert any("0000080424-26-000150" in e for e in summary["PG"]["errors"])
    assert not (tmp_path / "cache" / "form4" / "0000080424-26-000150.xml").exists()  # a bad document isn't cached


# ---- the gurus' 13F filings ---------------------------------------------------------------------------------------

ACKMAN = Guru("2026053", "PERSHING SQUARE INC.", "Bill Ackman",
              former=(("1336528", "Pershing Square Capital Management, L.P."),))
LI_LU = Guru("1709323", "Himalaya Capital Management LLC", "Li Lu")
HIMALAYA = "himalaya_2022q3_thousands.txt"
HIMALAYA_FEED = "https://data.sec.gov/submissions/CIK0001709323.json"
HIMALAYA_TEXT = "https://www.sec.gov/Archives/edgar/data/1709323/000170932322000005/0001709323-22-000005.txt"
HIMALAYA_ROW = ("0001709323-22-000005", "13F-HR", "2022-11-14", "2022-09-30", "xslForm13F_X01/primary_doc.xml")


def _13f(accession, period, values, entry_total=None, value_total=None, form="13F-HR", multiplier=1):
    """A parsed 13F with one entry per value (as filed, in units of *multiplier* dollars), its cover's totals agreeing
    with them unless given."""
    holdings = [Holding(row=i, cusip=f"00000000{i}", issuer=f"ISSUER {i}", value=Decimal(v),
                        value_usd=float(v * multiplier), shares=10.0, share_type="SH", discretion="SOLE")
                for i, v in enumerate(values)]
    return ThirteenF(accession=accession, form=form, period=period, report_type="13F HOLDINGS REPORT",
                     entry_total=len(values) if entry_total is None else entry_total,
                     value_total=Decimal(sum(values) if value_total is None else value_total), multiplier=multiplier,
                     holdings=holdings)


class _Filers:
    """A fake 13F source: each filer's feed rows (accession, form, filed, report date), or the error its feed raises,
    and each accession's parsed filing, or the error fetching it raises. Records the cutoffs asked for and the
    filings fetched."""

    def __init__(self, feeds, filings):
        self.feeds, self.filings = feeds, filings
        self.since, self.fetched = [], []

    def feed_pages(self, cik, since):
        self.since.append(since)
        rows = self.feeds.get(cik, [])
        if isinstance(rows, Exception):
            raise rows
        return iter([_page([row + ("xslForm13F_X02/primary_doc.xml",) for row in rows])])

    def filing(self, cik, ref):
        self.fetched.append((cik, ref.accession))
        result = self.filings[ref.accession]
        if isinstance(result, Exception):
            raise result
        return result


def _rows(db, sql):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _himalaya_sec(text=None):
    """The SEC serving Himalaya's feed (its Q3 2022 13F-HR) and that filing's full submission text."""
    return _SEC({HIMALAYA_FEED: (200, {"filings": {"recent": _page([HIMALAYA_ROW]), "files": []}}),
                 HIMALAYA_TEXT: (200, text if text is not None else (FIXTURES / HIMALAYA).read_bytes())})


def test_thirteenf_refs_keeps_the_holdings_reports_from_the_cutoff_period():
    page = _page([
        ("C-1", "13F-HR", "2026-08-14", "2026-06-30", "xslForm13F_X02/primary_doc.xml"),
        ("C-2", "13F-NT", "2026-08-14", "2026-06-30", "xslForm13F_X02/primary_doc.xml"),  # a notice: no holdings
        ("C-3", "13F-HR/A", "2025-04-16", "2024-12-31", "xslForm13F_X02/primary_doc.xml"),  # the cutoff's period
        ("C-4", "13F-HR/A", "2025-03-03", "2024-09-30", "xslForm13F_X02/primary_doc.xml"),  # filed later, for before
        ("C-5", "SC 13G/A", "2026-02-10", "2025-12-31", "sc13g.htm"),
        ("C-6", "13F-HR", "2024-11-14", "2024-09-30", "xslForm13F_X02/primary_doc.xml"),
    ])
    refs = thirteenf_refs([page], "2024-12-31", "1336528")
    assert [(r.accession, r.form, r.filed, r.report_date, r.document, r.cik) for r in refs] == [
        ("C-1", "13F-HR", "2026-08-14", "2026-06-30", "C-1.txt", "1336528"),
        ("C-3", "13F-HR/A", "2025-04-16", "2024-12-31", "C-3.txt", "1336528"),
    ]


def test_the_window_is_the_quarters_before_the_current_one():
    assert quarter_start(date(2026, 10, 22), 8) == "2024-10-01"  # Q4 2024 to Q3 2026
    assert quarter_start(date(2026, 1, 1), 8) == "2024-01-01"
    assert quarter_start(date(2026, 3, 31), 4) == "2025-01-01"
    assert quarter_start(date(2026, 12, 31), 1) == "2026-07-01"


def test_a_gurus_quarters_are_its_filers_reports_stored_under_the_guru(tmp_path):
    # Pershing Square Inc. reports Ackman's portfolio from Q2 2026, when the earlier filer's report became a notice.
    source = _Filers(
        feeds={"2026053": [("P-3", "13F-HR", "2026-08-14", "2026-06-30")],
               "1336528": [("P-2", "13F-NT", "2026-08-14", "2026-06-30"),
                           ("P-1", "13F-HR", "2026-05-15", "2026-03-31"),
                           ("P-0", "13F-HR", "2024-11-14", "2024-09-30")]},  # before the window
        filings={"P-3": _13f("P-3", "2026-06-30", [600, 400]), "P-1": _13f("P-1", "2026-03-31", [500])})
    db = tmp_path / "gurus.db"
    summary = collect_gurus(db, [ACKMAN], 8, source, today="2026-10-22")
    assert summary == {"2026053": {"listed": 2, "stored": 2, "checksum_failures": [], "errors": []}}
    assert source.since == ["2024-10-01", "2024-10-01"]
    assert sorted(source.fetched) == [("1336528", "P-1"), ("2026053", "P-3")]  # neither the notice nor P-0
    assert _rows(db, "SELECT accession, guru_cik, filer_cik, form, period, filed, entry_total, value_total, rows, "
                     "rows_value, checksum_ok FROM guru_filings ORDER BY accession") == [
        ("P-1", "2026053", "1336528", "13F-HR", "2026-03-31", "2026-05-15", 1, 500.0, 1, 500.0, 1),
        ("P-3", "2026053", "2026053", "13F-HR", "2026-06-30", "2026-08-14", 2, 1000.0, 2, 1000.0, 1)]
    assert _rows(db, "SELECT accession, row, guru_cik, cusip, value_usd, shares, share_type FROM guru_holdings "
                     "ORDER BY accession, row") == [
        ("P-1", 0, "2026053", "000000000", 500.0, 10.0, "SH"),
        ("P-3", 0, "2026053", "000000000", 600.0, 10.0, "SH"),
        ("P-3", 1, "2026053", "000000001", 400.0, 10.0, "SH")]


def test_a_filing_that_fails_to_fetch_or_parse_is_reported_and_left_out(tmp_path):
    source = _Filers(
        feeds={"1709323": [("H-3", "13F-HR", "2026-08-14", "2026-06-30"), ("H-2", "13F-HR", "2026-05-15", "2026-03-31"),
                           ("H-1", "13F-HR", "2026-02-17", "2025-12-31")]},
        filings={"H-3": _13f("H-3", "2026-06-30", [100]), "H-2": OSError("HTTP 503"),
                 "H-1": ThirteenFError("H-1: no INFORMATION TABLE document")})
    db = tmp_path / "gurus.db"
    summary = collect_gurus(db, [LI_LU], 8, source, today="2026-10-22")["1709323"]
    assert (summary["listed"], summary["stored"]) == (3, 1)
    assert [e.split(":")[0] for e in summary["errors"]] == ["H-2", "H-1"]
    assert _rows(db, "SELECT accession FROM guru_filings") == [("H-3",)]
    assert _rows(db, "SELECT DISTINCT accession FROM guru_holdings") == [("H-3",)]  # nothing stored as empty


def test_a_failed_feed_is_reported_for_its_filer_and_the_others_are_read(tmp_path):
    source = _Filers(feeds={"2026053": OSError("CIK0002026053.json: HTTP 503"),
                            "1336528": [("P-1", "13F-HR", "2026-05-15", "2026-03-31")]},
                     filings={"P-1": _13f("P-1", "2026-03-31", [500])})
    summary = collect_gurus(tmp_path / "gurus.db", [ACKMAN], 8, source, today="2026-10-22")["2026053"]
    assert (summary["listed"], summary["stored"]) == (1, 1)
    assert len(summary["errors"]) == 1 and summary["errors"][0].startswith("2026053")


def test_a_checksum_failure_is_stored_flagged_and_reported(tmp_path):
    # Two entries may differ from the total by 1.5 units of rounding; these, by 5.
    source = _Filers(feeds={"1709323": [("H-1", "13F-HR", "2026-08-14", "2026-06-30")]},
                     filings={"H-1": _13f("H-1", "2026-06-30", [100, 200], value_total=305)})
    db = tmp_path / "gurus.db"
    summary = collect_gurus(db, [LI_LU], 8, source, today="2026-10-22")["1709323"]
    assert (summary["stored"], summary["checksum_failures"]) == (1, ["H-1"])
    assert _rows(db, "SELECT entry_total, value_total, rows, rows_value, checksum_ok FROM guru_filings") == [
        (2, 305.0, 2, 300.0, 0)]


def test_a_guru_filings_table_from_before_the_multiplier_gets_its_column(tmp_path):
    # A database whose guru_filings predates the multiplier: the column is added, and the values are in dollars by it.
    db = tmp_path / "gurus.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE guru_filings (accession TEXT PRIMARY KEY, guru_cik TEXT NOT NULL, "
                 "filer_cik TEXT NOT NULL, form TEXT, period TEXT, filed TEXT, report_type TEXT, amendment_type TEXT, "
                 "entry_total INTEGER, value_total REAL, rows INTEGER, rows_value REAL, checksum_ok INTEGER, "
                 "confidential_omitted INTEGER, collected_at TEXT NOT NULL)")
    conn.commit()
    conn.close()
    source = _Filers(feeds={"1709323": [("H-1", "13F-HR", "2026-08-14", "2026-06-30")]},
                     filings={"H-1": _13f("H-1", "2026-06-30", [100, 200], multiplier=1000)})
    collect_gurus(db, [LI_LU], 8, source, today="2026-10-22")
    assert _rows(db, "SELECT accession, multiplier, value_total, rows_value FROM guru_filings") == [
        ("H-1", 1000, 300000.0, 300000.0)]
    assert _rows(db, "SELECT row, value_usd FROM guru_holdings ORDER BY row") == [(0, 100000.0), (1, 200000.0)]


def test_a_rerun_replaces_a_filings_rows(tmp_path):
    db = tmp_path / "gurus.db"
    feeds = {"1709323": [("H-1", "13F-HR", "2026-08-14", "2026-06-30")]}
    collect_gurus(db, [LI_LU], 8, _Filers(feeds, {"H-1": _13f("H-1", "2026-06-30", [100, 200, 300])}),
                  today="2026-10-22")
    collect_gurus(db, [LI_LU], 8, _Filers(feeds, {"H-1": _13f("H-1", "2026-06-30", [700])}), today="2026-10-22")
    assert _rows(db, "SELECT accession, entry_total, rows, rows_value FROM guru_filings") == [("H-1", 1, 1, 700.0)]
    assert _rows(db, "SELECT accession, row, value_usd FROM guru_holdings") == [("H-1", 0, 700.0)]


def test_a_filing_an_earlier_run_stored_and_this_one_couldnt_fetch_stays(tmp_path):
    db = tmp_path / "gurus.db"
    feeds = {"1709323": [("H-2", "13F-HR", "2026-08-14", "2026-06-30"), ("H-1", "13F-HR", "2026-05-15", "2026-03-31")]}
    collect_gurus(db, [LI_LU], 8, _Filers(feeds, {"H-2": _13f("H-2", "2026-06-30", [100]),
                                                  "H-1": _13f("H-1", "2026-03-31", [200, 300])}), today="2026-10-22")
    summary = collect_gurus(db, [LI_LU], 8, _Filers(feeds, {"H-2": _13f("H-2", "2026-06-30", [150]),
                                                            "H-1": OSError("HTTP 503")}), today="2026-10-22")
    assert summary["1709323"]["stored"] == 1 and len(summary["1709323"]["errors"]) == 1
    assert _rows(db, "SELECT accession, rows_value FROM guru_filings ORDER BY accession") == [
        ("H-1", 500.0), ("H-2", 150.0)]
    assert _rows(db, "SELECT COUNT(*) FROM guru_holdings WHERE accession = 'H-1'") == [(2,)]


def test_the_curated_filers_are_upserted_as_each_guru_is_stored(tmp_path):
    db = tmp_path / "gurus.db"
    collect_gurus(db, [ACKMAN, LI_LU], 8, _Filers({}, {}), today="2026-10-22")
    renamed = Guru("1709323", "HIMALAYA CAPITAL MANAGEMENT LLC", "Li Lu")
    collect_gurus(db, [renamed], 8, _Filers({}, {}), today="2026-10-22")
    assert _rows(db, "SELECT filer_cik, guru_cik, name, manager FROM guru_filers ORDER BY filer_cik") == [
        ("1336528", "2026053", "Pershing Square Capital Management, L.P.", "Bill Ackman"),  # its own name
        ("1709323", "1709323", "HIMALAYA CAPITAL MANAGEMENT LLC", "Li Lu"),
        ("2026053", "2026053", "PERSHING SQUARE INC.", "Bill Ackman")]


def test_the_guru_tables(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "gurus.db")
    conn = store._connect()
    store._create_schema(conn)
    columns = {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
               for t in ("guru_filers", "guru_filings", "guru_holdings")}
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    conn.close()
    assert columns == {
        "guru_filers": ["filer_cik", "guru_cik", "name", "manager"],
        "guru_filings": ["accession", "guru_cik", "filer_cik", "form", "period", "filed", "report_type",
                         "amendment_type", "entry_total", "value_total", "rows", "rows_value", "checksum_ok",
                         "confidential_omitted", "collected_at", "multiplier"],
        "guru_holdings": ["accession", "row", "guru_cik", "cusip", "issuer", "title_of_class", "value_usd", "shares",
                          "share_type", "put_call", "discretion"],
    }
    assert {"idx_guru_filings_guru", "idx_guru_holdings_cusip", "idx_guru_holdings_guru"} <= indexes


def test_the_13f_source_fetches_the_full_submission_text_once_then_reads_the_cache(tmp_path):
    sec = _himalaya_sec()
    ref = thirteenf_refs([_page([HIMALAYA_ROW])], "2022-07-01", "1709323")[0]
    first = ThirteenFSource(sec, tmp_path / "cache").filing("1709323", ref)
    again = ThirteenFSource(_SEC({}), tmp_path / "cache").filing("1709323", ref)  # no route: the cache serves
    assert first == again and (first.rows, first.checksum_ok) == (6, True)
    assert sec.requests == [HIMALAYA_TEXT]
    assert (tmp_path / "cache" / "13f" / "0001709323-22-000005.txt").exists()


def test_a_cached_text_that_no_longer_parses_is_fetched_again(tmp_path):
    cached = tmp_path / "cache" / "13f" / "0001709323-22-000005.txt"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"<html>a download cut short</html>")
    sec = _himalaya_sec()
    ref = thirteenf_refs([_page([HIMALAYA_ROW])], "2022-07-01", "1709323")[0]
    assert ThirteenFSource(sec, tmp_path / "cache").filing("1709323", ref).rows == 6
    assert sec.requests == [HIMALAYA_TEXT]
    assert cached.read_bytes() == (FIXTURES / HIMALAYA).read_bytes()  # the good copy replaced the bad one


def test_collect_gurus_from_the_feed_to_the_tables_in_dollars(tmp_path):
    db = tmp_path / "gurus.db"
    lines = []
    summary = collect_gurus(db, [LI_LU], 2, ThirteenFSource(_himalaya_sec(), tmp_path / "cache"), today="2023-01-10",
                            log=lines.append)
    assert summary == {"1709323": {"listed": 1, "stored": 1, "checksum_failures": [], "errors": []}}
    assert _rows(db, "SELECT accession, guru_cik, filer_cik, form, period, filed, report_type, amendment_type, "
                     "entry_total, value_total, rows, rows_value, checksum_ok, confidential_omitted, multiplier "
                     "FROM guru_filings") == [
        ("0001709323-22-000005", "1709323", "1709323", "13F-HR", "2022-09-30", "2022-11-14", "13F HOLDINGS REPORT",
         None, 6, 1743552000.0, 6, 1743552000.0, 1, 0, 1000)]
    assert _rows(db, "SELECT issuer, title_of_class, cusip, value_usd, shares, share_type, put_call, discretion "
                     "FROM guru_holdings WHERE cusip = '060505104'") == [
        ("BK OF AMERICA CORP", "COM", "060505104", 440527000.0, 14586987.0, "SH", None, "SOLE")]
    assert len(lines) == 1 and lines[0].isascii() and "1 stored" in lines[0]


def test_a_text_that_isnt_a_13f_is_reported_not_cached_and_not_stored(tmp_path):
    db = tmp_path / "gurus.db"
    source = ThirteenFSource(_himalaya_sec(b"<html>moved</html>"), tmp_path / "cache")
    summary = collect_gurus(db, [LI_LU], 2, source, today="2023-01-10")["1709323"]
    assert summary["stored"] == 0 and summary["errors"][0].startswith("0001709323-22-000005")
    assert not (tmp_path / "cache" / "13f" / "0001709323-22-000005.txt").exists()
    assert _rows(db, "SELECT COUNT(*) FROM guru_filings") == [(0,)]


def test_the_cli_collects_the_insiders_the_gurus_or_both(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(collect, "collect_insiders",
                        lambda db, tickers, years, source, **kw: calls.append(("insiders", tickers, years)) or {})
    monkeypatch.setattr(collect, "collect_gurus", lambda db, gurus, quarters, source, **kw: calls.append(
        ("gurus", tuple(gurus), quarters, type(source).__name__)) or {})
    cache = ["--cache-dir", str(tmp_path / "cache")]
    # The gurus alone don't read the companies: a database without them will do.
    assert collect.main(["--gurus", "--db", str(tmp_path / "fresh.db")] + cache) == 0
    assert calls == [("gurus", GURUS, 8, "ThirteenFSource")]
    out = capsys.readouterr().out
    assert "13F filings stored: 0 for 0 gurus" in out and out.isascii()
    db = ["--db", str(_db(tmp_path))] + cache
    calls.clear()
    collect.main(["--insiders", "--gurus", "--quarters", "4", "pg"] + db)
    assert calls == [("insiders", ["PG"], 3.0), ("gurus", GURUS, 4, "ThirteenFSource")]
    calls.clear()
    collect.main(["--insiders"] + db)
    assert calls == [("insiders", ["PG"], 3.0)]  # every company in the DB


def test_the_cli_refuses_to_collect_nothing(tmp_path, capsys):
    with pytest.raises(SystemExit):
        collect.main(["--db", str(tmp_path / "x.db")])
    assert "--gurus" in capsys.readouterr().err


# ---- the CUSIP map ------------------------------------------------------------------------------------------------

APPLE = "037833100"
AON = "G0403H108"
AON_LISTING = Listing(ticker="AON", name="AON PLC-CLASS A", security_type="Common Stock", exch_code="US",
                      figi="BBG00LLW2MF2")


class _FIGI:
    """A fake OpenFIGI client: each CUSIP's listing (None: no match), the error a lookup holding it raises, or the
    error OpenFIGI answers its job with (a str: the lookup raises JobErrors, the others answered). Records each
    lookup's CUSIPs."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def lookup(self, cusips):
        self.asked.append(list(cusips))
        for cusip in cusips:
            if isinstance(self.answers.get(cusip), Exception):
                raise self.answers[cusip]
        failed = {cusip: self.answers[cusip] for cusip in cusips if isinstance(self.answers[cusip], str)}
        found = {cusip: self.answers[cusip] for cusip in cusips if cusip not in failed}
        if failed:
            raise JobErrors(found, failed)
        return found


def _holdings_db(tmp_path, rows):
    """A database whose gurus hold *rows*, (cusip, issuer) each, in one filing."""
    store = SQLiteStore(db_path=tmp_path / "map.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.executemany("INSERT INTO guru_holdings (accession, row, guru_cik, cusip, issuer) VALUES ('A-1', ?, ?, ?, ?)",
                     [(i, "1067983", cusip, issuer) for i, (cusip, issuer) in enumerate(rows)])
    conn.commit()
    conn.close()
    return tmp_path / "map.db"


def test_the_cusip_map_table(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "gurus.db")
    conn = store._connect()
    store._create_schema(conn)
    columns = [(r[1], r[2], r[3], r[5]) for r in conn.execute("PRAGMA table_info(cusip_map)")]
    conn.close()
    assert columns == [("cusip", "TEXT", 0, 1), ("ticker", "TEXT", 0, 0), ("name", "TEXT", 0, 0),
                       ("source", "TEXT", 1, 0), ("security_type", "TEXT", 0, 0), ("mapped_at", "TEXT", 1, 0)]


def test_the_cusip_map_is_upserted_in_one_transaction(tmp_path):
    db = tmp_path / "gurus.db"
    store = SQLiteStore(db_path=db)
    row = {"cusip": APPLE, "ticker": "AAPL", "name": "APPLE INC", "source": "openfigi", "security_type": "Common Stock",
           "mapped_at": "2026-10-22T00:00:00+00:00"}
    store.export_cusip_map([row])
    store.export_cusip_map([dict(row, ticker="AAPL2", source="universe", security_type=None)])
    assert _rows(db, "SELECT cusip, ticker, name, source, security_type FROM cusip_map") == [
        (APPLE, "AAPL2", "APPLE INC", "universe", None)]
    with pytest.raises(sqlite3.IntegrityError):  # a row without its source: nothing of the batch is written
        store.export_cusip_map([dict(row, cusip=AON, ticker="AON"), dict(row, cusip="000000000", source=None)])
    assert _rows(db, "SELECT cusip FROM cusip_map") == [(APPLE,)]


def test_map_cusips_takes_ours_from_the_universe_and_asks_openfigi_for_the_rest(tmp_path):
    db = _holdings_db(tmp_path, [(APPLE, "APPLE INC"), (AON, "AON PLC"), ("000000000", "GONE CORP")])
    figi = _FIGI({AON: AON_LISTING, "000000000": None})
    summary = map_cusips(db, figi, universe={APPLE: "AAPL", "594918104": "MSFT"})  # no guru holds MSFT
    assert summary == {"universe": 1, "openfigi": 1, "none": 1, "errors": []}
    assert figi.asked == [["000000000", AON]]  # not Apple
    assert _rows(db, "SELECT cusip, ticker, name, source, security_type FROM cusip_map ORDER BY cusip") == [
        ("000000000", None, "GONE CORP", "none", None),  # OpenFIGI found nothing: the holdings' name
        (APPLE, "AAPL", "APPLE INC", "universe", None),
        (AON, "AON", "AON PLC-CLASS A", "openfigi", "Common Stock")]
    [(mapped_at,)] = _rows(db, "SELECT DISTINCT mapped_at FROM cusip_map")  # the run's time, in UTC
    assert datetime.fromisoformat(mapped_at).utcoffset() == timedelta(0)


def test_the_universe_overrides_an_earlier_row_and_a_mapped_cusip_isnt_asked_again(tmp_path):
    db = _holdings_db(tmp_path, [(APPLE, "APPLE INC"), (AON, "AON PLC")])
    map_cusips(db, _FIGI({APPLE: Listing("AAPL", "APPLE INC", "Common Stock", "US", "BBG000B9XRY4"),
                          AON: AON_LISTING}), universe={})
    figi = _FIGI({})
    summary = map_cusips(db, figi, universe={APPLE: "AAPL"})
    assert figi.asked == [] and summary == {"universe": 1, "openfigi": 0, "none": 0, "errors": []}
    assert _rows(db, "SELECT cusip, ticker, source FROM cusip_map ORDER BY cusip") == [
        (APPLE, "AAPL", "universe"), (AON, "AON", "openfigi")]


def test_a_failed_lookup_is_reported_and_its_cusips_are_left_for_the_next_run(tmp_path):
    cusips = [f"{i:09d}" for i in range(25)]
    db = _holdings_db(tmp_path, [(cusip, f"ISSUER {cusip}") for cusip in cusips])
    answers = dict.fromkeys(cusips)
    answers[cusips[12]] = OSError("OpenFIGI: HTTP 503")
    figi = _FIGI(answers)
    summary = map_cusips(db, figi, universe={})
    assert figi.asked == [cusips[:10], cusips[10:20], cusips[20:]]  # a request's CUSIPs apart: a failure loses its own
    assert (summary["none"], len(summary["errors"])) == (15, 1)
    assert "HTTP 503" in summary["errors"][0] and all(cusip in summary["errors"][0] for cusip in cusips[10:20])
    assert _rows(db, "SELECT COUNT(*), MIN(cusip), MAX(cusip) FROM cusip_map WHERE cusip BETWEEN '000000010' AND "
                     "'000000019'") == [(0, None, None)]  # never stored as no match
    again = _FIGI(dict.fromkeys(cusips))
    map_cusips(db, again, universe={})
    assert again.asked == [cusips[10:20]]


def test_a_code_openfigi_refuses_is_reported_and_its_neighbours_are_stored(tmp_path):
    # A job OpenFIGI answers with an error is that code's failure alone, and a request that is answered isn't a
    # failed one (four in a row don't stop the run).
    cusips = [f"{i:09d}" for i in range(40)]
    db = _holdings_db(tmp_path, [(cusip, f"ISSUER {cusip}") for cusip in cusips])
    answers = dict.fromkeys(cusips)
    refused = cusips[4::10]
    for cusip in refused:
        answers[cusip] = "Unexpected error."
    figi = _FIGI(answers)
    summary = map_cusips(db, figi, universe={})
    assert len(figi.asked) == 4
    assert summary["none"] == 36 and summary["errors"] == [f"{c}: OpenFIGI: Unexpected error." for c in refused]
    stored = {cusip for (cusip,) in _rows(db, "SELECT cusip FROM cusip_map")}
    assert len(stored) == 36 and not stored & set(refused)  # a refused code never stored as no match
    again = _FIGI(answers)
    map_cusips(db, again, universe={})
    assert again.asked == [refused]  # only the refused codes asked again


def test_after_three_failed_requests_in_a_row_the_rest_is_left_for_the_next_run(tmp_path):
    cusips = [f"{i:09d}" for i in range(80)]
    db = _holdings_db(tmp_path, [(cusip, f"ISSUER {cusip}") for cusip in cusips])
    answers = dict.fromkeys(cusips)
    for request in (0, 1, 3, 4, 5):  # two fail, one is answered, then three fail in a row
        answers[cusips[request * 10]] = OSError("OpenFIGI: connect timed out")
    figi = _FIGI(answers)
    summary = map_cusips(db, figi, universe={})
    assert figi.asked == [cusips[i:i + 10] for i in range(0, 60, 10)]  # the last two requests never made
    assert summary["none"] == 10 and len(summary["errors"]) == 6
    assert summary["errors"][-1] == ("OpenFIGI: 3 requests failed in a row; 20 CUSIPs not asked, left for the next "
                                     "run")
    assert collect.OPENFIGI_FAILURES_IN_A_ROW == 3


class _NoMatchServer:
    """OpenFIGI's mapping API knowing none of the codes asked but *table*'s (code -> its job's result); the request
    numbered *stop_at* raises *stop* instead. Records each request's codes."""

    def __init__(self, stop_at=None, stop=None, table=None):
        self.stop_at, self.stop, self.table = stop_at, stop, table or {}
        self.requests = []

    def post(self, url, **kwargs):
        jobs = kwargs["json"]
        self.requests.append([job["idValue"] for job in jobs])
        if len(self.requests) == self.stop_at:
            raise self.stop
        return _Response(200, [self.table.get(job["idValue"], {"warning": "No identifier found."}) for job in jobs])


def _figi_client(tmp_path, server):
    """The app's OpenFIGI client on a fake *server*, its cache under *tmp_path*, without waits."""
    return OpenFIGI(tmp_path / "cache", session=server, limiter=RateLimiter(min_interval=0), sleep=lambda seconds: None)


def test_a_mapping_stopped_midway_is_resumed_from_the_cache(tmp_path):
    cusips = [f"{i:09d}" for i in range(25)]
    db = _holdings_db(tmp_path, [(cusip, f"ISSUER {cusip}") for cusip in cusips])
    with pytest.raises(KeyboardInterrupt):  # the run stopped at its third request, before it stored a row
        map_cusips(db, _figi_client(tmp_path, _NoMatchServer(stop_at=3, stop=KeyboardInterrupt())), universe={})
    assert _rows(db, "SELECT COUNT(*) FROM cusip_map") == [(0,)]
    server = _NoMatchServer()
    summary = map_cusips(db, _figi_client(tmp_path, server), universe={})
    assert server.requests == [cusips[20:]]  # the first twenty answered from the cache
    assert summary == {"universe": 0, "openfigi": 0, "none": 25, "errors": []}


def test_a_malformed_code_is_mapped_as_not_found_with_its_holdings_name(tmp_path):
    # OpenFIGI answers "Invalid idValue format." for a code whose check digit is wrong (a filer's 78462F953): the
    # code will never map, so it is 'none', not an error on every run.
    db = _holdings_db(tmp_path, [("78462F953", "SPDR S&P 500 ETF TR"), ("000000000", "GONE CORP")])
    server = _NoMatchServer(table={"78462F953": {"error": "Invalid idValue format."}})
    summary = map_cusips(db, _figi_client(tmp_path, server), universe={})
    assert summary == {"universe": 0, "openfigi": 0, "none": 2, "errors": []}
    assert _rows(db, "SELECT cusip, ticker, name, source FROM cusip_map ORDER BY cusip") == [
        ("000000000", None, "GONE CORP", "none"), ("78462F953", None, "SPDR S&P 500 ETF TR", "none")]


def test_without_openfigi_only_the_universe_rows(tmp_path):
    db = _holdings_db(tmp_path, [(APPLE, "APPLE INC"), (AON, "AON PLC")])
    summary = map_cusips(db, None, universe={APPLE: "AAPL"})
    assert summary == {"universe": 1, "openfigi": 0, "none": 0, "errors": []}
    assert _rows(db, "SELECT cusip, ticker, source FROM cusip_map") == [(APPLE, "AAPL", "universe")]


def test_a_cusip_that_isnt_one_is_reported_not_asked(tmp_path):
    db = _holdings_db(tmp_path, [(APPLE, "APPLE INC"), ("12345", "SHORT CORP"), ("03783310*", "STAR CORP")])
    figi = _FIGI({APPLE: None})
    summary = map_cusips(db, figi, universe={})
    assert figi.asked == [[APPLE]] and summary["none"] == 1
    assert summary["errors"] == ["03783310*: not a CUSIP (9 letters or digits)",
                                 "12345: not a CUSIP (9 letters or digits)"]


def test_a_cusips_name_is_the_one_most_of_its_holdings_give(tmp_path):
    db = _holdings_db(tmp_path, [(APPLE, "Apple Inc"), (APPLE, "APPLE INC"), (APPLE, "Apple Inc"),
                                 ("000000000", "B CORP"), ("000000000", "A CORP"), ("000000000", None)])
    map_cusips(db, _FIGI({"000000000": None}), universe={APPLE: "AAPL"})
    assert _rows(db, "SELECT cusip, name FROM cusip_map ORDER BY cusip") == [
        ("000000000", "A CORP"), (APPLE, "Apple Inc")]  # a tie: the first alphabetically


def test_map_cusips_on_a_database_without_holdings(tmp_path):
    summary = map_cusips(tmp_path / "fresh.db", _FIGI({}), universe={APPLE: "AAPL"})
    assert summary == {"universe": 0, "openfigi": 0, "none": 0, "errors": []}
    assert _rows(tmp_path / "fresh.db", "SELECT COUNT(*) FROM cusip_map") == [(0,)]


def test_the_cli_maps_the_cusips_after_collecting_the_gurus(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(collect, "collect_insiders", lambda db, tickers, years, source, **kw: calls.append(
        "insiders") or {})
    monkeypatch.setattr(collect, "collect_gurus", lambda db, gurus, quarters, source, **kw: calls.append(
        "gurus") or {})
    monkeypatch.setattr(collect, "map_cusips", lambda db, client, **kw: calls.append(("map", db, client)) or {
        "universe": 2, "openfigi": 3, "none": 1, "errors": ["X: HTTP 503"]})
    db = tmp_path / "x.db"
    args = ["--gurus", "--db", str(db), "--cache-dir", str(tmp_path / "cache")]
    assert collect.main(args) == 0
    (gurus, (step, mapped_db, client)) = calls
    assert (gurus, step, mapped_db) == ("gurus", "map", db)
    assert isinstance(client, OpenFIGI) and client.cache == tmp_path / "cache" / "openfigi"
    out = capsys.readouterr().out
    assert "CUSIPs mapped: 2 from our universe, 3 by OpenFIGI, 1 not found; errors: 1" in out and out.isascii()
    calls.clear()
    collect.main(args + ["--no-openfigi"])
    assert calls == ["gurus", ("map", db, None)]  # the universe rows only
    calls.clear()
    collect.main(["--insiders", "--db", str(_db(tmp_path)), "--cache-dir", str(tmp_path / "cache")])
    assert calls == ["insiders"]  # the insiders alone map nothing

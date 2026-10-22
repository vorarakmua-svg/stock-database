"""Collecting insiders' Form 4 filings (Milestone 3): a company's submissions feed back N years, each document
fetched once and cached, parsed and stored, without losing what a failed fetch didn't bring
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
import json
import sqlite3
from pathlib import Path

from src.exporters.sqlite_store import SQLiteStore
from src.ownership.collect import Form4Source, collect_insiders, form4_refs, raw_document

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ownership"
FEED = "https://data.sec.gov/submissions/CIK0000080424.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/80424"


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


def _db(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "own.db")
    conn = store._connect()
    store._create_schema(conn)
    conn.execute("INSERT INTO companies (ticker, cik, sector_class) VALUES ('PG', '0000080424', 'general')")
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


def test_collect_stores_each_owner_and_trade_and_the_amendment_supersedes_its_original(tmp_path):
    db = _db(tmp_path)
    summary = collect_insiders(db, ["PG"], 3, Form4Source(_SEC(_routes()), tmp_path / "cache"), today="2026-10-22")
    assert summary["PG"]["stored"] == 2 and summary["PG"]["errors"] == []
    conn = sqlite3.connect(db)
    filings = conn.execute("SELECT accession, form, filed_date, owner_cik, officer_title, superseded_by "
                           "FROM insider_filings ORDER BY accession").fetchall()
    assert filings == [
        ("0000080424-26-000150", "4", "2026-08-24", "0001825382", "Chf Rsch, Dev & Innov Officer",
         "0000080424-26-000160"),
        ("0000080424-26-000160", "4/A", "2026-08-27", "0001825382", "Chf Rsch, Dev & Innov Officer", None),
    ]
    trades = conn.execute("SELECT accession, seq, ticker, kind, date, code, shares, price FROM insider_trades "
                          "ORDER BY accession").fetchall()
    assert trades == [("0000080424-26-000150", 0, "PG", "nonderivative", "2026-08-20", "S", 3053.0, 143.79),
                      ("0000080424-26-000160", 0, "PG", "nonderivative", "2026-08-20", "S", 3053.0, 143.79)]


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

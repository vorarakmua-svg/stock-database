"""Collecting insiders' Form 4 filings and the gurus' 13F filings (Milestone 3): a filer's submissions feed back N
years or quarters, each document fetched once and cached, parsed and stored, without losing what a failed fetch
didn't bring (docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.ownership import collect
from src.ownership.collect import (
    Form4Source,
    ThirteenFSource,
    collect_gurus,
    collect_insiders,
    form4_refs,
    quarter_start,
    raw_document,
    thirteenf_refs,
)
from src.ownership.gurus import GURUS, Guru
from src.ownership.thirteenf import Holding, ThirteenF, ThirteenFError

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

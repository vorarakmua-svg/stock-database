"""Filling the companies' SIC codes offline (Milestone 5): ``python -m src.universe.sic``.

docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and industries"
"""
import json
import os
import sqlite3

import pytest

from src.config import StorageConfig
from src.exporters.sqlite_store import SQLiteStore
from src.fetchers.sec_handler import SECHandler
from src.universe.sic import CACHE_FILE, main, parse_args

APPLE = {"sic": "3571", "sic_description": "Electronic Computers", "name": "Apple Inc."}
PFIZER = {"sic": "2834", "sic_description": "Pharmaceutical Preparations"}
JPM = {"sic": "6021", "sic_description": "National Commercial Banks"}
MSFT = {"sic": "7372", "sic_description": "Services-Prepackaged Software"}


def _db(tmp_path, companies, old=False):
    """A database with *companies* ((ticker, cik, stored code or None) each) and a financials row each; *old*, its
    companies table from before the codes."""
    db = tmp_path / "stock.db"
    store = SQLiteStore(db_path=db)
    conn = store._connect()
    if old:
        conn.execute("CREATE TABLE companies (ticker TEXT PRIMARY KEY, cik TEXT, company_name TEXT, "
                     "sector_class TEXT, updated_at TEXT)")
    store._create_schema(conn)
    for ticker, cik, sic in companies:
        conn.execute("INSERT INTO companies (ticker, cik, company_name, sector_class, updated_at) "
                     "VALUES (?, ?, ?, 'general', '2026-08-04T10:00:00')", (ticker, cik, f"{ticker} Inc."))
        if sic is not None:
            conn.execute("UPDATE companies SET sic = ?, sic_description = 'Stored before' WHERE ticker = ?",
                         (sic, ticker))
        conn.execute("INSERT INTO financials_annual (ticker, fiscal_year, revenue) VALUES (?, 2025, 1.0e9)", (ticker,))
    conn.commit()
    conn.close()
    return db


def _export(json_dir, ticker, submissions):
    json_dir.mkdir(exist_ok=True)
    payload = {"ticker": ticker, "sector_class": "general", "financials_annual": {"2025": {"revenue": 1.0}}}
    if submissions is not None:
        payload["sec_submissions"] = submissions
    (json_dir / f"{ticker}.json").write_text(json.dumps(payload), encoding="utf-8")


def _cached(cache_dir, cik, submissions):
    cache_dir.mkdir(exist_ok=True)
    path = cache_dir / f"submissions_CIK{cik}.json"
    path.write_text(json.dumps({**submissions, "filings": []}), encoding="utf-8")
    return path


def _codes(db):
    conn = sqlite3.connect(db)
    try:
        return {t: (s, d) for t, s, d in conn.execute("SELECT ticker, sic, sic_description FROM companies")}
    finally:
        conn.close()


def _dump(db):
    """Every table's rows, but the companies' two code columns."""
    conn = sqlite3.connect(db)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        out = {}
        for table in tables:
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")
                       if not (table == "companies" and r[1] in ("sic", "sic_description"))]
            names = ", ".join(f'"{c}"' for c in columns)
            out[table] = sorted(map(repr, conn.execute(f"SELECT {names} FROM {table}")))
        return out
    finally:
        conn.close()


def _run(db, tmp_path, *extra):
    return main(["--db", str(db), "--json-dir", str(tmp_path / "json"), "--cache-dir", str(tmp_path / "cache"),
                 *extra])


def test_the_codes_come_from_the_json_exports_then_the_cache(tmp_path, capsys):
    db = _db(tmp_path, [("AAPL", "0000320193", None), ("PFE", "0000078003", None), ("JPM", "0000019617", None),
                        ("MSFT", "0000789019", None), ("NONE", "0000000001", None), ("KO", "0000021344", None)])
    _export(tmp_path / "json", "AAPL", APPLE)
    _export(tmp_path / "json", "KO", {"sic": "2080", "sic_description": "Beverages"})
    _cached(tmp_path / "cache", "0000078003", PFIZER)  # PFE: no JSON export
    _export(tmp_path / "json", "JPM", {"sic": "", "sic_description": ""})  # JPM: an export without the code
    _cached(tmp_path / "cache", "0000019617", JPM)
    _export(tmp_path / "json", "MSFT", MSFT)  # MSFT: both; the export comes first
    _cached(tmp_path / "cache", "0000789019", {"sic": "9999", "sic_description": "Nonclassifiable"})
    assert _run(db, tmp_path) == 0
    assert _codes(db) == {"AAPL": ("3571", "Electronic Computers"), "PFE": ("2834", "Pharmaceutical Preparations"),
                          "JPM": ("6021", "National Commercial Banks"),
                          "MSFT": ("7372", "Services-Prepackaged Software"), "NONE": (None, None),
                          "KO": ("2080", "Beverages")}
    out = capsys.readouterr().out
    assert "6 companies" in out and "6 without a code" in out
    assert "from the JSON exports: 3" in out and "from the SEC cache: 2" in out
    assert "still missing: 1" in out and "NONE" in out
    out.encode("cp1252")  # the console's encoding


def test_a_stored_code_is_kept(tmp_path, capsys):
    db = _db(tmp_path, [("AAPL", "0000320193", "3571"), ("PFE", "0000078003", None)])
    _export(tmp_path / "json", "AAPL", PFIZER)  # never asked: AAPL has its code
    _export(tmp_path / "json", "PFE", PFIZER)
    assert _run(db, tmp_path) == 0
    assert _codes(db) == {"AAPL": ("3571", "Stored before"), "PFE": ("2834", "Pharmaceutical Preparations")}
    out = capsys.readouterr().out
    assert "1 without a code" in out and "still missing: 0" in out


def test_only_the_two_columns_are_written(tmp_path):
    db = _db(tmp_path, [("AAPL", "0000320193", None), ("PFE", "0000078003", None), ("NONE", "0000000001", None)])
    _export(tmp_path / "json", "AAPL", APPLE)
    _cached(tmp_path / "cache", "0000078003", PFIZER)
    before = _dump(db)
    assert _run(db, tmp_path) == 0
    assert _dump(db) == before  # updated_at too
    assert _codes(db)["AAPL"] == ("3571", "Electronic Computers")


def test_an_old_companies_table_gains_the_columns(tmp_path):
    db = _db(tmp_path, [("AAPL", "0000320193", None)], old=True)
    _export(tmp_path / "json", "AAPL", APPLE)
    assert _run(db, tmp_path) == 0
    assert _codes(db) == {"AAPL": ("3571", "Electronic Computers")}


def test_a_cache_file_is_read_whatever_its_age(tmp_path):
    db = _db(tmp_path, [("PFE", "0000078003", None)])
    path = _cached(tmp_path / "cache", "0000078003", PFIZER)
    os.utime(path, (0, 0))  # 1970: long past the handler's 7 days
    assert _run(db, tmp_path) == 0
    assert _codes(db)["PFE"] == ("2834", "Pharmaceutical Preparations")


def test_a_short_cik_is_padded_to_the_cache_s_name(tmp_path):
    db = _db(tmp_path, [("PFE", "78003", None)])
    _cached(tmp_path / "cache", "0000078003", PFIZER)
    assert _run(db, tmp_path) == 0
    assert _codes(db)["PFE"] == ("2834", "Pharmaceutical Preparations")


def test_an_export_that_does_not_read_falls_back_to_the_cache(tmp_path):
    db = _db(tmp_path, [("PFE", "0000078003", None), ("JPM", None, None)])
    (tmp_path / "json").mkdir()
    (tmp_path / "json" / "PFE.json").write_text("{not json", encoding="utf-8")
    _cached(tmp_path / "cache", "0000078003", PFIZER)
    _export(tmp_path / "json", "JPM", None)  # an export without its submissions section; no CIK for the cache
    assert _run(db, tmp_path) == 0
    assert _codes(db) == {"PFE": ("2834", "Pharmaceutical Preparations"), "JPM": (None, None)}


def test_the_codes_are_stored_as_codes(tmp_path):
    db = _db(tmp_path, [("AAPL", "0000320193", None), ("PFE", "0000078003", None)])
    _export(tmp_path / "json", "AAPL", {"sic": 3571, "sic_description": " Electronic Computers "})
    _export(tmp_path / "json", "PFE", {"sic": "28x4", "sic_description": "Pharmaceutical Preparations"})
    _cached(tmp_path / "cache", "0000078003", PFIZER)  # PFE's export holds no code: the cache's
    assert _run(db, tmp_path) == 0
    assert _codes(db) == {"AAPL": ("3571", "Electronic Computers"), "PFE": ("2834", "Pharmaceutical Preparations")}


def test_a_missing_database_is_exit_1_and_is_not_created(tmp_path, capsys):
    db = tmp_path / "none.db"
    assert _run(db, tmp_path) == 1
    assert not db.exists()
    assert "none.db" in capsys.readouterr().out


def test_a_database_without_companies_is_exit_1(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    assert _run(db, tmp_path) == 1


def test_the_cache_file_is_the_sec_handler_s(tmp_path):
    handler = SECHandler(cache_dir=tmp_path)
    try:
        assert handler._payload_cache_path("submissions", "0000320193") == tmp_path / CACHE_FILE.format(
            cik="0000320193")
    finally:
        handler.close()


def test_the_defaults_are_the_store_s():
    args = parse_args([])
    storage = StorageConfig()
    assert (args.db, args.json_dir, args.cache_dir) == (
        str(storage.database_path), str(storage.json_dir), str(storage.cache_dir))


@pytest.mark.parametrize("argv", [["--db"], ["--bogus"]])
def test_bad_arguments_are_refused(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)

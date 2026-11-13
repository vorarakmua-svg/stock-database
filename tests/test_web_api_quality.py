"""Tests for the data-quality & coverage API and dashboard (Task 7).

Fixtures from conftest.py:
- 4 tickers: AAA (general), BBB (bank), CCC (reit), EEE (general)
- AAA has 2 collection runs (2024-01-15, 2024-06-15)
- BBB, CCC, EEE each have 1 collection run (2024-01-15)
- AAA has unmapped_facts tag "SomeCustomTag"
- All 4 tickers have financials_annual with 'revenue' and 'total_assets' populated
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# /api/quality/latest
# ---------------------------------------------------------------------------

def test_latest_runs_ok(client):
    r = client.get("/api/quality/latest")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    tickers = {row["ticker"] for row in rows}
    assert tickers == {"AAA", "BBB", "CCC", "EEE"}


def test_latest_runs_has_quality_keys(client):
    r = client.get("/api/quality/latest")
    rows = r.json()
    for row in rows:
        assert "ticker" in row
        assert "collected_at" in row
        assert "quality_score" in row
        assert "warning_count" in row
        assert "error_count" in row
        assert "data_sources" in row


def test_latest_runs_aaa_newest(client):
    """AAA has two runs; latest must be the 2024-06-15 one."""
    r = client.get("/api/quality/latest")
    rows = r.json()
    aaa = next(row for row in rows if row["ticker"] == "AAA")
    assert aaa["collected_at"].startswith("2024-06-15")


# ---------------------------------------------------------------------------
# /api/quality/runs
# ---------------------------------------------------------------------------

def test_runs_all(client):
    r = client.get("/api/quality/runs")
    assert r.status_code == 200
    rows = r.json()
    # 5 total rows: AAA×2, BBB×1, CCC×1, EEE×1
    assert len(rows) == 5


def test_runs_ticker_filter(client):
    r = client.get("/api/quality/runs?ticker=AAA")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(row["ticker"] == "AAA" for row in rows)


def test_runs_newest_first(client):
    r = client.get("/api/quality/runs?ticker=AAA")
    rows = r.json()
    assert len(rows) == 2
    # first row must be the more-recent run
    assert rows[0]["collected_at"] > rows[1]["collected_at"]


def test_runs_give_their_warnings_as_a_list(tmp_path):
    # collection_runs keeps each run's warnings as JSON text (BUG-044); the API returns the list, not the text.
    from datetime import datetime

    from fastapi.testclient import TestClient

    from src.exporters.sqlite_store import SQLiteStore
    from src.models.stock_data import StockData
    from src.webapp import create_app

    stock = StockData(ticker="TSM", cik="0001046179", collected_at=datetime(2026, 10, 20))
    stock.add_warning("SEC EDGAR: no annual financials for TSM from CIK 0001046179 (TSMC): it reports in IFRS")
    SQLiteStore(tmp_path / "q.db").export([stock])
    rows = TestClient(create_app(db_path=tmp_path / "q.db")).get("/api/quality/runs?ticker=TSM").json()
    assert [row["warnings"] for row in rows] == [stock.warnings]


# ---------------------------------------------------------------------------
# /api/quality/coverage
# ---------------------------------------------------------------------------

def test_coverage_ok(client):
    r = client.get("/api/quality/coverage")
    assert r.status_code == 200
    body = r.json()
    assert "by_sector" in body
    assert "field_fill_rates" in body


def test_coverage_by_sector_structure(client):
    r = client.get("/api/quality/coverage")
    by_sector = r.json()["by_sector"]
    assert isinstance(by_sector, list)
    sectors = {s["sector_class"] for s in by_sector}
    assert "general" in sectors
    assert "bank" in sectors
    assert "reit" in sectors
    for s in by_sector:
        assert "n_companies" in s
        assert "median_quality" in s


def test_coverage_field_fill_rates_known_field(client):
    """'revenue' is present in all 4 companies' latest annual row -> fill_rate = 1.0."""
    r = client.get("/api/quality/coverage")
    ffr = r.json()["field_fill_rates"]
    assert isinstance(ffr, dict)
    assert ffr["revenue"] == pytest.approx(1.0)
    assert ffr["total_assets"] == pytest.approx(1.0)


def test_coverage_field_fill_rates_range(client):
    r = client.get("/api/quality/coverage")
    ffr = r.json()["field_fill_rates"]
    for field, rate in ffr.items():
        assert 0.0 <= rate <= 1.0, f"fill_rate out of range for {field}: {rate}"


def test_coverage_sector_filter(client):
    r = client.get("/api/quality/coverage?sector=bank")
    assert r.status_code == 200
    body = r.json()
    ffr = body["field_fill_rates"]
    # With only bank (BBB), revenue still present -> 1.0
    assert ffr["revenue"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# /api/quality/unmapped
# ---------------------------------------------------------------------------

def test_unmapped_ok(client):
    r = client.get("/api/quality/unmapped")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1


def test_unmapped_contains_fixture_tag(client):
    r = client.get("/api/quality/unmapped")
    tags = [row["tag"] for row in r.json()]
    assert "SomeCustomTag" in tags


def test_unmapped_has_fields(client):
    r = client.get("/api/quality/unmapped")
    row = r.json()[0]
    assert "ticker" in row
    assert "tag" in row
    assert "label" in row


# ---------------------------------------------------------------------------
# /api/quality/unmapped/top
# ---------------------------------------------------------------------------

def test_unmapped_top_ok(client):
    r = client.get("/api/quality/unmapped/top")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1


def test_unmapped_top_has_n_companies(client):
    r = client.get("/api/quality/unmapped/top")
    row = r.json()[0]
    assert "tag" in row
    assert "n_companies" in row
    assert "example_label" in row
    assert row["n_companies"] >= 1


# ---------------------------------------------------------------------------
# /api/quality/freshness
# ---------------------------------------------------------------------------

def test_freshness_ok(client):
    r = client.get("/api/quality/freshness")
    assert r.status_code == 200


def test_freshness_n_tickers(client):
    r = client.get("/api/quality/freshness")
    body = r.json()
    assert body["n_tickers"] == 4


def test_freshness_table_counts(client):
    r = client.get("/api/quality/freshness")
    body = r.json()
    assert "table_counts" in body
    tc = body["table_counts"]
    for tbl in ("companies", "financials_annual", "collection_runs", "unmapped_facts"):
        assert tbl in tc, f"table_counts missing key: {tbl}"
        assert tc[tbl] >= 0


def test_freshness_latest_company_update(client):
    r = client.get("/api/quality/freshness")
    body = r.json()
    assert "latest_company_update" in body


# ---------------------------------------------------------------------------
# UI smoke tests
# ---------------------------------------------------------------------------

def test_home_dashboard_ok(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Must contain a sector name visible on the dashboard
    assert "general" in html or "bank" in html or "reit" in html


def test_home_dashboard_has_quality_content(client):
    r = client.get("/")
    html = r.text.lower()
    # Dashboard must mention quality somewhere (table heading, badge, etc.)
    assert "quality" in html


def test_quality_page_ok(client):
    r = client.get("/quality")
    assert r.status_code == 200


def test_quality_page_has_canonical_field(client):
    r = client.get("/quality")
    html = r.text.lower()
    # 'revenue' is a canonical field that must appear in the fill-rate table
    assert "revenue" in html


def _runs_db(tmp_path, **stock_fields):
    from datetime import datetime

    from src.exporters.sqlite_store import SQLiteStore
    from src.models.stock_data import StockData
    stock = StockData(ticker="NEE", cik="0000753308", collected_at=datetime(2026, 10, 20), **stock_fields)
    stock.add_warning("SEC EDGAR: the companyfacts lack NEE's 10-Q of 2026-07-24")
    SQLiteStore(tmp_path / "q.db").export([stock])
    return tmp_path / "q.db"


def _runs(db):
    from fastapi.testclient import TestClient

    from src.webapp import create_app
    return TestClient(create_app(db_path=db)).get("/api/quality/runs?ticker=NEE").json()


def test_runs_always_carry_warnings_even_on_a_database_without_the_column(tmp_path):
    # BUG-069: rows from a database older than the column lacked the key.
    import sqlite3
    db = _runs_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE collection_runs DROP COLUMN warnings")
    conn.commit()
    conn.close()
    assert [row["warnings"] for row in _runs(db)] == [[]]


def test_runs_give_what_the_sec_read_lacked_as_an_object(tmp_path):
    # BUG-283: collection_runs.incomplete is JSON text; the API passed the text through.
    report = {"form": "10-Q", "filed": "2026-07-24", "accession": "0000753308-26-000042"}
    rows = _runs(_runs_db(tmp_path, missing_report=report))
    assert rows[0]["incomplete"] == {"report": report}


def test_the_fa_tab_shows_the_report_the_companyfacts_lack(tmp_path):
    # BUG-283: the run's SEC-lag warning explains why the figures stop a quarter early; FA showed nothing.
    from fastapi.testclient import TestClient

    from src.webapp import create_app
    db = _runs_db(tmp_path)
    body = TestClient(create_app(db_path=db)).get("/ui/stocks/NEE/fa").text
    assert "the companyfacts lack NEE&#39;s 10-Q of 2026-07-24" in body or "the companyfacts lack NEE's 10-Q" in body


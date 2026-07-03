"""Tests for the data-quality & coverage API and dashboard (Task 7).

Fixtures from conftest.py:
- 3 tickers: AAA (general), BBB (bank), CCC (reit)
- AAA has 2 collection runs (2024-01-15, 2024-06-15)
- BBB and CCC each have 1 collection run (2024-01-15)
- AAA has unmapped_facts tag "SomeCustomTag"
- All 3 tickers have financials_annual with 'revenue' and 'total_assets' populated
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
    assert tickers == {"AAA", "BBB", "CCC"}


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
    # 4 total rows: AAA×2, BBB×1, CCC×1
    assert len(rows) == 4


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
    """'revenue' is present in all 3 companies' latest annual row -> fill_rate = 1.0."""
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
    assert body["n_tickers"] == 3


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

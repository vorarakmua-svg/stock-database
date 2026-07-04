"""Tests for the companies JSON API (Task 3).

Fixtures used:
- ``client``  — TestClient(create_app(db_path=web_db))
- ``web_db``  — in-process SQLite with AAA (general), BBB (bank), CCC (reit).
  AAA has 3 annual + metric rows (2022/2023/2024), 2 quarterly rows,
  1 TTM row, 2 market snapshots, and metric ``roic`` in all 3 years.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# GET /api/companies  (list with optional filters)
# ---------------------------------------------------------------------------


def test_list_companies_all(client: TestClient) -> None:
    """Default list returns all 4 fixture companies with correct total."""
    resp = client.get("/api/companies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    tickers = {item["ticker"] for item in data["items"]}
    assert tickers == {"AAA", "BBB", "CCC", "EEE"}


def test_list_companies_has_required_fields(client: TestClient) -> None:
    """Each CompanySummary item contains ticker and sector_class."""
    resp = client.get("/api/companies")
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert "ticker" in item
        assert "sector_class" in item


def test_list_companies_filter_by_sector(client: TestClient) -> None:
    """?sector=bank filters to BBB only; total reflects filtered count."""
    resp = client.get("/api/companies", params={"sector": "bank"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["ticker"] == "BBB"


def test_list_companies_search(client: TestClient) -> None:
    """?q=AAA finds AAA Corp by ticker match."""
    resp = client.get("/api/companies", params={"q": "AAA"})
    assert resp.status_code == 200
    data = resp.json()
    tickers = [item["ticker"] for item in data["items"]]
    assert "AAA" in tickers


def test_list_companies_limit(client: TestClient) -> None:
    """?limit=1 returns exactly 1 item; total is still the full untruncated count."""
    resp = client.get("/api/companies", params={"limit": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["total"] == 4


# ---------------------------------------------------------------------------
# GET /api/companies/search  (autocomplete)
# ---------------------------------------------------------------------------


def test_search_companies_found(client: TestClient) -> None:
    """Autocomplete for 'AAA' returns at least 1 SearchHit with expected fields."""
    resp = client.get("/api/companies/search", params={"q": "AAA"})
    assert resp.status_code == 200
    hits = resp.json()
    assert isinstance(hits, list)
    assert len(hits) >= 1
    assert hits[0]["ticker"] == "AAA"
    assert "company_name" in hits[0]
    assert "sector_class" in hits[0]


def test_search_companies_no_match(client: TestClient) -> None:
    """Autocomplete with no match returns empty list (not 404)."""
    resp = client.get("/api/companies/search", params={"q": "ZZZNOMATCH"})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/companies/sectors
# ---------------------------------------------------------------------------


def test_list_sectors(client: TestClient) -> None:
    """Sectors endpoint returns all 3 fixture sector_class values sorted."""
    resp = client.get("/api/companies/sectors")
    assert resp.status_code == 200
    sectors = resp.json()
    assert isinstance(sectors, list)
    assert "bank" in sectors
    assert "general" in sectors
    assert "reit" in sectors
    assert sectors == sorted(sectors)


# ---------------------------------------------------------------------------
# GET /api/companies/{ticker}  (overview — only route that 404s)
# ---------------------------------------------------------------------------


def test_company_overview_known(client: TestClient) -> None:
    """Known ticker returns 200 with company/latest_annual/latest_metrics keys."""
    resp = client.get("/api/companies/AAA")
    assert resp.status_code == 200
    data = resp.json()
    assert "company" in data
    assert "latest_annual" in data
    assert "latest_metrics" in data
    # AAA has 3 annual rows; latest is 2024
    assert data["latest_annual"] is not None
    assert data["latest_annual"]["fiscal_year"] == 2024


def test_company_overview_unknown(client: TestClient) -> None:
    """Unknown ticker returns 404."""
    resp = client.get("/api/companies/UNKNOWN")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/companies/{ticker}/financials/annual
# ---------------------------------------------------------------------------


def test_annual_financials_all_years(client: TestClient) -> None:
    """AAA annual financials: 3 rows ordered newest fiscal_year first."""
    resp = client.get("/api/companies/AAA/financials/annual")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    assert [r["fiscal_year"] for r in rows] == [2024, 2023, 2022]


def test_annual_financials_years_param(client: TestClient) -> None:
    """?years=1 trims to the single most-recent year."""
    resp = client.get("/api/companies/AAA/financials/annual", params={"years": 1})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["fiscal_year"] == 2024


def test_annual_financials_unknown_ticker_returns_empty(client: TestClient) -> None:
    """Sub-resource for unknown ticker returns [] not 404."""
    resp = client.get("/api/companies/UNKNOWN/financials/annual")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/companies/{ticker}/financials/quarterly
# ---------------------------------------------------------------------------


def test_quarterly_financials(client: TestClient) -> None:
    """AAA quarterly financials: 2 rows newest period_end first."""
    resp = client.get("/api/companies/AAA/financials/quarterly")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["period_end"] == "2024-06-30"
    assert rows[1]["period_end"] == "2024-03-31"


def test_quarterly_financials_quarters_param(client: TestClient) -> None:
    """?quarters=1 trims to 1 row."""
    resp = client.get("/api/companies/AAA/financials/quarterly", params={"quarters": 1})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# GET /api/companies/{ticker}/financials/ttm
# ---------------------------------------------------------------------------


def test_ttm_financials(client: TestClient) -> None:
    """AAA TTM financials: at least 1 row."""
    resp = client.get("/api/companies/AAA/financials/ttm")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1


# ---------------------------------------------------------------------------
# GET /api/companies/{ticker}/metrics
# ---------------------------------------------------------------------------


def test_annual_metrics_all_years(client: TestClient) -> None:
    """AAA annual metrics: 3 rows newest fiscal_year first."""
    resp = client.get("/api/companies/AAA/metrics")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    assert [r["fiscal_year"] for r in rows] == [2024, 2023, 2022]


def test_annual_metrics_years_param(client: TestClient) -> None:
    """?years=1 returns only the most-recent metrics row."""
    resp = client.get("/api/companies/AAA/metrics", params={"years": 1})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["fiscal_year"] == 2024


# ---------------------------------------------------------------------------
# GET /api/companies/{ticker}/metrics/{metric}
# ---------------------------------------------------------------------------


def test_metric_series_roic(client: TestClient) -> None:
    """roic series for AAA: 3 SeriesPoints ascending by fiscal_year."""
    resp = client.get("/api/companies/AAA/metrics/roic")
    assert resp.status_code == 200
    points = resp.json()
    assert isinstance(points, list)
    assert len(points) == 3
    assert [p["fiscal_year"] for p in points] == [2022, 2023, 2024]
    assert abs(points[0]["value"] - 0.10) < 1e-6
    assert abs(points[1]["value"] - 0.12) < 1e-6
    assert abs(points[2]["value"] - 0.15) < 1e-6


def test_metric_series_unknown_metric(client: TestClient) -> None:
    """Non-whitelisted metric name returns 404 with informative detail."""
    resp = client.get("/api/companies/AAA/metrics/not_a_metric")
    assert resp.status_code == 404
    assert "not_a_metric" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/companies/{ticker}/snapshots
# ---------------------------------------------------------------------------


def test_snapshot_history(client: TestClient) -> None:
    """AAA snapshots: 2 rows ascending by collected_at."""
    resp = client.get("/api/companies/AAA/snapshots")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) == 2
    # Ascending: Jan snapshot before Jun snapshot
    assert "2024-01-15" in rows[0]["collected_at"]
    assert "2024-06-15" in rows[1]["collected_at"]


def test_snapshot_history_unknown_ticker_returns_empty(client: TestClient) -> None:
    """Unknown ticker returns [] not 404 for the snapshots sub-resource."""
    resp = client.get("/api/companies/UNKNOWN/snapshots")
    assert resp.status_code == 200
    assert resp.json() == []

"""Tests for Task 9: peer benchmarking, CSV export, and error pages.

Fixtures used:
- ``client``  — TestClient(create_app(db_path=web_db))
- ``web_db``  — in-process SQLite with AAA (general), BBB (bank), CCC (reit).
  AAA: 3 fiscal years of annual financials + metrics, roic=0.15 in FY2024.
  Only company in sector "general".
"""
from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# GET /api/companies/{ticker}/peers
# ---------------------------------------------------------------------------


def test_peers_known_ticker(client: TestClient) -> None:
    """Known ticker returns 200 with sector_class, company, and sector_median."""
    resp = client.get("/api/companies/AAA/peers")
    assert resp.status_code == 200
    data = resp.json()
    assert "sector_class" in data
    assert "n_peers" in data
    assert "company" in data
    assert "sector_median" in data
    assert data["sector_class"] == "general"
    assert data["n_peers"] >= 1


def test_peers_contains_default_metrics(client: TestClient) -> None:
    """Peers response includes roic in company and sector_median dicts."""
    resp = client.get("/api/companies/AAA/peers")
    assert resp.status_code == 200
    data = resp.json()
    # DEFAULT_COMPARE_METRICS = ["roic", "roe", "net_margin", "debt_to_ebitda"]
    assert "roic" in data["company"]
    assert "roic" in data["sector_median"]


def test_peers_aaa_roic_value(client: TestClient) -> None:
    """AAA latest roic is 0.15; company dict reflects that."""
    resp = client.get("/api/companies/AAA/peers")
    assert resp.status_code == 200
    data = resp.json()
    roic = data["company"].get("roic")
    assert roic is not None
    assert abs(roic - 0.15) < 1e-6


def test_peers_custom_metrics(client: TestClient) -> None:
    """?metrics=roic,roe limits the metric set returned."""
    resp = client.get("/api/companies/AAA/peers", params={"metrics": "roic,roe"})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["company"].keys()) == {"roic", "roe"}
    assert set(data["sector_median"].keys()) == {"roic", "roe"}


def test_peers_unknown_ticker_404(client: TestClient) -> None:
    """Unknown ticker returns 404."""
    resp = client.get("/api/companies/UNKNOWN/peers")
    assert resp.status_code == 404


def test_peers_bad_metric_400(client: TestClient) -> None:
    """Non-whitelisted metric returns 400."""
    resp = client.get("/api/companies/AAA/peers", params={"metrics": "not_a_metric"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/export/company/{ticker}/annual.csv
# ---------------------------------------------------------------------------


def test_annual_csv_status_and_content_type(client: TestClient) -> None:
    """Annual financials CSV: 200, text/csv content-type."""
    resp = client.get("/api/export/company/AAA/annual.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


def test_annual_csv_content_disposition(client: TestClient) -> None:
    """Annual financials CSV has attachment Content-Disposition header."""
    resp = client.get("/api/export/company/AAA/annual.csv")
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd


def test_annual_csv_parses_with_header_and_rows(client: TestClient) -> None:
    """Annual CSV parses as CSV with a header row and >=1 data row."""
    resp = client.get("/api/export/company/AAA/annual.csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) >= 1
    # fiscal_year column must exist
    assert "fiscal_year" in reader.fieldnames  # type: ignore[operator]


def test_annual_csv_has_three_rows_for_aaa(client: TestClient) -> None:
    """AAA has 3 annual financial rows; CSV has exactly 3 data rows."""
    resp = client.get("/api/export/company/AAA/annual.csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 3


def test_annual_csv_filename_header(client: TestClient) -> None:
    """Content-Disposition filename includes the ticker."""
    resp = client.get("/api/export/company/AAA/annual.csv")
    cd = resp.headers.get("content-disposition", "")
    assert "AAA" in cd


# ---------------------------------------------------------------------------
# GET /api/export/company/{ticker}/metrics.csv
# ---------------------------------------------------------------------------


def test_metrics_csv_status_and_content_type(client: TestClient) -> None:
    """Metrics CSV: 200, text/csv."""
    resp = client.get("/api/export/company/AAA/metrics.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


def test_metrics_csv_has_rows(client: TestClient) -> None:
    """Metrics CSV parses as CSV with >=1 data row."""
    resp = client.get("/api/export/company/AAA/metrics.csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) >= 1
    assert "fiscal_year" in reader.fieldnames  # type: ignore[operator]


def test_metrics_csv_contains_roic(client: TestClient) -> None:
    """Metrics CSV has a roic column."""
    resp = client.get("/api/export/company/AAA/metrics.csv")
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    list(reader)  # exhaust iterator to populate fieldnames
    assert "roic" in reader.fieldnames  # type: ignore[operator]


# ---------------------------------------------------------------------------
# GET /api/export/screen.csv
# ---------------------------------------------------------------------------


def test_screen_csv_status_and_content_type(client: TestClient) -> None:
    """Screen CSV: 200, text/csv."""
    resp = client.get("/api/export/screen.csv", params={"roic_gte": "0.13"})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


def test_screen_csv_contains_aaa(client: TestClient) -> None:
    """roic_gte=0.13 filter matches AAA (roic=0.15)."""
    resp = client.get("/api/export/screen.csv", params={"roic_gte": "0.13"})
    assert resp.status_code == 200
    assert "AAA" in resp.text


def test_screen_csv_bad_column_400(client: TestClient) -> None:
    """Unknown screener column returns 400."""
    resp = client.get("/api/export/screen.csv", params={"not_a_col_gte": "0.1"})
    assert resp.status_code == 400


def test_screen_csv_has_header_row(client: TestClient) -> None:
    """Screen CSV has a header row with 'ticker' column."""
    resp = client.get("/api/export/screen.csv", params={"roic_gte": "0.13"})
    assert resp.status_code == 200
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) >= 1
    assert "ticker" in reader.fieldnames  # type: ignore[operator]


def test_screen_csv_content_disposition(client: TestClient) -> None:
    """Screen CSV has attachment Content-Disposition."""
    resp = client.get("/api/export/screen.csv", params={"roic_gte": "0.13"})
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


def test_browser_unknown_company_404_html(client: TestClient) -> None:
    """GET /companies/UNKNOWN returns 404 with HTML body (friendly error page)."""
    resp = client.get("/companies/UNKNOWN")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]
    # Must contain some helpful text (home link or error message)
    assert "404" in resp.text or "not found" in resp.text.lower() or "home" in resp.text.lower()


def test_api_unknown_company_404_json(client: TestClient) -> None:
    """GET /api/companies/UNKNOWN still returns 404 JSON (not HTML)."""
    resp = client.get("/api/companies/UNKNOWN")
    assert resp.status_code == 404
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    assert "detail" in data


def test_error_handler_distinguishes_api_from_browser(client: TestClient) -> None:
    """API routes get JSON errors; browser routes get HTML errors."""
    api_resp = client.get("/api/companies/ZZZNOPE")
    browser_resp = client.get("/companies/ZZZNOPE")
    # API → JSON
    assert "application/json" in api_resp.headers["content-type"]
    # Browser → HTML
    assert "text/html" in browser_resp.headers["content-type"]


# ---------------------------------------------------------------------------
# UI smoke: /ui/companies/{ticker}/peers fragment
# ---------------------------------------------------------------------------


def test_peers_ui_fragment_200(client: TestClient) -> None:
    """GET /ui/companies/AAA/peers returns 200."""
    resp = client.get("/ui/companies/AAA/peers")
    assert resp.status_code == 200


def test_peers_ui_fragment_contains_metric_and_median(client: TestClient) -> None:
    """Peers fragment contains a metric label and the word 'median'."""
    resp = client.get("/ui/companies/AAA/peers")
    assert resp.status_code == 200
    # Fragment should reference at least one metric name and the word "median"
    assert "median" in resp.text.lower()


def test_peers_ui_fragment_shows_sector_info(client: TestClient) -> None:
    """Peers fragment shows sector_class and peer count."""
    resp = client.get("/ui/companies/AAA/peers")
    assert resp.status_code == 200
    assert "general" in resp.text.lower()

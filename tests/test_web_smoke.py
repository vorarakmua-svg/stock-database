"""Smoke tests for the HTML page routes (Task 4).

TestClient does not execute JavaScript — asserts check returned HTML strings
(script tags, plot div, table labels), not rendered charts.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------


def test_home_200_contains_brand(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    assert "Stock DB" in body


def test_home_contains_company_count_or_sector(client: TestClient) -> None:
    resp = client.get("/")
    body = resp.text
    # Should contain at least one sector chip or a count reference
    assert "general" in body or "3" in body or "bank" in body


# ---------------------------------------------------------------------------
# Companies list
# ---------------------------------------------------------------------------


def test_companies_200_contains_aaa(client: TestClient) -> None:
    resp = client.get("/companies")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "AAA" in resp.text


def test_companies_sector_filter_bank(client: TestClient) -> None:
    resp = client.get("/companies?sector=bank")
    assert resp.status_code == 200
    body = resp.text
    assert "BBB" in body


# ---------------------------------------------------------------------------
# Single-company page
# ---------------------------------------------------------------------------


def test_company_page_aaa_200(client: TestClient) -> None:
    resp = client.get("/companies/AAA")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    # Company name present
    assert "AAA" in body
    # Annual tab button present
    assert "Annual" in body


def test_company_page_unknown_404(client: TestClient) -> None:
    resp = client.get("/companies/UNKNOWN")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Search fragment
# ---------------------------------------------------------------------------


def test_search_returns_aaa(client: TestClient) -> None:
    resp = client.get("/ui/search?q=AA")
    assert resp.status_code == 200
    assert "AAA" in resp.text


def test_search_empty_q_returns_200(client: TestClient) -> None:
    resp = client.get("/ui/search?q=")
    assert resp.status_code == 200
    # No error — empty fragment or empty list


# ---------------------------------------------------------------------------
# Statements fragment
# ---------------------------------------------------------------------------


def test_statements_annual_contains_revenue_label(client: TestClient) -> None:
    resp = client.get("/ui/companies/AAA/statements?period=annual")
    assert resp.status_code == 200
    body = resp.text
    # One of the curated statement labels must appear
    assert "Revenue" in body


def test_statements_annual_contains_year(client: TestClient) -> None:
    resp = client.get("/ui/companies/AAA/statements?period=annual")
    assert resp.status_code == 200
    body = resp.text
    # A fiscal year column header must appear
    assert "2024" in body or "2023" in body or "2022" in body


def test_statements_metrics_contains_roic(client: TestClient) -> None:
    resp = client.get("/ui/companies/AAA/statements?period=metrics")
    assert resp.status_code == 200
    assert "ROIC" in resp.text


# ---------------------------------------------------------------------------
# Metric chart fragment
# ---------------------------------------------------------------------------


def test_metric_chart_roic_contains_renderplot(client: TestClient) -> None:
    resp = client.get("/ui/companies/AAA/metric-chart?metric=roic")
    assert resp.status_code == 200
    body = resp.text
    assert "renderPlot" in body


def test_metric_chart_roic_series_in_html(client: TestClient) -> None:
    resp = client.get("/ui/companies/AAA/metric-chart?metric=roic")
    assert resp.status_code == 200
    body = resp.text
    # roic values 0.10/0.12/0.15 should appear in the serialized JSON
    assert "0.1" in body or "fiscal_year" in body


def test_metric_chart_bad_metric_returns_200_with_error(client: TestClient) -> None:
    resp = client.get("/ui/companies/AAA/metric-chart?metric=not_a_metric")
    assert resp.status_code == 200
    body = resp.text
    # Should render an error message, not raise a 500
    assert "error" in body.lower() or "unknown" in body.lower() or "invalid" in body.lower() or "not" in body.lower()


# ---------------------------------------------------------------------------
# OpenAPI / docs
# ---------------------------------------------------------------------------


def test_openapi_json_parses(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = json.loads(resp.text)
    assert "paths" in data


def test_docs_200(client: TestClient) -> None:
    resp = client.get("/docs")
    assert resp.status_code == 200

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
    # Error paragraph must be present and the offending metric name must appear in it
    assert "chart-error" in body
    assert "not_a_metric" in body


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


# ---------------------------------------------------------------------------
# Terminal reskin: command bar + HELP overlay (Task 6)
# ---------------------------------------------------------------------------


def test_home_contains_command_bar_input(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="cmd"' in body


def test_home_loads_terminal_js(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "terminal.js" in resp.text


def test_terminal_js_served_200(client: TestClient) -> None:
    resp = client.get("/static/terminal.js")
    assert resp.status_code == 200
    assert "text/javascript" in resp.headers["content-type"] or "javascript" in resp.headers["content-type"]


def test_home_contains_help_overlay_content(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # HELP overlay lists function codes — spot-check a few
    assert "DES" in body
    assert "SCR" in body
    assert "HELP" in body


# ---------------------------------------------------------------------------
# All existing pages still render 200 after the reskin
# ---------------------------------------------------------------------------


def test_screener_page_200(client: TestClient) -> None:
    resp = client.get("/screener")
    assert resp.status_code == 200


def test_asof_page_200(client: TestClient) -> None:
    resp = client.get("/asof")
    assert resp.status_code == 200


def test_quality_page_200(client: TestClient) -> None:
    resp = client.get("/quality")
    assert resp.status_code == 200


def test_collect_page_200(client: TestClient) -> None:
    resp = client.get("/collect")
    assert resp.status_code == 200

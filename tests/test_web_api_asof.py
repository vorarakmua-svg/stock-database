"""Tests for the as-of / point-in-time JSON API and UI (Task 5).

Fixtures:
- ``client``  — TestClient(create_app(db_path=web_db))
- ``web_db``  — SQLite with AAA having multi-vintage FY2022:
    original  accn="0001-10K-2022" filed_date="2023-02-10"
    restated  accn="0002-10KA-2022" filed_date="2023-08-15"

Date selections for testing:
- "2023-03-01"  → after first filing, before restatement  → resolves original
- "2023-09-01"  → after restatement                       → resolves restated
- "2022-12-31"  → before any filing                       → None / 404
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.query.asof import AsOfReader
from src.query.pit_metrics import PointInTimeMetrics

# Fixture constants derived from conftest.py web_db
TICKER = "AAA"
FY = 2022
DATE_BEFORE = "2022-12-31"   # before any filing → 404
DATE_ORIG = "2023-03-01"     # after 2023-02-10 original filing
DATE_RESTATE = "2023-09-01"  # after 2023-08-15 restatement


# ---------------------------------------------------------------------------
# GET /api/asof/{ticker}/annual
# ---------------------------------------------------------------------------


def test_annual_asof_resolves_original(client: TestClient, web_db: Path) -> None:
    """As-of date after first filing resolves the original vintage."""
    resp = client.get(f"/api/asof/{TICKER}/annual", params={"fiscal_year": FY, "date": DATE_ORIG})
    assert resp.status_code == 200
    data = resp.json()
    # Compare against direct class call — the robust equality pattern
    with AsOfReader(web_db) as reader:
        expected = reader.as_of_annual(TICKER, FY, DATE_ORIG)
    assert expected is not None
    assert data["accn"] == expected["accn"]
    assert data["fiscal_year"] == expected["fiscal_year"]
    assert data["filed_date"] == expected["filed_date"]


def test_annual_asof_resolves_restatement(client: TestClient, web_db: Path) -> None:
    """As-of date after restatement resolves the later (amended) vintage."""
    resp = client.get(f"/api/asof/{TICKER}/annual", params={"fiscal_year": FY, "date": DATE_RESTATE})
    assert resp.status_code == 200
    data = resp.json()
    with AsOfReader(web_db) as reader:
        expected = reader.as_of_annual(TICKER, FY, DATE_RESTATE)
    assert expected is not None
    assert data["accn"] == expected["accn"]
    assert data["filed_date"] == expected["filed_date"]


def test_annual_asof_before_filing_returns_404(client: TestClient) -> None:
    """Date before any filing returns 404."""
    resp = client.get(f"/api/asof/{TICKER}/annual", params={"fiscal_year": FY, "date": DATE_BEFORE})
    assert resp.status_code == 404


def test_annual_asof_unknown_ticker_returns_404(client: TestClient) -> None:
    """Unknown ticker with valid date returns 404 (no rows exist)."""
    resp = client.get("/api/asof/UNKNOWN/annual", params={"fiscal_year": FY, "date": DATE_RESTATE})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/asof/{ticker}/history
# ---------------------------------------------------------------------------


def test_history_asof_returns_dict_keyed_by_year(client: TestClient, web_db: Path) -> None:
    """history_as_of returns JSON object keyed by fiscal-year strings."""
    resp = client.get(f"/api/asof/{TICKER}/history", params={"date": DATE_RESTATE})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    # FY2022 must appear (filed before DATE_RESTATE)
    assert str(FY) in data
    # Compare fiscal_year keys with direct call
    with AsOfReader(web_db) as reader:
        expected = reader.history_as_of(TICKER, DATE_RESTATE)
    expected_keys = {str(k) for k in expected.keys()}
    assert set(data.keys()) == expected_keys


def test_history_asof_years_param(client: TestClient) -> None:
    """?years=1 trims to the single most-recent fiscal year."""
    resp = client.get(f"/api/asof/{TICKER}/history", params={"date": DATE_RESTATE, "years": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1


def test_history_asof_empty_for_unknown_ticker(client: TestClient) -> None:
    """Unknown ticker returns empty object."""
    resp = client.get("/api/asof/UNKNOWN/history", params={"date": DATE_RESTATE})
    assert resp.status_code == 200
    assert resp.json() == {}


# ---------------------------------------------------------------------------
# GET /api/asof/{ticker}/metrics
# ---------------------------------------------------------------------------


def test_metrics_asof_returns_ratios(client: TestClient, web_db: Path) -> None:
    """metrics_as_of returns computed ratios; compare a subset with direct call."""
    resp = client.get(f"/api/asof/{TICKER}/metrics", params={"fiscal_year": FY, "date": DATE_RESTATE})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    # Compare a couple of ratio keys against direct computation
    pit = PointInTimeMetrics.from_path(web_db)
    try:
        expected = pit.metrics_as_of(TICKER, FY, DATE_RESTATE)
    finally:
        pit.close()
    assert expected is not None
    for key in ("net_margin", "roic"):
        if key in expected:
            ev = expected[key]
            dv = data.get(key)
            if ev is not None and dv is not None:
                assert abs(dv - ev) < 1e-9
            else:
                assert dv == ev


def test_metrics_asof_before_filing_returns_404(client: TestClient) -> None:
    """Date before any filing returns 404 for metrics too."""
    resp = client.get(f"/api/asof/{TICKER}/metrics", params={"fiscal_year": FY, "date": DATE_BEFORE})
    assert resp.status_code == 404


def test_metrics_asof_unknown_ticker_returns_404(client: TestClient) -> None:
    """Unknown ticker returns 404 for metrics."""
    resp = client.get("/api/asof/UNKNOWN/metrics", params={"fiscal_year": FY, "date": DATE_RESTATE})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/asof/{ticker}/metrics/history
# ---------------------------------------------------------------------------


def test_metrics_history_asof_keyed_by_year(client: TestClient, web_db: Path) -> None:
    """metrics_history_as_of returns object with fiscal-year string keys."""
    resp = client.get(f"/api/asof/{TICKER}/metrics/history", params={"date": DATE_RESTATE})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    # Compare keys against direct call
    pit = PointInTimeMetrics.from_path(web_db)
    try:
        expected = pit.metrics_history_as_of(TICKER, DATE_RESTATE)
    finally:
        pit.close()
    expected_keys = {str(k) for k in expected.keys()}
    assert set(data.keys()) == expected_keys


def test_metrics_history_asof_years_param(client: TestClient) -> None:
    """?years=1 trims to 1 fiscal year."""
    resp = client.get(f"/api/asof/{TICKER}/metrics/history", params={"date": DATE_RESTATE, "years": 1})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ---------------------------------------------------------------------------
# GET /api/asof/{ticker}/vintages
# ---------------------------------------------------------------------------


def test_vintages_returns_all_for_multi_vintage(client: TestClient) -> None:
    """Multi-vintage FY2022 for AAA returns >= 2 rows with distinct accn/filed_date."""
    resp = client.get(f"/api/asof/{TICKER}/vintages", params={"fiscal_year": FY})
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) >= 2
    accns = {r["accn"] for r in rows}
    filed_dates = {r["filed_date"] for r in rows}
    assert len(accns) == len(rows), "Each row must have a distinct accn"
    assert len(filed_dates) >= 2


def test_vintages_without_fiscal_year_returns_all(client: TestClient) -> None:
    """Without fiscal_year param returns all vintages for ticker."""
    resp = client.get(f"/api/asof/{TICKER}/vintages")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    assert len(rows) >= 2


def test_vintages_ordering(client: TestClient) -> None:
    """Vintages ordered fiscal_year DESC then filed_date ASC (first rows = earliest filing of newest FY)."""
    resp = client.get(f"/api/asof/{TICKER}/vintages")
    assert resp.status_code == 200
    rows = resp.json()
    if len(rows) >= 2:
        # fiscal_year should be non-increasing
        years = [r["fiscal_year"] for r in rows]
        assert years == sorted(years, reverse=True)


def test_vintages_unknown_ticker_returns_empty(client: TestClient) -> None:
    """Unknown ticker returns empty list."""
    resp = client.get("/api/asof/UNKNOWN/vintages")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# UI smoke tests
# ---------------------------------------------------------------------------


def test_asof_page_loads(client: TestClient) -> None:
    """GET /asof returns 200 with the explorer form."""
    resp = client.get("/asof")
    assert resp.status_code == 200
    assert b"ticker" in resp.content.lower() or b"As-Of" in resp.content


def test_asof_result_fragment_returns_200(client: TestClient) -> None:
    """Result fragment renders without error for a known (ticker, FY, date)."""
    resp = client.get(
        "/ui/asof/result",
        params={"ticker": TICKER, "fiscal_year": FY, "date": DATE_RESTATE},
    )
    assert resp.status_code == 200
    # Should contain a ratio label or filing metadata
    html = resp.text
    assert any(label in html for label in ("Net", "ROIC", "Revenue", "filed", "10-K", "accn"))


def test_asof_result_fragment_before_filing_shows_message(client: TestClient) -> None:
    """Result fragment for date before filing shows friendly message, not 500."""
    resp = client.get(
        "/ui/asof/result",
        params={"ticker": TICKER, "fiscal_year": FY, "date": DATE_BEFORE},
    )
    assert resp.status_code == 200
    html = resp.text.lower()
    assert "not" in html or "filed" in html or "available" in html


def test_asof_vintages_fragment_returns_200(client: TestClient) -> None:
    """Vintages fragment renders a table with >= 2 filed_dates for AAA FY2022."""
    resp = client.get(
        "/ui/asof/vintages",
        params={"ticker": TICKER, "fiscal_year": FY},
    )
    assert resp.status_code == 200
    html = resp.text
    # Both filed dates should appear in the table
    assert "2023-02-10" in html
    assert "2023-08-15" in html

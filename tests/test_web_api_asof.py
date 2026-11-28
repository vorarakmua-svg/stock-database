"""Tests for the as-of / point-in-time JSON API and UI (Task 5).

Fixtures:
- ``client``  — TestClient(create_app(db_path=web_db))
- ``web_db``  — SQLite with AAA having multi-vintage FY2022, and FY2022 as known from each filing:
    original  accn="0001-10K-2022" filed_date="2023-02-10"
    restated  accn="0002-10KA-2022" filed_date="2023-08-15"

Date selections for testing:
- "2023-03-01"  → after first filing, before restatement  → resolves original
- "2023-09-01"  → after restatement                       → resolves restated
- "2022-12-31"  → before any filing                       → None / 404
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.query.asof import AsOfReader
from src.query.pit_metrics import PointInTimeMetrics
from src.webapp import create_app
from tests.known_rows import build_db, snapshot

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


def test_history_asof_404_for_unknown_ticker(client: TestClient) -> None:
    """Unknown ticker answers 404 saying so (a backtest mustn't read it as a company with nothing filed)."""
    resp = client.get("/api/asof/UNKNOWN/history", params={"date": DATE_RESTATE})
    assert resp.status_code == 404
    assert "UNKNOWN is unknown" in resp.json()["detail"]


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


# ---------------------------------------------------------------------------
# BUG-110: the figures a later filing leaves out (the record's), named by carried_from
# ---------------------------------------------------------------------------


def _partial_refilings_client(tmp_path: Path) -> TestClient:
    """MSFT FY2016 as known from its FY2019 10-K, which holds only the opening equity: the income statement the
    FY2018 10-K's."""
    k18 = ("k18", "2018-08-03", "10-K")
    db = build_db(tmp_path, [snapshot(2016, "2019-08-01", accn="k19", period_end="2016-06-30", revenue=91.2e9,
                                      net_income=20.5e9, total_equity=83.1e9,
                                      earlier={"revenue": k18, "net_income": k18})], ticker="MSFT")
    return TestClient(create_app(db_path=Path(db)))


def test_annual_asof_returns_the_figures_a_later_filing_leaves_out(tmp_path: Path) -> None:
    resp = _partial_refilings_client(tmp_path).get(
        "/api/asof/MSFT/annual", params={"fiscal_year": 2016, "date": "2026-01-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert (data["revenue"], data["net_income"], data["total_equity"], data["accn"]) == (
        91.2e9, 20.5e9, 83.1e9, "k19")
    assert data["carried_from"]["revenue"] == {"accn": "k18", "filed_date": "2018-08-03", "form": "10-K"}


def test_asof_result_fragment_dates_a_figure_last_filed_earlier(tmp_path: Path) -> None:
    resp = _partial_refilings_client(tmp_path).get(
        "/ui/asof/result", params={"ticker": "MSFT", "fiscal_year": 2016, "date": "2026-01-01"})
    assert resp.status_code == 200
    html = resp.text
    assert "$91.20B" in html and "$20.50B" in html
    assert "(filed 2018-08-03)" in html


def test_asof_result_fragment_dates_only_the_figures_the_latest_filing_leaves_out(tmp_path: Path) -> None:
    # BUG-129 (surviving mutant: the date beside every figure): a figure the latest filing holds has no date beside it,
    # and no "(filed None)": only the revenue and the net income, carried from the FY2018 10-K, are dated.
    html = _partial_refilings_client(tmp_path).get(
        "/ui/asof/result", params={"ticker": "MSFT", "fiscal_year": 2016, "date": "2026-01-01"}).text
    assert html.count("(filed ") == 2
    assert "(filed None)" not in html and "(filed )" not in html


# ---------------------------------------------------------------------------
# A ticker whose as-of data isn't built (collected before the snapshots)
# ---------------------------------------------------------------------------


def test_a_ticker_without_snapshots_reads_none_and_the_page_says_not_built(tmp_path: Path) -> None:
    # Its vintages are stored, its snapshots not (a run that built none): no fallback to another rule.
    vintages = {"2022": {"k22": {"fiscal_year": 2022, "accn": "k22", "filed_date": "2023-02-10",
                                 "period_end": "2022-12-31", "form": "10-K", "net_income": 80.0}}}
    db = build_db(tmp_path, None, ticker="OLD", vintages=vintages)
    with AsOfReader(db) as reader:
        assert reader.has_snapshots("OLD") is False
        assert reader.as_of_annual("OLD", 2022, "2026-01-01") is None
        assert reader.history_as_of("OLD", "2026-01-01") == {}
    resp = TestClient(create_app(db_path=Path(db))).get(
        "/ui/asof/result", params={"ticker": "OLD", "fiscal_year": 2022, "date": "2026-01-01"})
    assert resp.status_code == 200
    assert "The as-of data for <strong>OLD</strong> isn't built yet" in resp.text
    assert "re-collect" in resp.text.lower()
    assert "had not been filed" not in resp.text


def test_a_ticker_with_snapshots_none_by_the_date_says_not_filed_yet(client: TestClient) -> None:
    resp = client.get("/ui/asof/result", params={"ticker": TICKER, "fiscal_year": FY, "date": DATE_BEFORE})
    assert resp.status_code == 200
    assert "had not been filed yet" in resp.text
    assert "isn't built yet" not in resp.text


def test_the_annual_api_says_a_ticker_without_snapshots_isnt_built(tmp_path: Path) -> None:
    vintages = {"2022": {"k22": {"fiscal_year": 2022, "accn": "k22", "filed_date": "2023-02-10",
                                 "period_end": "2022-12-31", "form": "10-K", "net_income": 80.0}}}
    db = build_db(tmp_path, None, ticker="OLD", vintages=vintages)
    resp = TestClient(create_app(db_path=Path(db))).get(
        "/api/asof/OLD/annual", params={"fiscal_year": 2022, "date": "2026-01-01"})
    assert resp.status_code == 404
    assert "isn't built yet" in resp.json()["detail"]
    assert "had not been filed" not in resp.json()["detail"]


def test_the_annual_api_says_a_built_tickers_year_wasnt_filed_yet(client: TestClient) -> None:
    resp = client.get(f"/api/asof/{TICKER}/annual", params={"fiscal_year": FY, "date": DATE_BEFORE})
    assert resp.status_code == 404
    assert "had not been filed" in resp.json()["detail"]
    assert "isn't built yet" not in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Every as-of endpoint tells an unbuilt or unknown ticker from a year not filed yet; tickers are normalised
# ---------------------------------------------------------------------------

_HISTORIES = ("history", "metrics/history")


def _old_client(tmp_path: Path) -> TestClient:
    """OLD is a collected company (its vintages stored) whose as-of data isn't built."""
    vintages = {"2022": {"k22": {"fiscal_year": 2022, "accn": "k22", "filed_date": "2023-02-10",
                                 "period_end": "2022-12-31", "form": "10-K", "net_income": 80.0}}}
    return TestClient(create_app(db_path=Path(build_db(tmp_path, None, ticker="OLD", vintages=vintages))))


@pytest.mark.parametrize("endpoint", _HISTORIES)
def test_a_history_of_a_ticker_without_snapshots_says_it_isnt_built(tmp_path: Path, endpoint: str) -> None:
    # A backtest over a partly rebuilt database mustn't read an unbuilt company as one with nothing filed.
    resp = _old_client(tmp_path).get(f"/api/asof/OLD/{endpoint}", params={"date": "2026-01-01"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "The as-of data for OLD isn't built yet: re-collecting OLD builds it"


@pytest.mark.parametrize("endpoint", _HISTORIES)
def test_a_history_of_a_built_ticker_with_nothing_by_the_date_is_empty(client: TestClient, endpoint: str) -> None:
    resp = client.get(f"/api/asof/{TICKER}/{endpoint}", params={"date": DATE_BEFORE})
    assert resp.status_code == 200
    assert resp.json() == {}


def test_the_metrics_of_a_ticker_without_snapshots_say_it_isnt_built(tmp_path: Path) -> None:
    resp = _old_client(tmp_path).get("/api/asof/OLD/metrics", params={"fiscal_year": 2022, "date": "2026-01-01"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "The as-of data for OLD isn't built yet: re-collecting OLD builds it"


def test_the_metrics_of_a_built_tickers_year_not_filed_yet_say_so(client: TestClient) -> None:
    resp = client.get(f"/api/asof/{TICKER}/metrics", params={"fiscal_year": FY, "date": DATE_BEFORE})
    assert resp.status_code == 404
    assert "not available as of" in resp.json()["detail"]
    assert "isn't built yet" not in resp.json()["detail"]


@pytest.mark.parametrize("endpoint, params", [
    ("annual", {"fiscal_year": FY, "date": DATE_RESTATE}),
    ("history", {"date": DATE_RESTATE}),
    ("metrics", {"fiscal_year": FY, "date": DATE_RESTATE}),
    ("metrics/history", {"date": DATE_RESTATE}),
])
def test_an_unknown_ticker_is_said_to_be_unknown(client: TestClient, endpoint: str, params: dict) -> None:
    resp = client.get(f"/api/asof/zzzz/{endpoint}", params=params)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "ZZZZ is unknown: no company ZZZZ has been collected"


@pytest.mark.parametrize("endpoint, params", [
    ("annual", {"fiscal_year": FY, "date": DATE_RESTATE}),
    ("history", {"date": DATE_RESTATE}),
    ("metrics", {"fiscal_year": FY, "date": DATE_RESTATE}),
    ("metrics/history", {"date": DATE_RESTATE}),
])
def test_the_api_reads_a_ticker_in_any_case(client: TestClient, endpoint: str, params: dict) -> None:
    resp = client.get(f"/api/asof/aaa%20/{endpoint}", params=params)
    assert resp.status_code == 200
    assert _untimed(resp.json()) == _untimed(client.get(f"/api/asof/{TICKER}/{endpoint}", params=params).json())


def _untimed(data: dict) -> dict:
    """*data* without the ratios' ``calculated_at`` stamps (each request's own)."""
    return {k: _untimed(v) if isinstance(v, dict) else v for k, v in data.items() if k != "calculated_at"}


def test_the_api_names_an_unbuilt_ticker_as_stored(tmp_path: Path) -> None:
    resp = _old_client(tmp_path).get("/api/asof/old/annual", params={"fiscal_year": 2022, "date": "2026-01-01"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "The as-of data for OLD isn't built yet: re-collecting OLD builds it"


def test_the_page_reads_a_ticker_in_any_case(client: TestClient) -> None:
    resp = client.get("/ui/asof/result", params={"ticker": " aaa ", "fiscal_year": FY, "date": DATE_RESTATE})
    assert resp.status_code == 200
    assert f"{TICKER} FY{FY} as of {DATE_RESTATE}" in resp.text
    resp = client.get("/ui/asof/vintages", params={"ticker": "aaa", "fiscal_year": FY})
    assert "2023-02-10" in resp.text and "2023-08-15" in resp.text


def test_the_page_says_an_unbuilt_tickers_name_as_stored(tmp_path: Path) -> None:
    resp = _old_client(tmp_path).get("/ui/asof/result", params={"ticker": "old", "fiscal_year": 2022,
                                                                 "date": "2026-01-01"})
    assert "The as-of data for <strong>OLD</strong> isn't built yet" in resp.text


def test_the_page_says_an_unknown_ticker_is_unknown(client: TestClient) -> None:
    resp = client.get("/ui/asof/result", params={"ticker": "zzzz", "fiscal_year": FY, "date": DATE_RESTATE})
    assert resp.status_code == 200
    assert "<strong>ZZZZ</strong> is unknown" in resp.text
    assert "isn't built yet" not in resp.text and "had not been filed" not in resp.text


def test_the_page_says_since_when_the_period_was_known(client: TestClient) -> None:
    resp = client.get("/ui/asof/result", params={"ticker": TICKER, "fiscal_year": FY, "date": DATE_ORIG})
    assert "as known from 2023-02-10" in resp.text
    resp = client.get("/ui/asof/result", params={"ticker": TICKER, "fiscal_year": FY, "date": DATE_RESTATE})
    assert "as known from 2023-08-15" in resp.text


def test_the_annual_api_says_since_when_the_period_was_known(client: TestClient) -> None:
    data = client.get(f"/api/asof/{TICKER}/annual", params={"fiscal_year": FY, "date": DATE_RESTATE}).json()
    assert (data["known_from"], data["known_accn"]) == ("2023-08-15", "0002-10KA-2022")

"""Tests for the per-stock workstation page and its DES/GP/FA/STAT/ERN fragments.

Uses the shared ``web_db``/``client`` fixtures (see ``tests/conftest.py``):
AAA is data-rich (2 snapshots, officers, description, analyst snapshot,
260 daily price bars); BBB/CCC are sparse (1 snapshot each, no officers, no
description, no analyst snapshot) and must render gracefully rather than
500. ``^GSPC`` has price bars but no ``companies`` row.

The ``bare_client`` fixture below backs a *second*, separate database
containing only a bare company (DDD, no financials/snapshot/analyst/earnings
data whatsoever) — used to exercise the FA/STAT/ERN "no data at all" path
without perturbing the shared ``web_db`` fixture (several other test modules
assert exact company counts against it).
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Page shell: /stocks/{ticker}
# ---------------------------------------------------------------------------


def test_stock_page_known_ticker_renders_tab_bar(client):
    resp = client.get("/stocks/AAA")
    assert resp.status_code == 200
    body = resp.text
    for label in ["DES", "GP", "FA", "ERN", "STAT", "HP", "DVD", "HDS", "INS"]:
        assert f">{label}<" in body
    assert 'hx-get="/ui/stocks/AAA/des"' in body
    assert 'hx-get="/ui/stocks/AAA/gp"' in body


def test_stock_page_default_tab_loads_des(client):
    resp = client.get("/stocks/AAA")
    assert resp.status_code == 200
    body = resp.text
    # The DES tab button is the one wired to fire on page load.
    des_start = body.index('hx-get="/ui/stocks/AAA/des"')
    des_snippet = body[max(0, des_start - 400) : des_start + 200]
    assert "load" in des_snippet


def test_stock_page_tab_query_param_selects_gp_as_load_tab(client):
    resp = client.get("/stocks/AAA", params={"tab": "gp"})
    assert resp.status_code == 200
    body = resp.text
    gp_start = body.index('hx-get="/ui/stocks/AAA/gp"')
    gp_snippet = body[max(0, gp_start - 400) : gp_start + 200]
    assert "load" in gp_snippet

    des_start = body.index('hx-get="/ui/stocks/AAA/des"')
    des_snippet = body[max(0, des_start - 400) : des_start + 200]
    assert "load" not in des_snippet


def test_stock_page_unknown_ticker_404_html(client):
    resp = client.get("/stocks/ZZZ")
    assert resp.status_code == 404
    assert "text/html" in resp.headers["content-type"]


def test_stock_page_header_shows_company_name_and_ticker(client):
    resp = client.get("/stocks/AAA")
    assert resp.status_code == 200
    body = resp.text
    assert "AAA Corp" in body
    assert "AAA" in body


# ---------------------------------------------------------------------------
# DES fragment
# ---------------------------------------------------------------------------


def test_des_fragment_known_ticker_shows_price_and_change(client):
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    body = resp.text
    # aaa2 snapshot: current_price=105.0, change=+5.0 (up)
    assert "$105.00" in body
    assert "up" in body


def test_des_fragment_shows_52wk_range_label(client):
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    assert "52" in resp.text


def test_des_fragment_shows_officers_and_description(client):
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    body = resp.text
    assert "Jane Doe" in body
    assert "John Smith" in body
    assert "AAA Corp designs" in body


def test_des_fragment_shows_as_of_stamp_and_refresh_placeholder(client):
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    body = resp.text
    assert "AS OF" in body
    assert "REFRESH" in body
    assert "disabled" in body


def test_des_fragment_shows_next_earnings_date(client):
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    assert "2024-08-01" in resp.text


def test_des_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/des")
    assert resp.status_code == 404


def test_des_fragment_sparse_ticker_renders_gracefully(client):
    # BBB has 1 snapshot, no officers, no description, no analyst snapshot.
    resp = client.get("/ui/stocks/BBB/des")
    assert resp.status_code == 200
    body = resp.text
    assert "$50.00" in body
    assert "—" in body  # missing fields fall back to the em-dash placeholder


def test_des_fragment_sparse_ticker_change_is_neutral_not_down(client):
    # BBB has no previous_close, so quote["change"] is None. That must render
    # as a neutral "flat" class, not "down" (which would falsely paint an
    # unknown change as a loss).
    resp = client.get("/ui/stocks/BBB/des")
    assert resp.status_code == 200
    body = resp.text
    assert "quote-change flat" in body
    assert "down" not in body


def test_des_fragment_very_sparse_ticker_no_500(client):
    resp = client.get("/ui/stocks/CCC/des")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GP fragment
# ---------------------------------------------------------------------------


def test_gp_fragment_known_ticker_has_chart_hook_and_range_buttons(client):
    resp = client.get("/ui/stocks/AAA/gp")
    assert resp.status_code == 200
    body = resp.text
    assert "renderGP(" in body
    assert 'id="gp-chart"' in body
    for rk in ["1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"]:
        assert f">{rk}<" in body


def test_gp_fragment_has_type_toggle_and_indicator_checkboxes(client):
    resp = client.get("/ui/stocks/AAA/gp")
    assert resp.status_code == 200
    body = resp.text
    assert ">Line<" in body
    assert ">Candle<" in body
    for label in ["MA50", "MA200", "RSI", "MACD"]:
        assert label in body


def test_gp_fragment_has_compare_select_with_benchmark(client):
    resp = client.get("/ui/stocks/AAA/gp")
    assert resp.status_code == 200
    body = resp.text
    assert "^GSPC" in body
    assert "S&amp;P 500" in body or "S&P 500" in body


def test_gp_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/gp")
    assert resp.status_code == 404


def test_gp_fragment_sparse_ticker_no_500(client):
    resp = client.get("/ui/stocks/CCC/gp")
    assert resp.status_code == 200
    assert "renderGP(" in resp.text


def test_gp_fragment_reflects_selected_range_and_indicators(client):
    resp = client.get(
        "/ui/stocks/AAA/gp",
        params={"range": "5Y", "type": "candle", "ind": "ma50,rsi"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert '"range": "5Y"' in body or '"range":"5Y"' in body
    assert '"chartType": "candle"' in body or '"chartType":"candle"' in body
    assert "ma50" in body
    assert "rsi" in body


def test_gp_fragment_rejects_script_injection_in_ind_and_compare(client):
    # Reflected-XSS regression: unknown/malicious `ind`/`compare` tokens must
    # never reach the inline <script>renderGP({...})</script> call. The route
    # should whitelist against known indicator keys / known tickers and
    # silently drop anything else, rather than 400ing or reflecting it.
    resp = client.get(
        "/ui/stocks/AAA/gp",
        params={
            "ind": '</script><script>alert(1)</script>',
            "compare": "<img src=x>",
        },
    )
    assert resp.status_code == 200
    body = resp.text
    assert "<script>alert" not in body
    assert "</script><script>alert(1)</script>" not in body
    assert "<img src=x>" not in body


def test_gp_fragment_valid_ind_and_compare_round_trip_into_cfg(client):
    resp = client.get(
        "/ui/stocks/AAA/gp",
        params={"ind": "ma50,rsi", "compare": "^GSPC"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "renderGP(" in body
    assert "ma50" in body
    assert "rsi" in body
    assert "^GSPC" in body


# ---------------------------------------------------------------------------
# FA fragment (reuses the existing /ui/companies/{ticker}/statements machinery)
# ---------------------------------------------------------------------------


def test_fa_fragment_known_ticker_shows_revenue_and_period_tabs(client):
    resp = client.get("/ui/stocks/AAA/fa")
    assert resp.status_code == 200
    body = resp.text
    assert "Revenue" in body
    for label in ["Annual", "Quarterly", "TTM", "Metrics"]:
        assert f">{label}<" in body
    assert 'hx-target="#fa-statements"' in body
    assert 'hx-get="/ui/companies/AAA/statements?period=quarterly"' in body


def test_fa_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/fa")
    assert resp.status_code == 404


def test_fa_fragment_sparse_ticker_no_500(client):
    resp = client.get("/ui/stocks/CCC/fa")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# STAT fragment (dense ratio grid)
# ---------------------------------------------------------------------------


def test_stat_fragment_known_ticker_shows_roe_and_short_interest_label(client):
    resp = client.get("/ui/stocks/AAA/stat")
    assert resp.status_code == 200
    body = resp.text
    assert "ROE" in body
    assert "Short % of float" in body


def test_stat_fragment_known_ticker_uses_fmt_value_for_roic(client):
    # AAA's latest (FY2024) metrics_annual row has roic=0.15 -> "15.0%".
    resp = client.get("/ui/stocks/AAA/stat")
    assert resp.status_code == 200
    assert "15.0%" in resp.text


def test_stat_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/stat")
    assert resp.status_code == 404


def test_stat_fragment_sparse_ticker_no_500(client):
    resp = client.get("/ui/stocks/BBB/stat")
    assert resp.status_code == 200
    assert "ROE" in resp.text  # label always renders; value falls back to "—"


# ---------------------------------------------------------------------------
# ERN fragment (earnings surprise + analyst targets/gauge)
# ---------------------------------------------------------------------------


def test_ern_fragment_known_ticker_shows_surprise_table_and_chart_hook(client):
    resp = client.get("/ui/stocks/AAA/ern")
    assert resp.status_code == 200
    body = resp.text
    assert "renderERN(" in body
    assert "2023-12-31" in body  # a quarter from the fixture history
    assert 'class="num down"' in body or 'class="num up"' in body


def test_ern_fragment_shows_analyst_target_and_gauge(client):
    resp = client.get("/ui/stocks/AAA/ern")
    assert resp.status_code == 200
    body = resp.text
    # target_price_low/high from the aaa2 analyst snapshot fixture
    assert "$90.00" in body
    assert "$140.00" in body
    assert "12" in body  # number_of_analysts
    assert "2024-08-01" in body  # earnings_date


def test_ern_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/ern")
    assert resp.status_code == 404


def test_ern_fragment_sparse_ticker_no_500(client):
    # BBB has no earnings_history and no analyst_snapshot rows at all.
    resp = client.get("/ui/stocks/BBB/ern")
    assert resp.status_code == 200
    assert "renderERN(" in resp.text


# ---------------------------------------------------------------------------
# Bare-company (no data at all) fixture — separate DB, see module docstring.
# ---------------------------------------------------------------------------


@pytest.fixture
def bare_client(tmp_path):
    """TestClient backed by a DB with a single company (DDD) and NO other rows
    anywhere (no financials, metrics, snapshot, analyst, earnings history).
    """
    from datetime import datetime

    from fastapi.testclient import TestClient

    from src.exporters.sqlite_store import SQLiteStore
    from src.models.stock_data import StockData
    from src.webapp import create_app

    db_path = tmp_path / "bare.db"
    store = SQLiteStore(db_path)
    ddd = StockData(
        ticker="DDD",
        cik="0000000004",
        company_name="DDD Bare Co",
        collected_at=datetime(2024, 1, 15),
    )
    store.export([ddd])
    return TestClient(create_app(db_path=db_path))


def test_fa_fragment_bare_ticker_no_500(bare_client):
    resp = bare_client.get("/ui/stocks/DDD/fa")
    assert resp.status_code == 200
    assert "No data available" in resp.text


def test_stat_fragment_bare_ticker_no_500(bare_client):
    resp = bare_client.get("/ui/stocks/DDD/stat")
    assert resp.status_code == 200
    assert "ROE" in resp.text


def test_ern_fragment_bare_ticker_no_500(bare_client):
    resp = bare_client.get("/ui/stocks/DDD/ern")
    assert resp.status_code == 200
    assert "No earnings history" in resp.text

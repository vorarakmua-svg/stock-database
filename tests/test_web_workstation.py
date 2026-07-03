"""Tests for the per-stock workstation page and its DES/GP fragments.

Uses the shared ``web_db``/``client`` fixtures (see ``tests/conftest.py``):
AAA is data-rich (2 snapshots, officers, description, analyst snapshot,
260 daily price bars); BBB/CCC are sparse (1 snapshot each, no officers, no
description, no analyst snapshot) and must render gracefully rather than
500. ``^GSPC`` has price bars but no ``companies`` row.
"""
from __future__ import annotations

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

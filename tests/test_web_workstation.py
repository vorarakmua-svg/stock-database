"""Tests for the per-stock workstation page and its DES/GP/FA/STAT/ERN/HP/DVD/HDS/INS
fragments.

Uses the shared ``web_db``/``client`` fixtures (see ``tests/conftest.py``):
AAA is data-rich (2 snapshots, officers, description, analyst snapshot,
260 daily price bars, 6 dividend events + 1 split, 3 institutional + 2
mutualfund holders, 3 insider transactions); BBB/CCC are sparse (1 snapshot
each, no officers, no description, no analyst snapshot, no dividends/
splits/holders/insiders) and must render gracefully rather than 500.
``^GSPC`` has price bars but no ``companies`` row.

The ``bare_client`` fixture below backs a *second*, separate database
containing only a bare company (DDD, no financials/snapshot/analyst/earnings
data whatsoever) — used to exercise the FA/STAT/ERN/HP/DVD/HDS/INS "no data
at all" path without perturbing the shared ``web_db`` fixture (several other
test modules assert exact company counts against it).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.webapp.routes import stocks_api, workstation

# AAA's 260 synthetic daily bars run from 2023-01-01 (see conftest's
# ``_synthetic_bars(260, start="2023-01-01", ...)``). HP range-slicing tests
# pin "today" near the end of that fixture data (mirrors the pattern in
# ``tests/test_web_api_stocks.py``) rather than depending on the real wall
# clock, since a real "today" would be years past the fixture's 2023 dates.
_AAA_BAR_COUNT = 260
_AAA_START = date(2023, 1, 1)
_AAA_LAST = _AAA_START + timedelta(days=_AAA_BAR_COUNT - 1)


def _pin_today(monkeypatch, fixed: date) -> None:
    monkeypatch.setattr(stocks_api, "_today", lambda: fixed)

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


def test_des_fragment_shows_as_of_stamp_and_refresh_button(client):
    # The `client` fixture uses default WebSettings, where allow_quote_refresh
    # defaults to True (Task 10) — so DES renders the LIVE refresh button here,
    # not the disabled placeholder (see test_web_quote_refresh.py for the
    # disabled-gate rendering case).
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200
    body = resp.text
    assert "AS OF" in body
    assert "REFRESH" in body
    assert 'hx-post="/api/stocks/AAA/refresh-quote"' in body


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
    assert "pill flat quote-change" in body
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
# HP fragment (historical OHLCV table + CSV)
# ---------------------------------------------------------------------------


def test_hp_fragment_known_ticker_shows_bar_date_and_csv_link(client, monkeypatch):
    _pin_today(monkeypatch, _AAA_LAST)
    resp = client.get("/ui/stocks/AAA/hp")
    assert resp.status_code == 200
    body = resp.text
    # HP renders newest-first with the default "1Y" range; "today" is pinned
    # to _AAA_LAST (the fixture's most recent bar), so it is always the FIRST
    # row regardless of how much of the 1Y window falls before _AAA_START.
    assert _AAA_LAST.isoformat() in body
    assert "/api/export/stock/AAA/bars.csv" in body


def test_hp_fragment_range_param_changes_row_count(client, monkeypatch):
    _pin_today(monkeypatch, _AAA_LAST)
    full = client.get("/ui/stocks/AAA/hp", params={"range": "MAX"}).text
    sliced = client.get("/ui/stocks/AAA/hp", params={"range": "1M"}).text
    # "hp-bar-date" is a marker unique to HP's bar rows (unlike the generic
    # "row-label" class, which also appears 14x in the always-rendered HELP
    # overlay in base.html — counting that would compare apples to oranges).
    full_rows = full.count("hp-bar-date")
    sliced_rows = sliced.count("hp-bar-date")
    assert 0 < sliced_rows < full_rows


def test_hp_fragment_unknown_range_falls_back_to_1y(client, monkeypatch):
    # An unrecognized `range` must fall back to the same default (1Y) the GP
    # route uses, silently — never echoed raw, never a 400/500.
    _pin_today(monkeypatch, _AAA_LAST)
    default_resp = client.get("/ui/stocks/AAA/hp", params={"range": "1Y"}).text
    bogus_resp = client.get("/ui/stocks/AAA/hp", params={"range": "BOGUS"}).text
    assert default_resp == bogus_resp


def test_hp_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/hp")
    assert resp.status_code == 404


def test_hp_fragment_sparse_ticker_no_500(client):
    resp = client.get("/ui/stocks/CCC/hp")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# DVD fragment (dividends + splits, stats header, annual bar chart)
# ---------------------------------------------------------------------------


def test_dvd_fragment_known_ticker_shows_amount_chart_and_cagr(client):
    resp = client.get("/ui/stocks/AAA/dvd")
    assert resp.status_code == 200
    body = resp.text
    assert "$0.25" in body  # a dividend amount from the fixture payments
    assert "renderDVD(" in body

    # AAA's 6 dividend events (see conftest, all in 2023/2024, no gap year):
    #   2023: 0.20 + 0.20 + 0.22 + 0.22 = 0.84
    #   2024: 0.25 + 0.25              = 0.50
    # CAGR = (last_positive / first_positive) ** (1 / (len(annual_sums) - 1)) - 1
    #      = (0.50 / 0.84) ** (1 / 1) - 1 = -0.404761904... -> fmt_pct -> "-40.5%"
    # Consistency = share of consecutive positive years that did NOT decrease:
    #   2024 (0.50) < 2023 (0.84) -> 0 of 1 -> 0.0 -> fmt_pct -> "0.0%"
    assert (
        '<span class="summary-label">Dividend CAGR</span>'
        '<span class="summary-value">-40.5%</span>'
    ) in body
    assert (
        '<span class="summary-label">Consistency</span>'
        '<span class="summary-value">0.0%</span>'
    ) in body


def test_annual_dividend_sums_zero_fills_gap_years():
    """A lapsed/suspended dividend year (no events) must appear as ``0.0`` in
    ``_annual_dividend_sums``, matching ``yahoo_handler._get_dividend_history``'s
    ``dividends.resample('YE').sum()`` (which bins the FULL year span, not just
    years with payments). Without the zero-fill, 2020 would simply be absent,
    shrinking ``len(annual_sums)`` and diverging CAGR/consistency from the
    handler.

    Cross-checked against an actual pandas ``resample('YE').sum()`` over an
    equivalent yfinance-style ``Series``, computed independently here (not by
    calling the route helpers) so a future change that reintroduces the
    divergence fails structurally rather than by coincidence.
    """
    events = [
        {"date": "2018-03-01", "amount": 1.00},
        {"date": "2019-06-01", "amount": 1.10},
        {"date": "2021-03-01", "amount": 1.20},  # 2020 has no events -> gap year
    ]

    annual_sums = workstation._annual_dividend_sums(events)
    assert annual_sums == {2018: 1.00, 2019: 1.10, 2020: 0.0, 2021: 1.20}

    cagr = workstation._dividend_cagr(annual_sums)
    consistency = workstation._dividend_consistency(annual_sums)

    # Mirror _get_dividend_history's own computation with pandas directly.
    idx = pd.to_datetime([e["date"] for e in events])
    series = pd.Series([e["amount"] for e in events], index=idx)
    annual_dividends = series.resample("YE").sum()
    assert len(annual_dividends) == 4  # 2018, 2019, 2020 (gap), 2021

    positive = annual_dividends[annual_dividends > 0]
    expected_years = len(annual_dividends) - 1
    expected_cagr = (positive.iloc[-1] / positive.iloc[0]) ** (1 / expected_years) - 1
    assert cagr == pytest.approx(expected_cagr)

    annual_list = positive.tolist()
    expected_increases = sum(
        1 for i in range(1, len(annual_list)) if annual_list[i] >= annual_list[i - 1]
    )
    expected_consistency = expected_increases / (len(annual_list) - 1)
    assert consistency == pytest.approx(expected_consistency)


def test_dvd_fragment_shows_splits_and_csv_link(client):
    resp = client.get("/ui/stocks/AAA/dvd")
    assert resp.status_code == 200
    body = resp.text
    assert "2023-06-01" in body  # fixture split date
    assert "/api/export/stock/AAA/dividends.csv" in body


def test_dvd_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/dvd")
    assert resp.status_code == 404


def test_dvd_fragment_sparse_ticker_no_500(client):
    # BBB has no dividend_events/split_events rows at all.
    resp = client.get("/ui/stocks/BBB/dvd")
    assert resp.status_code == 200
    assert "renderDVD(" in resp.text


# ---------------------------------------------------------------------------
# HDS fragment (institutional + mutual-fund holders)
# ---------------------------------------------------------------------------


def test_hds_fragment_known_ticker_shows_holder_names(client):
    resp = client.get("/ui/stocks/AAA/hds")
    assert resp.status_code == 200
    body = resp.text
    assert "BlackRock Inc" in body
    assert "Fidelity Contrafund" in body


def test_hds_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/hds")
    assert resp.status_code == 404


def test_hds_fragment_sparse_ticker_no_500(client):
    resp = client.get("/ui/stocks/BBB/hds")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# INS fragment (insider transactions, buy/sell tint)
# ---------------------------------------------------------------------------


def test_ins_fragment_known_ticker_shows_insider_and_tint(client):
    resp = client.get("/ui/stocks/AAA/ins")
    assert resp.status_code == 200
    body = resp.text
    assert "Jane Doe" in body  # "Sale" -> down/red
    assert "Alice Wu" in body  # "Purchase" -> up/green
    assert 'class="down"' in body
    assert 'class="up"' in body


def test_ins_fragment_unknown_ticker_404(client):
    resp = client.get("/ui/stocks/ZZZ/ins")
    assert resp.status_code == 404


def test_ins_fragment_sparse_ticker_no_500(client):
    resp = client.get("/ui/stocks/BBB/ins")
    assert resp.status_code == 200


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


def test_hp_fragment_bare_ticker_no_500(bare_client):
    resp = bare_client.get("/ui/stocks/DDD/hp")
    assert resp.status_code == 200
    assert "No price history collected" in resp.text


def test_dvd_fragment_bare_ticker_no_500(bare_client):
    resp = bare_client.get("/ui/stocks/DDD/dvd")
    assert resp.status_code == 200
    assert "renderDVD(" in resp.text


def test_hds_fragment_bare_ticker_no_500(bare_client):
    resp = bare_client.get("/ui/stocks/DDD/hds")
    assert resp.status_code == 200


def test_ins_fragment_bare_ticker_no_500(bare_client):
    resp = bare_client.get("/ui/stocks/DDD/ins")
    assert resp.status_code == 200

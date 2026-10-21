"""The VAL tab's valuation history (Milestone 1): a section loaded with the tab, the historical value against the
live price, each multiple's 10-year range, and the chart's inline config.

docs/superpowers/specs/2026-10-21-valuation-history-design.md
"""
import json
import re

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.history import HistoryResult, HistoryRow, MetricRange
from tests.test_web_workstation import _seed_val_rows


def _seed(db_path, ticker="AAA", fair_value=True, date="2025-03-31"):
    rows = [HistoryRow(date=d, close=c, market_cap=c * 1e8, ttm_period_end="2024-12-31",
                       multiples={"pe": c / 1.5, "ps": 2.0, "pb": None, "pfcf": None, "ev_ebitda": 9.0,
                                  "pffo": None, "div_yield": 0.02},
                       fair_value=fv, fair_value_n=2 if fv else 0)
            for d, c, fv in (("2025-01-31", 15.0, None), ("2025-02-28", 18.0, 16.0), (date, 21.0, 17.0))]
    fv_row = (MetricRange(metric="fair_value", applicable=True, as_of="2025-03-31", window_start="2018-03-31",
                          n_months=85, current=17.0, p25=14.0, p75=20.0, n_metrics=2) if fair_value else
              MetricRange(metric="fair_value", as_of="2025-03-31",
                          na_reason="only 1 metric with 36 months of values; 2 needed"))
    ranges = [
        MetricRange(metric="pe", applicable=True, as_of="2025-03-31", window_start="2015-04-30", n_months=120,
                    current=14.0, pct_rank=0.9, min=8.0, p25=10.0, median=11.5, p75=13.0, max=16.0,
                    implied_value=17.25, in_fair_value=True),
        MetricRange(metric="ps", applicable=True, as_of="2025-03-31", window_start="2022-03-31", n_months=37,
                    current=2.0, pct_rank=0.5, min=1.0, p25=1.5, median=2.0, p75=2.5, max=3.0,
                    implied_value=16.8, in_fair_value=True),
        MetricRange(metric="pffo", na_reason="REITs only", as_of="2025-03-31"),
        fv_row,
    ]
    SQLiteStore(db_path=db_path).export_valuation_history(
        ticker, HistoryResult(ticker=ticker, rows=rows, ranges=ranges), "2025-04-01T00:00:00+00:00")


def _config(html):
    match = re.search(r"var _valhCfg = (\{.*?\});\s*renderValHistory", html, re.S)
    assert match, "no inline chart config"
    return json.loads(match.group(1))


def test_the_val_tab_loads_the_history_section(client, web_db):
    _seed_val_rows(web_db)
    body = client.get("/ui/stocks/AAA/val").text
    assert 'hx-get="/ui/stocks/AAA/val-history"' in body
    assert 'hx-trigger="load"' in body


def test_the_section_shows_the_value_against_the_live_price(client, web_db):
    _seed(web_db)
    resp = client.get("/ui/stocks/AAA/val-history")
    assert resp.status_code == 200
    html = resp.text
    # web_db's AAA quote is $105.00, against a historical value of $17.00.
    assert "Price far above historical value" in html
    assert "verdict-expensive" in html
    assert "$17.00" in html and "$14.00" in html and "$20.00" in html and "$105.00" in html
    assert "P/E, P/S" in html  # the metrics combined, named in the method note


def test_the_table_gives_each_multiples_range(client, web_db):
    _seed(web_db)
    html = client.get("/ui/stocks/AAA/val-history").text
    assert "14.0x" in html and "8.0x" in html and "11.5x" in html and "16.0x" in html  # P/E now, low, median, high
    assert "90.0%" in html  # its percentile
    assert "10 years" in html  # a full window
    assert "since 2022-03 (37 months)" in html  # a shorter one, labelled
    assert "REITs only" in html


def test_the_chart_config_carries_the_series_and_the_cutoffs(client, web_db):
    _seed(web_db)
    cfg = _config(client.get("/ui/stocks/AAA/val-history").text)
    assert cfg["dates"] == ["2025-01-31", "2025-02-28", "2025-03-31"]
    assert cfg["close"] == [15.0, 18.0, 21.0]
    assert cfg["fair_value"] == [None, 16.0, 17.0]
    assert cfg["cutoffs"] == [0.7, 0.9, 1.1, 1.3]
    assert [m["key"] for m in cfg["metrics"]] == ["pe", "ps"]  # the applicable ones, in order
    assert cfg["metrics"][0]["values"] == [10.0, 12.0, 14.0]
    assert (cfg["metrics"][0]["min"], cfg["metrics"][0]["median"], cfg["metrics"][0]["max"]) == (8.0, 11.5, 16.0)


def test_the_inline_config_cannot_close_its_script(client, web_db):
    _seed(web_db, date="2025-03-31</script><script>alert(1)</script>")
    html = client.get("/ui/stocks/AAA/val-history").text
    assert "</script><script>alert(1)" not in html
    assert "<\\/script>" in html


def test_a_history_without_a_value_gives_its_reason(client, web_db):
    _seed(web_db, fair_value=False)
    html = client.get("/ui/stocks/AAA/val-history").text
    assert "Not available" in html
    assert "only 1 metric with 36 months of values; 2 needed" in html


def test_a_ticker_not_computed_yet(client):
    html = client.get("/ui/stocks/BBB/val-history").text
    assert "No valuation history computed yet" in html
    assert "renderValHistory" not in html


def test_an_unknown_ticker_is_404(client):
    assert client.get("/ui/stocks/ZZZZ/val-history").status_code == 404

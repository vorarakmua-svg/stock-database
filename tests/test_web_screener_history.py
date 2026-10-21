"""The screener's valuation-history fields (Milestone 1): each multiple's 10-year percentile and price / historical
value, whitelisted, joined only when the history's table exists.

docs/superpowers/specs/2026-10-21-valuation-history-design.md
"""
import sqlite3

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.history import HistoryResult, MetricRange
from src.webapp.repository import Reader
from src.webapp.screener import (
    HISTORY_EXPRS,
    METRIC_KINDS,
    SCREEN_COLUMNS,
    MetricFilter,
    ScreenSpec,
    build_count_query,
    build_screen_query,
)

FIELDS = ["pe_pct_10y", "ps_pct_10y", "pb_pct_10y", "pfcf_pct_10y", "ev_ebitda_pct_10y", "pffo_pct_10y",
          "div_yield_pct_10y", "price_to_hist_value"]


def _seed(db_path, ticker="AAA", pe_pct=0.9, value=17.0):
    ranges = [MetricRange(metric="pe", applicable=True, current=14.0, pct_rank=pe_pct, median=11.5),
              # A row that isn't applicable gives no percentile, whatever it holds.
              MetricRange(metric="ps", applicable=False, na_reason="35 months of values, fewer than 36",
                          pct_rank=0.4),
              MetricRange(metric="fair_value", applicable=value is not None, current=value)]
    SQLiteStore(db_path=db_path).export_valuation_history(
        ticker, HistoryResult(ticker=ticker, ranges=ranges), "2025-04-01T00:00:00+00:00")


def test_the_fields_are_whitelisted_and_listed():
    assert list(HISTORY_EXPRS) == FIELDS
    assert all(f in SCREEN_COLUMNS for f in FIELDS)
    assert all(METRIC_KINDS[f] == "pct" for f in FIELDS[:-1]) and METRIC_KINDS["price_to_hist_value"] == "mult"


def test_a_percentile_filter_reads_its_own_metric_and_binds_its_value():
    sql, params = build_screen_query(ScreenSpec(filters=[MetricFilter(field="pe_pct_10y", op="lte", value=0.3)]))
    assert 'vr_pe."pct_rank" <= ?' in sql and 0.3 in params
    assert "vr_pe.metric = 'pe'" in sql and "vr_pe.applicable = 1" in sql
    count_sql, _ = build_count_query(ScreenSpec(filters=[MetricFilter(field="pe_pct_10y", op="lte", value=0.3)]))
    assert "LEFT JOIN valuation_ranges vr_pe" in count_sql


def test_price_to_hist_value_sorts_with_nulls_last():
    sql, _ = build_screen_query(ScreenSpec(sort="price_to_hist_value", sort_dir="asc"))
    assert 'ms."current_price" / NULLIF(vr_fv."current_value", 0)' in sql
    assert "ORDER BY ((ms.\"current_price\" / NULLIF(vr_fv.\"current_value\", 0)) IS NULL)" in sql


def test_an_unknown_history_field_is_refused():
    with pytest.raises(ValueError):
        build_screen_query(ScreenSpec(filters=[MetricFilter(field="pe_pct_10y; DROP TABLE x", op="lte", value=1)]))


def test_the_screen_returns_and_filters_by_the_history(web_db):
    _seed(web_db)
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[]))["items"]}
        assert items["AAA"]["pe_pct_10y"] == 0.9
        assert items["AAA"]["ps_pct_10y"] is None  # not applicable: no percentile
        assert items["AAA"]["price_to_hist_value"] == pytest.approx(105.0 / 17.0)  # AAA's quote is $105.00
        assert items["EEE"]["pe_pct_10y"] is None  # no history
        low = r.screen(ScreenSpec(filters=[MetricFilter(field="pe_pct_10y", op="lte", value=0.5)]))
        high = r.screen(ScreenSpec(filters=[MetricFilter(field="pe_pct_10y", op="gte", value=0.5)]))
    assert low["total"] == 0
    assert [row["ticker"] for row in high["items"]] == ["AAA"] and high["total"] == 1


def test_a_database_without_the_history_table_still_screens(web_db):
    conn = sqlite3.connect(web_db)
    conn.execute("DROP TABLE IF EXISTS valuation_ranges")
    conn.commit()
    conn.close()
    with Reader(web_db) as r:
        result = r.screen(ScreenSpec(filters=[], sort="price_to_hist_value"))
        assert result["total"] == 2 and all(row["price_to_hist_value"] is None for row in result["items"])
        assert r.screen(ScreenSpec(filters=[MetricFilter(field="pe_pct_10y", op="lte", value=0.5)]))["total"] == 0


def test_the_screener_offers_the_history_fields(client):
    html = client.get("/screener").text
    assert '"group": "Historical value"' in html
    assert "P/E percentile (10y)" in html and "Price / historical value" in html


def test_the_results_sort_by_the_history(client, web_db):
    _seed(web_db)
    html = client.get("/ui/screen?sort=price_to_hist_value&sort_dir=asc").text
    assert "sort=price_to_hist_value" in html and "sort=pe_pct_10y" in html
    assert "6.2x" in html  # AAA: $105.00 / $17.00
    assert "90.0%" in html  # its P/E percentile


def test_the_csv_carries_the_history_columns(client, web_db):
    _seed(web_db)
    header = client.get("/api/export/screen.csv").text.splitlines()[0]
    assert "pe_pct_10y" in header and "price_to_hist_value" in header

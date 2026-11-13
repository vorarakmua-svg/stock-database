"""The screener's other valuation indicators (Milestone 4): the reverse DCF's implied growth, the EPV, median P/S value
and tangible Graham number's upsides from the snapshot price, and the price over Graham's net-net working capital,
whitelisted, over fixed-literal joins of applicable ``valuation_indicators`` rows, NULL before the table.

docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md ("Web for the indicators")
"""
import csv
import io
import json
import sqlite3

import pytest

from src.exporters.sqlite_store import _METRIC_COLUMNS, SQLiteStore
from src.valuation.indicators import INDICATORS, IndicatorResult
from src.webapp.repository import Reader
from src.webapp.screener import (
    GURU_EXPRS,
    HISTORY_EXPRS,
    INDICATOR_EXPRS,
    INSIDER_EXPRS,
    METRIC_KINDS,
    QUALITY_EXPRS,
    SCREEN_COLUMNS,
    SNAPSHOT_SCREEN_COLUMNS,
    VALUATION_EXPRS,
    MetricFilter,
    ScreenSpec,
    build_count_query,
    build_screen_query,
)

FIELDS = ["reverse_dcf_growth", "epv_upside_pct", "ps_value_upside_pct", "graham_tangible_upside_pct", "price_to_nnwc"]
LABELS = ["Reverse DCF implied growth", "EPV upside", "Median P/S value upside", "Tangible Graham upside",
          "Price / net-net working capital"]
#: The screened tickers' values: AAA at $105.00, EEE (given a quote) at $40.00; BBB (given metrics) has no rows.
AAA = {"reverse_dcf_growth": 0.12, "epv_upside_pct": 126.0 / 105.0 - 1, "ps_value_upside_pct": 84.0 / 105.0 - 1,
       "graham_tangible_upside_pct": 52.5 / 105.0 - 1, "price_to_nnwc": 105.0 / 70.0}
EEE = {"reverse_dcf_growth": 0.05, "epv_upside_pct": 30.0 / 40.0 - 1, "ps_value_upside_pct": 50.0 / 40.0 - 1,
       "graham_tangible_upside_pct": 44.0 / 40.0 - 1, "price_to_nnwc": 40.0 / 20.0}


def _rows(growth, epv, nnwc, ps, graham, applicable=True):
    return [
        IndicatorResult(indicator="reverse_dcf", applicable=applicable, value_base=growth, unit="rate"),
        IndicatorResult(indicator="epv", applicable=applicable, value_bear=0.9 * epv, value_base=epv,
                        value_bull=1.1 * epv),
        # The bear is Graham's net-net working capital; the base, NCAV, is larger.
        IndicatorResult(indicator="ncav", applicable=applicable, value_bear=nnwc, value_base=95.0),
        IndicatorResult(indicator="ps_value", applicable=applicable, value_bear=0.8 * ps, value_base=ps,
                        value_bull=1.2 * ps),
        IndicatorResult(indicator="graham_tangible", applicable=applicable, value_bear=0.9 * graham,
                        value_base=graham, value_bull=1.1 * graham),
    ]


def _seed(db_path, ticker, **values):
    SQLiteStore(db_path=db_path).export_valuation_indicators(ticker, _rows(**values), "2025-04-01T00:00:00+00:00")


def _universe(web_db):
    """AAA ($105.00) and EEE (given a $40.00 quote) with indicators; BBB (given a fiscal year's metrics) without."""
    conn = sqlite3.connect(web_db)
    conn.execute("INSERT INTO market_snapshots (ticker, collected_at, current_price) "
                 "VALUES ('EEE', '2024-01-15T00:00:00', 40.0)")
    conn.execute("INSERT INTO metrics_annual (ticker, fiscal_year) VALUES ('BBB', 2023)")
    conn.commit()
    conn.close()
    _seed(web_db, "AAA", growth=0.12, epv=126.0, nnwc=70.0, ps=84.0, graham=52.5)
    _seed(web_db, "EEE", growth=0.05, epv=30.0, nnwc=20.0, ps=50.0, graham=44.0)


def _items(r, **spec):
    return {row["ticker"]: row for row in r.screen(ScreenSpec(**spec))["items"]}


def _drop_table(web_db):
    conn = sqlite3.connect(web_db)
    conn.execute("DROP TABLE valuation_indicators")  # a database from before the indicators
    conn.commit()
    conn.close()


def test_the_fields_are_whitelisted_listed_and_typed():
    assert list(INDICATOR_EXPRS) == FIELDS
    assert all(f in SCREEN_COLUMNS for f in FIELDS)
    others = (set(_METRIC_COLUMNS) | set(SNAPSHOT_SCREEN_COLUMNS) | set(VALUATION_EXPRS) | set(HISTORY_EXPRS)
              | set(QUALITY_EXPRS) | set(INSIDER_EXPRS) | set(GURU_EXPRS))
    assert not others & set(INDICATOR_EXPRS)  # one meaning per name
    assert [METRIC_KINDS[f] for f in FIELDS] == ["pct", "pct", "pct", "pct", "mult"]


def test_each_indicator_is_one_fixed_join_of_its_applicable_row():
    spec = ScreenSpec(filters=[MetricFilter(field="epv_upside_pct", op="gt", value=0.25)])
    sql, params = build_screen_query(spec)
    count_sql, count_params = build_count_query(spec)
    for name in INDICATORS:
        join = (f"LEFT JOIN valuation_indicators vi_{name} ON vi_{name}.ticker = ma.ticker "
                f"AND vi_{name}.indicator = '{name}' AND vi_{name}.applicable = 1")
        assert sql.count(join) == 1 and count_sql.count(join) == 1
    # The value is bound, never written into the SQL.
    assert '((vi_epv."value_base" - ms."current_price") / NULLIF(ms."current_price", 0)) > ?' in sql
    assert 0.25 in params and 0.25 in count_params and "0.25" not in sql


def test_the_screen_returns_each_field(web_db):
    _universe(web_db)
    with Reader(web_db) as r:
        items = _items(r, filters=[])
    assert set(items) == {"AAA", "BBB", "EEE"}
    for field in FIELDS:
        assert items["AAA"][field] == pytest.approx(AAA[field]), field
        assert items["EEE"][field] == pytest.approx(EEE[field]), field
        assert items["BBB"][field] is None, field  # no rows


@pytest.mark.parametrize("field, op, value, expected", [
    ("reverse_dcf_growth", "gt", 0.1, ["AAA"]),
    ("epv_upside_pct", "gt", 0.0, ["AAA"]),
    ("ps_value_upside_pct", "gt", 0.0, ["EEE"]),
    ("graham_tangible_upside_pct", "gt", 0.0, ["EEE"]),
    ("price_to_nnwc", "lte", 1.6, ["AAA"]),
])
def test_each_field_filters(web_db, field, op, value, expected):
    _universe(web_db)
    with Reader(web_db) as r:
        result = r.screen(ScreenSpec(filters=[MetricFilter(field=field, op=op, value=value)]))
    assert [row["ticker"] for row in result["items"]] == expected and result["total"] == len(expected)


@pytest.mark.parametrize("field, ascending", [
    ("reverse_dcf_growth", ["EEE", "AAA"]),
    ("epv_upside_pct", ["EEE", "AAA"]),
    ("ps_value_upside_pct", ["AAA", "EEE"]),
    ("graham_tangible_upside_pct", ["AAA", "EEE"]),
    ("price_to_nnwc", ["AAA", "EEE"]),
])
def test_each_field_sorts_with_nulls_last(web_db, field, ascending):
    _universe(web_db)
    with Reader(web_db) as r:
        up = [row["ticker"] for row in r.screen(ScreenSpec(sort=field, sort_dir="asc"))["items"]]
        down = [row["ticker"] for row in r.screen(ScreenSpec(sort=field, sort_dir="desc"))["items"]]
    assert up == ascending + ["BBB"]
    assert down == ascending[::-1] + ["BBB"]


def test_a_row_that_isnt_applicable_gives_nothing_whatever_it_holds(web_db):
    _universe(web_db)
    _seed(web_db, "AAA", growth=0.12, epv=126.0, nnwc=70.0, ps=84.0, graham=52.5, applicable=False)
    with Reader(web_db) as r:
        items = _items(r, filters=[])
    assert all(items["AAA"][field] is None for field in FIELDS)
    assert all(items["EEE"][field] is not None for field in FIELDS)


@pytest.mark.parametrize("nnwc", [-4.0, 0.0, None])
def test_price_to_nnwc_only_for_a_positive_net_net(web_db, nnwc):
    _universe(web_db)
    _seed(web_db, "AAA", growth=0.12, epv=126.0, nnwc=nnwc, ps=84.0, graham=52.5)
    with Reader(web_db) as r:
        assert _items(r, filters=[])["AAA"]["price_to_nnwc"] is None
        assert r.screen(ScreenSpec(filters=[MetricFilter(field="price_to_nnwc", op="lt", value=1.9)]))["total"] == 0


def test_a_database_without_the_table_still_screens(web_db):
    _universe(web_db)
    _drop_table(web_db)
    with Reader(web_db) as r:
        for field in FIELDS:
            result = r.screen(ScreenSpec(sort=field))
            assert result["total"] == 3 and all(row[field] is None for row in result["items"]), field
            spec = ScreenSpec(filters=[MetricFilter(field=field, op="gt", value=-100.0)])
            assert r.screen(spec)["total"] == 0, field


def test_an_unknown_field_is_refused_before_any_sql():
    for flag in (True, False):  # with the table or without it, the whitelist decides
        with pytest.raises(ValueError):
            build_screen_query(ScreenSpec(filters=[MetricFilter(field="epv_upside_pct; DROP TABLE x", op="gt",
                                                                value=0)]), indicators=flag)
        with pytest.raises(ValueError):
            build_count_query(ScreenSpec(filters=[MetricFilter(field="price_to_nnwc_x", op="lt", value=1)]),
                              indicators=flag)
        with pytest.raises(ValueError):
            build_screen_query(ScreenSpec(sort="reverse_dcf_growth DESC; --"), indicators=flag)


def test_an_unknown_field_is_a_400(client):
    assert client.get("/api/screen", params={"reverse_dcf_growthx_gt": "0"}).status_code == 400


def test_the_screener_offers_the_fields(client):
    html = client.get("/screener").text
    metrics = json.loads(html.split('id="screener-metrics">', 1)[1].split("</script>", 1)[0])
    offered = [(m["key"], m["label"]) for m in metrics if m["group"] == "Other indicators"]
    assert offered == list(zip(FIELDS, LABELS))


def test_the_results_show_and_sort_by_the_implied_growth(client, web_db):
    _universe(web_db)
    html = client.get("/ui/screen?sort=reverse_dcf_growth&sort_dir=asc").text
    assert "sort=reverse_dcf_growth" in html and "Implied growth" in html
    assert "<td>12.0%</td>" in html and "<td>5.0%</td>" in html  # AAA's and EEE's growth
    assert html.index('value="EEE"') < html.index('value="AAA"') < html.index('value="BBB"')


def test_the_csv_carries_the_fields(client, web_db):
    _universe(web_db)
    rows = list(csv.DictReader(io.StringIO(client.get("/api/export/screen.csv?sort=reverse_dcf_growth").text)))
    assert all(field in rows[0] for field in FIELDS)
    aaa = next(row for row in rows if row["ticker"] == "AAA")
    assert float(aaa["reverse_dcf_growth"]) == 0.12 and float(aaa["price_to_nnwc"]) == pytest.approx(1.5)

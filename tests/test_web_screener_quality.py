"""The screener's quality-score fields and preset screens (Milestone 2): whitelisted, joined only when the scores'
table exists, plus the Lynch model's upside and Graham's price / NCAV.

docs/superpowers/specs/2026-10-21-quality-scores-design.md
"""
import json
import sqlite3
from urllib.parse import parse_qsl

import pytest

from src.exporters.sqlite_store import _METRIC_COLUMNS
from src.webapp.repository import Reader
from src.webapp.screener import (
    HISTORY_EXPRS,
    METRIC_KINDS,
    PRESETS,
    QUALITY_EXPRS,
    SCREEN_COLUMNS,
    SNAPSHOT_SCREEN_COLUMNS,
    VALUATION_EXPRS,
    MetricFilter,
    ScreenSpec,
    build_count_query,
    build_screen_query,
    parse_screen_params,
)
from tests.test_web_api_scores import _row, _seed
from tests.test_web_workstation import _seed_val_rows

FIELDS = ["piotroski_f", "altman_z", "beneish_m", "sloan_ratio", "predictability_stars", "rev_ps_cagr_5y",
          "eps_cagr_5y", "roic_minus_wacc", "rank_strength", "rank_profitability", "rank_growth", "rank_valuation",
          "rank_momentum", "composite_score", "momentum", "magic_formula_rank", "warning_count", "price_to_ncav",
          "scores_debt_to_ebitda", "ind_rank_strength", "ind_rank_profitability", "ind_rank_growth",
          "ind_rank_valuation", "ind_rank_momentum", "ind_composite"]


def test_the_fields_are_whitelisted_listed_and_typed():
    assert list(QUALITY_EXPRS) == FIELDS
    assert all(f in SCREEN_COLUMNS for f in FIELDS + ["lynch_upside_pct"])
    others = set(_METRIC_COLUMNS) | set(SNAPSHOT_SCREEN_COLUMNS) | set(VALUATION_EXPRS) | set(HISTORY_EXPRS)
    assert not others & set(QUALITY_EXPRS)  # one meaning per name
    assert all(f in METRIC_KINDS for f in FIELDS + ["lynch_upside_pct"])
    assert METRIC_KINDS["sloan_ratio"] == "pct" and METRIC_KINDS["price_to_ncav"] == "mult"
    assert METRIC_KINDS["scores_debt_to_ebitda"] == "mult"


def test_a_quality_filter_reads_its_column_and_binds_its_value():
    spec = ScreenSpec(filters=[MetricFilter(field="piotroski_f", op="gte", value=7)])
    sql, params = build_screen_query(spec)
    assert 'qs."piotroski_f" >= ?' in sql and 7 in params
    assert "LEFT JOIN quality_scores qs ON qs.ticker = ma.ticker" in sql
    count_sql, _ = build_count_query(spec)
    assert "LEFT JOIN quality_scores qs" in count_sql


def test_price_to_ncav_needs_a_positive_ncav():
    sql, _ = build_screen_query(ScreenSpec(sort="price_to_ncav", sort_dir="asc"))
    assert 'CASE WHEN qs."ncav_per_share" > 0 THEN ms."current_price" / qs."ncav_per_share" END' in sql


def test_an_unknown_quality_field_is_refused():
    with pytest.raises(ValueError):
        build_screen_query(ScreenSpec(filters=[MetricFilter(field="composite_score; DROP TABLE x", op="gte",
                                                            value=1)]))


def test_the_screen_returns_and_filters_by_the_scores(web_db):
    _seed(web_db, row=_row(ncav_per_share=210.0))
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[]))["items"]}
        assert (items["AAA"]["composite_score"], items["AAA"]["piotroski_f"], items["AAA"]["warning_count"]) == (
            62.5, 7, 2)
        assert items["AAA"]["price_to_ncav"] == pytest.approx(105.0 / 210.0)  # AAA's quote is $105.00
        assert items["EEE"]["composite_score"] is None  # no scores
        high = r.screen(ScreenSpec(filters=[MetricFilter(field="composite_score", op="gte", value=60)]))
        low = r.screen(ScreenSpec(filters=[MetricFilter(field="composite_score", op="lt", value=60)]))
    assert [row["ticker"] for row in high["items"]] == ["AAA"] and high["total"] == 1
    assert low["total"] == 0


def test_a_negative_ncav_gives_no_price_to_ncav(web_db):
    _seed(web_db, row=_row(ncav_per_share=-12.0))
    with Reader(web_db) as r:
        assert r.screen(ScreenSpec(filters=[MetricFilter(field="price_to_ncav", op="lte", value=0.67)]))["total"] == 0


def test_a_database_without_the_scores_table_still_screens(web_db):
    conn = sqlite3.connect(web_db)
    conn.execute("DROP TABLE IF EXISTS quality_scores")  # a database from before the scores
    conn.commit()
    conn.close()
    with Reader(web_db) as r:
        result = r.screen(ScreenSpec(filters=[], sort="composite_score"))
        assert result["total"] == 2 and all(row["composite_score"] is None for row in result["items"])
        assert r.screen(ScreenSpec(filters=[MetricFilter(field="piotroski_f", op="gte", value=1)]))["total"] == 0


def test_lynch_upside_reads_the_lynch_model(web_db):
    _seed_val_rows(web_db)
    conn = sqlite3.connect(web_db)
    conn.execute("INSERT OR REPLACE INTO valuations VALUES ('AAA', 'lynch', 1, NULL, 100.0, 126.0, 150.0, '{}', 2023, "
                 "'2024-01-05T00:00:00')")
    conn.commit()
    conn.close()
    with Reader(web_db) as r:
        result = r.screen(ScreenSpec(filters=[]))
        assert [row["ticker"] for row in result["items"]].count("AAA") == 1  # the Lynch row only, not the DCF's
        assert result["total"] == 2
        items = {row["ticker"]: row for row in result["items"]}
        assert items["AAA"]["lynch_upside_pct"] == pytest.approx((126.0 - 105.0) / 105.0)
        assert items["EEE"]["lynch_upside_pct"] is None


@pytest.mark.parametrize("key", [p[0] for p in PRESETS])
def test_each_preset_is_a_valid_screen(key):
    qs = next(p[2] for p in PRESETS if p[0] == key)
    spec = parse_screen_params(dict(parse_qsl(qs)))
    build_screen_query(spec)  # whitelisted fields and ops only
    assert spec.filters  # every preset filters


def test_the_presets_are_the_specs():
    assert [p[0] for p in PRESETS] == ["buffett_munger", "lynch", "graham_net_net", "magic_formula",
                                       "undervalued_predictable"]
    q = {p[0]: dict(parse_qsl(p[2])) for p in PRESETS}
    assert q["buffett_munger"] == {"predictability_stars_gte": "4", "roic_minus_wacc_gt": "0",
                                   "scores_debt_to_ebitda_lte": "3", "price_to_hist_value_lte": "1.1"}
    assert q["lynch"]["lynch_upside_pct_gt"] == "0"
    assert q["graham_net_net"]["price_to_ncav_lte"] == "0.67"
    assert q["magic_formula"]["magic_formula_rank_gte"] == "1" and q["magic_formula"]["sort"] == "magic_formula_rank"
    assert q["magic_formula"]["sort_dir"] == "asc"
    assert q["undervalued_predictable"] == {"predictability_stars_gte": "4", "price_to_hist_value_lt": "0.9",
                                            "sort": "price_to_hist_value", "sort_dir": "asc"}


def test_the_screener_offers_the_fields_and_the_presets(client):
    html = client.get("/screener").text
    metrics = json.loads(html.split('id="screener-metrics">', 1)[1].split("</script>", 1)[0])
    groups = {m["key"]: m["group"] for m in metrics}
    assert all(groups.get(f) == "Quality scores" for f in FIELDS)
    assert groups.get("lynch_upside_pct") == "Valuation models"
    assert 'id="preset-select"' in html
    for _, label, qs in PRESETS:
        assert label in html and qs.replace("&", "&amp;") in html


def test_the_results_show_and_sort_by_the_composite(client, web_db):
    _seed(web_db)
    html = client.get("/ui/screen?sort=composite_score&sort_dir=desc").text
    assert "sort=composite_score" in html and "sort=piotroski_f" in html
    assert "62.5" in html


def test_the_csv_carries_the_quality_columns(client, web_db):
    _seed(web_db)
    header = client.get("/api/export/screen.csv").text.splitlines()[0]
    assert "composite_score" in header and "piotroski_f" in header and "lynch_upside_pct" in header


def test_a_debt_free_filer_passes_the_scores_debt_test(web_db):
    # BUG-190: metrics_annual has no debt / EBITDA for a filer tagging no debt (ISRG), so the Buffett-Munger screen
    # reads the scores' own, 0 for it.
    _seed(web_db, row=_row(debt_to_ebitda=0.0))
    with Reader(web_db) as r:
        spec = ScreenSpec(filters=[MetricFilter(field="scores_debt_to_ebitda", op="lte", value=3)])
        assert [row["ticker"] for row in r.screen(spec)["items"]] == ["AAA"]


def test_a_scores_table_from_before_a_column_screens_on(web_db):
    # A table the scores wrote before debt_to_ebitda (BUG-190) screens with that field NULL until the next run.
    _seed(web_db, row=_row(debt_to_ebitda=0.0))
    conn = sqlite3.connect(web_db)
    conn.execute("ALTER TABLE quality_scores DROP COLUMN debt_to_ebitda")
    conn.commit()
    conn.close()
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec(filters=[]))["items"]}
        assert items["AAA"]["composite_score"] == 62.5 and items["AAA"]["scores_debt_to_ebitda"] is None
        spec = ScreenSpec(filters=[MetricFilter(field="scores_debt_to_ebitda", op="lte", value=3)])
        assert r.screen(spec)["total"] == 0

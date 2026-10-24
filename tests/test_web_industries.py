"""The SIC codes and industry ranks on the web (Milestone 5): the DES tab's code, the STAT tab's and the scores API's
industry block, and the screener's industry filter and ``ind_*`` fields.

docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC codes and industries"
"""
import csv
import io
import json
import re
import sqlite3

import pytest

from src.webapp.repository import Reader
from src.webapp.screener import (
    METRIC_KINDS,
    QUALITY_EXPRS,
    SCREEN_COLUMNS,
    ScreenSpec,
    build_count_query,
    build_screen_query,
    parse_screen_params,
)
from tests.test_web_api_scores import _row, _seed

#: AAA and EEE are the screen's two companies (the ones with metrics); BBB is a bank; CCC has no code.
CODES = {"AAA": ("3571", "Electronic Computers"), "EEE": ("3674", "Semiconductors & Related Devices"),
         "BBB": ("6021", "National Commercial Banks")}
IND_FIELDS = ["ind_rank_strength", "ind_rank_profitability", "ind_rank_growth", "ind_rank_valuation",
              "ind_rank_momentum", "ind_composite"]
M2_RANKS = {"rank_strength": 7, "rank_profitability": 9, "rank_growth": 6, "rank_valuation": 3, "rank_momentum": None,
            "composite_score": 62.5, "magic_formula_rank": 4}
LABEL = "SIC 357: Electronic Computers; Computer Storage Devices"
IN_357 = {**M2_RANKS, "industry_group": "357", "industry_label": LABEL, "industry_size": 12,
          "ind_rank_strength": 8, "ind_rank_profitability": 10, "ind_rank_growth": 5, "ind_rank_valuation": 2,
          "ind_rank_momentum": None, "ind_composite": 62.5}
NO_GROUP = {**M2_RANKS, **{c: None for c in ("industry_group", "industry_label", "industry_size", *IND_FIELDS)}}
BAD_PREFIXES = ["", "3", "3571", "35a", "a35", "35 ", " 35", "35\n", "3-5", "35%", "3_", "_5", "35' OR '1'='1",
                "\uff13\uff15"]  # full-width digits


def _code(db, codes=None):
    conn = sqlite3.connect(db)
    for ticker, (sic, description) in (codes or CODES).items():
        conn.execute("UPDATE companies SET sic = ?, sic_description = ? WHERE ticker = ?", (sic, description, ticker))
    conn.commit()
    conn.close()


def _drop(db, table, columns):
    conn = sqlite3.connect(db)
    for column in columns:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    conn.commit()
    conn.close()


# ---- the DES tab ------------------------------------------------------------------------------------------------


def test_des_shows_the_sic_code_and_description(client, web_db):
    _code(web_db)
    assert "SIC 3571 Electronic Computers" in client.get("/ui/stocks/AAA/des").text
    assert "SIC " not in client.get("/ui/stocks/CCC/des").text  # no code stored


def test_des_renders_for_a_companies_table_from_before_the_codes(client, web_db):
    _drop(web_db, "companies", ("sic", "sic_description"))
    resp = client.get("/ui/stocks/AAA/des")
    assert resp.status_code == 200 and "SIC " not in resp.text


# ---- the scores API and the STAT tab ----------------------------------------------------------------------------


def test_the_scores_api_gives_the_industry_ranks(client, web_db):
    _code(web_db)
    _seed(web_db, ranks=IN_357)
    body = client.get("/api/stocks/AAA/scores").json()
    assert body["industry"] == {"group": "357", "label": LABEL, "size": 12, "sic": "3571",
                                "ranks": {"strength": 8, "profitability": 10, "growth": 5, "valuation": 2,
                                          "momentum": None},
                                "composite": 62.5, "na_reason": None}
    assert body["composite"]["ranks"]["strength"] == 7  # the universe's, beside them


@pytest.mark.parametrize("ticker, reason", [
    ("CCC", "no SIC code stored"),
    ("BBB", "SIC 6021's industry has fewer than 10 companies here"),
])
def test_the_scores_api_says_why_there_are_no_industry_ranks(client, web_db, ticker, reason):
    _code(web_db)
    _seed(web_db, ticker=ticker, ranks=NO_GROUP)
    industry = client.get(f"/api/stocks/{ticker}/scores").json()["industry"]
    assert industry["na_reason"] == reason
    assert (industry["group"], industry["composite"]) == (None, None)
    assert set(industry["ranks"].values()) == {None}


def test_scores_not_ranked_by_industry_yet_say_so(client, web_db):
    _code(web_db)
    _seed(web_db, ranks=IN_357)
    _drop(web_db, "quality_scores", ("industry_group", "industry_label", "industry_size", *IND_FIELDS))
    body = client.get("/api/stocks/AAA/scores").json()
    assert body["industry"]["na_reason"] == "industry ranks not computed yet"
    assert body["composite"]["score"] == 62.5


def test_the_stat_tab_shows_the_industry_ranks_beside_the_universe_s(client, web_db):
    _code(web_db)
    _seed(web_db, ranks=IN_357)
    html = client.get("/ui/stocks/AAA/scores").text
    assert "Industry composite 62.5 / 100" in html
    assert re.search(r'Financial strength</td><td class="num">7</td><td class="num">8</td>', html)
    assert re.search(r'Momentum</td><td class="num">\S+</td><td class="num">\S+</td>', html)
    assert f"among the 12 companies of {LABEL}" in html


@pytest.mark.parametrize("ticker, reason", [
    ("CCC", "no SIC code stored"),
    ("BBB", "SIC 6021&#39;s industry has fewer than 10 companies here"),
])
def test_the_stat_tab_says_why_there_are_no_industry_ranks(client, web_db, ticker, reason):
    _code(web_db)
    _seed(web_db, ticker=ticker, ranks=NO_GROUP, row=_row())
    html = client.get(f"/ui/stocks/{ticker}/scores").text
    assert f"No industry ranks: {reason}" in html
    assert "Industry composite" not in html


# ---- the screener -----------------------------------------------------------------------------------------------


def test_the_industry_filter_is_parsed_like_the_sector():
    assert parse_screen_params({"industry": "357"}).industry == "357"
    assert parse_screen_params({"industry": "35", "sector": "general"}).industry == "35"
    assert parse_screen_params({"industry": ""}).industry is None
    assert parse_screen_params({}).industry is None
    assert parse_screen_params({"industry": "28"}).filters == []  # not a field filter


@pytest.mark.parametrize("value", BAD_PREFIXES[1:])
def test_an_industry_that_is_not_2_or_3_digits_is_refused_before_any_sql(value):
    with pytest.raises(ValueError):
        parse_screen_params({"industry": value})
    with pytest.raises(ValueError):
        build_screen_query(ScreenSpec(industry=value))
    with pytest.raises(ValueError):
        build_count_query(ScreenSpec(industry=value))


def test_an_empty_industry_in_a_spec_is_refused():
    with pytest.raises(ValueError):
        build_screen_query(ScreenSpec(industry=""))


def test_the_industry_is_bound_never_written_into_the_sql():
    sql, params = build_screen_query(ScreenSpec(industry="357"))
    assert "c.\"sic\" LIKE ? || '%'" in sql and "357" in params and "357" not in sql
    count_sql, count_params = build_count_query(ScreenSpec(industry="357"))
    assert "c.\"sic\" LIKE ? || '%'" in count_sql and count_params == ["357"]


@pytest.mark.parametrize("prefix, tickers", [("35", ["AAA"]), ("357", ["AAA"]), ("36", ["EEE"]), ("367", ["EEE"]),
                                             ("28", []), ("60", [])])
def test_the_screen_filters_by_the_industry(web_db, prefix, tickers):
    _code(web_db)
    with Reader(web_db) as r:
        result = r.screen(ScreenSpec(industry=prefix))
    assert [row["ticker"] for row in result["items"]] == tickers and result["total"] == len(tickers)


def test_the_screen_returns_each_company_s_code(web_db):
    _code(web_db)
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec())["items"]}
    assert (items["AAA"]["sic"], items["EEE"]["sic"]) == ("3571", "3674")
    assert "sic" in SCREEN_COLUMNS


def test_a_companies_table_from_before_the_codes_screens_on(web_db):
    _drop(web_db, "companies", ("sic", "sic_description"))
    with Reader(web_db) as r:
        everything = r.screen(ScreenSpec())
        assert everything["total"] == 2 and all(row["sic"] is None for row in everything["items"])
        assert r.screen(ScreenSpec(industry="35"))["total"] == 0


def test_the_api_and_the_fragment_take_the_industry(client, web_db):
    _code(web_db)
    assert [row["ticker"] for row in client.get("/api/screen?industry=36").json()["items"]] == ["EEE"]
    assert client.get("/api/screen?industry=3674").status_code == 400
    posted = client.post("/api/screen", json={"filters": [], "industry": "357"})
    assert [row["ticker"] for row in posted.json()["items"]] == ["AAA"]
    assert client.post("/api/screen", json={"filters": [], "industry": "3x"}).status_code == 400
    assert "/stocks/AAA" in client.get("/ui/screen?industry=357").text
    fragment = client.get("/ui/screen?industry=abc").text
    assert "alert" in fragment and "/stocks/AAA" not in fragment


def test_the_csv_takes_the_industry(client, web_db):
    _code(web_db)
    resp = client.get("/api/export/screen.csv?industry=36")
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert [r["ticker"] for r in rows] == ["EEE"] and rows[0]["sic"] == "3674"
    assert all(f in rows[0] for f in IND_FIELDS)
    assert client.get("/api/export/screen.csv?industry=3").status_code == 400


def test_the_industry_rank_fields_are_whitelisted_listed_and_typed():
    assert all(QUALITY_EXPRS.get(f) == f'qs."{f}"' for f in IND_FIELDS)
    assert all(f in SCREEN_COLUMNS for f in IND_FIELDS)
    assert all(METRIC_KINDS.get(f) == "raw" for f in IND_FIELDS)


def test_the_screen_reads_and_filters_by_the_industry_ranks(web_db):
    _code(web_db)
    _seed(web_db, ranks=IN_357)
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec())["items"]}
        assert (items["AAA"]["ind_composite"], items["AAA"]["ind_rank_growth"]) == (62.5, 5)
        assert items["EEE"]["ind_composite"] is None
        spec = parse_screen_params({"ind_rank_growth_gte": "5", "industry": "357"})
        assert [row["ticker"] for row in r.screen(spec)["items"]] == ["AAA"]
        assert r.screen(parse_screen_params({"ind_composite_gt": "70"}))["total"] == 0


def test_a_scores_table_from_before_the_industry_ranks_screens_on(web_db):
    _seed(web_db, ranks=IN_357)
    _drop(web_db, "quality_scores", ("industry_group", "industry_label", "industry_size", *IND_FIELDS))
    with Reader(web_db) as r:
        items = {row["ticker"]: row for row in r.screen(ScreenSpec(sort="ind_composite"))["items"]}
        assert items["AAA"]["composite_score"] == 62.5 and items["AAA"]["ind_composite"] is None
        assert r.screen(parse_screen_params({"ind_rank_growth_gte": "1"}))["total"] == 0


def test_the_screener_lists_the_industries_and_the_fields(client, web_db):
    _code(web_db)
    html = client.get("/screener").text
    assert 'id="industry-select"' in html
    options = dict(re.findall(r'<option value="(\d{2,3})">(?:&nbsp;)*([^<]*)</option>', html))
    assert options == {"35": "SIC 35: Electronic Computers (1)", "357": "SIC 357: Electronic Computers (1)",
                       "36": "SIC 36: Semiconductors &amp; Related Devices (1)",
                       "367": "SIC 367: Semiconductors &amp; Related Devices (1)",
                       "60": "SIC 60: National Commercial Banks (1)", "602": "SIC 602: National Commercial Banks (1)"}
    metrics = json.loads(html.split('id="screener-metrics">', 1)[1].split("</script>", 1)[0])
    groups = {m["key"]: m["group"] for m in metrics}
    assert all(groups.get(f) == "Quality scores" for f in IND_FIELDS)


def test_the_screener_page_renders_without_codes(client, web_db):
    _drop(web_db, "companies", ("sic", "sic_description"))
    html = client.get("/screener").text
    assert 'id="industry-select"' in html and not re.search(r'<option value="\d{2,3}">', html)

"""The STAT tab's Quality section (/ui/stocks/{ticker}/scores): the composite and its radar, the five ranks, the scores,
the growth table and the warning signs (docs/superpowers/specs/2026-10-21-quality-scores-design.md)."""
import json
import re

from tests.test_web_api_scores import _row, _seed


def _config(html):
    m = re.search(r"var _qualityCfg = (\{.*?\});", html, re.S)
    assert m, "no radar config in the section"
    return json.loads(m.group(1))


def test_the_stat_tab_loads_the_quality_section(client, web_db):
    html = client.get("/ui/stocks/AAA/stat").text
    assert 'hx-get="/ui/stocks/AAA/scores"' in html and 'hx-trigger="load"' in html


def test_the_section_shows_the_composite_the_ranks_and_the_scores(client, web_db):
    _seed(web_db)
    resp = client.get("/ui/stocks/AAA/scores")
    assert resp.status_code == 200
    html = resp.text
    assert "62.5" in html and "equal weights" in html
    for label in ("Financial strength", "Profitability", "Growth", "Valuation", "Momentum"):
        assert label in html
    assert "7 / 9" in html  # Piotroski, out of the tests run
    assert "Operating cash flow above net income" in html  # a failed test, named
    assert "3.40" in html and "safe" in html  # Altman Z and its zone
    assert "4.5" in html  # predictability stars
    assert "Revenue / share" in html and "8.0%" in html  # the growth table
    cfg = _config(html)
    assert cfg["labels"] == ["Financial strength", "Profitability", "Growth", "Valuation", "Momentum"]
    assert cfg["values"] == [7, 9, 6, 3, None]


def test_the_signs_list_severe_first(client, web_db):
    _seed(web_db)
    html = client.get("/ui/stocks/AAA/scores").text
    severe = html.index("interest coverage of 1.2x is below 1.5x")
    medium = html.index("accruals of 12% of assets")
    assert severe < medium


def test_a_bank_shows_why_its_scores_are_missing(client, web_db):
    reasons = {"piotroski": "not meaningful for banks", "altman": "not meaningful for banks"}
    _seed(web_db, ticker="BBB", row=_row(piotroski_f=None, piotroski_tested=None, piotroski_tests=json.dumps({}),
                                         altman_z=None, altman_zone=None, na_reasons=json.dumps(reasons)), signs=[])
    html = client.get("/ui/stocks/BBB/scores").text
    assert "not meaningful for banks" in html
    assert "No warning signs" in html


def test_a_ticker_not_computed_yet(client):
    html = client.get("/ui/stocks/CCC/scores").text
    assert "No quality scores computed yet" in html


def test_an_unknown_ticker_is_404(client):
    assert client.get("/ui/stocks/ZZZZ/scores").status_code == 404

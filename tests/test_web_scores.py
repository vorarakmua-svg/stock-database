"""The STAT tab's Quality section (/ui/stocks/{ticker}/scores): the composite and its radar, the five ranks, the scores,
the growth table and the warning signs (docs/superpowers/specs/2026-10-21-quality-scores-design.md)."""
import json
import re

from src.scores.scores import PIOTROSKI_TESTS
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


def test_a_piotroski_test_that_could_not_run_is_not_a_fail(client, web_db):
    """BUG-186: a test without its inputs (passed null) reads "Not tested"; only real failures read "Fail"."""
    tests = {code: code != "cfo_above_ni" for code in PIOTROSKI_TESTS}
    tests["gross_margin_up"] = None
    _seed(web_db, row=_row(piotroski_f=7, piotroski_tested=8, piotroski_tests=json.dumps(tests)))
    html = client.get("/ui/stocks/AAA/scores").text
    cell = re.search(r"Gross margin rose</td>\s*<td>(.*?)</td>", html, re.S)
    assert cell and "Not tested" in cell.group(1)
    assert html.count(">Fail<") == 1 and html.count(">Pass<") == 7


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


def test_an_unranked_company_says_why(client, web_db):
    _seed(web_db, row=_row(return_on_capital=None, na_reasons=json.dumps({"magic_formula": "no net PP&E figure"})),
          ranks={"magic_formula_rank": None})
    html = client.get("/ui/stocks/AAA/scores").text
    assert "not ranked: no net PP&amp;E figure" in html

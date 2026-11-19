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


def test_a_company_with_no_scored_year_says_so_and_does_not_claim_no_signs_fired(client, web_db):
    # BUG-201 (SPCX): no annual financials, so a row without a fiscal year, and no sign was ever tested.
    reasons = {"piotroski": "no annual financials", "altman": "no annual financials"}
    _seed(web_db, ticker="BBB", row=_row(fiscal_year=None, piotroski_f=None, piotroski_tested=None,
                                         piotroski_tests=json.dumps({}), altman_z=None, altman_zone=None,
                                         na_reasons=json.dumps(reasons)), signs=[])
    html = client.get("/ui/stocks/BBB/scores").text
    assert "fiscal year None" not in html and "no fiscal year scored" in html
    assert "No warning signs fired" not in html
    assert "No warning signs were tested: nothing was scored (no annual financials)" in " ".join(html.split())


def test_a_scored_company_without_signs_still_says_none_fired(client, web_db):
    _seed(web_db, signs=[])
    html = client.get("/ui/stocks/AAA/scores").text
    assert "fiscal year 2024" in html and "No warning signs fired" in html


def test_a_negative_ncav_reads_minus_dollar(client, web_db):
    # BUG-201: "$-67.48"; the sign goes before the dollar (BUG-137's fmt_price).
    _seed(web_db, row=_row(ncav_per_share=-67.48))
    html = client.get("/ui/stocks/AAA/scores").text
    assert "-$67.48" in html and "$-67" not in html


def test_the_scores_name_the_inputs_they_miss(client, web_db):
    # BUG-193: Altman, Sloan, NCAV and momentum said an input was missing, or nothing, and named none.
    reasons = {"altman": "missing retained earnings, operating income", "sloan": "missing investing cash flow",
               "ncav": "missing current assets", "momentum": "no price history"}
    _seed(web_db, row=_row(altman_z=None, altman_zone=None, sloan_ratio=None, ncav_per_share=None, momentum=None,
                           na_reasons=json.dumps(reasons)))
    html = client.get("/ui/stocks/AAA/scores").text
    for reason in reasons.values():
        assert reason in html
    body = client.get("/api/stocks/AAA/scores").json()
    assert (body["altman"]["na_reason"], body["sloan"]["na_reason"]) == (reasons["altman"], reasons["sloan"])
    assert (body["ncav_na_reason"], body["momentum_na_reason"]) == ("missing current assets", "no price history")


def test_a_figure_that_is_there_carries_no_na_reason(client, web_db):
    _seed(web_db, row=_row(na_reasons=json.dumps({"ncav": "missing current assets", "momentum": "no price history"})))
    body = client.get("/api/stocks/AAA/scores").json()
    assert body["ncav_na_reason"] is None and body["momentum_na_reason"] is None


def test_a_bank_has_no_magic_formula_rank_and_the_api_says_why(client, web_db):
    # BUG-193: the Magic Formula row of a bank, insurer, utility or REIT named nothing in the API; the page said it.
    _seed(web_db, ticker="BBB", row=_row(return_on_capital=None, earnings_yield=None, na_reasons=json.dumps({})),
          ranks={"magic_formula_rank": None})
    reason = client.get("/api/stocks/BBB/scores").json()["magic_formula"]["na_reason"]
    assert reason == "the Magic Formula leaves out banks, insurers, utilities and REITs"
    assert f"not ranked: {reason}" in client.get("/ui/stocks/BBB/scores").text
    _seed(web_db, ticker="AAA", row=_row(na_reasons=json.dumps({})), ranks={"magic_formula_rank": None})
    assert client.get("/api/stocks/AAA/scores").json()["magic_formula"]["na_reason"] is None  # a general company

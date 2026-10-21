"""/api/stocks/{ticker}/scores: the quality scores, their tests, the ranks, the composite, the growth table and the
warning signs (docs/superpowers/specs/2026-10-21-quality-scores-design.md)."""
import json

import pytest

from src.exporters.sqlite_store import SQLiteStore
from src.scores.scores import PIOTROSKI_TESTS
from src.scores.signs import Sign

FAILED_TESTS = ("cfo_above_ni", "gross_margin_up")


def _row(**overrides):
    row = {
        "fiscal_year": 2024,
        "rev_ps_cagr_3y": 0.05, "rev_ps_cagr_5y": 0.08, "rev_ps_cagr_10y": 0.07,
        "eps_cagr_3y": 0.10, "eps_cagr_5y": 0.12, "eps_cagr_10y": None,
        "ebitda_ps_cagr_3y": 0.06, "ebitda_ps_cagr_5y": 0.09, "ebitda_ps_cagr_10y": None,
        "fcf_ps_cagr_5y": 0.07, "book_ps_cagr_5y": 0.04,
        "piotroski_f": 7, "piotroski_tested": 9,
        "piotroski_tests": json.dumps({code: code not in FAILED_TESTS for code in PIOTROSKI_TESTS}),
        "altman_z": 3.4, "altman_zone": "safe",
        "beneish_m": -2.4,
        "beneish_variables": json.dumps({"dsri": 1.0, "gmi": 1.0, "aqi": 1.0, "sgi": 1.1, "depi": 1.0, "sgai": 1.0,
                                         "tata": 0.01, "lvgi": 1.0}),
        "sloan_ratio": 0.12,
        "roic": 0.18, "wacc": 0.09, "roic_minus_wacc": 0.09,
        "predictability_stars": 4.5, "predictability_r2": json.dumps({"revenue": 0.93, "ebitda": 0.88}),
        "ncav_per_share": -12.0, "earnings_yield": 0.05, "return_on_capital": 0.4,
        "momentum": 0.12,
        "components": json.dumps({}),
        "warning_count": 2,
        "na_reasons": json.dumps({}),
    }
    row.update(overrides)
    return row


SIGNS = [  # stored medium first: the API puts severe first
    Sign("sloan_high", "medium", "accruals of 12% of assets (Sloan ratio): earnings and cash flows part ways", 0.12,
         0.10),
    Sign("interest_coverage_low", "severe", "interest coverage of 1.2x is below 1.5x", 1.2, 1.5),
]
PER_SHARE = [
    {"fiscal_year": 2024, "revenue_ps": 11.0, "eps": 1.2, "ebitda_ps": 2.2, "fcf_ps": 1.0, "book_ps": 5.5},
    {"fiscal_year": 2023, "revenue_ps": 10.0, "eps": 1.0, "ebitda_ps": 2.0, "fcf_ps": 0.9, "book_ps": 5.0},
]


def _seed(db_path, ticker="AAA", row=None, signs=SIGNS, per_share=PER_SHARE, ranks=None):
    store = SQLiteStore(db_path=db_path)
    store.export_quality_scores(ticker, row or _row(), per_share, signs, "2025-04-01T00:00:00+00:00")
    store.update_quality_ranks({ticker: ranks if ranks is not None else {
        "rank_strength": 7, "rank_profitability": 9, "rank_growth": 6, "rank_valuation": 3, "rank_momentum": None,
        "composite_score": 62.5, "magic_formula_rank": 4}}, "2025-04-01T00:05:00+00:00")


def test_the_endpoint_gives_the_scores_the_ranks_the_growth_and_the_signs(client, web_db):
    _seed(web_db)
    resp = client.get("/api/stocks/AAA/scores")
    assert resp.status_code == 200
    body = resp.json()
    assert body["computed"] is True and body["na_reason"] is None
    assert (body["fiscal_year"], body["computed_at"], body["ranked_at"]) == (
        2024, "2025-04-01T00:00:00+00:00", "2025-04-01T00:05:00+00:00")
    composite = body["composite"]
    assert composite["score"] == 62.5 and composite["weights"] == "equal"
    assert composite["ranks"] == {"strength": 7, "profitability": 9, "growth": 6, "valuation": 3, "momentum": None}
    p = body["piotroski"]
    assert (p["f"], p["tested"], p["na_reason"]) == (7, 9, None)
    assert [t["code"] for t in p["tests"]] == list(PIOTROSKI_TESTS)  # the spec's order
    assert [t["code"] for t in p["tests"] if not t["passed"]] == list(FAILED_TESTS)
    assert all(t["label"] for t in p["tests"])
    assert body["altman"] == {"z": 3.4, "zone": "safe", "na_reason": None}
    assert (body["beneish"]["m"], body["beneish"]["flagged"]) == (-2.4, False)
    assert body["beneish"]["variables"]["sgi"] == 1.1
    assert body["sloan"] == {"ratio": 0.12, "zone": "warning", "na_reason": None}
    assert body["roic_vs_wacc"] == {"roic": 0.18, "wacc": 0.09, "spread": 0.09, "na_reason": None}
    assert body["predictability"] == {"stars": 4.5, "r2": {"revenue": 0.93, "ebitda": 0.88}, "na_reason": None}
    assert body["magic_formula"] == {"rank": 4, "earnings_yield": 0.05, "return_on_capital": 0.4}
    assert (body["ncav_per_share"], body["momentum"]) == (-12.0, 0.12)
    growth = {g["key"]: g for g in body["growth"]}
    assert list(growth) == ["rev_ps", "eps", "ebitda_ps", "fcf_ps", "book_ps"]
    assert (growth["rev_ps"]["cagr_3y"], growth["rev_ps"]["cagr_5y"], growth["rev_ps"]["cagr_10y"]) == (
        0.05, 0.08, 0.07)
    assert growth["fcf_ps"]["cagr_5y"] == 0.07 and growth["fcf_ps"]["cagr_3y"] is None
    assert [r["fiscal_year"] for r in body["per_share"]] == [2023, 2024]  # ascending
    assert [s["code"] for s in body["warning_signs"]] == ["interest_coverage_low", "sloan_high"]  # severe first
    assert body["warning_signs"][0] == {"code": "interest_coverage_low", "severity": "severe",
                                        "message": "interest coverage of 1.2x is below 1.5x", "value": 1.2,
                                        "threshold": 1.5}


@pytest.mark.parametrize("ratio, zone", [(0.05, "safe"), (-0.10, "safe"), (-0.2, "warning"), (0.3, "flagged"),
                                         (None, None)])
def test_the_sloan_zone_follows_the_published_bands(client, web_db, ratio, zone):
    _seed(web_db, row=_row(sloan_ratio=ratio))
    assert client.get("/api/stocks/AAA/scores").json()["sloan"]["zone"] == zone


def test_a_beneish_m_above_the_line_is_flagged(client, web_db):
    _seed(web_db, row=_row(beneish_m=-1.5))
    assert client.get("/api/stocks/AAA/scores").json()["beneish"]["flagged"] is True


def test_a_bank_says_why_its_scores_are_missing(client, web_db):
    reasons = {"piotroski": "not meaningful for banks", "altman": "not meaningful for banks",
               "beneish": "not meaningful for banks", "wacc": "not meaningful for banks"}
    _seed(web_db, ticker="BBB", row=_row(piotroski_f=None, piotroski_tested=None, piotroski_tests=json.dumps({}),
                                         altman_z=None, altman_zone=None, beneish_m=None,
                                         beneish_variables=json.dumps({}), wacc=None, roic_minus_wacc=None,
                                         na_reasons=json.dumps(reasons)), signs=[])
    body = client.get("/api/stocks/BBB/scores").json()
    assert body["piotroski"]["tests"] == [] and body["piotroski"]["na_reason"] == "not meaningful for banks"
    assert body["altman"] == {"z": None, "zone": None, "na_reason": "not meaningful for banks"}
    assert body["beneish"]["flagged"] is None and body["beneish"]["na_reason"] == "not meaningful for banks"
    assert body["roic_vs_wacc"]["na_reason"] == "not meaningful for banks"
    assert body["warning_signs"] == []


def test_a_ticker_not_computed_yet(client, web_db):
    body = client.get("/api/stocks/CCC/scores").json()  # no quality_scores table at all
    assert body == {"ticker": "CCC", "computed": False, "na_reason": "not computed yet"}
    _seed(web_db)  # the table exists, without a CCC row
    assert client.get("/api/stocks/CCC/scores").json()["na_reason"] == "not computed yet"


def test_an_unknown_ticker_is_404(client):
    assert client.get("/api/stocks/ZZZZ/scores").status_code == 404
    assert client.get("/api/stocks/^GSPC/scores").status_code == 404

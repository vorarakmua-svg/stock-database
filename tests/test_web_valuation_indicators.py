"""The other valuation indicators on the web (Milestone 4): the view shared by
``GET /api/stocks/{t}/valuation-indicators`` and the VAL tab's "Other indicators" section: the stored rows in INDICATORS
order with their labels, methods, upsides from the live price and notes, and the reverse DCF solved again at the live
price.

docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md ("Web for the indicators")
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.exporters.sqlite_store import SQLiteStore
from src.valuation.engine import compute_and_store
from src.valuation.indicators import INDICATORS, IndicatorResult, implied_growth
from src.valuation.models import dcf_per_share
from src.webapp import create_app
from src.webapp.formatting import fmt_pct
from src.webapp.repository import Reader
from src.webapp.valuation_indicators import indicators_view
from tests.test_valuation_indicators_store import _seed as _seed_financials
from tests.test_web_workstation import _seed_owner_earnings, _seed_val_rows

COMPUTED_AT = "2025-04-01T00:00:00+00:00"
LABELS = ["Reverse DCF", "Earnings power value (EPV)", "NCAV and Graham's net-net", "Median P/S value",
          "Graham number on tangible book"]
KEYS = {"indicator", "label", "method", "applicable", "na_reason", "unit", "value_bear", "value_base", "value_bull",
        "basis_fiscal_year", "assumptions", "upside_pct", "notes"}
#: The reverse DCF's inputs: 5.00 of free cash flow a share.
FCF, SHARES, DISCOUNT, TG = 500.0, 100.0, 0.09, 0.025
#: web_db's AAA: the indicators were stored at its 2024-01-15 snapshot's $95.00; its live quote is $105.00.
STORED_PRICE, LIVE_PRICE = 95.0, 105.0
STORED_GROWTH = implied_growth(FCF, SHARES, STORED_PRICE, DISCOUNT, TG)[0]
BELOW = "the price is below the DCF's value at -50% growth a year"
ABOVE = "the price is above the DCF's value at 100% growth a year"


def _dcf(fcf=FCF, shares=SHARES):
    return {"price": STORED_PRICE, "price_date": "2024-01-15T00:00:00", "fcf_basis": fcf, "shares_outstanding": shares,
            "discount_base": DISCOUNT, "terminal_growth": TG, "dcf_growth_base": 0.04, "dcf_value_base": 80.0,
            "search_low": -0.5, "search_high": 1.0}


EPV = {"margin_years": [2020, 2021, 2022, 2023, 2024], "mean_operating_margin": 0.2, "pretax_years": [2020, 2021, 2022],
       "no_interest_years": [2021], "latest_revenue": 1000.0, "normalized_ebit": 200.0, "tax_rate": 0.21,
       "tax_source": "effective", "adjusted_earnings": 158.0, "wacc": 0.0912, "total_debt": 300.0,
       "debt_missing": False, "cash_and_sti": 120.0, "shares_outstanding": 10.0,
       "maintenance_capex": "taken as equal to D&A"}
NCAV = {"ncav_per_share": 36.0, "nnwc_per_share": 21.0, "current_assets": 600.0, "total_liabilities": 240.0,
        "shares": 10.0, "cash_and_equivalents": 120.0, "short_term_investments": None, "accounts_receivable": 100.0,
        "inventory": 80.0, "receivables_weight": 0.75, "inventory_weight": 0.5,
        "nnwc_missing": ["short_term_investments"], "netnet_buy_price": 24.0}
PS = {"median_ps": 2.0, "p25_ps": 1.5, "p75_ps": 2.5, "current_ps": 2.5, "as_of": "2024-05-31", "n_months": 120}
GRAHAM = {"eps_base": 5.0, "eps_bear": 2.9, "eps_bull": 6.53, "bvps": 30.0, "tangible_bvps": 24.5,
          "total_equity": 300.0, "goodwill": 55.0, "intangible_assets": 0.0, "multiplier": 22.5,
          "missing": ["intangible_assets"]}


def _results(**over):
    """Every indicator applicable, as stored at $95.00; *over* replaces one by name."""
    results = {
        "reverse_dcf": IndicatorResult(indicator="reverse_dcf", applicable=True, value_base=STORED_GROWTH, unit="rate",
                                       assumptions=_dcf(), basis_fiscal_year=2024),
        "epv": IndicatorResult(indicator="epv", applicable=True, value_bear=110.0, value_base=126.0, value_bull=147.0,
                               assumptions=EPV, basis_fiscal_year=2024),
        "ncav": IndicatorResult(indicator="ncav", applicable=True, value_bear=21.0, value_base=36.0, assumptions=NCAV,
                                basis_fiscal_year=2024),
        "ps_value": IndicatorResult(indicator="ps_value", applicable=True, value_bear=50.0, value_base=63.0,
                                    value_bull=80.0, assumptions=PS),
        "graham_tangible": IndicatorResult(indicator="graham_tangible", applicable=True, value_bear=40.0,
                                           value_base=52.5, value_bull=60.0, assumptions=GRAHAM,
                                           basis_fiscal_year=2024),
    }
    results.update(over)
    return [results[name] for name in INDICATORS]


def _na(indicator, reason, assumptions=None, unit="per_share", basis=None):
    return IndicatorResult(indicator=indicator, applicable=False, na_reason=reason, unit=unit,
                           assumptions=assumptions or {}, basis_fiscal_year=basis)


def _seed(db_path, ticker="AAA", **over):
    SQLiteStore(db_path=db_path).export_valuation_indicators(ticker, _results(**over), COMPUTED_AT)


def _view(db_path, ticker="AAA"):
    with Reader(db_path) as r:
        return indicators_view(r, ticker)


def _by_name(view):
    return {i["indicator"]: i for i in view["indicators"]}


# ---- the view ----------------------------------------------------------------------------------------------------


def test_the_view_lists_the_rows_in_the_indicators_order_with_labels_and_methods(web_db):
    _seed(web_db)
    v = _view(web_db)
    assert (v["ticker"], v["price"], v["computed"], v["computed_at"]) == ("AAA", LIVE_PRICE, True, COMPUTED_AT)
    # The reader returns them by name (epv first); the view puts them in the spec's order.
    with Reader(web_db) as r:
        assert [row["indicator"] for row in r.valuation_indicators("AAA")] == sorted(INDICATORS)
    assert [i["indicator"] for i in v["indicators"]] == list(INDICATORS)
    assert [i["label"] for i in v["indicators"]] == LABELS
    assert all(set(i) == KEYS for i in v["indicators"])
    methods = {i["indicator"]: i["method"] for i in v["indicators"]}
    assert "fading to the terminal growth by year 10" in methods["reverse_dcf"]
    assert "Greenwald's no-growth value: the 5-year mean operating margin" in methods["epv"]
    assert "maintenance capex taken as D&A" in methods["epv"]
    assert "+ 75% of receivables + 50% of inventory - liabilities" in methods["ncav"]
    assert "the 25th and 75th percentile P/S" in methods["ps_value"]
    assert "sqrt(22.5 × EPS × tangible book per share)" in methods["graham_tangible"]


def test_each_row_carries_its_stored_figures_and_parsed_assumptions(web_db):
    _seed(web_db)
    rows = _by_name(_view(web_db))
    epv = rows["epv"]
    assert (epv["applicable"], epv["na_reason"], epv["unit"]) == (True, None, "per_share")
    assert (epv["value_bear"], epv["value_base"], epv["value_bull"], epv["basis_fiscal_year"]) == (
        110.0, 126.0, 147.0, 2024)
    assert epv["assumptions"] == EPV
    assert rows["reverse_dcf"]["unit"] == "rate"
    assert rows["reverse_dcf"]["value_base"] == pytest.approx(STORED_GROWTH)
    assert rows["ps_value"]["basis_fiscal_year"] is None


def test_the_upside_is_each_per_share_base_over_the_live_price(web_db):
    _seed(web_db)
    rows = _by_name(_view(web_db))
    assert rows["epv"]["upside_pct"] == pytest.approx(126.0 / LIVE_PRICE - 1)
    assert rows["ncav"]["upside_pct"] == pytest.approx(36.0 / LIVE_PRICE - 1)
    assert rows["ps_value"]["upside_pct"] == pytest.approx(63.0 / LIVE_PRICE - 1)
    assert rows["graham_tangible"]["upside_pct"] == pytest.approx(52.5 / LIVE_PRICE - 1)
    assert rows["reverse_dcf"]["upside_pct"] is None  # a rate, not a value


def test_the_notes_say_what_the_assumptions_hold(web_db):
    _seed(web_db)
    notes = {name: i["notes"] for name, i in _by_name(_view(web_db)).items()}
    assert notes["reverse_dcf"] == [
        "The DCF itself assumes 4.0% a year in years 1-5.",
        f"Stored at $95.00 on 2024-01-15: {fmt_pct(STORED_GROWTH)}, the screener's figure."]
    assert notes["epv"] == [
        "FY2020-FY2022 file no operating income: EBIT is pretax income plus interest expense.",
        "FY2021 has no interest expense figure: counted as 0.",
        "Discounted at the WACC of 9.1%; the bear and bull at 1 point above and below it."]
    assert notes["ncav"] == ["Graham bought below two thirds of NCAV: $24.00.",
                             "No short-term investments figure: counted as 0 in the net-net."]
    assert notes["ps_value"] == ["Median P/S 2.0x over 120 months (1.5x to 2.5x from the 25th to the 75th percentile).",
                                 "P/S now 2.5x."]
    assert notes["graham_tangible"] == [
        "No intangible assets figure: counted as 0, so any untagged intangibles stay in tangible book."]


def test_the_notes_of_na_rows_and_of_the_other_cases(web_db):
    _seed(web_db,
          reverse_dcf=_na("reverse_dcf", "no DCF: insufficient FCF history (need >= 4 fiscal years)", unit="rate"),
          epv=IndicatorResult(indicator="epv", applicable=True, value_bear=90.0, value_base=100.0, value_bull=112.0,
                              assumptions={**EPV, "pretax_years": [2020, 2022, 2023], "no_interest_years": [2022, 2023],
                                           "debt_missing": True, "wacc": 0.1}),
          ncav=_na("ncav", "net current assets are negative: total liabilities exceed current assets",
                   {**NCAV, "ncav_per_share": -9.17, "nnwc_per_share": -13.2, "netnet_buy_price": None,
                    "nnwc_missing": ["short_term_investments", "inventory"]}),
          ps_value=_na("ps_value", "no P/S range: 25 months of values, fewer than 36",
                       {"median_ps": None, "p25_ps": None, "p75_ps": None, "current_ps": 5.77, "as_of": "2024-05-31",
                        "n_months": 25}),
          graham_tangible=_na("graham_tangible", "tangible book is negative: goodwill and intangibles exceed equity",
                              {**GRAHAM, "tangible_bvps": -12.0, "missing": []}))
    notes = {name: i["notes"] for name, i in _by_name(_view(web_db)).items()}
    assert notes["reverse_dcf"] == []
    assert notes["epv"] == [
        "FY2020, FY2022-FY2023 file no operating income: EBIT is pretax income plus interest expense.",
        "FY2022-FY2023 have no interest expense figure: counted as 0.",
        "No debt figure: counted as 0.",
        "Discounted at the WACC of 10.0%; the bear and bull at 1 point above and below it."]
    assert notes["ncav"] == ["NCAV is -$9.17 a share."]  # no net-net shown, so no note on its parts
    assert notes["ps_value"] == ["P/S now 5.8x."]
    assert notes["graham_tangible"] == ["Tangible book is -$12.00 a share."]


def test_the_notes_of_a_net_net_below_zero_and_of_missing_figures(web_db):
    _seed(web_db,
          reverse_dcf=_na("reverse_dcf", BELOW, _dcf(), unit="rate", basis=2024),
          # Stopped before the WACC: GEV's normalized earnings below zero.
          epv=_na("epv", "normalized after-tax operating earnings are not positive",
                  {"margin_years": [2021, 2022, 2023, 2024], "mean_operating_margin": -0.02, "pretax_years": [2024],
                   "no_interest_years": [], "latest_revenue": 1000.0, "normalized_ebit": -20.0, "tax_rate": 0.0,
                   "tax_source": "effective", "adjusted_earnings": -20.0}),
          ncav=IndicatorResult(indicator="ncav", applicable=True, value_base=36.0,
                               assumptions={**NCAV, "nnwc_per_share": -3.0,
                                            "nnwc_missing": ["cash_and_equivalents", "accounts_receivable",
                                                             "inventory"]}),
          ps_value=IndicatorResult(indicator="ps_value", applicable=True, value_bear=50.0, value_base=63.0,
                                   value_bull=80.0, assumptions={**PS, "n_months": None, "current_ps": None}),
          graham_tangible=IndicatorResult(indicator="graham_tangible", applicable=True, value_base=52.5,
                                          assumptions={**GRAHAM, "missing": ["goodwill", "intangible_assets"]}))
    notes = {name: i["notes"] for name, i in _by_name(_view(web_db)).items()}
    assert notes["reverse_dcf"] == ["The DCF itself assumes 4.0% a year in years 1-5.",
                                    f"Stored at $95.00 on 2024-01-15: {BELOW}."]
    assert notes["epv"] == ["FY2024 files no operating income: EBIT is pretax income plus interest expense."]
    assert notes["ncav"] == ["Graham bought below two thirds of NCAV: $24.00.",
                             "Graham's net-net working capital is -$3.00 a share: no bear case.",
                             "No cash, receivables or inventory figure: counted as 0 in the net-net."]
    assert notes["ps_value"] == ["Median P/S 2.0x (1.5x to 2.5x from the 25th to the 75th percentile)."]
    assert notes["graham_tangible"] == [
        "No goodwill or intangible assets figure: counted as 0, so any untagged intangibles stay in tangible book."]


# ---- the reverse DCF at the live price -----------------------------------------------------------------------------


def test_the_reverse_dcf_is_solved_again_at_the_live_price(web_db):
    _seed(web_db)
    live = _view(web_db)["reverse_dcf_live"]
    assert set(live) == {"price", "growth", "na_reason", "dcf_growth_base"}
    expected, reason = implied_growth(FCF, SHARES, LIVE_PRICE, DISCOUNT, TG)
    assert reason is None
    assert (live["price"], live["na_reason"], live["dcf_growth_base"]) == (LIVE_PRICE, None, 0.04)
    assert live["growth"] == pytest.approx(expected, abs=1e-9)
    assert dcf_per_share(FCF, SHARES, live["growth"], DISCOUNT, TG) == pytest.approx(LIVE_PRICE, rel=1e-5)
    assert abs(live["growth"] - STORED_GROWTH) > 0.01  # not the growth at the stored $95.00


def test_a_row_no_growth_reached_when_stored_is_solved_at_the_live_price(web_db):
    # FCF set so the DCF's value at -50% growth is $100.00: $95.00 is out of the range, $105.00 in it.
    fcf = 100.0 / dcf_per_share(1.0, 1.0, -0.5, DISCOUNT, TG)
    assert implied_growth(fcf, 1.0, STORED_PRICE, DISCOUNT, TG) == (None, BELOW)
    _seed(web_db, reverse_dcf=_na("reverse_dcf", BELOW, _dcf(fcf=fcf, shares=1.0), unit="rate", basis=2024))
    live = _view(web_db)["reverse_dcf_live"]
    assert live["na_reason"] is None
    assert live["growth"] == pytest.approx(implied_growth(fcf, 1.0, LIVE_PRICE, DISCOUNT, TG)[0], abs=1e-9)
    assert -0.5 < live["growth"] < -0.4


def test_a_live_price_the_range_no_longer_reaches(web_db):
    # FCF set so the DCF's value at +100% growth is $100.00: $95.00 is in the range, $105.00 above it.
    fcf = 100.0 / dcf_per_share(1.0, 1.0, 1.0, DISCOUNT, TG)
    growth = implied_growth(fcf, 1.0, STORED_PRICE, DISCOUNT, TG)[0]
    _seed(web_db, reverse_dcf=IndicatorResult(indicator="reverse_dcf", applicable=True, value_base=growth, unit="rate",
                                              assumptions=_dcf(fcf=fcf, shares=1.0)))
    live = _view(web_db)["reverse_dcf_live"]
    assert (live["growth"], live["na_reason"]) == (None, ABOVE)


def test_a_reverse_dcf_without_its_inputs_keeps_its_reason(web_db):
    reason = "no DCF: not applicable to sector 'bank'"
    _seed(web_db, reverse_dcf=_na("reverse_dcf", reason, unit="rate"))
    live = _view(web_db)["reverse_dcf_live"]
    assert live == {"price": LIVE_PRICE, "growth": None, "na_reason": reason, "dcf_growth_base": None}


def test_without_a_live_price_nothing_is_solved_or_compared(web_db):
    _seed(web_db, ticker="EEE")  # EEE has no quote
    v = _view(web_db, "EEE")
    assert v["price"] is None
    assert v["reverse_dcf_live"]["growth"] is None and v["reverse_dcf_live"]["na_reason"] == "no price"
    assert all(i["upside_pct"] is None for i in v["indicators"])


def test_a_ticker_without_rows_is_not_computed(web_db):
    v = _view(web_db)
    assert (v["computed"], v["computed_at"], v["indicators"]) == (False, None, [])
    assert v["reverse_dcf_live"] == {"price": LIVE_PRICE, "growth": None, "na_reason": "not computed yet",
                                     "dcf_growth_base": None}


def test_assumptions_that_arent_json_read_as_empty(web_db):
    _seed(web_db)
    conn = sqlite3.connect(web_db)
    conn.execute("UPDATE valuation_indicators SET assumptions = 'not json' WHERE ticker = 'AAA'")
    conn.commit()
    conn.close()
    v = _view(web_db)
    assert all(i["assumptions"] == {} and i["notes"] == [] for i in v["indicators"])
    assert (v["reverse_dcf_live"]["growth"], v["reverse_dcf_live"]["na_reason"]) == (None, "no stored DCF inputs")


# ---- the API -----------------------------------------------------------------------------------------------------


def test_the_api_returns_the_view(client, web_db):
    _seed(web_db)
    resp = client.get("/api/stocks/AAA/valuation-indicators")
    assert resp.status_code == 200
    body = resp.json()
    assert body == json.loads(json.dumps(_view(web_db)))
    assert [i["indicator"] for i in body["indicators"]] == list(INDICATORS)
    assert body["reverse_dcf_live"]["growth"] == pytest.approx(
        implied_growth(FCF, SHARES, LIVE_PRICE, DISCOUNT, TG)[0])


def test_the_api_404s_an_unknown_ticker(client):
    resp = client.get("/api/stocks/ZZZZ/valuation-indicators")
    assert resp.status_code == 404


def test_the_api_on_a_database_from_before_the_indicators(client, web_db):
    conn = sqlite3.connect(web_db)
    conn.execute("DROP TABLE valuation_indicators")
    conn.commit()
    conn.close()
    resp = client.get("/api/stocks/AAA/valuation-indicators")
    assert resp.status_code == 200
    assert (resp.json()["computed"], resp.json()["indicators"]) == (False, [])


def test_the_price_charts_indicators_keep_their_path(client):
    # /api/stocks/{t}/indicators is the GP chart's MA/RSI/MACD bundle; the valuation indicators live beside it.
    body = client.get("/api/stocks/AAA/indicators", params={"range": "MAX"}).json()
    assert {"dates", "close", "ma_50", "ma_200", "rsi", "macd"} <= set(body)


# ---- the VAL tab's section ---------------------------------------------------------------------------------------


def test_the_val_tab_loads_the_section_after_owner_earnings(client, web_db):
    _seed_val_rows(web_db)
    _seed_owner_earnings(web_db)
    html = client.get("/ui/stocks/AAA/val").text
    assert 'hx-get="/ui/stocks/AAA/val-indicators"' in html
    placeholder = html.index('id="val-indicators"')
    assert html.index("Owner Earnings (Buffett)") < placeholder < html.index("DCF Sensitivity")
    tag = html[html.rindex("<div", 0, placeholder):html.index(">", placeholder)]
    assert 'hx-trigger="load"' in tag and 'hx-swap="outerHTML"' in tag


def test_the_section_shows_the_values_the_upsides_and_the_methods(client, web_db):
    _seed(web_db)
    resp = client.get("/ui/stocks/AAA/val-indicators")
    assert resp.status_code == 200
    html = resp.text
    assert '<section class="section" id="val-indicators">' in html and "Other indicators" in html
    assert "never enter the median, the verdict or the upside" in html
    for label in LABELS:
        assert label.replace("'", "&#39;") in html
    assert "$110.00" in html and "$126.00" in html and "$147.00" in html  # EPV's bear, base and bull
    assert "$52.50" in html and "$63.00" in html and "$36.00" in html
    for upside in ("20.0%", "-65.7%", "-40.0%", "-50.0%"):  # EPV's, NCAV's, P/S's and Graham's from $105.00
        assert f'<td class="num">{upside}</td>' in html
    # The reverse DCF at the live price, beside the DCF's own growth.
    live = fmt_pct(implied_growth(FCF, SHARES, LIVE_PRICE, DISCOUNT, TG)[0])
    assert live != fmt_pct(STORED_GROWTH) and f'<td class="num">{live}</td>' in html
    assert "the DCF assumes 4.0%" in html
    assert "fading to the terminal growth by year 10" in html  # a method
    assert "Graham bought below two thirds of NCAV: $24.00." in html  # a note
    assert "<details>" in html and "mean_operating_margin" in html  # the assumptions


def test_na_rows_show_their_reasons_across_the_values(client, web_db):
    reason = "net current assets are negative: total liabilities exceed current assets"
    _seed(web_db, ncav=_na("ncav", reason, {**NCAV, "ncav_per_share": -9.17}),
          epv=_na("epv", "not meaningful for banks"))
    html = client.get("/ui/stocks/AAA/val-indicators").text
    assert html.count('colspan="4"') == 2
    assert reason in html and "not meaningful for banks" in html
    assert "$126.00" not in html and "$36.00" not in html
    assert "NCAV is -$9.17 a share." in html


def test_the_reverse_dcf_row_shows_the_live_reason(client, web_db):
    fcf = 100.0 / dcf_per_share(1.0, 1.0, 1.0, DISCOUNT, TG)
    growth = implied_growth(fcf, 1.0, STORED_PRICE, DISCOUNT, TG)[0]
    _seed(web_db, reverse_dcf=IndicatorResult(indicator="reverse_dcf", applicable=True, value_base=growth, unit="rate",
                                              assumptions=_dcf(fcf=fcf, shares=1.0)))
    html = client.get("/ui/stocks/AAA/val-indicators").text
    assert ABOVE.replace("'", "&#39;") in html
    assert html.count('colspan="4"') == 1


def test_the_empty_state_says_how_to_compute_them(client):
    html = client.get("/ui/stocks/AAA/val-indicators").text
    assert "python -m src.valuation.backfill" in html
    assert "<table" not in html


def test_the_section_on_a_database_from_before_the_indicators(client, web_db):
    conn = sqlite3.connect(web_db)
    conn.execute("DROP TABLE valuation_indicators")
    conn.commit()
    conn.close()
    resp = client.get("/ui/stocks/AAA/val-indicators")
    assert resp.status_code == 200 and "python -m src.valuation.backfill" in resp.text


def test_the_section_404s_an_unknown_ticker(client):
    assert client.get("/ui/stocks/ZZZZ/val-indicators").status_code == 404


# ---- a real computation ------------------------------------------------------------------------------------------


def test_the_indicators_a_computation_stores_read_back(tmp_path):
    db = _seed_financials(tmp_path / "s.db")
    compute_and_store(db)
    client = TestClient(create_app(db_path=db))
    body = client.get("/api/stocks/AAA/valuation-indicators").json()
    assert body["computed"] is True and body["price"] == 50.0
    assert [i["indicator"] for i in body["indicators"]] == list(INDICATORS)
    assert all(i["applicable"] for i in body["indicators"]), [i["na_reason"] for i in body["indicators"]]
    stored = body["indicators"][0]
    # The live price is the snapshot's the growth was stored at, so the live solve gives it back.
    assert body["reverse_dcf_live"]["growth"] == pytest.approx(stored["value_base"], abs=1e-9)
    assert body["reverse_dcf_live"]["dcf_growth_base"] == stored["assumptions"]["dcf_growth_base"]
    assert all(i["notes"] for i in body["indicators"] if i["indicator"] in ("reverse_dcf", "epv", "ps_value"))
    bank = client.get("/api/stocks/BBB/valuation-indicators").json()
    assert bank["reverse_dcf_live"]["na_reason"] == "no DCF: not applicable to sector 'bank'"
    html = client.get("/ui/stocks/AAA/val-indicators").text
    assert all(label.replace("'", "&#39;") in html for label in LABELS)

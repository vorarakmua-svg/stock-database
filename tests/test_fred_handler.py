"""FREDHandler's key-less Treasury.gov fallback (no network: the feed request is stubbed)."""

import logging
from datetime import datetime

import pytest
import requests

from src.config import AppConfig, StorageConfig
from src.fetchers.fred_handler import FREDHandler
from src.fetchers.stock_data_fetcher import StockDataFetcher

# The daily Treasury par yield curve feed as Treasury.gov serves it: newest date first.
_HEADER = 'Date,"1 Mo","3 Mo","10 Yr","30 Yr"'
_ROWS = ["10/19/2026,4.30,4.25,5.15,5.40", "10/16/2026,4.31,4.26,5.10,5.38", "01/02/2026,4.40,4.33,4.19,4.70"]


class _Feed:
    def __init__(self, text: str) -> None:
        self.status_code = 200
        self.text = text


def _handler(monkeypatch, rows) -> FREDHandler:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    handler = FREDHandler(api_key=None, rate_limit_delay=0)
    feed = "\n".join([_HEADER, *rows])
    monkeypatch.setattr(handler, "_request", lambda url, params=None, timeout=30: _Feed(feed))
    return handler


def test_the_keyless_rate_is_the_feeds_latest_day(monkeypatch):
    # Treasury.gov lists the year newest first. Reading its last row gave the year's first trading day:
    # on 2026-10-20 every DCF and DDM used January 2's 4.19% instead of October 19's 5.15%.
    assert _handler(monkeypatch, _ROWS).get_risk_free_rate("10y") == pytest.approx(0.0515)


def test_the_treasury_feed_takes_its_latest_date_in_either_order(monkeypatch):
    yields = _handler(monkeypatch, list(reversed(_ROWS)))._fetch_treasury_gov_year(2026)
    assert (yields["treasury_10y"], yields["date"]) == (5.15, "2026-10-19")


def test_a_feed_row_without_yields_gives_no_rate(monkeypatch):
    # A dated row whose yields don't parse is no rate: the caller then tries the prior year or the estimate.
    assert _handler(monkeypatch, ["10/19/2026,N/A,N/A,N/A,N/A"])._fetch_treasury_gov_year(2026) is None


def test_the_rate_comes_with_its_day_and_source(monkeypatch):
    # The spec keeps the rate "and its as-of date so staleness is visible"; key-less runs recorded only
    # "fallback", whether the rate was Treasury.gov's or the static estimate.
    details = _handler(monkeypatch, _ROWS).get_risk_free_rate_details("10y")
    assert details == {"rate": pytest.approx(0.0515), "date": "2026-10-19", "source": "treasury_gov"}


def test_the_static_estimate_says_so_and_has_no_day(monkeypatch):
    handler = _handler(monkeypatch, _ROWS)
    monkeypatch.setattr(handler, "_treasury_gov_year", lambda year: None)  # no rows in either year
    details = handler.get_risk_free_rate_details("10y")
    assert (details["source"], details["date"]) == ("estimate", None)
    assert details["rate"] == pytest.approx(0.0435)



# ---- BUG-052: a failed year isn't an empty one, and a blank 10-year isn't the latest day's ----

_THIS_YEAR = datetime.now().year
#: The prior year's last trading day, which the fallback took for today's whenever the current year's request failed.
_LAST_YEAR = [f"12/31/{_THIS_YEAR - 1},4.31,4.29,4.57,4.78", f"12/30/{_THIS_YEAR - 1},4.32,4.30,4.55,4.77"]
_THIS_YEARS = [f"10/19/{_THIS_YEAR},4.30,4.25,5.15,5.40", f"10/16/{_THIS_YEAR},4.31,4.26,5.10,5.38"]


def _by_year(monkeypatch, answers, cache_dir=None) -> FREDHandler:
    """A key-less handler whose Treasury.gov answers are *answers*: a year -> its rows (served with the header), an
    HTTP status (served with the header alone, which is still no file), an exception to raise, or a text served as it
    is."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    handler = FREDHandler(api_key=None, rate_limit_delay=0, **({"cache_dir": cache_dir} if cache_dir else {}))

    def request(url, params=None, timeout=30):
        answer = next(a for year, a in answers.items() if f"daily-treasury-rates.csv/{year}/" in url)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            feed = _Feed(_HEADER)
            feed.status_code = answer
            return feed
        return _Feed(answer if isinstance(answer, str) else "\n".join([_HEADER, *answer]))

    monkeypatch.setattr(handler, "_request", request)
    return handler


@pytest.mark.parametrize("failure", [500, requests.exceptions.ConnectionError("refused"),
                                     "<html><body>Service Unavailable</body></html>"])
def test_a_failed_current_year_isnt_answered_with_last_years_year_end(monkeypatch, failure):
    # On 2026-10-20 a 500 from the current year's request gave 2025-12-31's 4.57% as the rate: the prior year is
    # read only while the current year has no rows (early January), never for a failure.
    details = _by_year(monkeypatch, {_THIS_YEAR: failure, _THIS_YEAR - 1: _LAST_YEAR}).get_risk_free_rate_details()
    assert details["date"] != f"{_THIS_YEAR - 1}-12-31"
    assert (details["source"], details["date"]) == ("estimate", None)


def test_a_failed_current_year_takes_the_rate_last_fetched_with_its_day(monkeypatch, tmp_path, caplog):
    _by_year(monkeypatch, {_THIS_YEAR: _THIS_YEARS}, tmp_path).get_risk_free_rate_details()
    handler = _by_year(monkeypatch, {_THIS_YEAR: 500, _THIS_YEAR - 1: _LAST_YEAR}, tmp_path)
    with caplog.at_level(logging.WARNING):
        details = handler.get_risk_free_rate_details("10y")
    assert details == {"rate": pytest.approx(0.0515), "date": f"{_THIS_YEAR}-10-19", "source": "treasury_gov"}
    assert f"the rate last fetched, for {_THIS_YEAR}-10-19" in caplog.text


def test_an_answer_with_rows_replaces_the_rate_kept(monkeypatch, tmp_path):
    _by_year(monkeypatch, {_THIS_YEAR: _THIS_YEARS[1:]}, tmp_path).get_risk_free_rate_details()
    _by_year(monkeypatch, {_THIS_YEAR: _THIS_YEARS}, tmp_path).get_risk_free_rate_details()
    details = _by_year(monkeypatch, {_THIS_YEAR: 503, _THIS_YEAR - 1: 503}, tmp_path).get_risk_free_rate_details()
    assert (details["rate"], details["date"]) == (pytest.approx(0.0515), f"{_THIS_YEAR}-10-19")


@pytest.mark.parametrize("kept", ["not json", '{"source": "estimate", "date": null, "treasury_10y": 4.35}',
                                  '{"source": "treasury_gov", "treasury_10y": 5.15}'])
def test_a_kept_rate_that_isnt_treasury_govs_dated_rate_is_none(monkeypatch, tmp_path, kept):
    (tmp_path / "treasury_gov_rate.json").write_text(kept, encoding="utf-8")
    details = _by_year(monkeypatch, {_THIS_YEAR: 500}, tmp_path).get_risk_free_rate_details()
    assert (details["source"], details["date"]) == ("estimate", None)


def test_without_a_cache_folder_no_rate_is_kept(monkeypatch, tmp_path):
    _by_year(monkeypatch, {_THIS_YEAR: _THIS_YEARS}).get_risk_free_rate_details()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("current", [[], [f"01/02/{_THIS_YEAR},4.30,4.25,N/A,5.40"]])
def test_a_current_year_without_rows_takes_the_prior_years_latest_day(monkeypatch, current):
    # Early January the current year's file has no rows yet (or none with a 10-year yield): the prior year's last day
    # is the latest rate (GATHER-04's prior-year path, untested until now).
    details = _by_year(monkeypatch, {_THIS_YEAR: current, _THIS_YEAR - 1: _LAST_YEAR}).get_risk_free_rate_details()
    assert details == {"rate": pytest.approx(0.0457), "date": f"{_THIS_YEAR - 1}-12-31", "source": "treasury_gov"}


@pytest.mark.parametrize("blank", ["", "N/A"])
def test_a_blank_latest_10_year_takes_the_latest_day_that_has_one(monkeypatch, blank):
    # A latest row whose 10 Yr is blank gave no rate at all, though the day before had one.
    rows = [f"10/19/{_THIS_YEAR},4.30,4.25,{blank},5.40", *_THIS_YEARS[1:]]
    details = _by_year(monkeypatch, {_THIS_YEAR: rows}).get_risk_free_rate_details("10y")
    assert details == {"rate": pytest.approx(0.0510), "date": f"{_THIS_YEAR}-10-16", "source": "treasury_gov"}


def test_a_collection_keeps_the_rate_in_its_cache_folder(tmp_path):
    config = AppConfig(storage=StorageConfig(base_dir=tmp_path))
    with StockDataFetcher(config=config) as fetcher:
        assert fetcher.fred_handler.cache_dir == config.storage.cache_dir

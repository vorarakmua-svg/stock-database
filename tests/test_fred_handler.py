"""FREDHandler's key-less Treasury.gov fallback (no network: the feed request is stubbed)."""

import pytest

from src.fetchers.fred_handler import FREDHandler

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

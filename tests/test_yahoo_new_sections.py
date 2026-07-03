"""Tests for Task 2 Yahoo pipeline additions: price bars, earnings history, splits,
pre/post-market fields, and the ^GSPC benchmark fetch.

All tests monkeypatch ``yf.Ticker`` with fakes exposing only the surface
``fetch_all`` touches — no network calls.
"""

import pandas as pd

from src.fetchers import yahoo_handler as yh_module
from src.fetchers.yahoo_handler import YahooHandler


class _FakeTicker:
    """Fake yfinance Ticker exposing the surface fetch_all touches."""

    def __init__(self, ticker="AAA"):
        self.ticker = ticker

    @property
    def info(self):
        return {
            "longName": "Fake Corp",
            "symbol": self.ticker,
            "currentPrice": 100.0,
            "postMarketPrice": 101.5,
            "preMarketPrice": 99.25,
        }

    def history(self, period="1y", interval="1d"):
        if period == "max" and interval == "1d":
            dates = pd.date_range("2024-01-01", periods=30, freq="D")
            return pd.DataFrame(
                {
                    "Open": [float(i) for i in range(30)],
                    "High": [float(i) + 1 for i in range(30)],
                    "Low": [float(i) - 1 for i in range(30)],
                    "Close": [float(i) + 0.5 for i in range(30)],
                    "Volume": [1000.0 + i for i in range(30)],
                },
                index=dates,
            )
        return pd.DataFrame()

    @property
    def earnings_history(self):
        dates = pd.to_datetime(["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"])
        return pd.DataFrame(
            {
                "epsEstimate": [1.0, 1.1, 1.2, 1.3],
                "epsActual": [1.05, 1.08, 1.25, 1.28],
                "surprisePercent": [5.0, -1.8, 4.2, -1.5],
            },
            index=dates,
        )

    @property
    def splits(self):
        return pd.Series([2.0], index=pd.to_datetime(["2024-06-10"]))

    @property
    def dividends(self):
        return pd.Series(dtype=float)

    @property
    def major_holders(self):
        return pd.DataFrame()

    @property
    def institutional_holders(self):
        return pd.DataFrame()

    @property
    def mutualfund_holders(self):
        return pd.DataFrame()

    @property
    def insider_transactions(self):
        return pd.DataFrame()

    @property
    def income_stmt(self):
        return pd.DataFrame()

    @property
    def quarterly_income_stmt(self):
        return pd.DataFrame()

    @property
    def balance_sheet(self):
        return pd.DataFrame()

    @property
    def quarterly_balance_sheet(self):
        return pd.DataFrame()

    @property
    def cashflow(self):
        return pd.DataFrame()

    @property
    def quarterly_cashflow(self):
        return pd.DataFrame()

    @property
    def calendar(self):
        return pd.DataFrame()

    @property
    def recommendations(self):
        return pd.DataFrame()


class _FakeTickerBadEarnings(_FakeTicker):
    """Earnings history whose columns match none of the known candidate names."""

    @property
    def earnings_history(self):
        return pd.DataFrame({"foo": [1, 2], "bar": [3, 4]})


def _patch_ticker(monkeypatch, fake_cls):
    monkeypatch.setattr(yh_module.yf, "Ticker", fake_cls)


def test_fetch_all_includes_price_bars(monkeypatch):
    _patch_ticker(monkeypatch, _FakeTicker)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    bars = data["price_bars"]
    assert len(bars) == 30
    assert bars[0] == {
        "date": "2024-01-01", "open": 0.0, "high": 1.0, "low": -1.0,
        "close": 0.5, "volume": 1000.0,
    }
    # ascending order
    assert [b["date"] for b in bars] == sorted(b["date"] for b in bars)


def test_fetch_all_includes_earnings_history(monkeypatch):
    _patch_ticker(monkeypatch, _FakeTicker)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    assert data["earnings_history"] == [
        {"quarter": "2023-03-31", "eps_estimate": 1.0, "eps_actual": 1.05, "surprise_pct": 5.0},
        {"quarter": "2023-06-30", "eps_estimate": 1.1, "eps_actual": 1.08, "surprise_pct": -1.8},
        {"quarter": "2023-09-30", "eps_estimate": 1.2, "eps_actual": 1.25, "surprise_pct": 4.2},
        {"quarter": "2023-12-31", "eps_estimate": 1.3, "eps_actual": 1.28, "surprise_pct": -1.5},
    ]


def test_fetch_all_earnings_history_defensive_on_unexpected_columns(monkeypatch):
    """An earnings_history frame whose columns match no known candidate -> []."""
    _patch_ticker(monkeypatch, _FakeTickerBadEarnings)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    assert data["earnings_history"] == []


def test_fetch_all_includes_splits(monkeypatch):
    _patch_ticker(monkeypatch, _FakeTicker)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    assert data["splits"] == [{"date": "2024-06-10", "ratio": 2.0}]


def test_fetch_all_includes_pre_post_market_price(monkeypatch):
    _patch_ticker(monkeypatch, _FakeTicker)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    assert data["market_data"]["post_market_price"] == 101.5
    assert data["market_data"]["pre_market_price"] == 99.25


def test_fetch_benchmark_bars(monkeypatch):
    _patch_ticker(monkeypatch, _FakeTicker)
    handler = YahooHandler(rate_limit_delay=0.0)
    bars = handler.fetch_benchmark_bars("^GSPC")

    assert len(bars) == 30
    assert bars[0]["date"] == "2024-01-01"
    assert bars[-1]["date"] == "2024-01-30"


def test_fetch_benchmark_bars_defaults_to_gspc(monkeypatch):
    captured = {}

    class _CapturingTicker(_FakeTicker):
        def __init__(self, ticker="AAA"):
            captured["ticker"] = ticker
            super().__init__(ticker)

    _patch_ticker(monkeypatch, _CapturingTicker)
    handler = YahooHandler(rate_limit_delay=0.0)
    handler.fetch_benchmark_bars()

    assert captured["ticker"] == "^GSPC"


def test_fetch_benchmark_bars_defensive_on_failure(monkeypatch):
    class _Boom:
        def __init__(self, ticker):
            raise RuntimeError("network down")

    monkeypatch.setattr(yh_module.yf, "Ticker", _Boom)
    handler = YahooHandler(rate_limit_delay=0.0)

    assert handler.fetch_benchmark_bars("^GSPC") == []

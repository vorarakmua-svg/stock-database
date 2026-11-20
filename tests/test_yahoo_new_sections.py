"""Tests for Task 2 Yahoo pipeline additions: price bars, earnings history, splits,
pre/post-market fields, and the ^GSPC benchmark fetch.

All tests monkeypatch ``yf.Ticker`` with fakes exposing only the surface
``fetch_all`` touches — no network calls.
"""

import json

import pandas as pd
import pytest

from src.fetchers import yahoo_handler as yh_module
from src.fetchers.yahoo_handler import YahooHandler
from src.models.stock_data import StockData


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

    def history(self, period="1y", interval="1d", **kwargs):
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
        # A chart Yahoo served without a payment, as yfinance gives it: its "Dividends" column, none of them non-zero.
        return pd.Series(dtype=float, name="Dividends", index=pd.DatetimeIndex([], tz="America/New_York"))

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


class _FakeTickerWithDividendYield(_FakeTicker):
    """Fake Ticker whose info carries percent-unit dividend yield fields,
    matching yfinance >= 0.2.50's actual (percent, not fraction) units."""

    @property
    def info(self):
        return {
            **super().info,
            "dividendYield": 0.35,
            "fiveYearAvgDividendYield": 0.52,
        }


class _FakeTickerBadEarnings(_FakeTicker):
    """Earnings history whose columns match none of the known candidate names."""

    @property
    def earnings_history(self):
        return pd.DataFrame({"foo": [1, 2], "bar": [3, 4]})


class _FakeTickerTzAware(_FakeTicker):
    """Fake Ticker returning timezone-aware DatetimeIndex (matching real yfinance)."""

    def history(self, period="1y", interval="1d", **kwargs):
        if period == "max" and interval == "1d":
            dates = pd.date_range("2024-01-01", periods=3, freq="D", tz="America/New_York")
            return pd.DataFrame(
                {
                    "Open": [100.0, 101.0, 102.0],
                    "High": [101.0, 102.0, 103.0],
                    "Low": [99.0, 100.0, 101.0],
                    "Close": [100.5, 101.5, 102.5],
                    "Volume": [1000.0, 1001.0, 1002.0],
                },
                index=dates,
            )
        return pd.DataFrame()

    @property
    def splits(self):
        return pd.Series([4.0], index=pd.DatetimeIndex(["2024-06-10"], tz="America/New_York"))


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


def test_fetch_all_price_bars_with_tz_aware_index(monkeypatch):
    """Guard against date-shift bugs: tz-aware indexes must not shift local dates."""
    _patch_ticker(monkeypatch, _FakeTickerTzAware)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    bars = data["price_bars"]
    assert len(bars) == 3
    assert [b["date"] for b in bars] == ["2024-01-01", "2024-01-02", "2024-01-03"]
    assert [b["date"] for b in bars] == sorted(b["date"] for b in bars)


def test_fetch_all_splits_with_tz_aware_index(monkeypatch):
    """Guard against date-shift bugs: tz-aware index on splits must not shift date."""
    _patch_ticker(monkeypatch, _FakeTickerTzAware)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    assert data["splits"] == [{"date": "2024-06-10", "ratio": 4.0}]


def test_fetch_all_normalizes_percent_unit_dividend_yields(monkeypatch):
    """yfinance >= 0.2.50 returns dividendYield/fiveYearAvgDividendYield in
    percent units (0.35 meaning 0.35%); the store must hold fractions so the
    webapp's fmt_pct (x100) renders them correctly, consistent with sibling
    fraction fields like short_percent_of_float."""
    _patch_ticker(monkeypatch, _FakeTickerWithDividendYield)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    assert data["valuation"]["dividend_yield"] == pytest.approx(0.0035)
    assert data["dividend_history"]["dividend_yield"] == pytest.approx(0.0035)
    assert data["dividend_history"]["five_year_avg_dividend_yield"] == pytest.approx(0.0052)


def test_fetch_all_dividend_yield_none_stays_none(monkeypatch):
    """When yfinance omits dividendYield/fiveYearAvgDividendYield, the
    normalization must not turn the missing value into 0.0 or crash."""
    _patch_ticker(monkeypatch, _FakeTicker)
    handler = YahooHandler(rate_limit_delay=0.0)
    data = handler.fetch_all("AAA")

    assert data["valuation"]["dividend_yield"] is None
    assert data["dividend_history"]["dividend_yield"] is None
    assert data["dividend_history"]["five_year_avg_dividend_yield"] is None


class _FakeTickerAdjusting(_FakeTicker):
    """Like yfinance 1.0: history() adjusts every price for later dividends unless auto_adjust=False is asked for."""

    def history(self, period="1y", interval="1d", auto_adjust=True, **kwargs):
        if period != "max":
            return pd.DataFrame()
        market = [59.0, 59.1, 59.21]
        later_dividends = 0.8706  # Yahoo's factor for the dividends paid since
        frame = {"Open": market, "High": market, "Low": market, "Close": market, "Volume": [1.0e7] * 3}
        if auto_adjust:
            frame = {k: v if k == "Volume" else [p * later_dividends for p in v] for k, v in frame.items()}
        else:
            frame["Adj Close"] = [p * later_dividends for p in market]
        return pd.DataFrame(frame, index=pd.date_range("2021-12-29", periods=3, freq="D"))


def test_price_bars_are_market_closes_not_dividend_adjusted(monkeypatch):
    # BUG-075: history() adjusts for dividends by default, so KO's 2021-12-31 close was stored as 51.55, not the 59.21
    # it closed at, and the multiples band read P/Es lowered by every dividend paid since.
    _patch_ticker(monkeypatch, _FakeTickerAdjusting)
    bars = YahooHandler(rate_limit_delay=0.0).fetch_all("KO")["price_bars"]
    assert [b["close"] for b in bars] == [59.0, 59.1, 59.21]


class _FakeTickerBarsFail(_FakeTicker):
    """Every request works but the price history's (auto_adjust=False)."""

    def history(self, period="1y", interval="1d", auto_adjust=True, **kwargs):
        if auto_adjust is False:
            raise RuntimeError("chart request failed")
        return super().history(period=period, interval=interval, **kwargs)


def test_a_failed_price_history_request_is_a_run_warning(monkeypatch):
    # BUG-084 (code review 8): a failed bar request returned no bars and was only logged, so a run could store newer
    # dividends beside the old bars with nothing to say they hadn't been refreshed.
    _patch_ticker(monkeypatch, _FakeTickerBarsFail)
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("AAA")
    assert data["price_bars"] == []
    stock = StockData(ticker="AAA")
    stock.merge_yahoo_data(data)
    assert [w for w in stock.warnings if "price history" in w] == [
        "Yahoo Finance: price history unavailable (chart request failed); the stored daily bars weren't refreshed"]


class _FakeTickerQuoteFails(_FakeTicker):
    """yfinance 1.0 when quoteSummary and v7/quote both fail: info raises, the chart (history, dividends, splits)
    answers."""

    @property
    def info(self):
        raise TypeError("argument of type 'NoneType' is not iterable")

    @property
    def dividends(self):
        return pd.Series([0.5, 0.05], index=pd.to_datetime(["2024-05-15", "2024-08-15"]))


def test_a_failed_quote_still_brings_the_dividends_with_the_splits(monkeypatch):
    # BUG-094 (code review 9): the dividend history read `info` before `dividends`, so a failed quote kept the stored
    # dividends from before a split the run stored from the chart, and DDM took them back by it: ten times over.
    _patch_ticker(monkeypatch, _FakeTickerQuoteFails)
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("AAA")
    assert [p["date"] for p in data["dividend_history"]["dividend_payments"]] == ["2024-05-15", "2024-08-15"]
    assert data["splits"] == [{"date": "2024-06-10", "ratio": 2.0}]
    stock = StockData(ticker="AAA")
    stock.merge_yahoo_data(data)
    assert "Yahoo Finance: quote unavailable; the stored price wasn't refreshed" in stock.warnings


class _FakeTickerDividendsFail(_FakeTicker):
    """The chart answers the split request but not the dividend one."""

    @property
    def dividends(self):
        raise RuntimeError("chart request failed")


def test_a_run_whose_dividends_fail_keeps_its_splits(monkeypatch):
    # BUG-103 (code review 10): BUG-094's fix dropped the splits whenever the dividend history failed, which helped only
    # when the quote failed too: with the quote and bars on the new basis, nothing recorded the split, and Graham and
    # Lynch read ten times their value. The stored dividends keep the day Yahoo served them (`served_on`), which takes
    # them to the price's basis instead.
    _patch_ticker(monkeypatch, _FakeTickerDividendsFail)
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("AAA")
    assert data["splits"] == [{"date": "2024-06-10", "ratio": 2.0}]
    stock = StockData(ticker="AAA")
    stock.merge_yahoo_data(data)
    assert [w for w in stock.warnings if "dividend history" in w] == [
        "Yahoo Finance: dividend history unavailable (chart request failed); the stored dividends weren't refreshed"]


# ---- BUG-107: yfinance answers a failed chart request with an empty history, not an error ----

def _silent_failure_frame():
    """yfinance's answer to a chart request that failed (``utils.empty_df``): no rows, and no event columns."""
    return pd.DataFrame(index=pd.DatetimeIndex([], name="Date"),
                        data={c: [] for c in ("Open", "High", "Low", "Close", "Adj Close", "Volume")})


class _FakeTickerChartFailsSilently(_FakeTicker):
    """yfinance 1.0 when the chart request fails with a cold timezone cache ("possibly delisted; no timezone found"),
    or Yahoo answers it with an error: history() gives an empty frame without the "Dividends" and "Stock Splits"
    columns, and dividends and splits an unnamed empty Series, rather than raise."""

    def history(self, period="1y", interval="1d", **kwargs):
        return _silent_failure_frame()

    @property
    def dividends(self):
        return pd.Series(dtype=object)

    @property
    def splits(self):
        return pd.Series(dtype=object)


_NO_HISTORY = "yfinance returned no history, its answer to a failed request"


def test_a_chart_request_yfinance_failed_silently_isnt_a_stock_without_dividends(monkeypatch):
    # BUG-107: an outage read "No dividend history (non-dividend paying stock)" with no warning, for KO, JNJ and PG.
    _patch_ticker(monkeypatch, _FakeTickerChartFailsSilently)
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("KO")
    assert "note" not in data["dividend_history"] and "dividend_payments" not in data["dividend_history"]
    stock = StockData(ticker="KO")
    stock.merge_yahoo_data(data)
    assert [w for w in stock.warnings if "history unavailable" in w] == [
        f"Yahoo Finance: dividend history unavailable ({_NO_HISTORY}); the stored dividends weren't refreshed",
        f"Yahoo Finance: split history unavailable ({_NO_HISTORY}); the stored splits weren't refreshed",
        f"Yahoo Finance: price history unavailable ({_NO_HISTORY}); the stored daily bars weren't refreshed",
    ]


def test_a_chart_yahoo_served_without_a_payment_is_a_stock_without_dividends(monkeypatch):
    class Served(_FakeTicker):
        @property
        def splits(self):
            return pd.Series(dtype=float, name="Stock Splits", index=pd.DatetimeIndex([], tz="America/New_York"))

    _patch_ticker(monkeypatch, Served)
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("TSLA")
    assert data["dividend_history"]["note"] == "No dividend history (non-dividend paying stock)"
    assert data["dividend_history"]["dividend_payments"] == [] and data["splits"] == []
    assert not [w for w in data.get("warnings", []) if "history unavailable" in w]


def test_a_served_history_without_a_day_is_no_failure(monkeypatch):
    # A chart Yahoo served holds the event columns, rows or not: only their absence says the request failed.
    class Empty(_FakeTicker):
        def history(self, period="1y", interval="1d", **kwargs):
            return _silent_failure_frame().assign(Dividends=[], **{"Stock Splits": []})

    _patch_ticker(monkeypatch, Empty)
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("AAA")
    assert data["price_bars"] == []
    assert not [w for w in data.get("warnings", []) if "price history" in w]


def test_a_failed_split_history_request_is_a_run_warning(monkeypatch):
    class SplitsFail(_FakeTicker):
        @property
        def splits(self):
            raise RuntimeError("chart request failed")

    _patch_ticker(monkeypatch, SplitsFail)
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("AAA")
    assert data["splits"] == []
    assert [w for w in data["warnings"] if "split history" in w] == [
        "split history unavailable (chart request failed); the stored splits weren't refreshed"]


def test_a_quote_whose_split_history_failed_isnt_stored(monkeypatch):
    # The refresh stores a price only once it knows no split came since the last collection (BUG-093): a split history
    # yfinance failed to get is no answer to that. The quote itself answered.
    _patch_ticker(monkeypatch, _FakeTickerChartFailsSilently)
    quote = YahooHandler(rate_limit_delay=0.0).fetch_quote("AAA")
    assert quote == {"error": f"Yahoo's split history unavailable ({_NO_HISTORY}), so the quote can't be checked "
                              "for a split since the last collection"}


def test_a_quote_whose_chart_was_served_is_stored_with_its_payments(monkeypatch):
    _patch_ticker(monkeypatch, _FakeTicker)
    quote = YahooHandler(rate_limit_delay=0.0).fetch_quote("AAA")
    assert "error" not in quote and quote["splits"] == [{"date": "2024-06-10", "ratio": 2.0}]
    assert quote["dividend_history"]["note"] == "No dividend history (non-dividend paying stock)"


# ---- the same through the installed yfinance, every request of it answered without the network ----

class _TzCache:
    """yfinance's timezone cache, holding *tz* for every ticker (None: cold)."""

    def __init__(self, tz):
        self.tz = tz

    def lookup(self, ticker):
        return self.tz

    def store(self, ticker, tz):
        pass


class _ChartResponse:
    """Yahoo's v8 chart answer for three days of a stock, with a dividend on the second when *paid*."""

    status_code = 200
    url = "https://query2.finance.yahoo.com/v8/finance/chart/"

    def __init__(self, paid):
        days = [1704205800, 1704292200, 1704378600]  # 2024-01-02 to 01-04, at the open
        self.payload = {"chart": {"error": None, "result": [{
            "meta": {"currency": "USD", "symbol": "AAA", "instrumentType": "EQUITY", "priceHint": 2,
                     "exchangeTimezoneName": "America/New_York", "timezone": "EST", "gmtoffset": -18000,
                     "validRanges": ["1d", "5d", "max"], "regularMarketPrice": 60.0},
            "timestamp": days,
            "events": {"dividends": {str(days[1]): {"amount": 0.46, "date": days[1]}}} if paid else {},
            "indicators": {"quote": [{"open": [59.0, 59.5, 60.0], "high": [60.0] * 3, "low": [58.0] * 3,
                                      "close": [59.5, 60.0, 60.0], "volume": [1000] * 3}],
                           "adjclose": [{"adjclose": [59.5, 60.0, 60.0]}]}}]}}
        self.text = json.dumps(self.payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


def _yfinance_answering(monkeypatch, tz, chart=None):
    """The installed yfinance with its timezone cache holding *tz*, and every request of it failing (no network) but
    the chart's, answered by *chart* when given."""
    import yfinance.cache
    import yfinance.data

    def get(self, url, *args, **kwargs):
        if chart is not None and "/v8/finance/chart/" in url:
            return chart
        raise OSError("no network")

    for method in ("get", "cache_get", "post"):
        monkeypatch.setattr(yfinance.data.YfData, method, get)
    monkeypatch.setattr(yfinance.cache, "get_tz_cache", lambda: _TzCache(tz))


@pytest.mark.parametrize("tz", [None, "America/New_York"])
def test_the_installed_yfinances_failed_chart_request_is_a_warning(monkeypatch, tz):
    # BUG-107's repro: with a cold timezone cache yfinance logs the failure and returns an empty history; with a warm
    # one it raises. Either way the run says the dividends, splits and bars weren't refreshed.
    _yfinance_answering(monkeypatch, tz)
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("JNJ")
    assert "note" not in data["dividend_history"]
    assert [w.split(" (")[0] for w in data["warnings"] if "history unavailable" in w] == [
        "dividend history unavailable", "split history unavailable", "price history unavailable"]


@pytest.mark.parametrize("paid, payments, note", [
    (False, [], "No dividend history (non-dividend paying stock)"),
    (True, [{"date": "2024-01-03", "amount": 0.46}], None),
])
def test_the_installed_yfinances_served_chart_is_read_as_served(monkeypatch, paid, payments, note):
    _yfinance_answering(monkeypatch, "America/New_York", _ChartResponse(paid))
    data = YahooHandler(rate_limit_delay=0.0).fetch_all("AAA")
    assert data["dividend_history"]["dividend_payments"] == payments
    assert data["dividend_history"].get("note") == note
    assert len(data["price_bars"]) == 3 and data["splits"] == []
    assert not [w for w in data["warnings"] if "history unavailable" in w]

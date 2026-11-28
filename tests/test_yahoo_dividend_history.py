"""The collection's yearly dividend sums read a payment Yahoo serves twice once (BUG-385; BUG-154)."""

import pandas as pd

from src.fetchers.yahoo_handler import YahooHandler


class _Ticker:
    """yfinance's Ticker as _get_dividend_history reads it: the chart's dividends and a quote."""

    def __init__(self, dividends):
        self.dividends = dividends
        self.info = {}


def test_the_runs_yearly_dividends_count_a_payment_yahoo_served_twice_once():
    # BUG-385 (BUG-154): KO's shape. Yahoo serves 2001-09-10's 0.18 again on 2001-09-12 (the market was shut).
    # distinct_payments reads it once in every reader of dividend_events, but the run's own aggregates, which the
    # JSON export writes (StockData.to_dict -> dividend_history), sum both: 2001 reads 0.90 for 0.72.
    days = ["2000-03-13", "2000-06-12", "2000-09-11", "2000-12-11",
            "2001-03-12", "2001-06-11", "2001-09-10", "2001-09-12", "2001-12-10",
            "2002-03-11", "2002-06-10", "2002-09-09", "2002-12-09"]
    amounts = [0.17] * 4 + [0.18] * 5 + [0.20] * 4
    series = pd.Series(amounts, index=pd.DatetimeIndex(days), name="Dividends")
    result = YahooHandler(rate_limit_delay=0)._get_dividend_history(_Ticker(series))
    assert abs(result["annual_dividends"]["2001"] - 0.72) < 1e-9, result["annual_dividends"]
    assert result["total_payments"] == 12, result["total_payments"]
    assert result["dividend_increases"] == 2 and result["dividend_consistency"] == 1.0, result
    assert abs(result["dividend_cagr"] - ((0.80 / 0.68) ** 0.5 - 1)) < 1e-9, result["dividend_cagr"]

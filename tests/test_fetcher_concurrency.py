"""Tests that parallel fetch_multiple preserves order and matches sequential."""

import time

from src.config import AppConfig, StorageConfig
from src.fetchers.stock_data_fetcher import StockDataFetcher
from src.models.stock_data import StockData


def _make_fetcher(tmp_path, workers):
    config = AppConfig(
        storage=StorageConfig(base_dir=tmp_path),
        max_workers=workers,
    )
    return StockDataFetcher(config=config)


def test_parallel_preserves_input_order(tmp_path, monkeypatch):
    fetcher = _make_fetcher(tmp_path, workers=4)

    def fake_fetch(ticker, include_yahoo=True, include_sec=True, years_back=10):
        # Tiny stagger so threads genuinely interleave.
        time.sleep(0.01)
        return StockData(ticker=ticker)

    monkeypatch.setattr(fetcher, "fetch_ticker", fake_fetch)

    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    results = fetcher.fetch_multiple(tickers)
    assert [s.ticker for s in results] == tickers


def test_parallel_matches_sequential(tmp_path, monkeypatch):
    def fake_fetch(ticker, include_yahoo=True, include_sec=True, years_back=10):
        return StockData(ticker=ticker, company_name=f"{ticker} Inc.")

    seq = _make_fetcher(tmp_path / "seq", workers=1)
    par = _make_fetcher(tmp_path / "par", workers=4)
    monkeypatch.setattr(seq, "fetch_ticker", fake_fetch)
    monkeypatch.setattr(par, "fetch_ticker", fake_fetch)

    tickers = ["AAA", "BBB", "CCC"]
    assert [s.ticker for s in seq.fetch_multiple(tickers)] == \
           [s.ticker for s in par.fetch_multiple(tickers)]


def test_failure_becomes_error_stock(tmp_path, monkeypatch):
    fetcher = _make_fetcher(tmp_path, workers=2)

    def boom(ticker, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetcher, "fetch_ticker", boom)
    results = fetcher.fetch_multiple(["AAA", "BBB"])
    assert len(results) == 2
    assert all(s.errors for s in results)

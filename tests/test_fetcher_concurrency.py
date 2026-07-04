"""Tests that parallel fetch_multiple preserves order and matches sequential."""

import sqlite3
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


def test_compute_metrics_is_sector_aware(tmp_path):
    fetcher = _make_fetcher(tmp_path, workers=1)
    stock = StockData(ticker="RIT", cik="000", company_name="R Inc.")
    stock.sector_class = "reit"
    stock.financials_annual = {
        "2024": {"net_income": 100.0, "depreciation_amortization": 40.0,
                 "capex": 10.0, "revenue": 500.0, "total_assets": 2000.0,
                 "total_equity": 800.0},
    }
    fetcher._compute_metrics(stock)
    cm = stock.calculated_metrics
    assert cm["ffo"] == 140.0                 # REIT ratio present
    assert cm["roic"] is None                 # suppressed for REITs
    assert cm["historical"]["2024"]["ffo"] == 140.0


def test_assess_flags_magnitude_outlier_end_to_end(tmp_path):
    fetcher = _make_fetcher(tmp_path, workers=1)
    stock = StockData(ticker="BAD", cik="000", company_name="Bad Inc.")
    # Full required-field set so the only finding is the revenue outlier, not
    # unrelated missing-field findings.
    # Spike is in interior year 2022 (reverts in 2023) — the new spike-and-revert
    # logic only flags a value that is >=100x BOTH its chronological neighbours;
    # the latest year is never flagged (no future neighbour to confirm reversion).
    base = {"net_income": 1.0e8, "operating_income": 1.5e8, "total_assets": 2.0e9,
            "total_liabilities": 1.0e9, "total_equity": 1.0e9, "operating_cash_flow": 2.0e8}
    stock.financials_annual = {
        "2021": {"fiscal_year": 2021, "revenue": 1.0e9, **base},
        "2022": {"fiscal_year": 2022, "revenue": 1.2e12, **base},  # 1000x spike (interior)
        "2023": {"fiscal_year": 2023, "revenue": 1.1e9, **base},   # reverts
        "2024": {"fiscal_year": 2024, "revenue": 1.2e9, **base},
    }
    fetcher._clean_and_derive(stock)
    fetcher._compute_metrics(stock)
    fetcher._assess(stock)
    codes = [f["code"] for f in stock.data_quality["findings"]]
    assert "magnitude_outlier" in codes
    assert stock.data_quality["score"] < 100


def test_assess_clean_company_scores_100(tmp_path):
    fetcher = _make_fetcher(tmp_path, workers=1)
    stock = StockData(ticker="OK", cik="000", company_name="OK Inc.")
    stock.financials_annual = {
        str(y): {"fiscal_year": y, "revenue": 1.0e9 + (y - 2021) * 1.0e8,
                 "net_income": 1.0e8, "operating_income": 1.5e8,
                 "total_assets": 2.0e9, "total_liabilities": 1.0e9,
                 "total_equity": 1.0e9, "operating_cash_flow": 2.0e8}
        for y in (2021, 2022, 2023, 2024)
    }
    fetcher._clean_and_derive(stock)
    fetcher._compute_metrics(stock)
    fetcher._assess(stock)
    integrity_codes = {"magnitude_outlier", "cashflow_imbalance",
                       "quarterly_sum_mismatch", "ratio_out_of_bounds"}
    codes = {f["code"] for f in stock.data_quality["findings"]}
    assert not (codes & integrity_codes)   # no integrity findings on clean data
    assert stock.data_quality["score"] == 100


def test_benchmark_bars_exported_once_per_run(tmp_path, monkeypatch):
    """fetch_and_export calls fetch_benchmark_bars once and writes ^GSPC bars, no companies row."""
    fetcher = _make_fetcher(tmp_path, workers=1)

    def fake_fetch(ticker, include_yahoo=True, include_sec=True, years_back=10):
        return StockData(ticker=ticker)

    monkeypatch.setattr(fetcher, "fetch_ticker", fake_fetch)
    calls = []
    monkeypatch.setattr(
        fetcher.yahoo_handler, "fetch_benchmark_bars",
        lambda symbol="^GSPC": calls.append(symbol) or [
            {"date": "2024-01-02", "open": 1.0, "high": 2.0, "low": 0.5,
             "close": 1.5, "volume": 100.0},
        ],
    )

    fetcher.fetch_and_export(["AAA"], formats=["sqlite"])

    assert calls == ["^GSPC"]
    conn = sqlite3.connect(fetcher.config.storage.database_path)
    try:
        row = conn.execute(
            "SELECT date, close FROM price_bars WHERE ticker='^GSPC'"
        ).fetchone()
        assert row == ("2024-01-02", 1.5)
        companies = conn.execute(
            "SELECT COUNT(*) FROM companies WHERE ticker='^GSPC'"
        ).fetchone()[0]
        assert companies == 0
    finally:
        conn.close()


def test_benchmark_bars_skipped_without_sqlite_format(tmp_path, monkeypatch):
    """Benchmark bars are only fetched when sqlite is among the export formats."""
    fetcher = _make_fetcher(tmp_path, workers=1)

    def fake_fetch(ticker, include_yahoo=True, include_sec=True, years_back=10):
        return StockData(ticker=ticker)

    monkeypatch.setattr(fetcher, "fetch_ticker", fake_fetch)
    calls = []
    monkeypatch.setattr(
        fetcher.yahoo_handler, "fetch_benchmark_bars",
        lambda symbol="^GSPC": calls.append(symbol) or [],
    )

    fetcher.fetch_and_export(["AAA"], formats=["json"])

    assert calls == []


def test_benchmark_bars_skipped_when_yahoo_disabled(tmp_path, monkeypatch):
    """Benchmark bars are only fetched when include_yahoo=True."""
    fetcher = _make_fetcher(tmp_path, workers=1)

    def fake_fetch(ticker, include_yahoo=True, include_sec=True, years_back=10):
        return StockData(ticker=ticker)

    monkeypatch.setattr(fetcher, "fetch_ticker", fake_fetch)
    calls = []
    monkeypatch.setattr(
        fetcher.yahoo_handler, "fetch_benchmark_bars",
        lambda symbol="^GSPC": calls.append(symbol) or [],
    )

    fetcher.fetch_and_export(["AAA"], include_yahoo=False, formats=["sqlite"])

    assert calls == []


def test_benchmark_bars_failure_does_not_fail_run(tmp_path, monkeypatch):
    """A benchmark fetch failure must never fail the run."""
    fetcher = _make_fetcher(tmp_path, workers=1)

    def fake_fetch(ticker, include_yahoo=True, include_sec=True, years_back=10):
        return StockData(ticker=ticker)

    monkeypatch.setattr(fetcher, "fetch_ticker", fake_fetch)

    def boom(symbol="^GSPC"):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(fetcher.yahoo_handler, "fetch_benchmark_bars", boom)

    summary = fetcher.fetch_and_export(["AAA"], formats=["sqlite"])
    assert summary["successful"] == 1


def test_assess_quarterly_only_company_scores_zero(tmp_path):
    # No annual financials -> assess_annual returns score 0 (no_financials);
    # _assess must not let integrity scoring net that up to 75.
    fetcher = _make_fetcher(tmp_path, workers=1)
    stock = StockData(ticker="QO", cik="000", company_name="Q-Only Inc.")
    stock.financials_quarterly = {
        "2024-03-31": {"fiscal_year": 2024, "fiscal_quarter": 1, "revenue": 2.5e8},
    }
    fetcher._clean_and_derive(stock)
    fetcher._compute_metrics(stock)
    fetcher._assess(stock)
    assert stock.data_quality["score"] == 0

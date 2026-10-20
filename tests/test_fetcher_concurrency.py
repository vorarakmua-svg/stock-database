"""Tests that parallel fetch_multiple preserves order and matches sequential."""

import sqlite3
import time

import pytest

from src.config import AppConfig, StorageConfig
from src.fetchers.stock_data_fetcher import StockDataFetcher
from src.models.stock_data import StockData
from tests.test_xbrl_parser import _split_restated_facts


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


def test_assess_flags_dividend_payments_missing_from_yahoo(tmp_path):
    # CB: Yahoo's history has no payment between 2025-06-13 and 2026-03-13, while its SEC
    # filings declare a full year of dividends for FY2025.
    fetcher = _make_fetcher(tmp_path, workers=1)
    stock = StockData(ticker="CB", cik="000", company_name="CB Inc.")
    stock.financials_annual = {
        str(y): {"fiscal_year": y, "period_end": f"{y}-12-31", "dividends_per_share": 3.88,
                 "revenue": 1.0e9, "net_income": 1.0e8, "operating_income": 1.5e8,
                 "total_assets": 2.0e9, "total_liabilities": 1.0e9, "total_equity": 1.0e9,
                 "operating_cash_flow": 2.0e8}
        for y in (2023, 2024, 2025)
    }
    stock.dividend_history = {"dividend_payments": [
        {"date": f"{y}-{m}-13", "amount": 0.97}
        for y in range(2021, 2027) for m in ("03", "06", "09", "12")
        if not ("2025-09" <= f"{y}-{m}" <= "2025-12" or f"{y}-{m}" > "2026-06")
    ]}
    fetcher._assess(stock)
    findings = [f for f in stock.data_quality["findings"] if f["code"] == "dividend_payments_missing"]
    assert [f["period"] for f in findings] == ["2025"]
    assert any("dividend" in w for w in stock.warnings)


def test_a_failed_summary_csv_still_writes_the_other_exports(tmp_path, monkeypatch):
    # Windows refuses to replace summary.csv while Excel has it open, so its export returns None. The
    # history file's path was then appended to a list that didn't exist (KeyError: 'csv'), and the run
    # stopped before the SQLite export and the valuations.
    fetcher = _make_fetcher(tmp_path, workers=1)
    monkeypatch.setattr(fetcher.csv_exporter, "export", lambda data, filename="summary.csv": None)
    stock = StockData(ticker="AAA", cik="000", company_name="A Inc.")
    stock.financials_annual = {"2024": {"fiscal_year": 2024, "revenue": 1.0e9}}
    results = fetcher.export([stock], formats=["csv", "sqlite"])
    assert [p.name for p in results["csv"]] == ["financial_history.csv"]
    assert results["sqlite"][0].exists()


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


def _sec_only_fetcher(tmp_path, monkeypatch, sec):
    fetcher = _make_fetcher(tmp_path, 1)
    monkeypatch.setattr(fetcher.sec_handler, "fetch_all", lambda ticker: sec)
    monkeypatch.setattr(fetcher.sec_handler, "get_insider_transactions", lambda cik, limit=50: [])
    monkeypatch.setattr(fetcher.fred_handler, "get_risk_free_rate_details", lambda maturity="10y": None)
    monkeypatch.setattr(fetcher.fred_handler, "get_market_risk_premium", lambda: {})
    return fetcher


def _collect_from_sec(tmp_path, monkeypatch, sec):
    return _sec_only_fetcher(tmp_path, monkeypatch, sec).fetch_ticker(sec["ticker"], include_yahoo=False)


def test_a_filer_without_annual_financials_is_explained(tmp_path, monkeypatch):
    # SEC moved XOM to ExxonMobil Holdings Corp (CIK 2115436), which has filed 10-Qs but no 10-K yet; the run
    # recorded no annual financials and only a generic data-quality warning.
    entry = {"val": 1.0e9, "start": "2026-04-01", "end": "2026-06-30", "fy": 2026, "fp": "Q2", "form": "10-Q",
             "filed": "2026-08-01"}
    stock = _collect_from_sec(tmp_path, monkeypatch, {
        "cik": "0002115436", "ticker": "XOM", "source": "sec_edgar",
        "company_facts": {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [entry]}}}}},
        "submissions": {"name": "ExxonMobil Holdings Corp",
                        "filings": [{"form": "10-Q", "filingDate": "2026-08-01"},
                                    {"form": "8-K", "filingDate": "2026-07-01"}]}})
    assert stock.financials_annual == {}
    assert ("SEC EDGAR: no annual financials for XOM from CIK 0002115436 (ExxonMobil Holdings Corp): it has filed "
            "no annual report (10-K) yet (its filings listed from 2026-07-01)") in stock.warnings


def test_a_failed_facts_request_is_not_explained_as_missing_data(tmp_path, monkeypatch):
    # A 403 or a timeout on companyfacts also leaves the record empty: the reason is the request, not the filer.
    stock = _collect_from_sec(tmp_path, monkeypatch, {
        "cik": "0000034088", "ticker": "XOM", "source": "sec_edgar",
        "company_facts_error": "403 Client Error: Forbidden",
        "submissions": {"name": "EXXON MOBIL CORP", "filings": []}})
    assert ("SEC EDGAR: no annual financials for XOM from CIK 0000034088 (EXXON MOBIL CORP): its companyfacts "
            "request failed (403 Client Error: Forbidden)") in stock.warnings


@pytest.mark.parametrize("part, endpoint", [("company_facts", "companyfacts"), ("submissions", "submissions")])
def test_a_failed_sec_request_is_an_error(tmp_path, monkeypatch, part, endpoint):
    # SECHandler keeps a failed request's message (a 403, a 5xx, a timeout), but nothing read it, so the ticker's
    # empty record counted as "Successfully fetched".
    stock = _collect_from_sec(tmp_path, monkeypatch, {"cik": "0000034088", "ticker": "XOM", "source": "sec_edgar",
                                                      f"{part}_error": "403 Client Error: Forbidden"})
    assert f"SEC EDGAR {endpoint}: 403 Client Error: Forbidden" in stock.errors


def test_the_run_summary_counts_a_failed_sec_request_as_an_error(tmp_path, monkeypatch):
    fetcher = _sec_only_fetcher(tmp_path, monkeypatch, {"cik": "0000034088", "ticker": "XOM", "source": "sec_edgar",
                                                        "submissions_error": "Read timed out. (read timeout=30)"})
    summary = fetcher.fetch_and_export(["XOM"], include_yahoo=False, formats=["json"])
    assert (summary["successful"], summary["with_errors"]) == (0, 1)


def _reit_fy2024_facts():
    def fy(val, instant=False):
        entry = {"val": val, "end": "2024-12-31", "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-02-20"}
        if not instant:
            entry["start"] = "2024-01-01"
        return {"units": {"USD": [entry]}}

    return {"facts": {"us-gaap": {
        "Revenues": fy(500.0e6), "NetIncomeLoss": fy(100.0e6), "DepreciationDepletionAndAmortization": fy(40.0e6),
        "PaymentsToAcquirePropertyPlantAndEquipment": fy(10.0e6), "Assets": fy(2000.0e6, instant=True),
        "StockholdersEquity": fy(800.0e6, instant=True)}}}


def test_a_failed_submissions_request_keeps_the_stored_sector(tmp_path, monkeypatch):
    # Code review 6 (I2): the sector comes only from SEC's submissions data, so when that request failed the run
    # recomputed a REIT's (or a bank's, an insurer's) metrics as a general company's and overwrote every stored
    # year's FFO, net interest margin or combined ratio.
    fetcher = _sec_only_fetcher(tmp_path, monkeypatch, {
        "cik": "0001045609", "ticker": "RIT", "source": "sec_edgar", "company_facts": _reit_fy2024_facts(),
        "submissions_error": "Read timed out. (read timeout=30)"})
    fetcher.sqlite_store.export([StockData(ticker="BNK", cik="0000019617", sector_class="bank"),
                                 StockData(ticker="RIT", cik="0001045609", sector_class="reit")])
    stock = fetcher.fetch_ticker("RIT", include_yahoo=False)
    assert stock.sector_class == "reit"
    assert stock.calculated_metrics["historical"]["2024"]["ffo"] == 140.0e6


def test_a_json_only_store_also_keeps_the_sector(tmp_path, monkeypatch):
    # The same failure without a database (a --formats json run): the earlier JSON export knows the sector.
    fetcher = _sec_only_fetcher(tmp_path, monkeypatch, {
        "cik": "0001045609", "ticker": "RIT", "source": "sec_edgar", "company_facts": _reit_fy2024_facts(),
        "submissions_error": "Read timed out. (read timeout=30)"})
    fetcher.json_exporter.export([StockData(ticker="RIT", cik="0001045609", sector_class="reit")])
    assert not fetcher.sqlite_store.db_path.exists()
    stock = fetcher.fetch_ticker("RIT", include_yahoo=False)
    assert stock.sector_class == "reit"


def test_a_tagging_error_put_right_is_a_warning(tmp_path, monkeypatch):
    def fy(val, year, filed):
        return {"val": val, "start": f"{year}-01-01", "end": f"{year}-12-31", "fy": year, "fp": "FY", "form": "10-K",
                "filed": filed, "accn": filed}

    dps = [fy(2.68, 2021, "2021-08-06"), fy(2.77, 2022, "2022-08-05"), fy(2.77, 2022, "2023-08-04"),
           fy(2770000.0, 2022, "2024-08-02"), fy(2.80, 2023, "2024-08-02")]
    stock = _collect_from_sec(tmp_path, monkeypatch, {
        "cik": "0001137789", "ticker": "STX", "source": "sec_edgar",
        "company_facts": {"facts": {"us-gaap": {
            "CommonStockDividendsPerShareDeclared": {"units": {"USD/shares": dps}},
            "Revenues": {"units": {"USD": [fy(1.0e10, y, f"{y + 1}-08-01") for y in (2021, 2022, 2023)]}}}}},
        "submissions": {"name": "Seagate Technology Holdings plc", "filings": []}})
    assert stock.financials_annual["2022"]["dividends_per_share"] == 2.77
    assert ("SEC EDGAR: STX FY2022 dividends_per_share is 2.77, as earlier filings report; the filing of "
            "2024-08-02 reports 2.77e+06, which looks like a tagging error (scale x10^6)") in stock.warnings


@pytest.mark.parametrize("folder", ["data#2", "pct%41"])
def test_the_stored_sector_is_found_under_any_folder_name(tmp_path, monkeypatch, folder):
    # BUG-080: the stored-sector fallback's read-only URI wasn't escaped either, so under such a folder it found none.
    fetcher = _sec_only_fetcher(tmp_path / folder, monkeypatch, {
        "cik": "0001045609", "ticker": "RIT", "source": "sec_edgar", "company_facts": _reit_fy2024_facts(),
        "submissions_error": "Read timed out. (read timeout=30)"})
    fetcher.sqlite_store.export([StockData(ticker="RIT", cik="0001045609", sector_class="reit")])
    assert fetcher.fetch_ticker("RIT", include_yahoo=False).sector_class == "reit"


def test_a_record_that_stops_where_ifrs_reports_begin_is_a_warning(tmp_path, monkeypatch):
    # BUG-066: the record was kept, and valued, as if current.
    from tests.test_xbrl_parser import _switcher_facts

    stock = _collect_from_sec(tmp_path, monkeypatch, {
        "cik": "0001046179", "ticker": "TSM", "source": "sec_edgar", "company_facts": _switcher_facts(),
        "submissions": {"name": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "filings": []}})
    assert sorted(stock.financials_annual) == ["2015", "2016", "2017", "2018", "2019"]
    assert ("SEC EDGAR: annual financials stop at FY2019 for TSM: later annual reports are in IFRS (20-F), which "
            "isn't mapped") in stock.warnings


def test_a_collected_quarterly_record_and_its_trailing_years_sit_on_one_split_basis(tmp_path, monkeypatch):
    # BUG-082 through a collection: the quarters take their year's basis, a trailing year its latest quarter's, and
    # the bookkeeping that carries each quarter's basis to the trailing years never reaches the stored record.
    stock = _collect_from_sec(tmp_path, monkeypatch, {
        "cik": "0001018724", "ticker": "AMZN", "source": "sec_edgar", "company_facts": _split_restated_facts()})
    assert stock.financials_quarterly["2020-12-31"]["eps_diluted"] == pytest.approx(0.704)
    # Q2-Q4 2019 on FY2019's basis, a twentieth of it on Q1 2020's, beside Q1 2020's 0.2505
    assert stock.financials_ttm["2020-03-31"]["eps_diluted"] == pytest.approx((5.31 + 4.03 + 6.58) / 20 + 0.2505)
    assert stock.financials_ttm["2019-12-31"]["eps_diluted"] == pytest.approx(23.01)
    assert not [end for end, period in stock.financials_quarterly.items() if "_split_levels" in period]


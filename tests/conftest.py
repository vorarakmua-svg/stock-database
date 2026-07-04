"""Shared pytest fixtures.

No fixture here performs network I/O — all SEC/Yahoo/FRED data is synthetic so the
test suite is fast and deterministic.
"""

import pytest

from src.models.stock_data import StockData


def usd(val, start=None, end=None, fy=None, fp="FY", form="10-K", filed=None, frame=None):
    """Build a single XBRL USD fact entry (the shape SEC companyfacts uses)."""
    entry = {"val": val, "fy": fy, "fp": fp, "form": form, "filed": filed, "accn": filed}
    if start is not None:
        entry["start"] = start
    if end is not None:
        entry["end"] = end
    if frame is not None:
        entry["frame"] = frame
    return entry


def _synthetic_bars(n, start="2023-01-01", base=100.0):
    """Deterministic synthetic daily OHLCV walk — no randomness, so bar-based tests
    (moving averages, ordering, counts) are fully reproducible across runs.
    """
    from datetime import date, timedelta

    start_date = date.fromisoformat(start)
    bars = []
    for i in range(n):
        price = base + i * 0.1 + (i % 5) * 0.05
        d = (start_date + timedelta(days=i)).isoformat()
        bars.append({
            "date": d,
            "open": round(price - 0.2, 4),
            "high": round(price + 0.5, 4),
            "low": round(price - 0.5, 4),
            "close": round(price, 4),
            "volume": float(1_000_000 + i * 500),
        })
    return bars


@pytest.fixture
def sample_company_facts():
    """A synthetic SEC ``companyfacts`` payload.

    Reproduces the real-world quirk that drives the A1 fix: a single 10-K reports
    several comparative fiscal years, and every fact carries that *filing's* ``fy``
    (not the period's). Here ``Revenues`` and ``Assets`` each have entries tagged
    ``fy=2024`` for the 2022, 2023 AND 2024 periods. Correct extraction must bucket
    by the period (``end``), not by ``fy``.

    Also includes a quarterly-length span inside the 10-K that the duration filter
    must reject from annual results.
    """
    return {
        "cik": 320193,
        "entityName": "Test Co.",
        "facts": {
            "us-gaap": {
                # Duration concept (income statement): full-year spans + one quarter
                "Revenues": {
                    "label": "Revenues",
                    "description": "Total revenue",
                    "units": {
                        "USD": [
                            # Original filings
                            usd(200, "2021-09-26", "2022-09-24", 2022, filed="2022-10-28", frame="CY2022"),
                            usd(300, "2022-09-25", "2023-09-30", 2023, filed="2023-11-03", frame="CY2023"),
                            # Comparatives carried in the FY2024 10-K (all tagged fy=2024).
                            # Ordered oldest-first to expose any "first value wins" bug.
                            usd(200, "2021-09-26", "2022-09-24", 2024, filed="2024-11-01"),
                            usd(300, "2022-09-25", "2023-09-30", 2024, filed="2024-11-01"),
                            usd(400, "2023-10-01", "2024-09-28", 2024, filed="2024-11-01", frame="CY2024"),
                            # A single quarter reported in the 10-K (must be excluded from annual)
                            usd(120, "2024-06-30", "2024-09-28", 2024, fp="Q4", filed="2024-11-01"),
                        ]
                    },
                },
                # Instant concept (balance sheet): no "start"
                "Assets": {
                    "label": "Assets",
                    "description": "Total assets",
                    "units": {
                        "USD": [
                            usd(50, end="2022-09-24", fy=2022, filed="2022-10-28"),
                            usd(60, end="2023-09-30", fy=2023, filed="2023-11-03"),
                            usd(70, end="2024-09-28", fy=2024, filed="2024-11-01"),
                            # Comparatives in FY2024 10-K
                            usd(50, end="2022-09-24", fy=2024, filed="2024-11-01"),
                            usd(60, end="2023-09-30", fy=2024, filed="2024-11-01"),
                        ]
                    },
                },
                # Quarterly data for extract_quarterly_financials
                "NetIncomeLoss": {
                    "label": "Net income",
                    "description": "Net income (loss)",
                    "units": {
                        "USD": [
                            usd(25, "2024-03-31", "2024-06-29", 2024, fp="Q3", form="10-Q", filed="2024-08-01"),
                            usd(30, "2024-06-30", "2024-09-28", 2024, fp="Q4", form="10-Q", filed="2024-11-01"),
                            # A full-year span inside a 10-Q (rare, but exercise the filter)
                            usd(400, "2023-10-01", "2024-09-28", 2024, fp="FY", form="10-Q", filed="2024-11-01"),
                        ]
                    },
                },
            }
        },
    }


@pytest.fixture
def sample_financials():
    """A single fiscal year's financials, keyed by canonical field names.

    Shaped the way ``CalculatedMetrics`` and ``StockData.to_summary`` consume it
    after standardization (see ``src/mappings/canonical.py``).
    """
    return {
        "fiscal_year": 2024,
        "revenue": 1000.0,
        "net_income": 150.0,
        "operating_income": 200.0,
        "gross_profit": 400.0,
        "operating_cash_flow": 250.0,
        "capex": 50.0,
        "depreciation_amortization": 40.0,
        "total_assets": 2000.0,
        "total_equity": 800.0,
        "long_term_debt": 300.0,
        "short_term_debt": 100.0,
        "cash_and_equivalents": 120.0,
        "interest_expense": 20.0,
        "income_tax_expense": 50.0,
        "pretax_income": 200.0,
        "current_assets": 600.0,
        "current_liabilities": 300.0,
        "accounts_receivable": 100.0,
        "inventory": 80.0,
        "cost_of_revenue": 600.0,
    }


@pytest.fixture
def sample_stock_data(sample_financials):
    """A populated StockData object for model/exporter tests."""
    stock = StockData(ticker="TEST", cik="0000320193", company_name="Test Co.")
    stock.market_data = {"current_price": 100.0, "market_cap": 5000.0, "beta": 1.1}
    stock.valuation = {"pe_trailing": 20.0, "ebitda": 240.0}
    stock.financials_annual = {"2024": sample_financials}
    stock.add_source("sec_edgar")
    return stock


# ---------------------------------------------------------------------------
# Shared web-layer fixtures (Task 2+)
# ---------------------------------------------------------------------------

@pytest.fixture
def web_db(tmp_path):
    """Minimal in-process SQLite database for web-layer tests.

    Three companies across sectors:
    - AAA (general): 3 fiscal years of financials + metrics, 2 quarterly periods,
      1 TTM period, multi-vintage FY2022 (original + restatement), 2 snapshots
      (latest has dividend_yield=0.02, short_percent_of_float=0.03 — for
      screener market/valuation-column tests), 1 unmapped fact, 260 daily price
      bars, 1 analyst snapshot, 4 earnings-surprise rows, 6 dividend events + 1
      split, 3 institutional + 2 mutualfund holders, 3 insider transactions,
      2 officers, and a description/address.
    - BBB (bank): 1 fiscal year, 1 snapshot (pe_trailing=10.0, price_to_book=1.2;
      no dividend_yield/short_percent_of_float — NULL for those columns).
    - CCC (reit): 1 fiscal year, 1 snapshot (pe_trailing=25.0, price_to_book=1.5;
      no dividend_yield/short_percent_of_float — NULL for those columns).
    - ``^GSPC`` benchmark: 30 daily price bars (via ``export_benchmark_bars``,
      never a ``companies`` row).

    All data is synthetic; no network I/O.
    """
    from datetime import datetime

    from src.exporters.sqlite_store import SQLiteStore

    db_path = tmp_path / "stock.db"
    store = SQLiteStore(db_path)

    _info_aaa = {
        "sector": "Technology",
        "industry": "Software",
        "country": "US",
        "full_time_employees": 1000,
        "website": "https://aaa.com",
        "description": "AAA Corp designs and sells enterprise software products.",
        "address": "1 Market Street",
        "city": "San Francisco",
        "state": "CA",
        "officers": [
            {"name": "Jane Doe", "title": "CEO", "age": 50, "total_pay": 5_000_000.0},
            {"name": "John Smith", "title": "CFO", "age": 45, "total_pay": 3_000_000.0},
        ],
    }

    # --- AAA first snapshot (2024-01-15): full data incl. financials/vintages ---
    aaa1 = StockData(
        ticker="AAA", cik="0000000001", company_name="AAA Corp",
        sector_class="general", collected_at=datetime(2024, 1, 15),
    )
    aaa1.company_info = _info_aaa
    aaa1.market_data = {"current_price": 95.0, "market_cap": 4750.0, "beta": 1.1}
    aaa1.valuation = {
        "pe_trailing": 19.0, "pe_forward": 17.0, "eps_trailing": 5.0,
        "price_to_book": 2.8, "dividend_yield": 0.02,
    }
    aaa1.financials_annual = {
        "2022": {
            "fiscal_year": 2022, "period_end": "2022-12-31",
            "filed_date": "2023-02-10", "calendar_year": 2022,
            "revenue": 800.0, "net_income": 80.0,
            "total_assets": 1600.0, "total_liabilities": 800.0, "total_equity": 800.0,
            "operating_cash_flow": 120.0, "capex": 40.0,
        },
        "2023": {
            "fiscal_year": 2023, "period_end": "2023-12-31",
            "filed_date": "2024-02-10", "calendar_year": 2023,
            "revenue": 900.0, "net_income": 90.0,
            "total_assets": 1800.0, "total_liabilities": 900.0, "total_equity": 900.0,
            "operating_cash_flow": 135.0, "capex": 45.0,
        },
        "2024": {
            "fiscal_year": 2024, "period_end": "2024-12-31",
            "filed_date": "2025-02-10", "calendar_year": 2024,
            "revenue": 1000.0, "net_income": 100.0,
            "total_assets": 2000.0, "total_liabilities": 1000.0, "total_equity": 1000.0,
            "operating_cash_flow": 150.0, "capex": 50.0,
        },
    }
    aaa1.calculated_metrics = {
        "historical": {
            "2022": {"roic": 0.10, "net_margin": 0.10, "gross_margin": 0.40},
            "2023": {"roic": 0.12, "net_margin": 0.10, "gross_margin": 0.42},
            "2024": {"roic": 0.15, "net_margin": 0.10, "gross_margin": 0.45},
        }
    }
    aaa1.financials_quarterly = {
        "2024-03-31": {
            "period_end": "2024-03-31", "fiscal_year": 2024,
            "fiscal_quarter": 1, "calendar_year": 2024, "calendar_quarter": 1,
            "revenue": 220.0, "net_income": 22.0,
        },
        "2024-06-30": {
            "period_end": "2024-06-30", "fiscal_year": 2024,
            "fiscal_quarter": 2, "calendar_year": 2024, "calendar_quarter": 2,
            "revenue": 240.0, "net_income": 24.0,
        },
    }
    aaa1.financials_ttm = {
        "2024-06-30": {
            "period_end": "2024-06-30", "fiscal_year": 2024,
            "calendar_year": 2024, "calendar_quarter": 2,
            "revenue": 950.0, "net_income": 95.0,
        },
    }
    # Multi-vintage FY2022: original filing + later restatement
    aaa1.financials_annual_vintages = {
        "2022": {
            "0001-10K-2022": {
                "fiscal_year": 2022, "accn": "0001-10K-2022",
                "filed_date": "2023-02-10", "period_end": "2022-12-31",
                "form": "10-K", "calendar_year": 2022,
                "revenue": 800.0, "net_income": 80.0,
            },
            "0002-10KA-2022": {
                "fiscal_year": 2022, "accn": "0002-10KA-2022",
                "filed_date": "2023-08-15", "period_end": "2022-12-31",
                "form": "10-K/A", "calendar_year": 2022,
                "revenue": 800.0, "net_income": 82.0,
            },
        }
    }
    aaa1.unmapped_facts = [
        {
            "tag": "SomeCustomTag", "label": "Custom Label",
            "unit": "USD", "period_end": "2024-12-31",
            "value": 42.0, "form": "10-K",
        }
    ]
    # Daily bars: >=260 rows for AAA (enough for MA200 warmup in later tasks).
    aaa1.price_bars = _synthetic_bars(260, start="2023-01-01", base=100.0)
    # Earnings-surprise history: 4 quarters, mixed beats and misses.
    # Insertion order deliberately NOT pre-sorted, to exercise Reader-side ordering.
    aaa1.earnings_history = [
        {"quarter": "2023-12-31", "eps_estimate": 1.15, "eps_actual": 1.05, "surprise_pct": -8.7},
        {"quarter": "2023-09-30", "eps_estimate": 1.10, "eps_actual": 1.20, "surprise_pct": 9.1},
        {"quarter": "2023-06-30", "eps_estimate": 1.05, "eps_actual": 0.95, "surprise_pct": -9.5},
        {"quarter": "2023-03-31", "eps_estimate": 1.00, "eps_actual": 1.10, "surprise_pct": 10.0},
    ]
    # Dividend history: 6 payments + 1 split.
    # Insertion order deliberately NOT pre-sorted, to exercise Reader-side ordering.
    aaa1.dividend_history = {
        "dividend_payments": [
            {"date": "2024-05-15", "amount": 0.25},
            {"date": "2024-02-15", "amount": 0.25},
            {"date": "2023-11-15", "amount": 0.22},
            {"date": "2023-08-15", "amount": 0.22},
            {"date": "2023-05-15", "amount": 0.20},
            {"date": "2023-02-15", "amount": 0.20},
        ],
    }
    aaa1.splits = [{"date": "2023-06-01", "ratio": 2.0}]
    aaa1.add_source("sec_edgar")

    # --- AAA second snapshot (2024-06-15): updated price, no new financials ---
    aaa2 = StockData(
        ticker="AAA", cik="0000000001", company_name="AAA Corp",
        sector_class="general", collected_at=datetime(2024, 6, 15),
    )
    aaa2.company_info = _info_aaa
    aaa2.market_data = {
        "current_price": 105.0, "market_cap": 5250.0, "beta": 1.15,
        "previous_close": 100.0,
    }
    aaa2.valuation = {
        "pe_trailing": 21.0, "pe_forward": 18.5, "eps_trailing": 5.0,
        "price_to_book": 3.1, "dividend_yield": 0.02,
        "short_percent_of_float": 0.03,
    }
    # Analyst snapshot: one row, attached to the later (2024-06-15) collection.
    aaa2.analyst_estimates = {
        "target_price_low": 90.0, "target_price_mean": 115.0, "target_price_median": 112.0,
        "target_price_high": 140.0, "recommendation": "buy", "recommendation_mean": 2.1,
        "number_of_analysts": 12, "earnings_date": "2024-08-01", "forward_eps": 5.5,
        "forward_pe": 19.0, "earnings_growth": 0.12, "revenue_growth": 0.09,
        "upside_potential": 0.10,
    }
    # Shareholders: 3 institutional + 2 mutualfund holders, 3 insider transactions.
    # Set on aaa2 (not aaa1): SQLiteStore._write_holders deletes-then-inserts per call,
    # and aaa2 is processed AFTER aaa1 for the same ticker, so holders set on aaa1
    # would otherwise be wiped out by aaa2's (empty) shareholders dict.
    # Insertion order deliberately NOT pre-sorted, to exercise Reader-side ordering.
    aaa2.shareholders = {
        "institutional_holders": [
            {"Holder": "BlackRock Inc", "Shares": 4_500_000.0, "Date Reported": "2024-01-01",
             "pctHeld": 0.07, "Value": 450_000_000.0},
            {"Holder": "Vanguard Group", "Shares": 5_000_000.0, "Date Reported": "2024-01-01",
             "pctHeld": 0.08, "Value": 500_000_000.0},
            {"Holder": "State Street Corp", "Shares": 3_000_000.0, "Date Reported": "2024-01-01",
             "pctHeld": 0.05, "Value": 300_000_000.0},
        ],
        "mutualfund_holders": [
            {"Holder": "Fidelity Contrafund", "Shares": 1_000_000.0, "Date Reported": "2024-01-01",
             "pctHeld": 0.02, "Value": 100_000_000.0},
            {"Holder": "American Funds Growth", "Shares": 800_000.0, "Date Reported": "2024-01-01",
             "pctHeld": 0.015, "Value": 80_000_000.0},
        ],
        "insider_transactions": [
            {"Insider": "Jane Doe", "Position": "CEO", "Start Date": "2024-03-01",
             "Shares": 10_000.0, "Value": 1_050_000.0, "Text": "Sale", "Ownership": "D"},
            {"Insider": "John Smith", "Position": "CFO", "Start Date": "2024-02-15",
             "Shares": 5_000.0, "Value": 525_000.0, "Text": "Sale", "Ownership": "D"},
            {"Insider": "Alice Wu", "Position": "Director", "Start Date": "2024-04-01",
             "Shares": 2_000.0, "Value": 210_000.0, "Text": "Purchase", "Ownership": "D"},
        ],
    }
    aaa2.add_source("sec_edgar")

    # --- BBB: bank ---
    bbb = StockData(
        ticker="BBB", cik="0000000002", company_name="BBB Bank",
        sector_class="bank", collected_at=datetime(2024, 1, 15),
    )
    bbb.company_info = {"sector": "Finance", "industry": "Banking", "country": "US"}
    bbb.market_data = {"current_price": 50.0, "market_cap": 2500.0, "beta": 0.8}
    bbb.valuation = {"pe_trailing": 10.0, "price_to_book": 1.2}
    bbb.financials_annual = {
        "2023": {
            "fiscal_year": 2023, "period_end": "2023-12-31",
            "calendar_year": 2023,
            "revenue": 500.0, "net_income": 100.0, "total_assets": 10000.0,
        }
    }
    bbb.add_source("sec_edgar")

    # --- CCC: reit ---
    ccc = StockData(
        ticker="CCC", cik="0000000003", company_name="CCC Realty",
        sector_class="reit", collected_at=datetime(2024, 1, 15),
    )
    ccc.company_info = {"sector": "Real Estate", "industry": "REITs", "country": "US"}
    ccc.market_data = {"current_price": 30.0, "market_cap": 1500.0, "beta": 0.6}
    ccc.valuation = {"pe_trailing": 25.0, "price_to_book": 1.5}
    ccc.financials_annual = {
        "2023": {
            "fiscal_year": 2023, "period_end": "2023-12-31",
            "calendar_year": 2023,
            "revenue": 200.0, "net_income": 40.0, "total_assets": 2000.0,
        }
    }
    ccc.add_source("sec_edgar")

    # --- EEE: general sector, metrics but NO snapshot (no market_data/valuation) ---
    # Fixture to guard the LEFT JOIN regression: EEE gets >=1 metrics_annual row
    # but NO market_snapshots row (market_data & valuation both empty). This ensures
    # unfiltered screens include EEE with NULL snapshot columns, and snapshot filters
    # exclude it via NULL comparisons.
    eee = StockData(
        ticker="EEE", cik="0000000004", company_name="EEE Corp",
        sector_class="general", collected_at=datetime(2024, 1, 15),
    )
    eee.company_info = {"sector": "Technology", "industry": "Software", "country": "US"}
    # Deliberately NO market_data/valuation: snapshot will NOT be written.
    eee.financials_annual = {
        "2023": {
            "fiscal_year": 2023, "period_end": "2023-12-31",
            "filed_date": "2024-02-10", "calendar_year": 2023,
            "revenue": 600.0, "net_income": 60.0,
            "total_assets": 1200.0, "total_liabilities": 600.0, "total_equity": 600.0,
            "operating_cash_flow": 90.0, "capex": 30.0,
        }
    }
    # roic=0.08 (below the 0.13 threshold used in existing tests)
    eee.calculated_metrics = {
        "historical": {
            "2023": {"roic": 0.08, "net_margin": 0.10, "gross_margin": 0.40},
        }
    }
    eee.add_source("sec_edgar")

    store.export([aaa1, aaa2, bbb, ccc, eee])
    # ^GSPC benchmark bars: 30 rows, never a companies row (see export_benchmark_bars).
    store.export_benchmark_bars("^GSPC", _synthetic_bars(30, start="2024-01-01", base=4700.0))
    return db_path


@pytest.fixture
def client(web_db):
    """TestClient for the FastAPI web app backed by the shared web_db fixture."""
    from fastapi.testclient import TestClient

    from src.webapp import create_app

    return TestClient(create_app(db_path=web_db))

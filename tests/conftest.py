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
      1 TTM period, multi-vintage FY2022 (original + restatement), 2 snapshots,
      and 1 unmapped fact.
    - BBB (bank): 1 fiscal year, 1 snapshot.
    - CCC (reit): 1 fiscal year, 1 snapshot.

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
    aaa1.add_source("sec_edgar")

    # --- AAA second snapshot (2024-06-15): updated price, no new financials ---
    aaa2 = StockData(
        ticker="AAA", cik="0000000001", company_name="AAA Corp",
        sector_class="general", collected_at=datetime(2024, 6, 15),
    )
    aaa2.company_info = _info_aaa
    aaa2.market_data = {"current_price": 105.0, "market_cap": 5250.0, "beta": 1.15}
    aaa2.valuation = {
        "pe_trailing": 21.0, "pe_forward": 18.5, "eps_trailing": 5.0,
        "price_to_book": 3.1, "dividend_yield": 0.02,
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

    store.export([aaa1, aaa2, bbb, ccc])
    return db_path


@pytest.fixture
def client(web_db):
    """TestClient for the FastAPI web app backed by the shared web_db fixture."""
    from fastapi.testclient import TestClient

    from src.webapp import create_app

    return TestClient(create_app(db_path=web_db))

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
    """A single fiscal year's financials, keyed by the parser's simple names.

    Shaped the way ``CalculatedMetrics`` and ``StockData.to_summary`` consume it.
    """
    return {
        "fiscal_year": 2024,
        "Revenue": 1000.0,
        "Net Income": 150.0,
        "Operating Income": 200.0,
        "Gross Profit": 400.0,
        "Operating Cash Flow": 250.0,
        "Capital Expenditures": 50.0,
        "Depreciation and Amortization": 40.0,
        "Total Assets": 2000.0,
        "Total Stockholders Equity": 800.0,
        "Long-Term Debt": 300.0,
        "Short-Term Debt": 100.0,
        "Cash and Cash Equivalents": 120.0,
        "Interest Expense": 20.0,
        "Income Tax Expense": 50.0,
        "Pre-Tax Income": 200.0,
        "Current Assets": 600.0,
        "Current Liabilities": 300.0,
        "Accounts Receivable": 100.0,
        "Inventory": 80.0,
        "Cost of Goods Sold": 600.0,
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

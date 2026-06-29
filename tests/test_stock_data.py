"""Tests for the StockData model: serialization, round-trips, summary mapping."""

from datetime import datetime

from src.models.stock_data import StockData


def test_to_dict_serializes_datetime(sample_stock_data):
    d = sample_stock_data.to_dict()
    assert isinstance(d["collected_at"], str)
    # ISO string is parseable back to a datetime
    datetime.fromisoformat(d["collected_at"])


def test_from_dict_round_trip(sample_stock_data):
    d = sample_stock_data.to_dict()
    restored = StockData.from_dict(d)
    assert restored.ticker == "TEST"
    assert restored.cik == "0000320193"
    assert isinstance(restored.collected_at, datetime)


def test_from_dict_tolerates_exporter_keys(sample_stock_data):
    """Merged JSON carries bookkeeping keys that aren't dataclass fields (A5)."""
    d = sample_stock_data.to_dict()
    d["collection_history"] = [{"collected_at": "2024-01-01"}]
    d["first_collected_at"] = "2023-01-01"
    d["price_history_snapshots"] = [{"cagr": 0.1}]

    restored = StockData.from_dict(d)  # must not raise
    assert restored.ticker == "TEST"


def test_to_summary_maps_sec_financials(sample_stock_data):
    summary = sample_stock_data.to_summary()
    assert summary["ticker"] == "TEST"
    assert summary["sec_revenue"] == 1000.0
    assert summary["sec_net_income"] == 150.0
    assert summary["sec_total_assets"] == 2000.0
    assert summary["data_sources"] == "sec_edgar"


def test_merge_yahoo_records_error():
    stock = StockData(ticker="TEST")
    stock.merge_yahoo_data({"error": "boom"})
    assert any("boom" in e for e in stock.errors)
    assert "yahoo_finance" not in stock.data_sources


def test_add_source_deduplicates():
    stock = StockData(ticker="TEST")
    stock.add_source("sec_edgar")
    stock.add_source("sec_edgar")
    assert stock.data_sources == ["sec_edgar"]


def test_financials_annual_vintages_defaults_empty():
    from src.models.stock_data import StockData
    s = StockData(ticker="T", cik="1", company_name="Test")
    assert s.financials_annual_vintages == {}


def test_vintages_excluded_from_to_dict():
    from src.models.stock_data import StockData
    s = StockData(ticker="T", cik="1", company_name="Test")
    s.financials_annual_vintages = {"2022": {"acc-1": {"revenue": 100}}}
    assert "financials_annual_vintages" not in s.to_dict()


def test_from_dict_keeps_vintages_field_when_present():
    # financials_annual_vintages is a declared dataclass field, so from_dict keeps it
    # when present. (The JSON exporter never WRITES the key — see the to_dict test —
    # so in practice JSON has no vintages; this just pins round-trip behavior.)
    from src.models.stock_data import StockData
    s = StockData.from_dict({"ticker": "T", "cik": "1", "company_name": "Test",
                             "financials_annual_vintages": {"2022": {"a": {"revenue": 1}}}})
    assert s.financials_annual_vintages == {"2022": {"a": {"revenue": 1}}}

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

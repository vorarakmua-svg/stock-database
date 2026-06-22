"""Tests for JSONExporter — merge semantics and history bookkeeping."""

import json

from src.exporters.json_exporter import JSONExporter
from src.models.stock_data import StockData


def test_export_then_merge_adds_history(tmp_path, sample_stock_data):
    exporter = JSONExporter(output_dir=tmp_path)

    first = exporter.export(sample_stock_data)
    assert len(first) == 1

    # Second export of the same ticker merges with the existing file.
    exporter.export(sample_stock_data)

    with open(tmp_path / "TEST.json", encoding="utf-8") as f:
        data = json.load(f)

    assert "collection_history" in data
    assert "first_collected_at" in data
    # The merged file still reloads cleanly into the model (A5).
    assert StockData.from_dict(data).ticker == "TEST"


def test_merge_unions_annual_years(tmp_path):
    exporter = JSONExporter(output_dir=tmp_path)
    existing = {"financials_annual": {"2022": {"Revenue": 1}, "2023": {"Revenue": 2}}}
    new = {"financials_annual": {"2023": {"Revenue": 22}, "2024": {"Revenue": 3}}}

    merged = exporter._merge_data(existing, new)
    fa = merged["financials_annual"]
    assert set(fa.keys()) == {"2022", "2023", "2024"}
    assert fa["2023"]["Revenue"] == 22  # new overwrites same year


def test_merge_transactions_dedup_and_sort(tmp_path):
    exporter = JSONExporter(output_dir=tmp_path)
    existing = [{"transaction_date": "2024-01-01", "reporting_owner": "A",
                 "shares": 10, "transaction_type": "P"}]
    new = [
        {"transaction_date": "2024-02-01", "reporting_owner": "B",
         "shares": 5, "transaction_type": "S"},
        # duplicate of the existing one
        {"transaction_date": "2024-01-01", "reporting_owner": "A",
         "shares": 10, "transaction_type": "P"},
    ]

    merged = exporter._merge_transactions(existing, new)
    assert len(merged) == 2  # duplicate collapsed
    # Sorted by date descending
    assert merged[0]["transaction_date"] == "2024-02-01"


def test_price_history_snapshots_capped_at_10(tmp_path):
    exporter = JSONExporter(output_dir=tmp_path)
    existing = {
        "collected_at": "2024-01-01",
        "price_history": {"cagr": 0.1, "annual_returns": {"2023": 0.2}},
        "price_history_snapshots": [{"cagr": i} for i in range(10)],
    }
    new = {"price_history": {"cagr": 0.3}}
    merged = exporter._merge_data(existing, new)
    assert len(merged["price_history_snapshots"]) == 10


def test_collection_history_capped_at_50(tmp_path):
    exporter = JSONExporter(output_dir=tmp_path)
    existing = {"collection_history": [{"collected_at": str(i)} for i in range(50)]}
    new = {"collected_at": "latest", "data_sources": ["sec_edgar"]}
    merged = exporter._merge_data(existing, new)
    assert len(merged["collection_history"]) == 50
    assert merged["collection_history"][-1]["collected_at"] == "latest"

"""USAGE_GUIDE.md's Python examples must run against data shaped like the current export.

The guide drifted once (display-style keys like ``'Revenue'`` where the export uses the canonical
``'revenue'``) and ``.get(..., 0)`` hid it by printing zeros. These tests run the guide's code blocks
in order, notebook-style, against a trimmed real export (``tests/fixtures/usage_guide/AAPL.json``) and
a ``summary.csv`` written by the current CSV exporter, recording every key an example asks for that
the data doesn't have.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set, Tuple

import pytest

from src.exporters.csv_exporter import CSVExporter
from src.mappings.canonical import CANONICAL_BY_KEY
from src.models.stock_data import StockData

GUIDE = Path(__file__).resolve().parents[1] / "USAGE_GUIDE.md"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "usage_guide" / "AAPL.json"

# Optional packages a few examples need; their blocks are skipped when the package isn't installed.
OPTIONAL_PACKAGES = {"streamlit": "import streamlit", "plotly": "plotly", "openpyxl": "ExcelWriter"}
# Per-period bookkeeping stored next to the canonical financial fields.
PERIOD_METADATA = {"_source_tags", "calendar_year", "filed_date", "fiscal_period", "fiscal_year", "form", "frame",
                   "period_end"}


def _python_blocks() -> Iterator[Tuple[int, str, str]]:
    """Yield (block number, nearest heading, code) for each ```python block, in order."""
    text = GUIDE.read_text(encoding="utf-8")
    for number, match in enumerate(re.finditer(r"```python\n(.*?)\n```", text, re.DOTALL), start=1):
        headings = re.findall(r"^#+ (.+)$", text[: match.start()], re.MULTILINE)
        yield number, headings[-1] if headings else "top", match.group(1)


def _tracking_dict(missing: Set[str]) -> type:
    """A dict type that records lookups (``[]`` and ``.get``) of keys it doesn't have."""

    class TrackingDict(dict):
        def __getitem__(self, key: Any) -> Any:
            if key not in self:
                missing.add(key)
            return super().__getitem__(key)

        def get(self, key: Any, default: Any = None) -> Any:
            if key not in self:
                missing.add(key)
            return super().get(key, default)

    return TrackingDict


@pytest.fixture
def guide_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """data/output/json/{AAPL,MSFT,GOOGL}.json plus data/output/csv/summary.csv, as the guide expects."""
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    json_dir = tmp_path / "data" / "output" / "json"
    json_dir.mkdir(parents=True)
    stocks: List[StockData] = []
    for ticker, name in (("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corporation"), ("GOOGL", "Alphabet Inc.")):
        data = copy.deepcopy(base)
        data["ticker"], data["company_name"] = ticker, name
        (json_dir / f"{ticker}.json").write_text(json.dumps(data), encoding="utf-8")
        stocks.append(StockData.from_dict(data))
    CSVExporter(tmp_path / "data" / "output" / "csv").export(stocks)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_usage_guide_examples_run_against_the_current_export(
    guide_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing: Set[str] = set()
    tracking = _tracking_dict(missing)
    real_load = json.load
    monkeypatch.setattr(json, "load", lambda fp, **kw: real_load(fp, object_hook=tracking, **kw))

    namespace: Dict[str, Any] = {"__name__": "usage_guide_examples"}
    failures: List[str] = []
    for number, heading, code in _python_blocks():
        if any(trigger in code and importlib.util.find_spec(package) is None
               for package, trigger in OPTIONAL_PACKAGES.items()):
            continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(code, f"<USAGE_GUIDE block {number}: {heading}>", "exec"), namespace)
        except Exception as exc:  # report every failing block, not just the first
            failures.append(f"block {number} ({heading}): {type(exc).__name__}: {exc}")

    problems = failures + ([f"keys looked up that the data doesn't have: {sorted(missing)}"] if missing else [])
    assert not problems, "USAGE_GUIDE examples are out of sync with the export:\n" + "\n".join(problems)


def test_fixture_is_shaped_like_the_current_export() -> None:
    # The docs test is only as good as its fixture: refresh tests/fixtures/usage_guide/AAPL.json from a
    # real export (keep every key, shorten lists) whenever this fails.
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(StockData.from_dict(copy.deepcopy(data)).to_dict()) == set(data)
    fields = {key for period in data["financials_annual"].values() for key in period}
    assert fields - set(CANONICAL_BY_KEY) - PERIOD_METADATA == set()

"""BUG-164: one unit for every "%" filter of the screener.

What a user types after a "%"-shown field's label is a percent: "Insider % > 5" is 5%. The URL, the saved screens,
the presets and ``/api/screen`` keep the stored unit (a fraction for the "pct" kinds, Yahoo's percent for
``debt_to_equity``), so nothing saved changes meaning; ``screener.js`` converts at the popover and the chips.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.webapp.screener import METRIC_KINDS

JS = Path(__file__).resolve().parent.parent / "src" / "webapp" / "static" / "screener.js"
NODE = shutil.which("node")


def _metrics(client) -> List[Dict[str, Any]]:  # type: ignore[no-untyped-def]
    html = client.get("/screener").content.decode()
    return json.loads(html.split('id="screener-metrics">', 1)[1].split("</script>", 1)[0])


def test_every_metric_carries_its_kind_for_the_script(client) -> None:  # type: ignore[no-untyped-def]
    metrics = _metrics(client)
    assert metrics
    for m in metrics:
        assert m["kind"] == METRIC_KINDS.get(m["key"], "raw"), m["key"]
    kinds = {m["key"]: m["kind"] for m in metrics}
    assert kinds["insider_percent"] == "pct"
    assert kinds["debt_to_equity"] == "percent"
    assert kinds["roic"] == "pct"
    assert kinds["pe_trailing"] == "mult"


def test_the_popover_says_what_unit_to_type(client) -> None:  # type: ignore[no-untyped-def]
    html = client.get("/screener").content.decode()
    assert 'id="popover-unit"' in html
    js = JS.read_text(encoding="utf-8")
    assert "popover-unit" in js


def _run_js(expr: str) -> Any:
    """Evaluate *expr* in node with screener.js loaded under a page without the screener's elements."""
    script = (
        "global.document = {getElementById: function () { return null; }};"
        f"var u = require({json.dumps(str(JS))});"
        f"process.stdout.write(JSON.stringify({expr}));"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=60)  # type: ignore[list-item]
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("kind, typed, stored", [
    ("pct", "5", "0.05"),          # Insider % > 5 is the stored 0.05
    ("pct", "15", "0.15"),
    ("pct", "7", "0.07"),          # no binary-float tail: 7 / 100 is 0.07, not 0.07000000000000001
    ("pct", "0.5", "0.005"),
    ("pct", "-12.5", "-0.125"),
    ("pct", "0", "0"),
    ("pct", "1e-3", "0.00001"),
    ("percent", "100", "100"),     # Debt / equity (%) is Yahoo's percent: typed as stored
    ("percent", "78.4", "78.4"),
    ("mult", "5", "5"),
    ("raw", "0.3", "0.3"),
    ("money", "1e9", "1e9"),
    ("count", "3", "3"),
    ("pct", "", ""),               # nothing typed stays nothing (the popover refuses it before it gets here)
    ("pct", "abc", "abc"),
])
def test_a_typed_value_is_converted_to_the_stored_unit(kind: str, typed: str, stored: str) -> None:
    assert _run_js(f"u.typedToStored({json.dumps(kind)}, {json.dumps(typed)})") == stored


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("kind, stored, shown", [
    ("pct", "0.05", "5"),
    ("pct", "0.15", "15"),
    ("pct", "0.07", "7"),
    ("pct", "-0.125", "-12.5"),
    ("pct", "1.5", "150"),
    ("percent", "100", "100"),
    ("mult", "0.67", "0.67"),
    ("pct", "", ""),
    ("pct", "abc", "abc"),         # not a number: shown as it is, the server answers it
])
def test_a_stored_value_is_shown_in_the_typed_unit(kind: str, stored: str, shown: str) -> None:
    assert _run_js(f"u.storedToTyped({json.dumps(kind)}, {json.dumps(stored)})") == shown


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_what_is_typed_and_shown_round_trips() -> None:
    for kind in ("pct", "percent", "mult"):
        for typed in ("0", "1", "5", "7", "12.5", "33.3", "100", "-4", "0.01", "250"):
            stored = _run_js(f"u.typedToStored({json.dumps(kind)}, {json.dumps(typed)})")
            assert float(_run_js(f"u.storedToTyped({json.dumps(kind)}, {json.dumps(stored)})")) == float(typed)


def test_the_script_converts_where_a_filter_is_added_and_shown() -> None:
    js = JS.read_text(encoding="utf-8")
    # the popover stores the converted value; the chips and a saved screen's name show the typed unit
    assert "value: typedToStored(BY_KEY[pendingKey].kind, popValue.value)" in js
    assert js.count("storedToTyped(") >= 3  # the definition, the chip text and the saved screen's name
    # the URL, saved screens and presets are parsed as stored: parseQS does not convert
    parse = js[js.index("function parseQS"):js.index("function buildQS")]
    assert "typedToStored" not in parse and "storedToTyped" not in parse

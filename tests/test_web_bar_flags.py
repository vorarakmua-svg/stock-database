"""BUG-156, BUG-157, BUG-222: what the HP table, the compare chart and the screener's table show.

BUG-156: the HP table listed every stored bar as an ordinary row. CVS's last bar of a copy collected mid-session
opened at 105.00 above its high of 104.94 (and 112 of 128 last bars carried a part of a day's volume), a split-adjusted
1970s price read "$0.00" and a flat bar without volume (a day the feed filled in) read like a trading day. Now a
price under $1 has four decimals, a zero price and a zero volume read "—", a placeholder row (no price, or flat
with no volume) is marked "placeholder", and the ticker's newest bar is marked "partial" when it is dated today in
New York before the close or its open or close lies outside its range. The chart and the CSV carry the same bars.

BUG-157: a range without a single bar drew an empty figure with a legend: now the message "No price bars in this
range." (a node run of app.js, as test_web_screener_units runs screener.js).

BUG-222: the screener's table is tighter and its Valuation column stays pinned at the right edge (CSS: asserted
statically here; the widths were measured in headless Edge, see qa/evidence/bug156-investigation.txt).
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.exporters.sqlite_store import SQLiteStore
from src.webapp import create_app, formatting
from src.webapp.routes import stocks_api, workstation

STATIC = Path(__file__).resolve().parent.parent / "src" / "webapp" / "static"
NODE = shutil.which("node")
UTC = timezone.utc

#: (date, open, high, low, close, volume): an ordinary day, a penny bar, a zero price, a flat bar without volume,
#: a non-flat bar without volume, and an older bar whose open lies outside its range (a provider's quirk, not partial).
BARS = [
    ("2026-07-01", 100.0, 102.0, 99.0, 101.0, 1_500_000.0),
    ("2026-07-02", 0.0033, 0.0041, 0.0030, 0.0033, 2_000_000.0),
    ("2026-07-03", 0.0, 0.0, 0.0, 0.0, 10.0),
    ("2026-07-06", 38.3, 38.3, 38.3, 38.3, 0.0),
    ("2026-07-07", 38.0, 39.0, 37.5, 38.5, 0.0),
    ("2026-07-08", 195.98, 198.64, 196.21, 198.13, 127_234.0),
    ("2026-07-09", 50.0, 51.0, 49.0, 50.5, 3_000_000.0),
]
LAST_OK = ("2026-07-10", 50.5, 51.5, 50.0, 51.0, 3_100_000.0)
LAST_INCONSISTENT = ("2026-07-10", 105.0, 104.94, 103.0, 104.0, 900_000.0)  # CVS: the open above the high


def _client(tmp_path, last):
    path = tmp_path / "stock.db"
    store = SQLiteStore(db_path=path)
    conn = store._connect()
    store._create_schema(conn)
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO companies (ticker) VALUES ('FLAGS')")
    conn.executemany(
        "INSERT INTO price_bars (ticker, date, open, high, low, close, volume) VALUES ('FLAGS', ?, ?, ?, ?, ?, ?)",
        BARS + [last])
    conn.commit()
    conn.close()
    return TestClient(create_app(db_path=path))


def _pin(monkeypatch, now):
    monkeypatch.setattr(stocks_api, "_today", lambda: now.date())
    monkeypatch.setattr(workstation, "_now_utc", lambda: now, raising=False)


def _table(client, rng="MAX"):
    """The HP rows newest first: {"date", "class", "cells" (open, high, low, close, volume), "tag"}."""
    resp = client.get("/ui/stocks/FLAGS/hp", params={"range": rng})
    assert resp.status_code == 200
    rows = []
    for attrs, body in re.findall(r"<tr([^>]*)>(.*?)</tr>", resp.text.split("<tbody>", 1)[1], re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)
        date_cell = cells[0]
        rows.append({
            "date": re.match(r"[^<]+", date_cell).group(0),
            "class": re.search(r'class="([^"]*)"', attrs).group(1) if "class=" in attrs else "",
            "cells": cells[1:],
            "tag": re.search(r'class="hp-flag hp-flag-row">([^<]+)<', date_cell),
        })
    return rows, resp.text


@pytest.fixture
def after_close(monkeypatch):
    _pin(monkeypatch, datetime(2026, 7, 10, 21, 0, tzinfo=UTC))  # 17:00 in New York (EDT)


# ---- BUG-156: precision ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("price, text", [
    (0.0033, "$0.0033"), (0.04069, "$0.0407"), (0.5, "$0.5000"), (1.0, "$1.00"), (101.0, "$101.00"),
    (1234.5, "$1,234.50"), (0.0, "—"), (None, "—"), (-1.0, "—"),
])
def test_a_bar_price_has_enough_precision_and_no_price_reads_a_dash(price, text):
    assert formatting.fmt_bar_price(price) == text


def test_a_penny_price_and_a_zero_price_in_the_table(tmp_path, after_close):
    rows, _ = _table(_client(tmp_path, LAST_OK))
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-07-02"]["cells"][:4] == ["$0.0033", "$0.0041", "$0.0030", "$0.0033"]
    assert by_date["2026-07-03"]["cells"][:4] == ["—", "—", "—", "—"]


# ---- BUG-156: placeholders --------------------------------------------------------------------------------------


def test_placeholders_are_marked_and_a_missing_volume_reads_a_dash(tmp_path, after_close):
    rows, text = _table(_client(tmp_path, LAST_OK))
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-07-03"]["class"] == "hp-placeholder"           # no price
    assert by_date["2026-07-06"]["class"] == "hp-placeholder"           # flat, no volume
    assert by_date["2026-07-06"]["cells"][4] == "—"
    assert by_date["2026-07-06"]["tag"].group(1) == "placeholder"
    assert by_date["2026-07-07"]["class"] == ""                         # a real range with no volume: unmarked ...
    assert by_date["2026-07-07"]["cells"][4] == "—"                     # ... but its volume is not 0
    assert by_date["2026-07-01"]["class"] == "" and by_date["2026-07-01"]["cells"][4] == "1.50M"
    assert "hp-flag-key" in text and "Placeholder: no trades recorded" in text.split("hp-flag-key", 1)[1]   # the key


def test_an_older_bar_outside_its_range_is_not_partial(tmp_path, after_close):
    rows, _ = _table(_client(tmp_path, LAST_OK))
    assert {r["date"]: r["class"] for r in rows}["2026-07-08"] == ""


# ---- BUG-156: the partial bar ------------------------------------------------------------------------------------


def test_the_newest_bar_with_its_open_above_its_high_is_partial(tmp_path, after_close):
    rows, text = _table(_client(tmp_path, LAST_INCONSISTENT))
    assert rows[0]["date"] == "2026-07-10" and rows[0]["class"] == "hp-partial"
    assert rows[0]["tag"].group(1) == "partial"
    assert [r["class"] for r in rows[1:] if r["class"] == "hp-partial"] == []
    assert "Partial: taken before the close" in text.split("hp-flag-key", 1)[1]            # the key under the table


def test_a_complete_newest_bar_after_the_close_is_not_marked(tmp_path, after_close):
    rows, text = _table(_client(tmp_path, LAST_OK))
    assert rows[0]["date"] == "2026-07-10" and rows[0]["class"] == ""
    assert "Partial:" not in text and "Placeholder:" in text


@pytest.mark.parametrize("utc_hour, partial", [
    (13, True),    # 09:00 EDT: before the open
    (19, True),    # 15:00 EDT: in the session
    (20, False),   # 16:00 EDT: the close
    (23, False),
])
def test_the_bar_of_today_is_partial_until_the_close_in_new_york(tmp_path, monkeypatch, utc_hour, partial):
    _pin(monkeypatch, datetime(2026, 7, 10, utc_hour, 0, tzinfo=UTC))
    rows, _ = _table(_client(tmp_path, LAST_OK))
    assert (rows[0]["class"] == "hp-partial") is partial


def test_a_bar_of_an_earlier_day_is_complete_whatever_the_clock(tmp_path, monkeypatch):
    _pin(monkeypatch, datetime(2026, 7, 11, 14, 0, tzinfo=UTC))   # the next morning, 10:00 in New York
    rows, _ = _table(_client(tmp_path, LAST_OK))
    assert rows[0]["class"] == ""


def test_only_the_newest_bar_can_be_partial_by_the_clock(tmp_path, monkeypatch):
    _pin(monkeypatch, datetime(2026, 7, 10, 15, 0, tzinfo=UTC))   # 11:00 EDT on the last bar's day
    rows, _ = _table(_client(tmp_path, LAST_OK))
    assert [r["date"] for r in rows if r["class"] == "hp-partial"] == ["2026-07-10"]


@pytest.mark.parametrize("utc, ny", [
    (datetime(2026, 7, 10, 15, 0, tzinfo=UTC), datetime(2026, 7, 10, 11, 0)),      # EDT
    (datetime(2026, 1, 15, 15, 0, tzinfo=UTC), datetime(2026, 1, 15, 10, 0)),      # EST
    (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), datetime(2026, 3, 8, 1, 59)),        # the second Sunday of March 2026
    (datetime(2026, 3, 8, 7, 0, tzinfo=UTC), datetime(2026, 3, 8, 3, 0)),
    (datetime(2026, 11, 1, 5, 59, tzinfo=UTC), datetime(2026, 11, 1, 1, 59)),      # the first Sunday of November
    (datetime(2026, 11, 1, 6, 0, tzinfo=UTC), datetime(2026, 11, 1, 1, 0)),
    (datetime(2026, 11, 1, 7, 0, tzinfo=UTC), datetime(2026, 11, 1, 2, 0)),
])
def test_the_new_york_clock_follows_daylight_saving_time(utc, ny):
    assert workstation._new_york_clock(utc) == ny


def test_the_flag_of_a_bar_is_decided_by_bar_flag():
    now = datetime(2026, 7, 10, 21, 0, tzinfo=UTC)
    bar = {"date": "2026-07-10 16:00:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}
    assert workstation.bar_flag(bar, True, now) is None
    assert workstation.bar_flag(dict(bar, open=2.5), True, now) == "partial"      # open above the high
    assert workstation.bar_flag(dict(bar, close=0.4), True, now) == "partial"     # close below the low
    assert workstation.bar_flag(dict(bar, high=0.4, low=0.5, open=0.45, close=0.45), True, now) == "partial"
    assert workstation.bar_flag(dict(bar, open=2.5), False, now) is None
    morning = datetime(2026, 7, 10, 14, 0, tzinfo=UTC)                           # 10:00 in New York, the bar's day
    assert workstation.bar_flag(bar, True, morning) == "partial"                 # a date stored with a time of day
    assert workstation.bar_flag(dict(bar, date="2026-07-10"), True, morning) == "partial"
    assert workstation.bar_flag(dict(bar, close=None), False, now) == "placeholder"
    assert workstation.bar_flag(dict(bar, volume=None, open=1.5, high=1.5, low=1.5), False, now) == "placeholder"
    assert workstation.bar_flag(dict(bar, volume=0.0), False, now) is None        # real range: not flat
    assert workstation.bar_flag(dict(bar, open=1.5, high=1.5, low=1.5), False, now) is None   # flat, but traded
    # float32 noise on the high (a stored 0.1 + 1e-9) is not an inconsistency
    assert workstation.bar_flag(dict(bar, open=2.0000000001), True, now) is None


# ---- BUG-157 ----------------------------------------------------------------------------------------------------


def _run_compare_figure(series, compare=("SPY",)):
    """Call app.js's ``_gpRenderCompareFigure`` in node with a stand-in element and Plotly; returns (html, plotted)."""
    script = (
        "var vm = require('vm'), fs = require('fs');"
        "var plotted = [];"
        "var ctx = {document: {addEventListener: function () {}, getElementById: function () { return null; }},"
        " Plotly: {newPlot: function (id, traces) { plotted.push(traces.length); }}, console: console};"
        "vm.createContext(ctx);"
        f"vm.runInContext(fs.readFileSync({json.dumps(str(STATIC / 'app.js'))}, 'utf8'), ctx);"
        "var el = {id: 'gp-chart', innerHTML: ''};"
        f"ctx.el = el; ctx.data = {json.dumps({'series': series})}; ctx.cmp = {json.dumps(list(compare))};"
        "vm.runInContext(\"_gpRenderCompareFigure(el, data, 'GP', cmp)\", ctx);"
        "process.stdout.write(JSON.stringify({html: el.innerHTML, plotted: plotted}));"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=60)  # type: ignore[list-item]
    result = json.loads(out.stdout)
    return result["html"], result["plotted"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_empty_range_says_so_instead_of_drawing_a_blank_compare_chart():
    html, plotted = _run_compare_figure({"GP": {"dates": [], "pct": []}, "SPY": {"dates": [], "pct": []}})
    assert "No price bars in this range." in html
    assert plotted == []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_compare_chart_with_points_is_still_drawn():
    html, plotted = _run_compare_figure({"GP": {"dates": ["2026-07-01", "2026-07-02"], "pct": [0, 0.01]},
                                         "SPY": {"dates": [], "pct": []}})
    assert html == "" and plotted == [2]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_compare_without_any_series_keeps_its_own_message():
    html, plotted = _run_compare_figure({})
    assert "No comparison data available." in html and plotted == []


# ---- BUG-222 ----------------------------------------------------------------------------------------------------


def test_the_screener_table_pins_its_verdict_column():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    rule = re.search(r"\.screen-table th:last-child, \.screen-table td:last-child \{([^}]*)\}", css)
    assert rule is not None
    assert "position: sticky" in rule.group(1) and "right: 0" in rule.group(1) and "background" in rule.group(1)
    assert re.search(r"\.screen-table thead th, \.screen-table td \{[^}]*padding-left: 0\.5rem", css)


def test_the_results_table_carries_that_class_and_ends_with_the_verdict(client):
    html = client.get("/ui/screen").text
    assert 'class="table table-sm table-hover screen-table"' in html
    head = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)[-1].strip() == "Valuation"

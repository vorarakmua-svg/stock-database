"""Tests for :mod:`src.webapp.indicators` — pure-pandas technical indicators.

Covers the exact arithmetic cases from the Task 4 brief (moving average warm-up,
Wilder RSI bounds, MACD histogram identity, percent normalization) plus the
empty/short-input and bar-shaped bundle edge cases the chart API depends on.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

import pytest

from src.webapp.indicators import (
    indicator_bundle,
    macd,
    moving_average,
    normalize_pct,
    rsi,
)

# ---------------------------------------------------------------------------
# moving_average
# ---------------------------------------------------------------------------


def test_moving_average_exact_case() -> None:
    closes = [float(i) for i in range(1, 11)]
    result = moving_average(closes, 5)
    assert result == [None, None, None, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]


def test_moving_average_empty() -> None:
    assert moving_average([], 5) == []


def test_moving_average_shorter_than_window_all_none() -> None:
    result = moving_average([1.0, 2.0, 3.0], 5)
    assert result == [None, None, None]


def test_moving_average_mid_series_nan() -> None:
    closes = [float(i) for i in range(60)]
    closes[30] = float("nan")
    result = moving_average(closes, 5)
    assert len(result) == len(closes)
    assert all(isinstance(v, (float, type(None))) for v in result)


# ---------------------------------------------------------------------------
# rsi
# ---------------------------------------------------------------------------


def test_rsi_strictly_rising_exceeds_70_after_warmup() -> None:
    closes = [100.0 + i for i in range(60)]
    result = rsi(closes, period=14)
    assert len(result) == len(closes)
    tail = result[-10:]
    assert all(v is not None for v in tail)
    for v in tail:
        assert v is not None
        assert 0.0 <= v <= 100.0
    assert tail[-1] is not None and tail[-1] > 70.0


def test_rsi_strictly_falling_below_30_after_warmup() -> None:
    closes = [200.0 - i for i in range(60)]
    result = rsi(closes, period=14)
    tail = result[-10:]
    assert all(v is not None for v in tail)
    for v in tail:
        assert v is not None
        assert 0.0 <= v <= 100.0
    assert tail[-1] is not None and tail[-1] < 30.0


def test_rsi_flat_series_no_crash_and_bounded() -> None:
    closes = [100.0] * 30
    result = rsi(closes, period=14)
    assert len(result) == len(closes)
    for v in result:
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_rsi_shorter_than_period_all_none() -> None:
    result = rsi([1.0, 2.0, 3.0], period=14)
    assert result == [None, None, None]


def test_rsi_empty() -> None:
    assert rsi([], period=14) == []


def test_rsi_mid_series_nan() -> None:
    closes = [100.0 + float(i) * 0.5 for i in range(60)]
    closes[30] = float("nan")
    result = rsi(closes, period=14)
    assert len(result) == len(closes)
    assert all(isinstance(v, (float, type(None))) for v in result)


# ---------------------------------------------------------------------------
# macd
# ---------------------------------------------------------------------------


def test_macd_linear_rising_positive_after_warmup_and_hist_identity() -> None:
    closes = [100.0 + i * 0.5 for i in range(120)]
    result = macd(closes, fast=12, slow=26, signal=9)
    assert set(result.keys()) == {"macd", "signal", "hist"}
    for key in ("macd", "signal", "hist"):
        assert len(result[key]) == len(closes)

    tail_macd = result["macd"][-10:]
    tail_signal = result["signal"][-10:]
    tail_hist = result["hist"][-10:]
    for m, s, h in zip(tail_macd, tail_signal, tail_hist):
        assert m is not None and s is not None and h is not None
        assert m > 0.0
        assert abs(h - (m - s)) < 1e-9


def test_macd_hist_identity_holds_across_full_series() -> None:
    closes = [100.0 + math.sin(i / 5.0) * 3 for i in range(80)]
    result = macd(closes)
    for m, s, h in zip(result["macd"], result["signal"], result["hist"]):
        if m is None or s is None or h is None:
            assert m is None and s is None and h is None
        else:
            assert abs(h - (m - s)) < 1e-9


def test_macd_empty() -> None:
    assert macd([]) == {"macd": [], "signal": [], "hist": []}


def test_macd_shorter_than_slow_window_all_none() -> None:
    result = macd([1.0, 2.0, 3.0], fast=12, slow=26, signal=9)
    assert result["macd"] == [None, None, None]
    assert result["signal"] == [None, None, None]
    assert result["hist"] == [None, None, None]


def test_macd_mid_series_nan() -> None:
    closes = [100.0 + float(i) * 0.5 for i in range(60)]
    closes[30] = float("nan")
    result = macd(closes, fast=12, slow=26, signal=9)
    assert len(result["macd"]) == len(closes)
    assert len(result["signal"]) == len(closes)
    assert len(result["hist"]) == len(closes)
    for m, s, h in zip(result["macd"], result["signal"], result["hist"]):
        if m is not None and s is not None and h is not None:
            assert abs(h - (m - s)) < 1e-9


# ---------------------------------------------------------------------------
# normalize_pct
# ---------------------------------------------------------------------------


def test_normalize_pct_exact_case() -> None:
    result = normalize_pct([100.0, 110.0, 90.0])
    assert len(result) == 3
    assert result[0] == pytest.approx(0.0)
    assert result[1] == pytest.approx(0.10)
    assert result[2] == pytest.approx(-0.10)


def test_normalize_pct_empty() -> None:
    assert normalize_pct([]) == []


def test_normalize_pct_zero_first_close_guards_all_none() -> None:
    result = normalize_pct([0.0, 10.0, 20.0])
    assert result == [None, None, None]


def test_normalize_pct_leading_nan_uses_first_non_null_close() -> None:
    closes = [float("nan"), 100.0, 110.0, 90.0]
    result = normalize_pct(closes)
    assert result[0] is None
    assert result[1] == pytest.approx(0.0)
    assert result[2] == pytest.approx(0.10)
    assert result[3] == pytest.approx(-0.10)


# ---------------------------------------------------------------------------
# indicator_bundle
# ---------------------------------------------------------------------------


def _synthetic_bars(n: int) -> List[Dict[str, Any]]:
    bars = []
    price = 100.0
    for i in range(n):
        price += 0.1 if i % 2 == 0 else -0.05
        bars.append(
            {
                "date": f"2024-{(i // 28) % 12 + 1:02d}-{(i % 28) + 1:02d}",
                "open": price - 0.1,
                "high": price + 0.2,
                "low": price - 0.2,
                "close": price,
                "volume": 1000.0 + i,
            }
        )
    return bars


def test_indicator_bundle_shape_and_alignment() -> None:
    bars = _synthetic_bars(260)
    result = indicator_bundle(bars)
    n = len(bars)

    assert result["dates"] == [b["date"] for b in bars]
    assert result["close"] == [b["close"] for b in bars]
    assert len(result["ma_50"]) == n
    assert len(result["ma_200"]) == n
    assert len(result["rsi"]) == n
    assert len(result["macd"]["macd"]) == n
    assert len(result["macd"]["signal"]) == n
    assert len(result["macd"]["hist"]) == n

    # ma_200 needs 200 data points to warm up: first 199 entries None, 200th real.
    assert all(v is None for v in result["ma_200"][:199])
    assert result["ma_200"][199] is not None


def test_indicator_bundle_empty() -> None:
    result = indicator_bundle([])
    assert result == {
        "dates": [],
        "close": [],
        "ma_50": [],
        "ma_200": [],
        "rsi": [],
        "macd": {"macd": [], "signal": [], "hist": []},
    }

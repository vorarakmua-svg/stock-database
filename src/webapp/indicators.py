"""Pure-pandas technical indicator service consumed by the chart API.

Standalone module: no dependency on other ``src.webapp`` code. The only
implicit contract is with ``Reader.price_bars`` (see ``src/webapp/repository.py``),
whose ascending bar dicts (``{"date", "open", "high", "low", "close", "volume"}``)
are what :func:`indicator_bundle` expects.

Formulas (see ``docs/superpowers/plans/2026-07-03-terminal-workstation.md`` Task 4):

- Moving average: simple rolling mean (``rolling(window).mean()``).
- RSI: Wilder smoothing via ``ewm(alpha=1/period, adjust=False)`` applied to the
  up/down legs of the close-to-close difference.
- MACD: ``ewm(span=fast, adjust=False).mean() - ewm(span=slow, adjust=False).mean()``;
  the signal line is ``ewm(span=signal, adjust=False)`` of the MACD line.
- ``normalize_pct``: percent change vs. the first non-null/non-NaN close, for
  overlaying series with different price scales on one chart.

NaN handling: every function accepts plain ``float`` lists per its type signature,
but defensively tolerates stray ``NaN`` values inside the series (e.g. a gap in
recorded prices) — pandas propagates ``NaN`` through ``rolling``/``ewm`` at the
affected positions natively, which keeps the output list aligned 1:1 with the
input length (no rows are dropped). All ``NaN`` results are converted to ``None``
in the returned lists, matching the JSON-friendly ``Optional[float]`` contract.

Warm-up: values before an indicator has enough history to be meaningful are
``None`` — this includes both the natural ``NaN`` an operation produces (e.g.
``rolling`` before the window fills) and an explicit mask applied for RSI/MACD
(whose ``ewm`` computation, unlike ``rolling``, would otherwise "warm up" almost
immediately from partial data). Inputs shorter than the relevant warm-up window
resolve to an all-``None`` list rather than raising.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def _series(closes: List[float]) -> "pd.Series[float]":
    return pd.Series(closes, dtype=float)


def _to_optional_floats(series: "pd.Series[float]") -> List[Optional[float]]:
    return [None if pd.isna(value) else float(value) for value in series]


def moving_average(closes: List[float], window: int) -> List[Optional[float]]:
    """Simple moving average; ``None`` for the first ``window - 1`` entries."""
    if not closes:
        return []
    series = _series(closes)
    ma = series.rolling(window=window).mean()
    return _to_optional_floats(ma)


def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    """Wilder RSI (0..100); ``None`` for the first ``period`` entries.

    A perfectly flat run (no gains or losses at all) is reported as ``50.0``
    rather than the ``NaN`` a naive ``0/0`` ratio would produce.
    """
    if not closes:
        return []
    series = _series(closes)
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)

    avg_gain = up.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = down.ewm(alpha=1.0 / period, adjust=False).mean()

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi_values = 100.0 - (100.0 / (1.0 + rs))

    flat = (avg_gain == 0.0) & (avg_loss == 0.0)
    rsi_values = rsi_values.where(~flat, 50.0)

    warm = min(period, len(closes))
    rsi_values.iloc[:warm] = np.nan
    return _to_optional_floats(rsi_values)


def macd(
    closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Dict[str, List[Optional[float]]]:
    """MACD line, signal line, and histogram; ``None`` for the first ``slow`` entries."""
    if not closes:
        return {"macd": [], "signal": [], "hist": []}
    series = _series(closes)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line

    warm = min(slow, len(closes))
    macd_line.iloc[:warm] = np.nan
    signal_line.iloc[:warm] = np.nan
    hist.iloc[:warm] = np.nan

    return {
        "macd": _to_optional_floats(macd_line),
        "signal": _to_optional_floats(signal_line),
        "hist": _to_optional_floats(hist),
    }


def normalize_pct(closes: List[float]) -> List[Optional[float]]:
    """Percent change vs. the first non-null close: ``close / first_non_null - 1``.

    If the first non-null close is ``0`` (or every close is null/``NaN``), the
    ratio is undefined and the whole series resolves to ``None``.
    """
    if not closes:
        return []
    series = _series(closes)
    first_valid = series.first_valid_index()
    if first_valid is None:
        return [None] * len(closes)
    base = series.iloc[first_valid]
    if base == 0.0:
        return [None] * len(closes)
    pct = (series / base) - 1.0
    return _to_optional_floats(pct)


def indicator_bundle(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Date-aligned bundle of close prices plus MA/RSI/MACD for the chart API.

    ``bars`` is the ascending bar-dict shape produced by ``Reader.price_bars``.
    Every list in the result is the same length as ``bars`` and aligned by index.
    """
    if not bars:
        return {
            "dates": [],
            "close": [],
            "ma_50": [],
            "ma_200": [],
            "rsi": [],
            "macd": {"macd": [], "signal": [], "hist": []},
        }
    dates = [bar["date"] for bar in bars]
    closes: List[float] = [bar["close"] for bar in bars]
    return {
        "dates": dates,
        "close": closes,
        "ma_50": moving_average(closes, 50),
        "ma_200": moving_average(closes, 200),
        "rsi": rsi(closes),
        "macd": macd(closes),
    }

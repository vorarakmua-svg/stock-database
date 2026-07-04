"""``/api/stocks`` JSON router: quote, bars, indicators, compare, and the
analyst/earnings/dividends/splits/holders/insiders/profile sub-resources
that back the terminal workstation UI (Task 7+).

Range/interval semantics
-------------------------
``range`` resolves to a start date measured back from "today"
(``_today()``, real UTC date by default — overridable in tests via
monkeypatching this module's ``_today``):

- ``1M``/``3M``/``6M``/``1Y``/``5Y`` -> a fixed day count back from today.
- ``YTD`` -> January 1st of the current year.
- ``MAX`` -> no lower bound (``None``).

``interval`` controls bar granularity for ``/bars`` (and the bars CSV
export): ``auto`` resolves to ``1wk`` for ``range=5Y``, ``1mo`` for
``range=MAX``, and ``1d`` otherwise. Weekly/monthly bars are built with
pandas ``resample`` (O=first, H=max, L=min, C=last, V=sum); the aggregated
bar is labeled with the **last actual bar date included in that period**
(not the period boundary), so labels always correspond to a real trading
day present in the source data.

``/indicators`` and ``/compare-bars`` are always daily: the indicator
bundle is computed on the ticker's FULL price history (so long moving
averages are warm at the start of whatever range is requested) and then
sliced down to the requested range.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_job_manager, get_reader, get_settings
from ..indicators import indicator_bundle, normalize_pct
from ..jobs import CollectionJobManager
from ..repository import Reader
from ..schemas import (
    AnalystOut,
    BarOut,
    DividendEvent,
    EarningsRow,
    HolderRow,
    InsiderRow,
    ProfileOut,
    QuoteOut,
    SplitEvent,
)
from ..settings import WebSettings

router = APIRouter(prefix="/api/stocks", tags=["stocks"])

# ---------------------------------------------------------------------------
# Range / interval resolution helpers (shared with export_api.py CSV routes)
# ---------------------------------------------------------------------------

_RANGE_DAYS: Dict[str, int] = {"1M": 30, "3M": 91, "6M": 182, "1Y": 365, "5Y": 1826}
VALID_RANGES = frozenset({"1M", "3M", "6M", "YTD", "1Y", "5Y", "MAX"})
VALID_INTERVALS = frozenset({"auto", "1d", "1wk", "1mo"})


def _today() -> date:
    """The reference "today" used to resolve ``range`` to a start date.

    A thin, monkeypatchable seam — tests override this function (not
    ``datetime.now`` directly) to pin "today" near fixture data without
    touching the system clock.
    """
    return datetime.now(timezone.utc).date()


def resolve_range_start(range_key: str, today: Optional[date] = None) -> Optional[date]:
    """Resolve a ``range`` key to an inclusive start date, or ``None`` for MAX.

    Raises ``ValueError`` if ``range_key`` is not one of ``VALID_RANGES``.
    """
    if range_key not in VALID_RANGES:
        raise ValueError(f"Unknown range {range_key!r}. Must be one of {sorted(VALID_RANGES)}.")
    if today is None:
        today = _today()
    if range_key == "MAX":
        return None
    if range_key == "YTD":
        return date(today.year, 1, 1)
    return today - timedelta(days=_RANGE_DAYS[range_key])


def resolve_interval(interval: str, range_key: str) -> str:
    """Resolve an ``interval`` key (resolving ``auto`` against ``range_key``).

    Raises ``ValueError`` if ``interval`` is not one of ``VALID_INTERVALS``.
    """
    if interval not in VALID_INTERVALS:
        raise ValueError(f"Unknown interval {interval!r}. Must be one of {sorted(VALID_INTERVALS)}.")
    if interval != "auto":
        return interval
    if range_key == "5Y":
        return "1wk"
    if range_key == "MAX":
        return "1mo"
    return "1d"


def resample_bars(bars: List[Dict[str, Any]], interval: str) -> List[Dict[str, Any]]:
    """Aggregate ascending daily ``bars`` to ``interval`` granularity.

    ``interval="1d"`` just re-shapes each row to the ``date/open/high/low/
    close/volume`` fields (dropping any extra columns such as ``ticker``).
    ``"1wk"``/``"1mo"`` resample via pandas: O=first, H=max, L=min, C=last,
    V=sum. Each aggregated bar's ``date`` is the LAST actual bar date folded
    into that period (not the period boundary), so periods with no source
    data are dropped rather than appearing as an empty/NaN bar.
    """
    if not bars:
        return []
    if interval == "1d":
        return [
            {
                "date": b["date"],
                "open": b.get("open"),
                "high": b.get("high"),
                "low": b.get("low"),
                "close": b.get("close"),
                "volume": b.get("volume"),
            }
            for b in bars
        ]

    freq = "W" if interval == "1wk" else "ME"
    df = pd.DataFrame(bars)
    df["_dt"] = pd.to_datetime(df["date"])
    df = df.sort_values("_dt").set_index("_dt")
    agg = df.resample(freq).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "date": "last",
        }
    )
    agg = agg.dropna(subset=["close"])
    return [
        {
            "date": row["date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for _, row in agg.iterrows()
    ]


def _slice_bundle(bundle: Dict[str, Any], start: Optional[date]) -> Dict[str, Any]:
    """Slice an ``indicator_bundle`` result to bars on/after ``start``.

    ``start=None`` (MAX range) returns the bundle unchanged. Slicing happens
    AFTER the bundle is computed on full history, so long-window indicators
    (MA200) are already warm by the first date in the slice, as long as
    enough history precedes it.
    """
    if start is None:
        return bundle
    start_iso = start.isoformat()
    dates: List[str] = bundle["dates"]
    idx = next((i for i, d in enumerate(dates) if d >= start_iso), len(dates))
    macd = bundle["macd"]
    return {
        "dates": dates[idx:],
        "close": bundle["close"][idx:],
        "ma_50": bundle["ma_50"][idx:],
        "ma_200": bundle["ma_200"][idx:],
        "rsi": bundle["rsi"][idx:],
        "macd": {
            "macd": macd["macd"][idx:],
            "signal": macd["signal"][idx:],
            "hist": macd["hist"][idx:],
        },
    }


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


@router.get("/{ticker}/quote", response_model=QuoteOut)
def quote(ticker: str, r: Reader = Depends(get_reader)) -> Dict[str, Any]:
    """Latest quote for *ticker*.

    404 if the ticker is unknown; 404 (detail "no quote...") if the ticker
    is known but has no ``market_snapshots`` row yet.
    """
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    row = r.quote(ticker)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no quote available for {ticker}")
    return row


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------


@router.get("/{ticker}/bars", response_model=List[BarOut])
def bars(
    ticker: str,
    range: str = "1Y",  # noqa: A002 - matches the public query-param name
    interval: str = "auto",
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """OHLCV bars for *ticker* over ``range``, at ``interval`` granularity.

    400 on an invalid ``range``/``interval`` value.
    """
    try:
        start = resolve_range_start(range)
        resolved_interval = resolve_interval(interval, range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    start_iso = start.isoformat() if start is not None else None
    raw_bars = r.price_bars(ticker, start=start_iso)
    return resample_bars(raw_bars, resolved_interval)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


@router.get("/{ticker}/indicators")
def indicators(
    ticker: str,
    range: str = "1Y",  # noqa: A002
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Indicator bundle (close/MA50/MA200/RSI/MACD) for *ticker* over ``range``.

    Computed on the ticker's FULL price history, then sliced to ``range`` —
    so long-window indicators (MA200) are correct at the start of the slice,
    not restarted from scratch there. 400 on an invalid ``range``.
    """
    try:
        start = resolve_range_start(range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    full_bars = r.price_bars(ticker)
    bundle = indicator_bundle(full_bars)
    return _slice_bundle(bundle, start)


# ---------------------------------------------------------------------------
# Compare bars
# ---------------------------------------------------------------------------


@router.get("/{ticker}/compare-bars")
def compare_bars(
    ticker: str,
    others: str = "",
    range: str = "1Y",  # noqa: A002
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Normalized (percent-change) close series for *ticker* plus ``others``.

    ``others`` is a comma-separated ticker list and may include benchmark
    symbols such as ``^GSPC`` (which has price_bars but no ``companies``
    row). Only the primary *ticker* gets a 404 existence guard; unknown
    "others" simply contribute an empty series rather than erroring, since
    ``Reader.price_bars`` returns ``[]`` for a ticker with no bars. 400 on
    an invalid ``range``.
    """
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    try:
        start = resolve_range_start(range)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    start_iso = start.isoformat() if start is not None else None

    other_tickers = [t.strip() for t in others.split(",") if t.strip()]
    all_tickers = [ticker] + other_tickers

    series: Dict[str, Any] = {}
    for t in all_tickers:
        t_bars = r.price_bars(t, start=start_iso)
        dates = [b["date"] for b in t_bars]
        closes = [b["close"] for b in t_bars]
        series[t] = {"dates": dates, "pct": normalize_pct(closes)}
    return {"series": series}


# ---------------------------------------------------------------------------
# Analyst / earnings / dividends / splits / profile
# ---------------------------------------------------------------------------


@router.get("/{ticker}/analyst", response_model=AnalystOut)
def analyst(ticker: str, r: Reader = Depends(get_reader)) -> Dict[str, Any]:
    """Latest analyst snapshot for *ticker*; 404 if none has ever been collected."""
    row = r.analyst_snapshot(ticker)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no analyst data for {ticker}")
    return row


@router.get("/{ticker}/earnings", response_model=List[EarningsRow])
def earnings(ticker: str, r: Reader = Depends(get_reader)) -> List[Dict[str, Any]]:
    """Earnings-surprise history for *ticker*, ascending by quarter."""
    return r.earnings_history(ticker)


@router.get("/{ticker}/dividends", response_model=List[DividendEvent])
def dividends(ticker: str, r: Reader = Depends(get_reader)) -> List[Dict[str, Any]]:
    """Dividend payment history for *ticker*, ascending by date."""
    return r.dividend_events(ticker)


@router.get("/{ticker}/splits", response_model=List[SplitEvent])
def splits(ticker: str, r: Reader = Depends(get_reader)) -> List[Dict[str, Any]]:
    """Stock split history for *ticker*, ascending by date."""
    return r.split_events(ticker)


@router.get("/{ticker}/profile", response_model=ProfileOut)
def profile(ticker: str, r: Reader = Depends(get_reader)) -> Dict[str, Any]:
    """Company profile (companies row + officer roster); 404 if unknown."""
    result = r.profile(ticker)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")
    return result


# ---------------------------------------------------------------------------
# Holders / insiders
# ---------------------------------------------------------------------------


@router.get("/{ticker}/holders", response_model=List[HolderRow])
def holders(
    ticker: str,
    type: str = "institutional",  # noqa: A002 - matches the public query-param name
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Institutional or mutual-fund holders for *ticker*; 400 on a bad ``type``."""
    try:
        return r.holders(ticker, type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{ticker}/insiders", response_model=List[InsiderRow])
def insiders(
    ticker: str,
    limit: int = 100,
    r: Reader = Depends(get_reader),
) -> List[Dict[str, Any]]:
    """Insider (Form-4-derived) transactions for *ticker*, newest first."""
    return r.insider_transactions(ticker, limit=limit)


# ---------------------------------------------------------------------------
# On-demand quote refresh (Task 10) — DES panel's REFRESH button
# ---------------------------------------------------------------------------


@router.post("/{ticker}/refresh-quote", status_code=202)
def refresh_quote(
    ticker: str,
    settings: WebSettings = Depends(get_settings),
    manager: CollectionJobManager = Depends(get_job_manager),
    r: Reader = Depends(get_reader),
) -> Dict[str, Any]:
    """Submit a lightweight quote-only refresh job for *ticker*.

    409 if quote refresh is disabled (``settings.allow_quote_refresh``); 404
    if *ticker* is unknown; else 202 with ``{"job_id": ...}`` — poll via the
    existing ``GET /api/collection/jobs/{job_id}`` JSON endpoint, or (for the
    DES panel) the HTMX fragment at
    ``GET /ui/stocks/{ticker}/refresh-status/{job_id}``.

    Runs on the SAME serialized single-writer job manager as full collection
    jobs (``manager.submit(..., mode="quote")``) — never a second writer.
    """
    if not settings.allow_quote_refresh:
        raise HTTPException(status_code=409, detail="quote refresh is disabled")
    if r.get_company(ticker) is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker}")

    job_id = manager.submit(tickers=[ticker], mode="quote")
    return {"job_id": job_id}

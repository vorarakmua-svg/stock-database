"""Display-formatting helpers for the stock-database web UI."""
from __future__ import annotations

from typing import Optional


def _suffixed(abs_x: float) -> str:
    """A non-negative magnitude with its B/M/K suffix and two decimals, whole below a thousand."""
    if abs_x >= 1_000_000_000:
        return f"{abs_x / 1_000_000_000:.2f}B"
    if abs_x >= 1_000_000:
        return f"{abs_x / 1_000_000:.2f}M"
    if abs_x >= 1_000:
        return f"{abs_x / 1_000:.2f}K"
    return f"{abs_x:.0f}"


def fmt_money(x: Optional[float]) -> str:
    """Format a monetary value with B/M/K suffix.

    None  -> "—"
    Sign is preserved for negative values.
    Scale is based on the absolute value.
    """
    if x is None:
        return "—"
    return f"{'-' if x < 0 else ''}${_suffixed(abs(x))}"


def fmt_count(x: Optional[float]) -> str:
    """Format a count (shares, volume) with B/M/K suffix and no currency.

    None -> "—"; 14_590_000_000 -> "14.59B". Counts read as money ("$14.59B")
    before (BUG-136).
    """
    if x is None:
        return "—"
    return f"{'-' if x < 0 else ''}{_suffixed(abs(x))}"


def fmt_pct(x: Optional[float]) -> str:
    """Format a decimal ratio as a percentage.

    None -> "—"; 0.15 -> "15.0%".
    """
    if x is None:
        return "—"
    return f"{x * 100:.1f}%"


def fmt_mult(x: Optional[float]) -> str:
    """Format a multiplier.

    None -> "—"; 1.234 -> "1.2x".
    """
    if x is None:
        return "—"
    return f"{x:.1f}x"


def fmt_raw2(x: Optional[float]) -> str:
    """Format a raw value to 2 decimal places (used for EPS).

    None -> "—".
    """
    if x is None:
        return "—"
    return f"{x:.2f}"


def fmt_price(x: Optional[float]) -> str:
    """Format a share price with $ prefix and 2 decimal places.

    None -> "—"; else "$x,xxx.xx" (cents matter for share prices).
    """
    if x is None:
        return "—"
    return f"${x:,.2f}"


def fmt_value(x: Optional[float], kind: str) -> str:
    """Dispatch formatting by kind.

    kind in {"money", "count", "pct", "percent", "mult", "raw"}: "count" is a
    number of shares; "pct" is a fraction (0.15 -> "15.0%"), "percent" a figure
    already in percent (Yahoo's debtToEquity: 78.445 -> "78.4%").
    "raw" -> str(x) or "—".
    """
    if kind == "money":
        return fmt_money(x)
    if kind == "count":
        return fmt_count(x)
    if kind == "pct":
        return fmt_pct(x)
    if kind == "percent":
        return fmt_pct(None if x is None else x / 100)
    if kind == "mult":
        return fmt_mult(x)
    # "raw" or unknown
    if x is None:
        return "—"
    return str(x)

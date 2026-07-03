"""Display-formatting helpers for the stock-database web UI."""
from __future__ import annotations

from typing import Optional


def fmt_money(x: Optional[float]) -> str:
    """Format a monetary value with B/M/K suffix.

    None  -> "—"
    Sign is preserved for negative values.
    Scale is based on the absolute value.
    """
    if x is None:
        return "—"
    sign = "-" if x < 0 else ""
    abs_x = abs(x)
    if abs_x >= 1_000_000_000:
        return f"{sign}${abs_x / 1_000_000_000:.2f}B"
    if abs_x >= 1_000_000:
        return f"{sign}${abs_x / 1_000_000:.2f}M"
    if abs_x >= 1_000:
        return f"{sign}${abs_x / 1_000:.2f}K"
    return f"{sign}${abs_x:.0f}"


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

    kind in {"money", "pct", "mult", "raw"}.
    "raw" -> str(x) or "—".
    """
    if kind == "money":
        return fmt_money(x)
    if kind == "pct":
        return fmt_pct(x)
    if kind == "mult":
        return fmt_mult(x)
    # "raw" or unknown
    if x is None:
        return "—"
    return str(x)

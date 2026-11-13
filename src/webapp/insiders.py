"""Insiders' trades on the web (Milestone 3), shared by ``GET /api/stocks/{t}/insiders`` and the INS tab: the SEC's
Form 4 trades with their open-market aggregates, or Yahoo's rows where no Form 4 was collected
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md)."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from ..ownership.insiders import CODE_MEANINGS, summarize
from .repository import Reader


def _role(t: Dict[str, Any]) -> str:
    """The owner's role as the filing gives it: the officer title, else director or 10% owner."""
    parts = [t["officer_title"]] if t.get("officer_title") else []
    if t.get("is_director") and not parts:
        parts.append("Director")
    if t.get("is_ten_percent"):
        parts.append("10% owner")
    return ", ".join(parts) or "—"


def _price_note(price: Optional[float], close: Optional[float]) -> str:
    """What a suspect price is: as filed, and a decimal point off the close that day (its direction where the close
    is known)."""
    if close is None or price is None:
        return "as filed, 1,000 times off the close that day"
    if price > close:
        return "as filed, about 1,000 times the close that day"
    return "as filed, about a thousandth of the close that day"


def _trade_out(t: Dict[str, Any], close: Optional[float] = None) -> Dict[str, Any]:
    """A trade for the web; *close* is the stock's close that day, read for a suspect price's note."""
    shares, price = t.get("shares"), t.get("price")
    plan, suspect = t.get("aff10b5one"), t.get("price_suspect")
    return {
        "accession": t["accession"], "form": t.get("form"), "filed_date": t.get("filed_date"), "date": t.get("date"),
        "kind": t.get("kind"), "code": t.get("code"), "meaning": CODE_MEANINGS.get(t.get("code") or "", "—"),
        "security_title": t.get("security_title"), "shares": shares, "price": price,
        # A price that lost its decimal point is shown as filed and has no value: its trade counts in shares only.
        "value": shares * price if shares is not None and price is not None and not suspect else None,
        "price_suspect": None if suspect is None else bool(suspect),
        "price_note": _price_note(price, close) if suspect else None,
        "acquired_disposed": t.get("acquired_disposed"), "owned_after": t.get("owned_after"),
        "direct_indirect": t.get("direct_indirect"), "owner_cik": t.get("owner_cik"),
        "owner_name": t.get("owner_name"), "officer_title": t.get("officer_title"), "role": _role(t),
        "is_ceo_cfo": bool(t.get("is_ceo_cfo")), "plan": None if plan is None else bool(plan),
    }


def insider_view(r: Reader, ticker: str, today: Optional[str] = None) -> Dict[str, Any]:
    """The ticker's insider trades: ``source`` is "sec" with the aggregates as of *today*, "yahoo" with Yahoo's
    rows when no Form 4 is stored, or None when there are neither. A suspect price (``insiders.price_suspect``) is
    shown as filed with a note, and left out of the dollar figures."""
    trades = r.sec_insider_trades(ticker)
    if trades:
        return {"ticker": ticker, "source": "sec",
                "summary": summarize(trades, today or date.today().isoformat()),
                "trades": [_trade_out(t, r.close_on_or_before(ticker, t["date"])
                                      if t.get("price_suspect") and t.get("date") else None) for t in trades]}
    yahoo = r.insider_transactions(ticker)
    return {"ticker": ticker, "source": "yahoo" if yahoo else None, "summary": None, "trades": yahoo}

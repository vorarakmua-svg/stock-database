"""Insider aggregates (Milestone 3), from the stored Form 4 trades
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

Only open-market trades count: purchases (code P) and sales (S) in the non-derivative table, from filings no
amendment superseded. Grants, exercises, tax withholding and gifts are shown but never summed.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: The Form 4 transaction codes (the SEC's General Instructions, item 8).
CODE_MEANINGS: Dict[str, str] = {
    "P": "open-market purchase", "S": "open-market sale", "A": "grant or award", "D": "sale back to the issuer",
    "F": "shares withheld for tax", "I": "discretionary transaction", "M": "exercise or conversion",
    "C": "conversion of a derivative", "E": "expiration of a short derivative position",
    "H": "expiration of a long derivative position", "O": "exercise of an out-of-the-money derivative",
    "X": "exercise of an in-the-money derivative", "G": "gift", "L": "small acquisition", "W": "by will",
    "Z": "into or out of a voting trust", "J": "other acquisition or disposition", "K": "equity swap",
    "U": "tendered in a change of control", "V": "reported early, voluntarily",
}
#: The look-back windows, in days: 3, 6 and 12 months.
WINDOWS = (("3m", 91), ("6m", 182), ("12m", 365))
#: A cluster buy: at least CLUSTER_BUYERS distinct insiders buying within CLUSTER_DAYS of the first.
CLUSTER_BUYERS = 3
CLUSTER_DAYS = 30

_CHIEF = re.compile(r"\b(CEO|CFO|Chief\s+(Executive|Financial)\s+Officer)\b", re.IGNORECASE)
#: A unit named right after the title: "CEO, BGS", "CEO of Payments", "CEO - Americas".
_UNIT_AFTER = re.compile(r"^\s*(,|of\b|-|–)\s*\S", re.IGNORECASE)


def ceo_or_cfo(title: Optional[str]) -> bool:
    """Whether an officer title names the company's chief executive or financial officer. A title naming a unit
    after it ("EVP, Pres. & CEO, BGS"; "CEO of Payments") belongs to that unit's head."""
    if not title:
        return False
    return any(not _UNIT_AFTER.match(title[m.end():]) for m in _CHIEF.finditer(title))


def _value(trades: Sequence[Mapping[str, Any]]) -> float:
    return float(sum(t["shares"] * t["price"] for t in trades
                     if t.get("shares") is not None and t.get("price") is not None))


def _shares(trades: Sequence[Mapping[str, Any]]) -> float:
    return sum(t.get("shares") or 0 for t in trades)


def _clusters(buys: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Runs of purchases by at least CLUSTER_BUYERS insiders within CLUSTER_DAYS of the run's first, earliest
    first; a purchase belongs to one run at most."""
    ordered = sorted(buys, key=lambda t: t["date"])
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(ordered):
        end = (date.fromisoformat(ordered[i]["date"]) + timedelta(days=CLUSTER_DAYS)).isoformat()
        group = [t for t in ordered[i:] if t["date"] <= end]
        owners = {t.get("owner_cik") for t in group}
        if len(owners) >= CLUSTER_BUYERS:
            out.append({"start": group[0]["date"], "end": group[-1]["date"], "buyers": len(owners),
                        "names": sorted({t.get("owner_name") or "" for t in group}), "shares": _shares(group),
                        "value": _value(group)})
            i += len(group)
        else:
            i += 1
    return out


def summarize(trades: Sequence[Mapping[str, Any]], today: str) -> Dict[str, Any]:
    """The aggregates of one company's trades (each with its filing's owner, title, CEO/CFO flag and 10b5-1 flag)
    as of *today*: per window, the open-market shares and dollars bought and sold, the net and the distinct
    buyers and sellers; over 12 months, the CEO and CFO purchases, the cluster buys and the share of sales made
    under a 10b5-1 plan, where the filings say."""
    day = date.fromisoformat(today[:10])
    market = [t for t in trades if t.get("kind", "nonderivative") == "nonderivative" and t.get("date")]
    out: Dict[str, Any] = {"windows": {}}
    for label, days in WINDOWS:
        cutoff = (day - timedelta(days=days)).isoformat()
        buys = [t for t in market if t["code"] == "P" and t["date"] >= cutoff]
        sells = [t for t in market if t["code"] == "S" and t["date"] >= cutoff]
        out["windows"][label] = {
            "bought_shares": _shares(buys), "bought_value": _value(buys),
            "sold_shares": _shares(sells), "sold_value": _value(sells),
            "net_value": _value(buys) - _value(sells),
            "buyers": len({t.get("owner_cik") for t in buys}), "sellers": len({t.get("owner_cik") for t in sells}),
        }
    year = (day - timedelta(days=365)).isoformat()
    buys = [t for t in market if t["code"] == "P" and t["date"] >= year]
    sells = [t for t in market if t["code"] == "S" and t["date"] >= year]
    out["ceo_cfo_buys_12m"] = [{"date": t["date"], "owner_name": t.get("owner_name"),
                                "officer_title": t.get("officer_title"), "shares": t.get("shares"),
                                "value": _value([t])} for t in buys if t.get("is_ceo_cfo")]
    out["clusters_12m"] = _clusters(buys)
    known = [t for t in sells if t.get("aff10b5one") is not None]
    total = _shares(known)
    out["planned_sale_share_12m"] = _shares([t for t in known if t["aff10b5one"]]) / total if total else None
    return out

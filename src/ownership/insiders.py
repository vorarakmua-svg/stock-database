"""Insider aggregates (Milestone 3), from the stored Form 4 trades, and the trades each 4/A restates
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

Only open-market trades count: purchases (code P) and sales (S) in the non-derivative table, that no amendment
restated (``supersessions``). Grants, exercises, tax withholding and gifts are shown but never summed. A price that
lost its decimal point (``price_suspect``) is kept as filed and counts as missing: its trade counts in shares only.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

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

#: A price that lost its decimal point (BUG-220: the Lilly Endowment's "$1,031,414" a share for a $1,025.28 close) is
#: DECIMAL_SHIFT times, or a DECIMAL_SHIFT-th of, a price within PRICE_TOLERANCE of the stock's close that day.
#: BRK-B's Class A shares, about 1,500 times the B close, stay outside it.
DECIMAL_SHIFT = 1000.0
PRICE_TOLERANCE = 0.10

_CHIEF = re.compile(r"\b(CEO|CFO|Chief\s+(Executive|Financial)\s+Officer)\b", re.IGNORECASE)
#: A unit named right after the title: "CEO, BGS", "CEO of Payments", "CEO - Americas".
_UNIT_AFTER = re.compile(r"^\s*(,|of\b|-|–)\s*\S", re.IGNORECASE)


def ceo_or_cfo(title: Optional[str]) -> bool:
    """Whether an officer title names the company's chief executive or financial officer. A title naming a unit
    after it ("EVP, Pres. & CEO, BGS"; "CEO of Payments") belongs to that unit's head."""
    if not title:
        return False
    return any(not _UNIT_AFTER.match(title[m.end():]) for m in _CHIEF.finditer(title))


def price_suspect(price: Optional[float], close: Optional[float]) -> Optional[bool]:
    """Whether a trade's *price* lost its decimal point: DECIMAL_SHIFT times, or a DECIMAL_SHIFT-th of, a price
    within PRICE_TOLERANCE of *close*, the stock's close on the trade's date. None without a price, or a close to
    check it against."""
    if price is None or not close:
        return None
    return any(abs(shifted - close) <= PRICE_TOLERANCE * close
               for shifted in (price / DECIMAL_SHIFT, price * DECIMAL_SHIFT))


def _value(trades: Sequence[Mapping[str, Any]]) -> float:
    """The trades' dollars: shares times price, a missing or suspect price aside."""
    return float(sum(t["shares"] * t["price"] for t in trades
                     if t.get("shares") is not None and t.get("price") is not None and not t.get("price_suspect")))


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


_Row = Mapping[str, Any]


def _table_and_title(trade: _Row) -> Tuple[Any, str]:
    """What a restatement always keeps of a trade: its table and its security's title, the title's case and spacing
    aside."""
    return trade.get("kind"), " ".join((trade.get("security_title") or "").split()).casefold()


def _same_security(a: _Row, b: _Row) -> bool:
    """The same table, security and transaction code."""
    return _table_and_title(a) == _table_and_title(b) and a.get("code") == b.get("code")


def _same_day(a: _Row, b: _Row) -> bool:
    return a.get("date") == b.get("date")


def _same_amount(a: _Row, b: _Row) -> bool:
    return (a.get("shares"), a.get("price")) == (b.get("shares"), b.get("price"))


def _same_trade(a: _Row, b: _Row) -> bool:
    return _same_security(a, b) and _same_day(a, b) and _same_amount(a, b)


def _code_corrected(a: _Row, b: _Row) -> bool:
    """The same trade but for its code: a purchase filed as a sale, say. Left standing, the two would cancel each
    other in the net buying."""
    return _table_and_title(a) == _table_and_title(b) and _same_day(a, b) and _same_amount(a, b)


def _date_corrected(a: _Row, b: _Row) -> bool:
    """Its date corrected."""
    return _same_security(a, b) and _same_amount(a, b)


def _amount_corrected(a: _Row, b: _Row) -> bool:
    """Its shares or its price corrected."""
    return _same_security(a, b) and _same_day(a, b)


#: How an amendment's trade finds the trade it restates, each way tried over all its trades before the next: the
#: same trade; the same but for its code; the same security, shares and price (its date corrected); the same
#: security and date (its shares or price corrected).
_RESTATES: Tuple[Callable[[_Row, _Row], bool], ...] = (_same_trade, _code_corrected, _date_corrected,
                                                        _amount_corrected)


def _amended(amendment: _Row, earlier: Sequence[_Row], owners: Mapping[str, Set[str]]) -> Optional[str]:
    """The filing a 4/A amends, of those filed before it: a reporting owner's own, filed on the 4/A's original filing
    day (a Form 4, or an earlier 4/A); of several, the one of the 4/A's period of report."""
    day, mine = amendment.get("original_filed"), owners[amendment["accession"]]
    found = [f for f in earlier if day and f.get("filed_date") == day and owners[f["accession"]] & mine]
    if len(found) > 1:
        found = [f for f in found if f.get("period_of_report") == amendment.get("period_of_report")] or found
    return found[-1]["accession"] if found else None


def supersessions(filings: Iterable[_Row], trades: Iterable[_Row]
                  ) -> Tuple[Dict[str, str], Dict[Tuple[str, int], str]]:
    """What one company's 4/As amend: each filing's latest amendment, and each trade an amendment restates.

    *filings* has a row per filing and reporting owner (accession, owner_cik, form, filed_date, period_of_report,
    original_filed); *trades* a row per trade (accession, seq, kind, code, security_title, date, shares, price).
    A 4/A amends the filing ``_amended`` finds, and joins its chain: the first filing and its amendments. Taken in
    the order they were filed, each restates a trade of its chain that still stands when it has a trade of the same
    table, code and security on the same date or with the same shares and price, or the same trade but for its code
    (``_RESTATES``), matched one for one. Every other trade stands: an amendment adding an omitted trade replaces
    none.

    Returns the filings amended (accession -> its latest amendment's) and the trades restated ((accession, seq) ->
    the restating amendment's accession)."""
    first: Dict[str, _Row] = {}
    owners: Dict[str, Set[str]] = {}  # each filing's reporting owners' CIKs, a blank one aside
    for row in filings:
        first.setdefault(row["accession"], row)
        ciks = owners.setdefault(row["accession"], set())
        if row.get("owner_cik"):
            ciks.add(row["owner_cik"])
    own: Dict[str, List[_Row]] = {}
    for trade in sorted(trades, key=lambda t: (t["accession"], t["seq"])):
        own.setdefault(trade["accession"], []).append(trade)
    # The order filed, a day's Form 4s before its 4/As.
    order = sorted(first.values(), key=lambda f: (f.get("filed_date") or "", f.get("form") == "4/A", f["accession"]))
    amended_by: Dict[str, str] = {}
    restated: Dict[Tuple[str, int], str] = {}
    chains: Dict[str, List[str]] = {}  # each filing's chain, first filing first (one list shared by its members)
    for i, filing in enumerate(order):
        accession = filing["accession"]
        parent = _amended(filing, order[:i], owners) if filing.get("form") == "4/A" else None
        if parent is None:
            chains[accession] = [accession]
            continue
        amended_by[parent] = accession
        chain = chains[accession] = chains[parent]
        standing = [t for member in chain for t in own.get(member, [])]
        mine = own.get(accession, [])
        matched: Set[int] = set()
        for same in _RESTATES:
            for j, trade in enumerate(mine):
                if j in matched:
                    continue
                for old in standing:
                    key = (old["accession"], old["seq"])
                    if key not in restated and same(old, trade):
                        restated[key] = accession
                        matched.add(j)
                        break
        chain.append(accession)
    return amended_by, restated


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

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
#: A filing's buyer (BUG-206), the one owner the INS tab, the API and the screener read: of its reporting owners, the
#: company's CEO or CFO if one is among them, else the lowest CIK. A joint filing's owners report the same trades
#: (Berkshire with Buffett; ValueAct's eight entities), so it is one buyer, and a CEO's buy when the CEO files with
#: their trust. SQL for the owner_cik of the filing whose accession is {accession}.
FILING_OWNER_SQL = ("(SELECT fo.owner_cik FROM insider_filings fo WHERE fo.accession = {accession} "
                    "ORDER BY fo.is_ceo_cfo DESC, fo.owner_cik LIMIT 1)")

#: A price that lost its decimal point (BUG-220: the Lilly Endowment's "$1,031,414" a share for a $1,025.28 close) is
#: DECIMAL_SHIFT times, or a DECIMAL_SHIFT-th of, a price within PRICE_TOLERANCE of the stock's close that day.
#: BRK-B's Class A shares, about 1,500 times the B close, stay outside it.
DECIMAL_SHIFT = 1000.0
PRICE_TOLERANCE = 0.10

#: The chief executive or financial officer in a title (BUG-207): "CEO" or "CFO" (a co-CEO; "C.E.O."), or "Chief
#: Executive Officer" or "Chief Financial Officer" as filers shorten or misspell it ("Chief Exec. Officer", "CHIEF
#: EXECUTIVE OFF.", "Chief Fin Ofc", "Chief Financal Officer", "Chief Financial Office") or join a second role into it
#: ("Chief Fin & Op Officer", "Chief Finance/Strat Ofcr").
_CHIEF = re.compile(
    r"(?<![\w.])(?:ceo|cfo|c\.e\.o\.?|c\.f\.o\.?)(?!\w)"
    r"|\bchief\s+(?:exec(?:utive)?|fin(?:ancial|ancal|ance)?)\b\.?(?:\s*(?:&|and|/)\s*[a-z]+\.?)?"
    r"\s*(?:officer|office|offr|off\.?|ofcr|ofc\.?)(?!\w)", re.IGNORECASE)
#: Before the name, in its part of the title (back to a comma or a semicolon): someone else's chief ("Advisor to the
#: CEO", "COS to the Chairman & CEO", "Office of the CEO") or a past one ("Former Senior VP & CFO").
_NOT_THEIRS = re.compile(r"\b(?:to|former|office\s+of)\b[^,;]*$", re.IGNORECASE)
#: Right before the name: a deputy, or a unit ("KFC Division CEO", "BU and Ops CFO").
_UNDER = re.compile(r"\b(?:deputy|division|ops)\s*$", re.IGNORECASE)
#: The roles joined to the name ("& President", "and Treasurer", "/Chairman"), each up to a comma, a semicolon, a
#: colon, a dash or a bracket; and a bracket's words ("Chief Executive Officer (CEO)").
_JOINED = re.compile(r"(?:\s*(?:&|\band\b|/)[^,;:\-–(]*|\s*\([^)]*\))*", re.IGNORECASE)
#: A word that starts another of the person's roles rather than a unit's name: "CEO, Chairman", "CFO, EVP & Treasurer",
#: "CFO, President International".
_ROLE = re.compile(
    r"(?:chair\w*|vice|pres\w*|director|c[a-z]?[a-z]o|chief|exec\w*|evp|svp|vp|sr|senior|treas\w*|secretary|"
    r"controller|ctrl|general\s+counsel|founder|head|interim|acting)\b", re.IGNORECASE)


def _a_unit(rest: str, ticker: Optional[str]) -> bool:
    """Whether *rest*, a title after a chief's name and the roles joined to it, names a unit the chief heads: "of" or
    "at" one ("CEO of Payments"), or one after a comma, a dash or a space ("CEO, BGS"; "CEO-The Americas"; "CEO Amazon
    Web Services"), unless it is another role (``_ROLE``: "CEO, Chairman") or the company's own *ticker* ("Pres. & CEO
    of NCLH" at NCLH)."""
    named = re.match(r"\s*(?:of|at)\b(.*)", rest, re.IGNORECASE | re.DOTALL)
    words = (named.group(1) if named else re.sub(r"^\s*[,;:\-–]?\s*(?:(?:and|&)\s+)?", "", rest)).strip(" .")
    if not words or (not named and _ROLE.match(words)):
        return False
    unit = re.sub(r"[,;:(].*", "", words, flags=re.DOTALL).strip(" .")
    return not (ticker and unit.casefold() == ticker.casefold())


def ceo_or_cfo(title: Optional[str], ticker: Optional[str] = None) -> bool:
    """Whether an officer title names the chief executive or financial officer (``_CHIEF``) of the company, *ticker*.

    Not when the name follows someone else's or a past chief's mark (``_NOT_THEIRS``) or a deputy's or a unit's word
    (``_UNDER``); nor when a unit follows it (``_a_unit``), right after it or after the roles joined to it
    (``_JOINED``) by a dash or a space ("CEO & President - RJA"), or within them ("CEO and EVP at PG&E Company"). After
    a joined role, a comma's unit is that role's ("CFO and SVP, Business Ops"). So "EVP, Pres. & CEO, BGS" and "CEO of
    Payments" head units, and "CEO-Elect", "CFO - Designate" and "CFO Emeritus" read as no chief in office, while "CEO,
    Chairman" and "CFO, EVP & Treasurer" are the company's."""
    if not title:
        return False
    for m in _CHIEF.finditer(title):
        before, after = title[:m.start()], title[m.end():]
        if _NOT_THEIRS.search(before) or _UNDER.search(before):
            continue
        joined = _JOINED.match(after).group()  # type: ignore[union-attr]  # (it matches the empty string too)
        rest = after[len(joined):]
        if re.search(r"\bat\b", joined, re.IGNORECASE):
            continue
        if joined.strip() and re.match(r"\s*[,;]", rest):
            return True
        if not _a_unit(rest, ticker):
            return True
    return False


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
    """The aggregates of one company's trades (each with its filing's accession, its buyer's (``FILING_OWNER_SQL``)
    CIK, name, title and CEO/CFO flag, and its 10b5-1 flag) as of *today*: per window, the open-market shares and
    dollars bought and sold, the net and the distinct buyers and sellers; over 12 months, the CEO and CFO buys (a
    filing each), the cluster buys and the share of sales made under a 10b5-1 plan, where the filings say."""
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
    # A CEO or CFO buy is a filing (BUG-215), as the screener's ins_ceo_cfo_buys_12m counts them: its purchases in
    # the year summed, from the first one's date to the last's.
    filings: Dict[Any, List[Mapping[str, Any]]] = {}
    for t in buys:
        if t.get("is_ceo_cfo"):
            filings.setdefault(t.get("accession"), []).append(t)
    out["ceo_cfo_buys_12m"] = [{"accession": accession, "date": min(t["date"] for t in rows),
                                "last_date": max(t["date"] for t in rows), "owner_name": rows[0].get("owner_name"),
                                "officer_title": rows[0].get("officer_title"), "shares": _shares(rows),
                                "value": _value(rows)} for accession, rows in filings.items()]
    out["clusters_12m"] = _clusters(buys)
    known = [t for t in sells if t.get("aff10b5one") is not None]
    total = _shares(known)
    out["planned_sale_share_12m"] = _shares([t for t in known if t["aff10b5one"]]) / total if total else None
    return out

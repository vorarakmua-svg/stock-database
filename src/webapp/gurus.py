"""The gurus' 13F holdings on the web (Milestone 3), shared by the guru pages, the HDS tab's Gurus section and the
API: views over the quarters and positions the collector stores (``guru_quarters``, ``guru_positions``), with the
gurus' names (``guru_filers``), their filings (``guru_filings``) and our companies (``companies``)
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md). Read-only, and empty (or None) for a database without
the tables.

A guru's latest quarter is the latest period stored for it. A position's ``put_call`` is None for shares and principal
amounts (stored as ''), 'Put' or 'Call' for options; ``ours`` says its ticker is one of our companies.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set, Tuple

from ..ownership.portfolio import share_classes
from .repository import Reader

#: The changes of a quarter, in the order the views group them.
CHANGE_GROUPS: Tuple[str, ...] = ("new", "added", "reduced", "sold_out")
#: The tables without which there is nothing to show.
_TABLES = frozenset({"guru_quarters", "guru_positions"})
#: Each guru's latest stored quarter, to join on (guru_cik, period).
_LATEST = "(SELECT guru_cik, MAX(period) AS period FROM guru_quarters GROUP BY guru_cik)"
#: A quarter's positions in the views' order: the shares before the options, the largest first (a position sold
#: out by what it was worth before).
_ORDER = "put_call != '', COALESCE(value_usd, value_before) DESC, security, put_call"
#: A quarter's (q) positions in shares and principal amounts whose action passes a test.
_COUNT = ("(SELECT COUNT(*) FROM guru_positions p WHERE p.guru_cik = q.guru_cik AND p.period = q.period "
          "AND p.put_call = '' AND p.action {test})")
_COUNT_HELD = _COUNT.format(test="IS NOT 'sold_out'")
_COUNT_NEW = _COUNT.format(test="= 'new'")
_COUNT_SOLD_OUT = _COUNT.format(test="= 'sold_out'")


def _tables(r: Reader) -> Set[str]:
    return {name for (name,) in r.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def has_gurus(r: Reader) -> bool:
    """Whether a guru's quarter is stored: the tables are there and hold one."""
    return _TABLES <= _tables(r) and r.conn.execute("SELECT 1 FROM guru_quarters LIMIT 1").fetchone() is not None


def _gurus(r: Reader, tables: Set[str]) -> Dict[str, Dict[str, Any]]:
    """Each guru's manager and its filers' names, the current filer's first (``guru_filers``)."""
    gurus: Dict[str, Dict[str, Any]] = {}
    if "guru_filers" not in tables:
        return gurus
    for guru_cik, name, manager in r.conn.execute(
            "SELECT guru_cik, name, manager FROM guru_filers ORDER BY guru_cik, filer_cik != guru_cik, filer_cik"):
        guru = gurus.setdefault(guru_cik, {"manager": manager, "filers": []})
        if name:
            guru["filers"].append(name)
    return gurus


def _ours(r: Reader) -> Set[str]:
    """Our companies' tickers."""
    return {ticker for (ticker,) in r.conn.execute("SELECT ticker FROM companies")}


def _cik(cik: str) -> str:
    """A CIK as the tables keep it, without leading zeros. A CIK has ten digits at most: anything longer is left as it
    is, and found nowhere."""
    return str(int(cik)) if cik.isdigit() and len(cik) <= 10 else cik


def _position(row: Any, ours: Set[str]) -> Dict[str, Any]:
    """A ``guru_positions`` row as the views give it."""
    return {
        "security": row["security"], "ticker": row["ticker"], "ours": row["ticker"] in ours,
        "put_call": row["put_call"] or None, "cusips": row["cusips"].split(",") if row["cusips"] else [],
        "issuer": row["issuer"], "title_of_class": row["title_of_class"], "share_type": row["share_type"],
        "shares": row["shares"], "value": row["value_usd"], "weight": row["weight"], "action": row["action"],
        "shares_before": row["shares_before"], "value_before": row["value_before"],
        "split_factor": row["split_factor"], "change_pct": row["change_pct"],
    }


def guru_list(r: Reader) -> List[Dict[str, Any]]:
    """Each guru with a stored quarter, at its latest, the largest total value first: its CIK, manager and filers'
    names, the quarter (period, filing date, total value, whether complete), the positions held in shares and
    principal amounts (options apart), and of those the quarter's new ones and the ones it sold out (None without
    the quarter before)."""
    tables = _tables(r)
    if not _TABLES <= tables:
        return []
    gurus = _gurus(r, tables)
    out = []
    for row in r.conn.execute(
            f"SELECT q.*, {_COUNT_HELD} AS held, {_COUNT_NEW} AS new, {_COUNT_SOLD_OUT} AS sold_out "
            f"FROM guru_quarters q JOIN {_LATEST} l ON l.guru_cik = q.guru_cik AND l.period = q.period "
            "ORDER BY q.total_value DESC, q.guru_cik"):
        guru = gurus.get(row["guru_cik"], {})
        prior = bool(row["has_prior"])
        out.append({"cik": row["guru_cik"], "manager": guru.get("manager"), "filers": guru.get("filers", []),
                    "period": row["period"], "filed": row["filed"], "total_value": row["total_value"],
                    "positions": row["held"], "new": row["new"] if prior else None,
                    "sold_out": row["sold_out"] if prior else None, "complete": bool(row["complete"]),
                    "has_prior": prior})
    return out


def _filings(r: Reader, tables: Set[str], accessions: List[str]) -> List[Dict[str, Any]]:
    """A quarter's filings in its order, each with its filer, form, filing date and amendment type where stored."""
    stored: Dict[str, Any] = {}
    if accessions and "guru_filings" in tables:
        marks = ", ".join("?" * len(accessions))
        stored = {row["accession"]: row for row in r.conn.execute(
            "SELECT accession, filer_cik, form, filed, amendment_type FROM guru_filings "
            f"WHERE accession IN ({marks})", accessions)}
    names = ({cik: name for cik, name in r.conn.execute("SELECT filer_cik, name FROM guru_filers")}
             if "guru_filers" in tables else {})
    out = []
    for accession in accessions:
        row = stored.get(accession)
        filer = row["filer_cik"] if row is not None else None
        out.append({"accession": accession, "filer_cik": filer, "filer": names.get(filer),
                    **{key: row[key] if row is not None else None for key in ("form", "filed", "amendment_type")}})
    return out


def guru_view(r: Reader, cik: str, period: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One guru's quarter, its latest unless *period* names another: the quarter (filing date, total value, whether
    complete, whether the quarter before is there, its filings), the periods stored (newest first), the positions
    held (the shares before the options, the largest first) and the quarter's changes grouped (CHANGE_GROUPS; None
    without the quarter before). None for a guru or period not stored."""
    tables = _tables(r)
    if not _TABLES <= tables:
        return None
    cik = _cik(cik)
    periods = [p for (p,) in r.conn.execute(
        "SELECT period FROM guru_quarters WHERE guru_cik = ? ORDER BY period DESC", (cik,))]
    period = period or (periods[0] if periods else None)
    if period not in periods:
        return None
    quarter = r.conn.execute("SELECT * FROM guru_quarters WHERE guru_cik = ? AND period = ?", (cik, period)).fetchone()
    ours = _ours(r)
    rows = [_position(row, ours) for row in r.conn.execute(
        f"SELECT * FROM guru_positions WHERE guru_cik = ? AND period = ? ORDER BY {_ORDER}", (cik, period))]
    guru = _gurus(r, tables).get(cik, {})
    accessions = json.loads(quarter["accessions"] or "[]")
    prior = bool(quarter["has_prior"])
    return {
        "cik": cik, "manager": guru.get("manager"), "filers": guru.get("filers", []), "period": period,
        "periods": periods, "filed": quarter["filed"], "total_value": quarter["total_value"],
        "complete": bool(quarter["complete"]), "has_prior": prior, "computed_at": quarter["computed_at"],
        "accessions": accessions, "filings": _filings(r, tables, accessions),
        "positions": [p for p in rows if p["action"] != "sold_out"],
        "changes": {group: [p for p in rows if p["action"] == group] for group in CHANGE_GROUPS} if prior else None,
    }


def gurus_holding(r: Reader, ticker: str) -> List[Dict[str, Any]]:
    """The gurus' positions in the stock or its other share class (``portfolio.share_classes``: GOOGL with GOOG, BRK-B
    with BRK-A) in each one's latest quarter: the shares held, the largest value first, then the options, then the
    positions that quarter sold out. Each with the guru (CIK, manager), its quarter (period, filing date, whether
    complete), the class (ticker) and the position: shares, value, weight in the guru's portfolio and its change."""
    tables = _tables(r)
    if not _TABLES <= tables:
        return []
    classes = share_classes(ticker)
    gurus, ours = _gurus(r, tables), _ours(r)
    return [{"cik": row["guru_cik"], "manager": gurus.get(row["guru_cik"], {}).get("manager"), "period": row["period"],
             "filed": row["filed"], "complete": bool(row["complete"]), **_position(row, ours)}
            for row in r.conn.execute(
                f"SELECT p.*, q.filed, q.complete FROM guru_positions p JOIN {_LATEST} l "
                "ON l.guru_cik = p.guru_cik AND l.period = p.period "
                "JOIN guru_quarters q ON q.guru_cik = p.guru_cik AND q.period = p.period "
                f"WHERE p.ticker IN ({', '.join('?' * len(classes))}) "
                "ORDER BY p.action IS 'sold_out', p.put_call != '', COALESCE(p.value_usd, p.value_before) DESC, "
                "p.guru_cik, p.security, p.put_call", classes)]


def guru_portfolio(r: Reader, limit: int = 200) -> List[Dict[str, Any]]:
    """The gurus' portfolio aggregated over each one's latest quarter, in shares and principal amounts (options
    apart): per security a guru holds, the gurus holding it, their summed shares and value, the buyers (new or added)
    and the sellers (reduced, or sold out that quarter), its ticker (``ours`` when it's one of our companies) and the
    issuer of its largest holding. The *limit* most held: by the gurus holding, then the value."""
    tables = _tables(r)
    if not _TABLES <= tables:
        return []
    ours = _ours(r)
    held: Dict[Optional[str], Dict[str, Any]] = {}
    for row in r.conn.execute(
            f"SELECT p.* FROM guru_positions p JOIN {_LATEST} l ON l.guru_cik = p.guru_cik AND l.period = p.period "
            "WHERE p.put_call = '' ORDER BY p.action IS 'sold_out', p.value_usd DESC, p.guru_cik"):
        security = held.setdefault(row["security"], {
            "security": row["security"], "ticker": row["ticker"], "ours": row["ticker"] in ours,
            "issuer": row["issuer"], "holders": set(), "shares": 0.0, "value": 0.0, "buyers": set(),
            "sellers": set()})
        if row["action"] != "sold_out":
            security["holders"].add(row["guru_cik"])
            security["shares"] += row["shares"] or 0.0
            security["value"] += row["value_usd"] or 0.0
        if row["action"] in ("new", "added"):
            security["buyers"].add(row["guru_cik"])
        elif row["action"] in ("reduced", "sold_out"):
            security["sellers"].add(row["guru_cik"])
    rows = [{**{k: v for k, v in s.items() if k not in ("holders", "buyers", "sellers")},
             "gurus": len(s["holders"]), "buyers": len(s["buyers"]), "sellers": len(s["sellers"])}
            for s in held.values() if s["holders"]]
    rows.sort(key=lambda s: (-s["gurus"], -s["value"], s["security"] or ""))
    return rows[:limit]

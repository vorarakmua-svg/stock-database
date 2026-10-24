"""The other valuation indicators' view (Milestone 4), shared by ``GET /api/stocks/{t}/valuation-indicators`` and the
VAL tab's "Other indicators" section: the stored ``valuation_indicators`` rows in INDICATORS order, each with its label,
its method in one line, its base's upside from the live price and short notes read from its assumptions, and the
reverse DCF solved again at the live price from the DCF inputs its row stores. Display-only, like owner earnings: none
of it enters the median, the verdicts or the upside (docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md,
"Indicators" and "Web for the indicators").
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..valuation.engine import upside_pct
from ..valuation.indicators import (
    EPV_WACC_SPREAD,
    EPV_YEARS,
    GRAHAM_MULTIPLIER,
    INDICATORS,
    NNWC_INVENTORY,
    NNWC_RECEIVABLES,
    implied_growth,
)
from .formatting import fmt_mult, fmt_pct, fmt_price
from .repository import Reader

#: Each indicator's name on the page.
INDICATOR_LABELS: Dict[str, str] = {
    "reverse_dcf": "Reverse DCF",
    "epv": "Earnings power value (EPV)",
    "ncav": "NCAV and Graham's net-net",
    "ps_value": "Median P/S value",
    "graham_tangible": "Graham number on tangible book",
}
#: Each indicator's method in one line: the published formulas, on the constants the indicators use.
INDICATOR_METHODS: Dict[str, str] = {
    "reverse_dcf": ("The growth in years 1-5, fading to the terminal growth by year 10, at which the DCF gives the "
                    "price."),
    "epv": (f"Greenwald's no-growth value: the {EPV_YEARS}-year mean operating margin × the latest revenue, after tax, "
            "over the WACC, less debt plus cash and short-term investments (maintenance capex taken as D&A)."),
    "ncav": ("Current assets less all liabilities, per share; the bear is Graham's net-net working capital (cash and "
             f"short-term investments + {NNWC_RECEIVABLES:.0%} of receivables + {NNWC_INVENTORY:.0%} of inventory - "
             "liabilities)."),
    "ps_value": ("The 10-year median P/S × trailing revenue per share; the bear and bull at the 25th and 75th "
                 "percentile P/S."),
    "graham_tangible": (f"sqrt({GRAHAM_MULTIPLIER:g} × EPS × tangible book per share), tangible book being equity - "
                        "goodwill - intangible assets."),
}
#: The figures a note names: the net-net's parts and tangible book's deductions.
_FIGURE_NAMES: Dict[str, str] = {
    "cash_and_equivalents": "cash", "short_term_investments": "short-term investments",
    "accounts_receivable": "receivables", "inventory": "inventory", "goodwill": "goodwill",
    "intangible_assets": "intangible assets",
}


def _json(text: Any) -> Dict[str, Any]:
    """An assumptions column as a dict; {} when it isn't a JSON object."""
    try:
        value = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> Optional[float]:
    """A stored figure as a float; None when it isn't a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _list(a: Dict[str, Any], key: str) -> List[Any]:
    value = a.get(key)
    return value if isinstance(value, list) else []


def _signed_price(x: float) -> str:
    """A per-share figure with its sign before the dollar: "-$9.17"."""
    return f"-{fmt_price(-x)}" if x < 0 else fmt_price(x)


def _years(years: List[Any]) -> str:
    """Fiscal years as runs: [2020, 2022, 2023] reads "FY2020, FY2022-FY2023"."""
    runs: List[List[int]] = []
    for year in sorted(int(y) for y in years):
        if runs and year == runs[-1][-1] + 1:
            runs[-1].append(year)
        else:
            runs.append([year])
    return ", ".join(f"FY{run[0]}" if len(run) == 1 else f"FY{run[0]}-FY{run[-1]}" for run in runs)


def _names(keys: List[Any]) -> str:
    """Figures by name: "cash, receivables or inventory"."""
    names = [_FIGURE_NAMES.get(str(k), str(k)) for k in keys]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _reverse_dcf_notes(row: Dict[str, Any], a: Dict[str, Any]) -> List[str]:
    """The DCF's own growth, and the snapshot price and day the stored growth (the screener's) was solved at."""
    notes: List[str] = []
    growth = _number(a.get("dcf_growth_base"))
    if growth is not None:
        notes.append(f"The DCF itself assumes {fmt_pct(growth)} a year in years 1-5.")
    price = _number(a.get("price"))
    if price is not None:
        day = f" on {str(a['price_date'])[:10]}" if a.get("price_date") else ""
        stored = (f"{fmt_pct(row.get('value_base'))}, the screener's figure" if row.get("applicable")
                  else row.get("na_reason") or "no growth in the range")
        notes.append(f"Stored at {fmt_price(price)}{day}: {stored}.")
    return notes


def _epv_notes(row: Dict[str, Any], a: Dict[str, Any]) -> List[str]:
    """The years EBIT is pretax income plus interest, those without an interest figure, a missing debt figure, and the
    WACC."""
    notes: List[str] = []
    pretax = _list(a, "pretax_years")
    if pretax:
        verb = "files" if len(pretax) == 1 else "file"
        notes.append(f"{_years(pretax)} {verb} no operating income: EBIT is pretax income plus interest expense.")
    no_interest = _list(a, "no_interest_years")
    if no_interest:
        verb = "has" if len(no_interest) == 1 else "have"
        notes.append(f"{_years(no_interest)} {verb} no interest expense figure: counted as 0.")
    if a.get("debt_missing") is True:
        notes.append("No debt figure: counted as 0.")
    rate = _number(a.get("wacc"))
    if rate is not None:
        notes.append(f"Discounted at the WACC of {fmt_pct(rate)}; the bear and bull at {EPV_WACC_SPREAD * 100:g} "
                     "point above and below it.")
    return notes


def _ncav_notes(row: Dict[str, Any], a: Dict[str, Any]) -> List[str]:
    """Graham's buy line, a net-net below zero and the net-net's missing parts; on an N/A row, how far below zero NCAV
    is."""
    if not row.get("applicable"):
        ncav = _number(a.get("ncav_per_share"))
        return [f"NCAV is {_signed_price(ncav)} a share."] if ncav is not None else []
    notes: List[str] = []
    buy = _number(a.get("netnet_buy_price"))
    if buy is not None:
        notes.append(f"Graham bought below two thirds of NCAV: {fmt_price(buy)}.")
    nnwc = _number(a.get("nnwc_per_share"))
    if row.get("value_bear") is None and nnwc is not None:
        notes.append(f"Graham's net-net working capital is {_signed_price(nnwc)} a share: no bear case.")
    missing = _list(a, "nnwc_missing")
    if missing:
        notes.append(f"No {_names(missing)} figure: counted as 0 in the net-net.")
    return notes


def _ps_value_notes(row: Dict[str, Any], a: Dict[str, Any]) -> List[str]:
    """The 10-year median P/S with its quartiles, and the P/S now."""
    notes: List[str] = []
    median = _number(a.get("median_ps"))
    if median is not None:
        months = _number(a.get("n_months"))
        over = f" over {months:.0f} months" if months else ""
        notes.append(f"Median P/S {fmt_mult(median)}{over} ({fmt_mult(_number(a.get('p25_ps')))} to "
                     f"{fmt_mult(_number(a.get('p75_ps')))} from the 25th to the 75th percentile).")
    current = _number(a.get("current_ps"))
    if current is not None:
        notes.append(f"P/S now {fmt_mult(current)}.")
    return notes


def _graham_tangible_notes(row: Dict[str, Any], a: Dict[str, Any]) -> List[str]:
    """The goodwill or intangibles figures counted as 0; on an N/A row, how far below zero tangible book is."""
    notes: List[str] = []
    missing = _list(a, "missing")
    if missing:
        notes.append(f"No {_names(missing)} figure: counted as 0, so any untagged intangibles stay in tangible book.")
    tangible = _number(a.get("tangible_bvps"))
    if not row.get("applicable") and tangible is not None:
        notes.append(f"Tangible book is {_signed_price(tangible)} a share.")
    return notes


#: Each indicator's notes, from its stored row and its parsed assumptions.
_NOTES: Dict[str, Callable[[Dict[str, Any], Dict[str, Any]], List[str]]] = {
    "reverse_dcf": _reverse_dcf_notes, "epv": _epv_notes, "ncav": _ncav_notes, "ps_value": _ps_value_notes,
    "graham_tangible": _graham_tangible_notes,
}


def _dcf_inputs(a: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """The DCF inputs a reverse DCF row stores (the FCF basis, the share count, the base discount and the terminal
    growth), or None without them or where the DCF can't run on them."""
    fcf, shares, discount, terminal = (_number(a.get(k)) for k in ("fcf_basis", "shares_outstanding", "discount_base",
                                                                   "terminal_growth"))
    if fcf is None or shares is None or discount is None or terminal is None:
        return None
    if fcf <= 0 or shares <= 0 or discount <= terminal:
        return None
    return fcf, shares, discount, terminal


def live_reverse_dcf(row: Optional[Dict[str, Any]], price: Optional[float]) -> Dict[str, Any]:
    """The reverse DCF solved again at the live *price* (``indicators.implied_growth``) from the DCF inputs the stored
    *row* keeps, an N/A row's too: a price out of the growth range when stored may be in it now. Without the inputs,
    the stored N/A reason."""
    if row is None:
        return {"price": price, "growth": None, "na_reason": "not computed yet", "dcf_growth_base": None}
    a = _json(row.get("assumptions"))
    out: Dict[str, Any] = {"price": price, "growth": None, "na_reason": row.get("na_reason") or "no stored DCF inputs",
                           "dcf_growth_base": _number(a.get("dcf_growth_base"))}
    inputs = _dcf_inputs(a)
    if inputs is None:
        return out
    if price is None or price <= 0:
        out["na_reason"] = "no price"
        return out
    fcf, shares, discount, terminal = inputs
    out["growth"], out["na_reason"] = implied_growth(fcf, shares, price, discount, terminal)
    return out


def _indicator_out(row: Dict[str, Any], price: Optional[float]) -> Dict[str, Any]:
    name = row["indicator"]
    a = _json(row.get("assumptions"))
    base = row.get("value_base")
    return {
        "indicator": name,
        "label": INDICATOR_LABELS[name],
        "method": INDICATOR_METHODS[name],
        "applicable": bool(row["applicable"]),
        "na_reason": row.get("na_reason"),
        "unit": row.get("unit"),
        "value_bear": row.get("value_bear"),
        "value_base": base,
        "value_bull": row.get("value_bull"),
        "basis_fiscal_year": row.get("basis_fiscal_year"),
        "assumptions": a,
        # A rate (the reverse DCF's growth) has no upside.
        "upside_pct": upside_pct(base, price) if row.get("unit") == "per_share" else None,
        "notes": _NOTES[name](row, a),
    }


def indicators_view(r: Reader, ticker: str) -> Dict[str, Any]:
    """The ticker's indicators as the API returns them, against the live quote's price; ``computed`` is False before
    any is stored (or before the table exists)."""
    quote = r.quote(ticker)
    price = quote.get("current_price") if quote else None
    stored = {row["indicator"]: row for row in r.valuation_indicators(ticker)}
    rows = [stored[name] for name in INDICATORS if name in stored]
    return {
        "ticker": ticker,
        "price": price,
        "computed": bool(rows),
        "computed_at": max((str(row["computed_at"]) for row in rows), default=None),
        "indicators": [_indicator_out(row, price) for row in rows],
        "reverse_dcf_live": live_reverse_dcf(stored.get("reverse_dcf"), price),
    }

"""The quality scores' view (Milestone 2), shared by ``GET /api/stocks/{t}/scores`` and the STAT tab's Quality
section: the stored scores with their tests, zones and N/A reasons, the five ranks and the composite, the same within
the company's SIC industry group (Milestone 5) or why there are none, the growth table, and the warning signs, severe
first (docs/superpowers/specs/2026-10-21-quality-scores-design.md, docs/superpowers/specs/2026-10-25-universe-
automation-design.md)."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from ..scores.ranks import RANKS
from ..scores.scores import BENEISH_FLAG, PIOTROSKI_TESTS
from ..scores.signs import SEVERE, SIGN_CODES, SLOAN_MEDIUM, SLOAN_SEVERE
from ..universe.industries import MIN_INDUSTRY_SIZE, normalize_sic
from .repository import Reader

#: The nine Piotroski tests, in the spec's order.
PIOTROSKI_LABELS: Dict[str, str] = {
    "roa_positive": "Return on assets above zero",
    "cfo_positive": "Operating cash flow above zero",
    "roa_up": "Return on assets rose",
    "cfo_above_ni": "Operating cash flow above net income",
    "leverage_down": "Long-term debt to assets fell",
    "current_ratio_up": "Current ratio rose",
    "no_new_shares": "No new shares (within 0.5%)",
    "gross_margin_up": "Gross margin rose",
    "asset_turnover_up": "Asset turnover rose",
}
RANK_LABELS: Dict[str, str] = {"strength": "Financial strength", "profitability": "Profitability",
                               "growth": "Growth", "valuation": "Valuation", "momentum": "Momentum"}
#: The growth table's rows, each with its CAGR over GROWTH_SPANS where one is stored.
GROWTH_ROWS: Tuple[Tuple[str, str], ...] = (("rev_ps", "Revenue / share"), ("eps", "EPS"),
                                            ("ebitda_ps", "EBITDA / share"), ("fcf_ps", "FCF / share"),
                                            ("book_ps", "Book / share"))
GROWTH_SPANS: Tuple[str, ...] = ("3y", "5y", "10y")


def sloan_zone(ratio: Optional[float]) -> Optional[str]:
    """The spec's bands: within 10% of assets safe, up to 25% a warning, beyond that flagged."""
    if ratio is None:
        return None
    size = abs(ratio)
    return "safe" if size <= SLOAN_MEDIUM else "warning" if size <= SLOAN_SEVERE else "flagged"


def _json(text: Any) -> Dict[str, Any]:
    try:
        value = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _sign_order(sign: Dict[str, Any]) -> Tuple[int, int]:
    """Severe first, then the signs' published order."""
    code = sign.get("code")
    return (0 if sign.get("severity") == SEVERE else 1,
            SIGN_CODES.index(code) if code in SIGN_CODES else len(SIGN_CODES))


def industry_view(row: Dict[str, Any], sic: Any) -> Dict[str, Any]:
    """The industry block: the company's SIC industry group, its label and size, and the five ranks and the composite
    within it, from its ``quality_scores`` *row*; or why there are none: no code stored (*sic*), a row the rank pass
    hasn't ranked by industry yet (a table from before the industry columns, or not ranked), or an industry with
    fewer than MIN_INDUSTRY_SIZE companies."""
    code = normalize_sic(sic)
    group = row.get("industry_group")
    if group is not None:
        reason = None
    elif code is None:
        reason = "no SIC code stored"
    elif "industry_group" not in row or row.get("ranked_at") is None:
        reason = "industry ranks not computed yet"
    else:
        reason = f"SIC {code}'s industry has fewer than {MIN_INDUSTRY_SIZE} companies here"
    return {"group": group, "label": row.get("industry_label"), "size": row.get("industry_size"), "sic": code,
            "ranks": {k: row.get(f"ind_rank_{k}") for k in RANKS}, "composite": row.get("ind_composite"),
            "na_reason": reason}


def quality_view(r: Reader, ticker: str) -> Dict[str, Any]:
    """The ticker's quality scores as the API returns them; ``computed`` is False before they are."""
    row = r.quality_scores(ticker)
    if row is None:
        return {"ticker": ticker, "computed": False, "na_reason": "not computed yet"}
    na = _json(row.get("na_reasons"))
    tests = _json(row.get("piotroski_tests"))
    m = row.get("beneish_m")
    company = r.get_company(ticker) or {}
    return {
        "ticker": ticker,
        "computed": True,
        "na_reason": None,
        "fiscal_year": row.get("fiscal_year"),
        "computed_at": row.get("computed_at"),
        "ranked_at": row.get("ranked_at"),
        "composite": {"score": row.get("composite_score"), "weights": "equal",
                      "ranks": {k: row.get(f"rank_{k}") for k in RANKS}},
        "industry": industry_view(row, company.get("sic")),
        "piotroski": {"f": row.get("piotroski_f"), "tested": row.get("piotroski_tested"),
                      "tests": [{"code": c, "label": PIOTROSKI_LABELS[c], "passed": tests[c]}
                                for c in PIOTROSKI_TESTS if c in tests],
                      "na_reason": na.get("piotroski")},
        "altman": {"z": row.get("altman_z"), "zone": row.get("altman_zone"), "na_reason": na.get("altman")},
        "beneish": {"m": m, "flagged": m > BENEISH_FLAG if m is not None else None,
                    "variables": _json(row.get("beneish_variables")), "na_reason": na.get("beneish")},
        "sloan": {"ratio": row.get("sloan_ratio"), "zone": sloan_zone(row.get("sloan_ratio")),
                  "na_reason": na.get("sloan")},
        "roic_vs_wacc": {"roic": row.get("roic"), "wacc": row.get("wacc"), "spread": row.get("roic_minus_wacc"),
                         "na_reason": na.get("wacc") or na.get("roic")},
        "predictability": {"stars": row.get("predictability_stars"), "r2": _json(row.get("predictability_r2")),
                           "na_reason": na.get("predictability")},
        "magic_formula": {"rank": row.get("magic_formula_rank"), "earnings_yield": row.get("earnings_yield"),
                          "return_on_capital": row.get("return_on_capital"), "na_reason": na.get("magic_formula")},
        "ncav_per_share": row.get("ncav_per_share"),
        "momentum": row.get("momentum"),
        "growth": [{"key": key, "label": label,
                    **{f"cagr_{span}": row.get(f"{key}_cagr_{span}") for span in GROWTH_SPANS}}
                   for key, label in GROWTH_ROWS],
        "per_share": [{k: v for k, v in ps.items() if k != "ticker"} for ps in r.per_share_annual(ticker)],
        "warning_signs": [{k: s.get(k) for k in ("code", "severity", "message", "value", "threshold")}
                          for s in sorted(r.warning_signs(ticker), key=_sign_order)],
    }

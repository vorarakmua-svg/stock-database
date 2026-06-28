"""Derive canonical fields via accounting identities.

Some universal concepts aren't always tagged directly. Notably many filers (e.g.
Walmart, Duke Energy) never tag ``Liabilities``, yet it equals
``total_assets - total_equity``. Deriving these closes coverage gaps so the
universal core approaches 100% across all companies.

A derived value is only filled when the field is *absent* from a reported tag, and
is marked in the period's ``_source_tags`` as ``"derived"`` so reported vs derived
is always auditable.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple


def _num(period: Dict[str, Any], key: str) -> Optional[float]:
    val = period.get(key)
    return float(val) if isinstance(val, (int, float)) else None


def _total_liabilities(p: Dict[str, Any]) -> Optional[float]:
    assets = _num(p, "total_assets")
    equity = _num(p, "total_equity")
    if assets is not None and equity is not None:
        return assets - equity
    return None


def _gross_profit(p: Dict[str, Any]) -> Optional[float]:
    revenue = _num(p, "revenue")
    cogs = _num(p, "cost_of_revenue")
    if revenue is not None and cogs is not None:
        return revenue - cogs
    return None


def _bank_revenue(p: Dict[str, Any]) -> Optional[float]:
    """Bank total revenue = net interest income + noninterest income.

    Some banks (e.g. Wells Fargo) don't file a single top-line revenue tag. Both
    components present (noninterest income is essentially bank-only) implies a bank.
    """
    nii = _num(p, "net_interest_income")
    noninterest = _num(p, "noninterest_income")
    if nii is not None and noninterest is not None:
        return nii + noninterest
    return None


def _discontinued_ops_cash(p: Dict[str, Any]) -> Optional[float]:
    """Aggregate discontinued-operations cash = sum of the operating/investing/financing
    discontinued components, used when the single aggregate tag is not filed."""
    parts = [
        _num(p, "discontinued_operating_cash_flow"),
        _num(p, "discontinued_investing_cash_flow"),
        _num(p, "discontinued_financing_cash_flow"),
    ]
    present = [v for v in parts if v is not None]
    return sum(present) if present else None


# Ordered list of (canonical_key, fn, formula). Only applied when the key is
# absent. Order matters if a derivation depends on another's output.
DERIVATIONS: List[Tuple[str, Callable[[Dict[str, Any]], Optional[float]], str]] = [
    ("revenue", _bank_revenue, "net_interest_income + noninterest_income"),
    ("total_liabilities", _total_liabilities, "total_assets - total_equity"),
    ("gross_profit", _gross_profit, "revenue - cost_of_revenue"),
    ("cash_from_discontinued_operations", _discontinued_ops_cash,
     "discontinued operating + investing + financing cash flows"),
]


def apply_derivations(period: Dict[str, Any]) -> List[str]:
    """Fill missing derived fields in ``period`` (in place).

    Returns the list of canonical keys that were derived (for logging/auditing).
    Each derived value is recorded in ``period['_source_tags'][key] = 'derived'``.
    """
    derived_keys: List[str] = []
    for key, fn, _formula in DERIVATIONS:
        if period.get(key) is not None:
            continue  # already reported — never overwrite
        value = fn(period)
        if value is not None:
            period[key] = value
            period.setdefault("_source_tags", {})[key] = "derived"
            derived_keys.append(key)
    return derived_keys

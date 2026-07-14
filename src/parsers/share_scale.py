"""Correct share counts that filers tagged in thousands or millions.

SEC XBRL facts are stored as filed, and the scale is not always units. COP
tagged diluted shares as ``1,245,440`` (thousands) through FY2019 and
``1,078,030,000`` (units) from FY2020; MCD tags ``752`` (millions). SEC's own
companyconcept feed sometimes carries BOTH scales for the same period
(``1,491,067`` and ``1,491,067,000``), so the data is wrong at the source and a
faithful parser stores wrong numbers.

Left alone, the discontinuity silently corrupts anything that compares share
counts or per-share figures across years.

The correction is anchored in the filing itself: EPS and net income come from
the same document and are correctly scaled, so ``net_income / eps`` implies the
true share count. A reported count that differs from the implied one by a power
of 1000 is mis-scaled, and the power tells us by how much. Nothing is rescaled
unless the correction lands close to the implied value, so a rounded, near-zero
EPS (which makes the implied count meaningless) never triggers a rescale — the
reported number stands.
"""

import statistics
from typing import Any, Dict, List, Optional

#: Scale corrections worth testing: thousands and millions, in both directions.
_SCALES = (1e-6, 1e-3, 1.0, 1e3, 1e6)

#: A correction is only trusted when it lands within this fraction of the value
#: implied by EPS. Filers round EPS to the cent, so a few percent of drift is
#: normal; anything beyond this means the implied count is not a usable oracle.
_TOLERANCE = 0.10

#: (share field, the EPS field that implies it)
_ORACLE_PAIRS = (
    ("weighted_avg_shares_diluted", "eps_diluted"),
    ("weighted_avg_shares_basic", "eps_basic"),
)

#: Share counts with no EPS of their own; normalized against the corrected
#: weighted-average count for the same period, which they track closely.
_REFERENCE_FIELDS = ("shares_outstanding",)


def _num(period: Dict[str, Any], key: str) -> Optional[float]:
    val = period.get(key)
    return float(val) if isinstance(val, (int, float)) else None


def _best_scale(reported: float, implied: float) -> Optional[float]:
    """The power-of-1000 correction that reconciles *reported* with *implied*.

    Returns None when no correction lands within tolerance (so the implied
    count is not a usable oracle) or when the value is already correct.
    """
    if reported <= 0 or implied <= 0:
        return None
    best = min(_SCALES, key=lambda s: abs((reported * s) / implied - 1.0))
    if best == 1.0:
        return None
    if abs((reported * best) / implied - 1.0) > _TOLERANCE:
        return None
    return best


def _rescale(period: Dict[str, Any], key: str, scale: float,
             rescaled: List[str]) -> None:
    value = _num(period, key)
    if value is None:
        return
    period[key] = value * scale
    period.setdefault("_source_tags", {})[key] = f"rescaled x{scale:g}"
    rescaled.append(key)


def normalize_share_scale(
    periods: Dict[str, Dict[str, Any]]
) -> Dict[str, List[str]]:
    """Fix mis-scaled share counts across every period of one statement.

    Mutates *periods* in place and returns ``{period_key: [rescaled fields]}``
    for the periods that were corrected. Each corrected value is recorded in
    the period's ``_source_tags`` (e.g. ``"rescaled x1000"``) so a reported
    value is never silently overwritten without an audit trail.

    Works across periods rather than one at a time because a year whose EPS is
    missing has no oracle of its own: correcting its siblings and leaving it
    behind would manufacture exactly the 1000x seam this exists to remove, so
    it inherits the scale its corrected siblings agree on.
    """
    rescaled: Dict[str, List[str]] = {}

    # Pass 1: correct against each period's own EPS, the strongest evidence
    # available — it comes from the same filing as the share count.
    for period_key, period in periods.items():
        done: List[str] = []
        for share_key, eps_key in _ORACLE_PAIRS:
            reported = _num(period, share_key)
            eps = _num(period, eps_key)
            net_income = _num(period, "net_income")
            if reported is None or not eps or not net_income:
                continue
            implied = abs(net_income / eps)
            scale = _best_scale(reported, implied)
            if scale is not None:
                _rescale(period, share_key, scale, done)
        if done:
            rescaled[period_key] = done

    # Pass 2: periods with no usable EPS oracle inherit the scale their
    # corrected siblings agree on, so a gap in EPS coverage cannot leave a
    # lone year on the wrong scale.
    diluted_key = "weighted_avg_shares_diluted"
    corrected = [
        v for pk, p in periods.items()
        if (v := _num(p, diluted_key)) is not None
        and diluted_key in rescaled.get(pk, [])
    ]
    if corrected:
        reference = statistics.median(corrected)
        for period_key, period in periods.items():
            if diluted_key in rescaled.get(period_key, []):
                continue  # already corrected against its own EPS
            reported = _num(period, diluted_key)
            if reported is None:
                continue
            scale = _best_scale(reported, reference)
            if scale is not None:
                done = rescaled.setdefault(period_key, [])
                _rescale(period, diluted_key, scale, done)

    # Pass 3: share counts with no EPS of their own follow the corrected
    # weighted-average count for the same period.
    for period_key, period in periods.items():
        anchor = _num(period, diluted_key) or _num(period, "weighted_avg_shares_basic")
        if anchor is None:
            continue
        for key in _REFERENCE_FIELDS:
            reported = _num(period, key)
            if reported is None:
                continue
            scale = _best_scale(reported, anchor)
            if scale is not None:
                done = rescaled.setdefault(period_key, [])
                _rescale(period, key, scale, done)

    return rescaled

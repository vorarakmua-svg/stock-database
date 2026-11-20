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

A count no EPS checks (HSY's are none in the company facts) sets no other count's
scale; it takes the scale its own field's neighbouring periods agree on, when two
or more do, within a factor of two of one another, and it is a power of 1000 from
them, unless another count of its period is on its scale (BUG-329).
"""

import math
import statistics
from typing import Any, Dict, List, Optional, Set, Tuple

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
    periods: Dict[str, Dict[str, Any]], neighbours: bool = True
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

    Without *neighbours*, a count no EPS checks keeps its scale (pass 4 is off):
    the filings' vintages of one year are other filings' views, later ones among
    them, which a filing's own view mustn't read.
    """
    rescaled: Dict[str, List[str]] = {}
    # (period, count) that an EPS checked or corrected siblings put right: only such a
    # count can set another's scale (BUG-329)
    checked: Set[Tuple[str, str]] = set()

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
            if scale is not None or abs(reported / implied - 1.0) <= _TOLERANCE:
                checked.add((period_key, share_key))
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
                checked.add((period_key, diluted_key))

    # Pass 3: share counts with no EPS of their own follow the corrected
    # weighted-average count for the same period: one an EPS checked, as a count
    # nothing checked is no evidence of another's scale (HSY's FY2009 count of
    # 227,998,276 shares read 227,998 beside weighted counts tagged in thousands).
    for period_key, period in periods.items():
        anchor = next((_num(period, key) for key in (diluted_key, "weighted_avg_shares_basic")
                       if (period_key, key) in checked and _num(period, key) is not None), None)
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

    # Pass 4: a filed count no EPS checked takes the scale its own field's
    # neighbouring periods agree on (BUG-329): HSY's FY2011 10-K tags FY2009's
    # diluted count and FY2011's basic count in thousands, and no EPS of HSY's
    # is in the company facts. A count beside another of its period's on its
    # scale stays: the neighbours are then the ones off (HIG's 2010 quarters).
    order = sorted(periods)
    for key in _COUNT_FIELDS if neighbours else ():
        for i, period_key in enumerate(order):
            period = periods[period_key]
            reported = _num(period, key)
            if (reported is None or reported <= 0 or (period_key, key) in checked
                    or key in rescaled.get(period_key, [])
                    or str(period.get("_source_tags", {}).get(key, "")).startswith("derived")):
                continue
            if any(abs(math.log10(reported / v)) <= _BAND for other in _COUNT_FIELDS
                   if other != key and (v := _num(period, other) or 0.0) > 0):
                continue
            before = [v for pk in reversed(order[:i]) if (v := _num(periods[pk], key))][:_NEIGHBOURS]
            after = [v for pk in order[i + 1:] if (v := _num(periods[pk], key))][:_NEIGHBOURS]
            scale = _neighbours_scale(reported, before + after)
            if scale is not None:
                done = rescaled.setdefault(period_key, [])
                _rescale(period, key, scale, done)

    return rescaled


#: The share counts pass 4 checks against their neighbouring periods.
_COUNT_FIELDS = ("weighted_avg_shares_diluted", "weighted_avg_shares_basic", "shares_outstanding")

#: How many periods on each side of a count decide its scale in pass 4.
_NEIGHBOURS = 2

#: Counts within a factor of two of each other (log10) are on one scale in pass 4: a
#: count a power of 1000 off is put right only where that lands it this close to its
#: neighbours (FITB's Q3 2010 diluted count of 6,000,000 beside about 800 million is no
#: scale error; nor are a spin-off's 100 shares beside the hundreds of millions after).
_BAND = math.log10(2.0)


def _neighbours_scale(reported: float, neighbours: List[float]) -> Optional[float]:
    """The power-of-1000 correction that puts *reported* on the scale of *neighbours*.

    Their scale is the one most of them share: two or more, and more than half of
    them, within a factor of two of the median of their logarithms (MSI's 2009
    quarters beside a year-end count on the basis before its reverse split). The
    corrected count must lie within a factor of two of their median, and the
    reported count more than a factor of ten from it. Otherwise nothing is decided.
    """
    logs = [math.log10(v) for v in neighbours if v > 0]
    if len(logs) < 2:
        return None
    near = [x for x in logs if abs(x - statistics.median(logs)) <= _BAND]
    if 2 * len(near) <= len(logs):
        return None  # no scale most of them share
    mid = statistics.median(near)
    if abs(math.log10(reported) - mid) <= 1:
        return None  # the count is near them
    best = min(_SCALES, key=lambda s: abs(math.log10(reported * s) - mid))
    return best if abs(math.log10(reported * best) - mid) <= _BAND else None

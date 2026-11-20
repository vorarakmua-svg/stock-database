"""A filer's annual record, built from its companyfacts alone.

One function of the facts: the fetcher builds the record a run keeps from the companyfacts it read, and an as-of
snapshot replays it on the facts known at a filing's date.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..models.canonical import validate_period
from .derived_fields import apply_derivations
from .share_scale import normalize_share_scale
from .xbrl_parser import XBRLParser

#: The statement the record's share-scale and validation warnings name.
ANNUAL = "financials_annual"
#: The one taxonomy the record's parse reads. ``extract_annual_financials`` reads its facts only through
#: ``_filer_facts``, which takes ``facts["facts"]["us-gaap"]``: no other taxonomy (dei's share counts, srt, ifrs-full,
#: ...) and no top-level key. The record reads the other taxonomies only for their latest filed day (``_last_filed``).
PARSED_TAXONOMY = "us-gaap"


@dataclass
class AnnualRecord:
    """The fiscal years a run keeps (``{fiscal_year: period}``, each period with its ``_source_tags`` and
    ``_source_filings``) and what building them found, in order: the later filings' tagging errors the parse put
    right (BUG-061), the share counts rescaled, the periods that failed validation."""

    periods: Dict[str, Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)
    #: How many of *warnings* lead them as the parse's (the repairs); the rest are the cleaning's. A run adds the
    #: parse's where it reads the facts and the cleaning's where it cleans its other statements.
    repairs: int = 0
    #: The ends of the parse's fiscal years left out as not yet ended on the latest filed day (BUG-113), in the
    #: parse's order: a later day opens those it has reached, with no new fact.
    unended: List[str] = field(default_factory=list)


def _last_filed(facts: Dict[str, Any]) -> str:
    """The latest day a fact in *facts* was filed, of every taxonomy's ("" with none)."""
    return max((entry.get("filed") or "" for concepts in (facts.get("facts") or {}).values()
                for concept in concepts.values() for entries in (concept.get("units") or {}).values()
                for entry in entries), default="")


def clean_annual(annual: Dict[str, Dict[str, Any]], warnings: List[str]) -> Dict[str, Dict[str, Any]]:
    """The fiscal years *annual* cleaned as a run keeps them, the cleaning's warnings added to *warnings* as found:
    their share counts put in units, their identities derived and each validated (a period that fails is kept as it
    was)."""
    # Fix share counts filed in thousands/millions BEFORE anything reads
    # them — derivations, validation and every per-share metric downstream
    # all assume units. Cross-period, so a year with no EPS to anchor on
    # still inherits the scale its siblings agree on.
    for period_key, fields in normalize_share_scale(annual).items():
        warnings.append(
            f"share scale {ANNUAL} {period_key}: rescaled {', '.join(fields)} "
            "(filed in thousands/millions)"
        )
    cleaned = {}
    for period_key, period in annual.items():
        apply_derivations(period)
        clean, errors = validate_period(period)
        cleaned[period_key] = clean
        for err in errors:
            warnings.append(f"validation {ANNUAL} {period_key}: {err}")
    return cleaned


def build_annual_record(parser: XBRLParser, facts: Dict[str, Any], *, ticker: str,
                        years_back: Optional[int]) -> AnnualRecord:
    """*ticker*'s annual record from its companyfacts *facts*: the parser's fiscal years that have ended by the latest
    filing the facts hold (the latest *years_back* of them, or all), cleaned (``clean_annual``). A fiscal year whose
    period ends after that day isn't in the record: a 10-K tagging a figure of the year ahead (BAC's FY2010 10-K, filed
    2011-02-25, a dividend for 2011) opens no year that figure alone holds (BUG-113). A cleaning that fails keeps
    the periods as it left them and says why ("Data cleaning: ..."), as the run always has.

    *years_back* trims the years BEFORE the cleaning, so the share scale (which reads a year's neighbours) sees only
    the kept years; ``build_snapshots`` cleans every year and trims after. A *years_back* run's record can so differ
    from its latest snapshot at the window's edge; a full run's (and the refresh's, which collects full runs) agree."""
    last = _last_filed(facts)
    annual: Dict[str, Dict[str, Any]] = {}
    unended: List[str] = []
    for fy, period in parser.extract_annual_financials(facts).items():
        end = period.pop("_year_end", None) or period.get("period_end")
        if not end or end <= last:
            annual[fy] = period
        else:
            unended.append(end)
    if years_back:
        annual = {fy: annual[fy] for fy in list(annual)[:years_back]}
    warnings: List[str] = []
    # A later filing's tagging error the parser put right (BUG-061).
    for fy, period in annual.items():
        for r in period.pop("_repairs", []):
            warnings.append(
                f"SEC EDGAR: {ticker} FY{fy} {r['field']} is {r['kept']:.4g}, as earlier "
                f"filings report; the filing of {r['filed']} reports {r['dropped']:.4g}, "
                f"which looks like a tagging error ({r['kind']})")
    repairs = len(warnings)
    if not annual:
        return AnnualRecord(annual, warnings, repairs, unended)
    try:
        cleaned = clean_annual(annual, warnings)
    except Exception as e:
        parser.logger.warning(f"Data cleaning error for {ticker}: {e}")
        warnings.append(f"Data cleaning: {str(e)}")
        return AnnualRecord(annual, warnings, repairs, unended)
    return AnnualRecord(cleaned, warnings, repairs, unended)

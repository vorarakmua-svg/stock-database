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


def build_annual_record(parser: XBRLParser, facts: Dict[str, Any], *, ticker: str,
                        years_back: Optional[int]) -> AnnualRecord:
    """*ticker*'s annual record from its companyfacts *facts*: the parser's fiscal years (the latest *years_back*, or
    all), their share counts put in units, their identities derived and each validated. A cleaning that fails keeps
    the periods as it left them and says why ("Data cleaning: ..."), as the run always has."""
    annual = parser.extract_annual_financials(facts, years_back=years_back)
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
        return AnnualRecord(annual, warnings, repairs)
    try:
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
    except Exception as e:
        parser.logger.warning(f"Data cleaning error for {ticker}: {e}")
        warnings.append(f"Data cleaning: {str(e)}")
        return AnnualRecord(annual, warnings, repairs)
    return AnnualRecord(cleaned, warnings, repairs)

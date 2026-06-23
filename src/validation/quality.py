"""Data-quality checks over standardized (canonical) financials.

Validates the comparability the standardization layer promises: required line
items are present, accounting identities hold, sign conventions are respected,
and year-over-year values are continuous. Produces structured findings plus a
0-100 quality score so low-confidence company data can be spotted before it is
used for cross-company comparison.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..mappings.sectors import BANK, GENERAL, INSURANCE, REIT

# Severities and the score penalty each carries.
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
INFO = "info"

_PENALTY = {HIGH: 25, MEDIUM: 10, LOW: 3, INFO: 0}

# Required canonical fields per sector — banks/insurers/REITs report different
# statements, so they must not be penalized for missing operating-company lines.
_GENERAL_REQUIRED = (
    "revenue", "net_income", "operating_income",
    "total_assets", "total_liabilities", "total_equity", "operating_cash_flow",
)
REQUIRED_BY_SECTOR: Dict[str, tuple] = {
    GENERAL: _GENERAL_REQUIRED,
    BANK: (
        "revenue", "net_income", "net_interest_income", "noninterest_income",
        "total_assets", "total_liabilities", "total_equity", "total_deposits",
        "operating_cash_flow",
    ),
    INSURANCE: (
        "revenue", "net_income", "premiums_earned",
        "total_assets", "total_liabilities", "total_equity", "operating_cash_flow",
    ),
    # REIT sub-types tag real estate very differently (property REITs vs tower/data-
    # center REITs that use PP&E), so beyond the universal core we only require the
    # anchors that are genuinely universal.
    REIT: (
        "revenue", "net_income",
        "total_assets", "total_liabilities", "total_equity", "operating_cash_flow",
    ),
}

# Tolerances for accounting-identity checks (fraction of the reference figure).
_BALANCE_TOL = 0.02
_GROSS_PROFIT_TOL = 0.01

# Cash-outflow fields the extractor normalizes to positive magnitudes.
_NONNEGATIVE_FIELDS = ("capex", "dividends_paid", "share_repurchases", "debt_repaid")


@dataclass
class Finding:
    """A single data-quality observation about one fiscal period."""

    severity: str
    code: str
    message: str
    period: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "period": self.period,
        }


@dataclass
class QualityReport:
    score: int = 100
    findings: List[Finding] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"score": self.score, "findings": [f.as_dict() for f in self.findings]}

    def warning_messages(self) -> List[str]:
        """MEDIUM+ findings rendered as warning strings for StockData.warnings."""
        return [
            f"[data-quality/{f.severity}] {f.message}"
            for f in self.findings
            if f.severity in (HIGH, MEDIUM)
        ]


def _num(period: Dict[str, Any], key: str) -> Optional[float]:
    val = period.get(key)
    return val if isinstance(val, (int, float)) else None


def assess_annual(annual: Dict[str, Dict[str, Any]],
                  sector: Optional[str] = None,
                  recent_years: int = 5) -> QualityReport:
    """Assess a company's canonical annual financials.

    Args:
        annual: ``{fiscal_year: {canonical_field: value, ...}}`` as produced by
            ``XBRLParser.extract_annual_financials``.
        sector: Sector class (bank/insurance/reit/...) selecting which fields are
            required, so e.g. a bank isn't penalized for lacking operating_income.
        recent_years: Score only the most recent N fiscal years. Early XBRL filings
            (~2009-2014) are sparse and shouldn't drag down a company's current
            data-quality score now that full history is retained.

    Returns:
        A :class:`QualityReport` with a 0-100 score and structured findings.
    """
    report = QualityReport()
    required_fields = REQUIRED_BY_SECTOR.get(sector or GENERAL, _GENERAL_REQUIRED)

    if not annual:
        report.findings.append(
            Finding(HIGH, "no_financials", "No annual financial data extracted.")
        )
        report.score = 0
        return report

    scored_years = sorted(annual.keys(), reverse=True)[:recent_years]
    for year in scored_years:
        period = annual[year]
        source_tags = period.get("_source_tags", {})

        # Required fields present for this sector
        for key in required_fields:
            if _num(period, key) is None:
                report.findings.append(
                    Finding(MEDIUM, "missing_field",
                            f"Missing required field '{key}'.", year)
                )

        # Accounting identity: Assets == Liabilities + Equity + Non-controlling
        # interest. NCI matters for REITs (operating partnerships) and any group with
        # minority interests, since total_equity is parent-only. Only meaningful when
        # liabilities is *reported* — when derived (A - E) it balances by construction.
        assets = _num(period, "total_assets")
        liabilities = _num(period, "total_liabilities")
        equity = _num(period, "total_equity")
        nci = _num(period, "minority_interest") or 0.0
        liabilities_reported = source_tags.get("total_liabilities") != "derived"
        if (liabilities_reported and assets and liabilities is not None
                and equity is not None and assets != 0):
            right = liabilities + equity + nci
            if abs(assets - right) / abs(assets) > _BALANCE_TOL:
                report.findings.append(
                    Finding(MEDIUM, "balance_sheet_imbalance",
                            f"Assets ({assets:,.0f}) != Liabilities + Equity + NCI "
                            f"({right:,.0f}).", year)
                )

        # Gross profit consistency
        revenue = _num(period, "revenue")
        cogs = _num(period, "cost_of_revenue")
        gross = _num(period, "gross_profit")
        if revenue and cogs is not None and gross is not None and revenue != 0:
            if abs(gross - (revenue - cogs)) / abs(revenue) > _GROSS_PROFIT_TOL:
                report.findings.append(
                    Finding(LOW, "gross_profit_mismatch",
                            "Gross profit != revenue - cost of revenue.", year)
                )

        # Sign conventions: normalized outflows should be non-negative
        for key in _NONNEGATIVE_FIELDS:
            val = _num(period, key)
            if val is not None and val < 0:
                report.findings.append(
                    Finding(LOW, "unexpected_sign",
                            f"'{key}' is negative ({val:,.0f}); expected a magnitude.",
                            year)
                )

        # Plausibility: revenue and assets should be non-negative
        if revenue is not None and revenue < 0:
            report.findings.append(
                Finding(LOW, "negative_revenue", f"Revenue is negative ({revenue:,.0f}).", year)
            )

        # Per-share sanity
        for key in ("eps_basic", "eps_diluted"):
            eps = _num(period, key)
            if eps is not None and abs(eps) > 1000:
                report.findings.append(
                    Finding(LOW, "implausible_eps",
                            f"'{key}' = {eps} is outside a plausible range.", year)
                )

    # Year-over-year revenue continuity (within the scored window)
    years = sorted(scored_years)
    for prev, curr in zip(years, years[1:]):
        r0 = _num(annual[prev], "revenue")
        r1 = _num(annual[curr], "revenue")
        if r0 and r1 and r0 > 0:
            change = (r1 - r0) / r0
            if change < -0.9 or change > 5.0:
                report.findings.append(
                    Finding(INFO, "revenue_discontinuity",
                            f"Revenue changed {change:+.0%} from {prev} to {curr}.", curr)
                )

    penalty = sum(_PENALTY.get(f.severity, 0) for f in report.findings)
    report.score = max(0, 100 - penalty)
    return report

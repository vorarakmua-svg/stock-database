"""Data-quality checks over standardized (canonical) financials.

Validates the comparability the standardization layer promises: required line
items are present, accounting identities hold, sign conventions are respected,
and year-over-year values are continuous. Produces structured findings plus a
0-100 quality score so low-confidence company data can be spotted before it is
used for cross-company comparison.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..mappings.canonical import REQUIRED_FIELDS

# Severities and the score penalty each carries.
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
INFO = "info"

_PENALTY = {HIGH: 25, MEDIUM: 10, LOW: 3, INFO: 0}

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


def assess_annual(annual: Dict[str, Dict[str, Any]]) -> QualityReport:
    """Assess a company's canonical annual financials.

    Args:
        annual: ``{fiscal_year: {canonical_field: value, ...}}`` as produced by
            ``XBRLParser.extract_annual_financials``.

    Returns:
        A :class:`QualityReport` with a 0-100 score and structured findings.
    """
    report = QualityReport()

    if not annual:
        report.findings.append(
            Finding(HIGH, "no_financials", "No annual financial data extracted.")
        )
        report.score = 0
        return report

    for year in sorted(annual.keys(), reverse=True):
        period = annual[year]

        # Required fields present per statement
        for required in REQUIRED_FIELDS.values():
            for key in required:
                if _num(period, key) is None:
                    report.findings.append(
                        Finding(MEDIUM, "missing_field",
                                f"Missing required field '{key}'.", year)
                    )

        # Accounting identity: Assets == Liabilities + Equity
        assets = _num(period, "total_assets")
        liabilities = _num(period, "total_liabilities")
        equity = _num(period, "total_equity")
        if assets and liabilities is not None and equity is not None and assets != 0:
            if abs(assets - (liabilities + equity)) / abs(assets) > _BALANCE_TOL:
                report.findings.append(
                    Finding(MEDIUM, "balance_sheet_imbalance",
                            f"Assets ({assets:,.0f}) != Liabilities + Equity "
                            f"({liabilities + equity:,.0f}).", year)
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

    # Year-over-year revenue continuity
    years = sorted(annual.keys())
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

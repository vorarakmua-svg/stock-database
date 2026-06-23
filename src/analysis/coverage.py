"""Measure how well canonical fields are populated across a company universe.

This is the empirical engine for "standardize across all companies": run it over a
cross-sector basket, see per-field / per-sector fill rates, and find which companies
miss their sector-core fields (and the raw tags they *did* file) so the candidate-tag
lists can be extended until coverage is ~100%.

Pure analysis functions take already-fetched ``companyfacts``/``submissions`` so they
are unit-testable without network. ``coverage_report.py`` wires in live fetching.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..mappings.canonical import CANONICAL_BY_KEY, CANONICAL_FIELDS
from ..mappings.sectors import SPECIALIZED_SECTORS, classify_submissions
from ..parsers.derived_fields import apply_derivations
from ..parsers.unmapped import detect_unmapped
from ..parsers.xbrl_parser import XBRLParser

# Fields every company should have regardless of sector (the comparability anchors).
UNIVERSAL_CORE = (
    "revenue", "net_income", "total_assets",
    "total_liabilities", "total_equity", "operating_cash_flow",
)

REPORTED = "reported"
DERIVED = "derived"
EMPTY = "empty"

# A curated, deterministic cross-sector basket (~40) for the coverage harness.
CURATED_BASKET: Dict[str, List[str]] = {
    "banks": ["JPM", "BAC", "WFC", "C", "GS", "USB"],
    "insurers": ["PGR", "TRV", "ALL", "MET", "PRU", "CB"],
    "reits": ["PLD", "AMT", "EQIX", "SPG", "O", "PSA"],
    "utilities": ["NEE", "DUK", "SO"],
    "energy": ["XOM", "CVX", "COP"],
    "tech": ["AAPL", "MSFT", "GOOGL", "NVDA"],
    "consumer": ["WMT", "COST", "HD", "PG"],
    "health": ["JNJ", "UNH", "PFE", "ABBV"],
    "industrial": ["CAT", "HON", "GE"],
    "telecom": ["VZ", "T"],
}


def basket_tickers() -> List[str]:
    return [t for group in CURATED_BASKET.values() for t in group]


@dataclass
class CompanyCoverage:
    """Per-company field-population status for its latest fiscal year."""

    ticker: str
    sector: str
    fiscal_year: Optional[str]
    calendar_year: Optional[int] = None
    status: Dict[str, str] = field(default_factory=dict)  # key -> reported/derived/empty
    unmapped: List[Dict[str, Any]] = field(default_factory=list)  # material unmapped tags

    def filled(self, key: str) -> bool:
        return self.status.get(key) in (REPORTED, DERIVED)


def analyze(ticker: str, facts: Dict[str, Any], submissions: Optional[Dict[str, Any]],
            parser: Optional[XBRLParser] = None) -> CompanyCoverage:
    """Classify every canonical field as reported / derived / empty for one company."""
    parser = parser or XBRLParser()
    sector = classify_submissions(submissions)
    status = {f.key: EMPTY for f in CANONICAL_FIELDS}

    annual = parser.extract_annual_financials(facts or {}, years_back=1)
    fiscal_year = None
    calendar_year = None
    if annual:
        fiscal_year = sorted(annual.keys())[-1]
        period = dict(annual[fiscal_year])
        apply_derivations(period)
        calendar_year = period.get("calendar_year")
        source_tags = period.get("_source_tags", {})
        for f in CANONICAL_FIELDS:
            if period.get(f.key) is not None:
                status[f.key] = DERIVED if source_tags.get(f.key) == DERIVED else REPORTED

    return CompanyCoverage(ticker, sector, fiscal_year, calendar_year, status,
                           detect_unmapped(facts or {}))


@dataclass
class CoverageSummary:
    n_companies: int
    universal_core: Dict[str, float]                 # field -> fill rate (0..1)
    sector_core: Dict[str, Dict[str, float]]         # sector -> {field -> fill rate}
    gaps: List[str]                                  # human-readable gap lines
    # Unmapped tags ranked by how many companies use them (tag, n_companies,
    # example_value) — an evidence-based worklist of tags to add to the registry.
    unmapped_top: List[tuple] = field(default_factory=list)


def _sector_core_fields(sector: str) -> List[str]:
    return [f.key for f in CANONICAL_FIELDS if sector in f.sectors]


def summarize(coverages: List[CompanyCoverage]) -> CoverageSummary:
    """Aggregate per-company coverage into fill-rate tables and a gap list."""
    n = len(coverages)
    universal = {}
    for key in UNIVERSAL_CORE:
        hits = sum(1 for c in coverages if c.filled(key))
        universal[key] = hits / n if n else 0.0

    by_sector: Dict[str, List[CompanyCoverage]] = defaultdict(list)
    for c in coverages:
        by_sector[c.sector].append(c)

    sector_core: Dict[str, Dict[str, float]] = {}
    for sector in SPECIALIZED_SECTORS:
        members = by_sector.get(sector, [])
        if not members:
            continue
        core = _sector_core_fields(sector)
        sector_core[sector] = {
            key: sum(1 for c in members if c.filled(key)) / len(members)
            for key in core
        }

    gaps: List[str] = []
    for c in coverages:
        missing_universal = [k for k in UNIVERSAL_CORE if not c.filled(k)]
        sector_missing = [k for k in _sector_core_fields(c.sector) if not c.filled(k)]
        if missing_universal or sector_missing:
            parts = []
            if missing_universal:
                parts.append(f"universal: {missing_universal}")
            if sector_missing:
                parts.append(f"{c.sector}-core: {sector_missing}")
            gaps.append(f"{c.ticker} ({c.sector}, FY{c.fiscal_year}) -> " + "; ".join(parts))

    # Aggregate unmapped tags across the universe: how many companies use each, plus
    # an example value. Ranked by frequency = a worklist of tags to map next.
    tag_companies: Dict[str, int] = defaultdict(int)
    tag_example: Dict[str, Any] = {}
    for c in coverages:
        for fact in c.unmapped:
            tag = fact["tag"]
            tag_companies[tag] += 1
            tag_example.setdefault(tag, fact.get("value"))
    unmapped_top = sorted(
        ((tag, cnt, tag_example.get(tag)) for tag, cnt in tag_companies.items()),
        key=lambda x: (x[1], abs(x[2] or 0)), reverse=True,
    )[:25]

    return CoverageSummary(n, universal, sector_core, gaps, unmapped_top)


def format_summary(summary: CoverageSummary) -> str:
    """Render a CoverageSummary as a readable report."""
    lines = ["=" * 64, f"COVERAGE REPORT — {summary.n_companies} companies", "=" * 64]

    lines.append("\nUniversal core (fill rate across all companies):")
    for key in UNIVERSAL_CORE:
        rate = summary.universal_core[key]
        flag = "OK" if rate >= 0.99 else "!!"
        lines.append(f"  [{flag}] {key:22} {rate*100:5.1f}%")

    for sector, fields in summary.sector_core.items():
        lines.append(f"\n{sector.upper()} sector-core (fill rate among {sector} companies):")
        for key, rate in fields.items():
            label = CANONICAL_BY_KEY[key].label
            flag = "OK" if rate >= 0.99 else ("~ " if rate >= 0.5 else "!!")
            lines.append(f"  [{flag}] {key:28} {rate*100:5.1f}%  ({label})")

    lines.append(f"\nGaps ({len(summary.gaps)} companies with missing core/sector-core):")
    if summary.gaps:
        lines.extend(f"  - {g}" for g in summary.gaps)
    else:
        lines.append("  none — full core coverage across the universe")

    if summary.unmapped_top:
        lines.append("\nTop unmapped tags (worklist — tag: #companies, example value):")
        for tag, cnt, example in summary.unmapped_top:
            ex = f"{example/1e9:,.1f}B" if isinstance(example, (int, float)) else "?"
            lines.append(f"  {cnt:>3} cos  {tag}  (e.g. {ex})")

    lines.append("=" * 64)
    return "\n".join(lines)

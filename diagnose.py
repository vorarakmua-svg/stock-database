"""Diagnostic tool for stock data collection issues.

Run: python diagnose.py TICKER
Example: python diagnose.py GOOGL

This will analyze the collected data and identify:
1. Missing critical fields
2. Data quality issues
3. Suggested fixes
"""

import json
import sys
from pathlib import Path


# Critical fields for DCF modeling
CRITICAL_FIELDS = {
    "financials": [
        ("Net Revenue", "Revenue"),
        ("Net Income",),
        ("Operating Income",),
        ("Operating Cash Flow",),
        ("Capital Expenditures",),
        ("Total Assets",),
        ("Total Liabilities",),
        ("Total Stockholders Equity",),
        ("Long-Term Debt", "Long-Term Debt Non-Current"),
        ("Cash and Cash Equivalents",),
    ],
    "calculated": [
        "ebitda",
        "free_cash_flow",
        "roic",
        "net_debt",
        "enterprise_value",
    ],
    "market": [
        "current_price",
        "market_cap",
        "beta",
    ],
}


def load_stock_data(ticker: str) -> dict:
    """Load stock data from JSON file."""
    path = Path(f"data/output/json/{ticker.upper()}.json")
    if not path.exists():
        print(f"[ERROR] File not found: {path}")
        print(f"        Run: python -m src.main {ticker}")
        sys.exit(1)

    with open(path) as f:
        return json.load(f)


def check_financials(data: dict) -> list:
    """Check for missing financial fields."""
    issues = []

    fa = data.get("financials_annual", {})
    if not fa:
        issues.append({
            "severity": "HIGH",
            "field": "financials_annual",
            "problem": "No SEC financial data",
            "fix": "Check if CIK is valid. Company may not file with SEC."
        })
        return issues

    # Get latest year
    years = sorted(fa.keys(), reverse=True)
    latest = fa[years[0]]

    for field_options in CRITICAL_FIELDS["financials"]:
        found = False
        for field in field_options:
            if latest.get(field):
                found = True
                break

        if not found:
            primary = field_options[0]
            issues.append({
                "severity": "MEDIUM",
                "field": primary,
                "problem": f"Missing in SEC data",
                "fix": f"Add XBRL tag alternative to src/mappings/xbrl_tags.py"
            })

    return issues


def check_calculated_metrics(data: dict) -> list:
    """Check calculated metrics."""
    issues = []

    cm = data.get("calculated_metrics", {})
    if not cm:
        issues.append({
            "severity": "HIGH",
            "field": "calculated_metrics",
            "problem": "No calculated metrics",
            "fix": "Check if financials_annual has data"
        })
        return issues

    for field in CRITICAL_FIELDS["calculated"]:
        value = cm.get(field)
        if value is None:
            # Provide specific fix suggestions
            if field == "ebitda":
                fix = "Missing D&A or Operating Income. Check Yahoo valuation.ebitda as fallback."
            elif field == "free_cash_flow":
                fix = "Missing Operating Cash Flow or CapEx in SEC data."
            elif field == "roic":
                fix = "Missing NOPAT or Invested Capital components."
            elif field == "net_debt":
                fix = "Missing debt or cash fields in SEC data."
            elif field == "enterprise_value":
                fix = "Missing market_cap from Yahoo Finance."
            else:
                fix = "Check source data availability."

            issues.append({
                "severity": "MEDIUM",
                "field": field,
                "problem": "Could not calculate",
                "fix": fix
            })

    # Check for Yahoo fallback usage
    if cm.get("ebitda_source") == "yahoo":
        issues.append({
            "severity": "INFO",
            "field": "ebitda",
            "problem": "Using Yahoo EBITDA (SEC D&A not available)",
            "fix": "This is normal for some companies. No action needed."
        })

    return issues


def check_market_data(data: dict) -> list:
    """Check market data from Yahoo."""
    issues = []

    md = data.get("market_data", {})
    if not md:
        issues.append({
            "severity": "HIGH",
            "field": "market_data",
            "problem": "No Yahoo Finance data",
            "fix": "Check ticker symbol. May be delisted or invalid."
        })
        return issues

    for field in CRITICAL_FIELDS["market"]:
        if not md.get(field):
            issues.append({
                "severity": "MEDIUM",
                "field": field,
                "problem": "Missing from Yahoo Finance",
                "fix": "Yahoo may not have this data. Check ticker validity."
            })

    return issues


def check_interest_coverage(data: dict) -> list:
    """Check interest coverage calculation."""
    issues = []

    cm = data.get("calculated_metrics", {})
    fa = data.get("financials_annual", {})

    if not cm.get("interest_coverage") and fa:
        years = sorted(fa.keys(), reverse=True)
        latest = fa[years[0]]

        # Check what interest fields are available
        interest_fields = [k for k in latest.keys() if "interest" in k.lower()]

        if not interest_fields:
            issues.append({
                "severity": "LOW",
                "field": "interest_coverage",
                "problem": "No interest expense data in SEC filings",
                "fix": "Company may have no debt. This is not necessarily an issue."
            })
        else:
            issues.append({
                "severity": "MEDIUM",
                "field": "interest_coverage",
                "problem": f"Interest data exists but not recognized: {interest_fields}",
                "fix": "Add these field names to calculated_metrics.py _calculate_interest_coverage()"
            })

    return issues


def show_available_fields(data: dict):
    """Show all available fields for debugging."""
    print("\n" + "="*60)
    print("AVAILABLE SEC FIELDS (Latest Year)")
    print("="*60)

    fa = data.get("financials_annual", {})
    if fa:
        years = sorted(fa.keys(), reverse=True)
        latest = fa[years[0]]

        # Group by type
        fields = sorted(latest.keys())
        for field in fields:
            value = latest[field]
            if value and field not in ["fiscal_year", "period_end", "form", "filed_date"]:
                if isinstance(value, (int, float)) and abs(value) > 1e6:
                    print(f"  {field}: ${value/1e9:.2f}B")
                else:
                    print(f"  {field}: {value}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python diagnose.py TICKER")
        print("Example: python diagnose.py GOOGL")
        sys.exit(1)

    ticker = sys.argv[1].upper()
    print(f"\n{'='*60}")
    print(f"DIAGNOSING: {ticker}")
    print("="*60)

    # Load data
    data = load_stock_data(ticker)

    # Show basic info
    print(f"\nCompany: {data.get('company_name', 'Unknown')}")
    print(f"CIK: {data.get('cik', 'Not found')}")
    print(f"Data Sources: {data.get('data_sources', [])}")
    print(f"Warnings: {len(data.get('warnings', []))}")
    print(f"Errors: {len(data.get('errors', []))}")

    # Run checks
    all_issues = []
    all_issues.extend(check_financials(data))
    all_issues.extend(check_calculated_metrics(data))
    all_issues.extend(check_market_data(data))
    all_issues.extend(check_interest_coverage(data))

    # Display issues
    if all_issues:
        print(f"\n{'='*60}")
        print(f"ISSUES FOUND: {len(all_issues)}")
        print("="*60)

        # Sort by severity
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
        all_issues.sort(key=lambda x: severity_order.get(x["severity"], 99))

        for issue in all_issues:
            sev = issue["severity"]
            if sev == "HIGH":
                marker = "[!!]"
            elif sev == "MEDIUM":
                marker = "[!]"
            elif sev == "LOW":
                marker = "[?]"
            else:
                marker = "[i]"

            print(f"\n{marker} {issue['field']}")
            print(f"    Problem: {issue['problem']}")
            print(f"    Fix: {issue['fix']}")
    else:
        print(f"\n{'='*60}")
        print("NO ISSUES FOUND - All critical fields present!")
        print("="*60)

    # Show available fields for debugging
    if "--verbose" in sys.argv or "-v" in sys.argv:
        show_available_fields(data)
    else:
        print("\nTip: Run with -v to see all available SEC fields")

    # Quick metrics summary
    cm = data.get("calculated_metrics", {})
    if cm:
        print(f"\n{'='*60}")
        print("KEY METRICS SUMMARY")
        print("="*60)

        def fmt(val, is_pct=False, is_ratio=False):
            if val is None:
                return "N/A"
            if is_pct:
                return f"{val*100:.1f}%"
            if is_ratio:
                return f"{val:.1f}x"
            if abs(val) > 1e12:
                return f"${val/1e12:.2f}T"
            if abs(val) > 1e9:
                return f"${val/1e9:.1f}B"
            return f"${val/1e6:.0f}M"

        print(f"  EBITDA:           {fmt(cm.get('ebitda'))}")
        print(f"  FCF:              {fmt(cm.get('free_cash_flow'))}")
        print(f"  ROIC:             {fmt(cm.get('roic'), is_pct=True)}")
        print(f"  Net Debt:         {fmt(cm.get('net_debt'))}")
        print(f"  EV:               {fmt(cm.get('enterprise_value'))}")
        print(f"  EV/EBITDA:        {fmt(cm.get('ev_to_ebitda'), is_ratio=True)}")
        print(f"  Interest Cov:     {fmt(cm.get('interest_coverage'), is_ratio=True)}")


if __name__ == "__main__":
    main()

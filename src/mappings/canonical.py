"""Canonical financial line items for cross-company comparability.

Different filers report the same economic concept under different US-GAAP XBRL
tags (e.g. Apple's revenue is ``RevenueFromContractWithCustomerExcludingAssessedTax``
while another company uses ``Revenues`` or ``SalesRevenueNet``). To compare across
companies we resolve each concept to a single **canonical field** with a stable
snake_case key, by trying an ordered list of candidate tags and taking the first
one available for a given period.

This registry is the single source of truth for "what standardized fields exist
and how to find them in raw XBRL". The candidate tag lists are seeded from
``xbrl_tags.py`` (PRIORITY_TAGS / ALTERNATIVE_TAGS / XBRL_TAG_MAPPING).
"""

from dataclasses import dataclass
from typing import Dict, Tuple

# Statement groupings
INCOME = "income"
BALANCE = "balance"
CASHFLOW = "cashflow"
PER_SHARE = "per_share"
SHARES = "shares"

# Units, mapped to the keys used inside an XBRL fact's "units" dict.
UNIT_USD = "USD"
UNIT_USD_PER_SHARE = "USD_per_share"
UNIT_SHARES = "shares"

UNIT_TO_XBRL: Dict[str, str] = {
    UNIT_USD: "USD",
    UNIT_USD_PER_SHARE: "USD/shares",
    UNIT_SHARES: "shares",
}

# Period kind: instant facts (balance-sheet snapshots, share counts) have only an
# "end"; duration facts (flows over a period) have "start"+"end".
DURATION = "duration"
INSTANT = "instant"

# Sign normalization. "as_reported" keeps the filed value; "abs" stores the
# magnitude so outflow concepts (capex, dividends, buybacks, repayments) are
# comparable regardless of whether a filer signs them negative.
SIGN_AS_REPORTED = "as_reported"
SIGN_ABS = "abs"


@dataclass(frozen=True)
class CanonicalField:
    """One standardized line item and how to resolve it from raw XBRL."""

    key: str
    label: str
    statement: str
    unit: str
    kind: str
    tags: Tuple[str, ...]
    sign: str = SIGN_AS_REPORTED
    description: str = ""

    @property
    def xbrl_unit(self) -> str:
        """The key to look up inside a fact's ``units`` dict (e.g. 'USD')."""
        return UNIT_TO_XBRL[self.unit]


# Ordered registry. Order is cosmetic for output; per-field `tags` order is what
# drives resolution priority (first available tag wins for a period).
CANONICAL_FIELDS: Tuple[CanonicalField, ...] = (
    # ---------------- Income statement (duration) ----------------
    CanonicalField(
        "revenue", "Revenue", INCOME, UNIT_USD, DURATION,
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
        description="Total net revenue / sales.",
    ),
    CanonicalField(
        "cost_of_revenue", "Cost of Revenue", INCOME, UNIT_USD, DURATION,
        ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"),
    ),
    CanonicalField(
        "gross_profit", "Gross Profit", INCOME, UNIT_USD, DURATION,
        ("GrossProfit",),
    ),
    CanonicalField(
        "operating_expenses", "Operating Expenses", INCOME, UNIT_USD, DURATION,
        ("OperatingExpenses",),
    ),
    CanonicalField(
        "rd_expense", "R&D Expense", INCOME, UNIT_USD, DURATION,
        ("ResearchAndDevelopmentExpense",),
    ),
    CanonicalField(
        "sga_expense", "SG&A Expense", INCOME, UNIT_USD, DURATION,
        ("SellingGeneralAndAdministrativeExpense",),
    ),
    CanonicalField(
        "operating_income", "Operating Income", INCOME, UNIT_USD, DURATION,
        ("OperatingIncomeLoss",),
    ),
    CanonicalField(
        "interest_expense", "Interest Expense", INCOME, UNIT_USD, DURATION,
        (
            "InterestExpense",
            "InterestExpenseDebt",
            "InterestAndDebtExpense",
            "InterestExpenseBorrowings",
            "InterestExpenseOther",
            "InterestCostsIncurred",
        ),
    ),
    CanonicalField(
        "pretax_income", "Pre-Tax Income", INCOME, UNIT_USD, DURATION,
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
    ),
    CanonicalField(
        "income_tax_expense", "Income Tax Expense", INCOME, UNIT_USD, DURATION,
        ("IncomeTaxExpenseBenefit",),
    ),
    CanonicalField(
        "net_income", "Net Income", INCOME, UNIT_USD, DURATION,
        (
            "NetIncomeLoss",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
            "NetIncomeLossAttributableToParent",
            "ProfitLoss",
        ),
    ),

    # ---------------- Per share (duration) ----------------
    CanonicalField(
        "eps_basic", "EPS Basic", PER_SHARE, UNIT_USD_PER_SHARE, DURATION,
        ("EarningsPerShareBasic",),
    ),
    CanonicalField(
        "eps_diluted", "EPS Diluted", PER_SHARE, UNIT_USD_PER_SHARE, DURATION,
        ("EarningsPerShareDiluted",),
    ),
    CanonicalField(
        "dividends_per_share", "Dividends Per Share", PER_SHARE, UNIT_USD_PER_SHARE, DURATION,
        ("CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"),
    ),

    # ---------------- Balance sheet (instant) ----------------
    CanonicalField(
        "total_assets", "Total Assets", BALANCE, UNIT_USD, INSTANT, ("Assets",),
    ),
    CanonicalField(
        "current_assets", "Current Assets", BALANCE, UNIT_USD, INSTANT, ("AssetsCurrent",),
    ),
    CanonicalField(
        "cash_and_equivalents", "Cash and Equivalents", BALANCE, UNIT_USD, INSTANT,
        ("CashAndCashEquivalentsAtCarryingValue", "Cash", "CashEquivalentsAtCarryingValue"),
    ),
    CanonicalField(
        "short_term_investments", "Short-Term Investments", BALANCE, UNIT_USD, INSTANT,
        ("ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"),
    ),
    CanonicalField(
        "accounts_receivable", "Accounts Receivable", BALANCE, UNIT_USD, INSTANT,
        ("AccountsReceivableNetCurrent", "AccountsReceivableNet", "ReceivablesNetCurrent"),
    ),
    CanonicalField(
        "inventory", "Inventory", BALANCE, UNIT_USD, INSTANT, ("InventoryNet",),
    ),
    CanonicalField(
        "ppe_net", "Property, Plant & Equipment (Net)", BALANCE, UNIT_USD, INSTANT,
        ("PropertyPlantAndEquipmentNet",),
    ),
    CanonicalField(
        "goodwill", "Goodwill", BALANCE, UNIT_USD, INSTANT, ("Goodwill",),
    ),
    CanonicalField(
        "intangible_assets", "Intangible Assets", BALANCE, UNIT_USD, INSTANT,
        ("IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"),
    ),
    CanonicalField(
        "total_liabilities", "Total Liabilities", BALANCE, UNIT_USD, INSTANT, ("Liabilities",),
    ),
    CanonicalField(
        "current_liabilities", "Current Liabilities", BALANCE, UNIT_USD, INSTANT,
        ("LiabilitiesCurrent",),
    ),
    CanonicalField(
        "accounts_payable", "Accounts Payable", BALANCE, UNIT_USD, INSTANT,
        ("AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"),
    ),
    CanonicalField(
        "long_term_debt", "Long-Term Debt", BALANCE, UNIT_USD, INSTANT,
        ("LongTermDebtNoncurrent", "LongTermDebt"),
    ),
    CanonicalField(
        "short_term_debt", "Short-Term Debt", BALANCE, UNIT_USD, INSTANT,
        ("DebtCurrent", "ShortTermBorrowings", "LongTermDebtCurrent"),
    ),
    CanonicalField(
        "commercial_paper", "Commercial Paper", BALANCE, UNIT_USD, INSTANT, ("CommercialPaper",),
    ),
    CanonicalField(
        "total_equity", "Total Stockholders Equity", BALANCE, UNIT_USD, INSTANT,
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ),
    CanonicalField(
        "retained_earnings", "Retained Earnings", BALANCE, UNIT_USD, INSTANT,
        ("RetainedEarningsAccumulatedDeficit",),
    ),
    CanonicalField(
        "additional_paid_in_capital", "Additional Paid-In Capital", BALANCE, UNIT_USD, INSTANT,
        ("AdditionalPaidInCapital", "AdditionalPaidInCapitalCommonStock",
         "CommonStocksIncludingAdditionalPaidInCapital"),
    ),
    CanonicalField(
        "treasury_stock", "Treasury Stock", BALANCE, UNIT_USD, INSTANT, ("TreasuryStockValue",),
    ),

    # ---------------- Cash flow (duration) ----------------
    CanonicalField(
        "operating_cash_flow", "Operating Cash Flow", CASHFLOW, UNIT_USD, DURATION,
        ("NetCashProvidedByUsedInOperatingActivities",
         "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    ),
    CanonicalField(
        "investing_cash_flow", "Investing Cash Flow", CASHFLOW, UNIT_USD, DURATION,
        ("NetCashProvidedByUsedInInvestingActivities",
         "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"),
    ),
    CanonicalField(
        "financing_cash_flow", "Financing Cash Flow", CASHFLOW, UNIT_USD, DURATION,
        ("NetCashProvidedByUsedInFinancingActivities",
         "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"),
    ),
    CanonicalField(
        "capex", "Capital Expenditures", CASHFLOW, UNIT_USD, DURATION,
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
        sign=SIGN_ABS, description="Stored as a positive magnitude (cash outflow).",
    ),
    CanonicalField(
        "depreciation_amortization", "Depreciation & Amortization", CASHFLOW, UNIT_USD, DURATION,
        ("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
         "DepreciationAmortizationAndOther", "DepreciationAndAmortization", "Depreciation"),
    ),
    CanonicalField(
        "stock_based_comp", "Stock-Based Compensation", CASHFLOW, UNIT_USD, DURATION,
        ("ShareBasedCompensation",),
    ),
    CanonicalField(
        "dividends_paid", "Dividends Paid", CASHFLOW, UNIT_USD, DURATION,
        ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
        sign=SIGN_ABS, description="Stored as a positive magnitude (cash outflow).",
    ),
    CanonicalField(
        "share_repurchases", "Share Repurchases", CASHFLOW, UNIT_USD, DURATION,
        ("PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"),
        sign=SIGN_ABS, description="Stored as a positive magnitude (cash outflow).",
    ),
    CanonicalField(
        "debt_issued", "Long-Term Debt Issued", CASHFLOW, UNIT_USD, DURATION,
        ("ProceedsFromIssuanceOfLongTermDebt",),
    ),
    CanonicalField(
        "debt_repaid", "Long-Term Debt Repaid", CASHFLOW, UNIT_USD, DURATION,
        ("RepaymentsOfLongTermDebt",),
        sign=SIGN_ABS, description="Stored as a positive magnitude (cash outflow).",
    ),

    # ---------------- Shares ----------------
    CanonicalField(
        "shares_outstanding", "Shares Outstanding", SHARES, UNIT_SHARES, INSTANT,
        ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"),
    ),
    CanonicalField(
        "weighted_avg_shares_basic", "Weighted Avg Shares (Basic)", SHARES, UNIT_SHARES, DURATION,
        ("WeightedAverageNumberOfSharesOutstandingBasic",),
    ),
    CanonicalField(
        "weighted_avg_shares_diluted", "Weighted Avg Shares (Diluted)", SHARES, UNIT_SHARES, DURATION,
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    ),
)

# key -> CanonicalField
CANONICAL_BY_KEY: Dict[str, CanonicalField] = {f.key: f for f in CANONICAL_FIELDS}

# Required fields per statement, used by the data-quality layer to score
# completeness (the core comparables an analyst expects every filer to report).
REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    INCOME: ("revenue", "net_income", "operating_income"),
    BALANCE: ("total_assets", "total_liabilities", "total_equity"),
    CASHFLOW: ("operating_cash_flow",),
}

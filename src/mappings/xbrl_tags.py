"""
XBRL US-GAAP taxonomy tag mappings to human-readable field names.

This module provides comprehensive mappings for SEC EDGAR XBRL data,
converting complex GAAP tags to simple, standardized field names.
"""

# Full XBRL tag mapping with metadata
XBRL_TAG_MAPPING = {
    # ============== BALANCE SHEET - ASSETS ==============
    "Assets": {
        "simple_name": "Total Assets",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "AssetsCurrent": {
        "simple_name": "Current Assets",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "CashAndCashEquivalentsAtCarryingValue": {
        "simple_name": "Cash and Cash Equivalents",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "ShortTermInvestments": {
        "simple_name": "Short-Term Investments",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "AccountsReceivableNetCurrent": {
        "simple_name": "Accounts Receivable",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "InventoryNet": {
        "simple_name": "Inventory",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "PropertyPlantAndEquipmentNet": {
        "simple_name": "Property Plant and Equipment Net",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "Goodwill": {
        "simple_name": "Goodwill",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "IntangibleAssetsNetExcludingGoodwill": {
        "simple_name": "Intangible Assets",
        "category": "balance_sheet",
        "subcategory": "assets",
    },

    # ============== BALANCE SHEET - LIABILITIES ==============
    "Liabilities": {
        "simple_name": "Total Liabilities",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "LiabilitiesCurrent": {
        "simple_name": "Current Liabilities",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "AccountsPayableCurrent": {
        "simple_name": "Accounts Payable",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "LongTermDebt": {
        "simple_name": "Long-Term Debt",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "LongTermDebtNoncurrent": {
        "simple_name": "Long-Term Debt Noncurrent",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "LongTermDebtCurrent": {
        "simple_name": "Current Portion of Long-Term Debt",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "DebtCurrent": {
        "simple_name": "Short-Term Debt",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },

    # ============== BALANCE SHEET - EQUITY ==============
    "StockholdersEquity": {
        "simple_name": "Total Stockholders Equity",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "CommonStockValue": {
        "simple_name": "Common Stock",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "RetainedEarningsAccumulatedDeficit": {
        "simple_name": "Retained Earnings",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "TreasuryStockValue": {
        "simple_name": "Treasury Stock",
        "category": "balance_sheet",
        "subcategory": "equity",
    },

    # ============== INCOME STATEMENT ==============
    "Revenues": {
        "simple_name": "Revenue",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "RevenueFromContractWithCustomerExcludingAssessedTax": {
        "simple_name": "Net Revenue",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "CostOfRevenue": {
        "simple_name": "Cost of Revenue",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "CostOfGoodsAndServicesSold": {
        "simple_name": "Cost of Goods Sold",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "GrossProfit": {
        "simple_name": "Gross Profit",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "OperatingExpenses": {
        "simple_name": "Operating Expenses",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "ResearchAndDevelopmentExpense": {
        "simple_name": "R&D Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "SellingGeneralAndAdministrativeExpense": {
        "simple_name": "SG&A Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "OperatingIncomeLoss": {
        "simple_name": "Operating Income",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "InterestExpense": {
        "simple_name": "Interest Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestIncome": {
        "simple_name": "Interest Income",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "IncomeTaxExpenseBenefit": {
        "simple_name": "Income Tax Expense",
        "category": "income_statement",
        "subcategory": "taxes",
    },
    "NetIncomeLoss": {
        "simple_name": "Net Income",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "EarningsPerShareBasic": {
        "simple_name": "EPS Basic",
        "category": "income_statement",
        "subcategory": "per_share",
    },
    "EarningsPerShareDiluted": {
        "simple_name": "EPS Diluted",
        "category": "income_statement",
        "subcategory": "per_share",
    },

    # ============== CASH FLOW STATEMENT ==============
    "NetCashProvidedByUsedInOperatingActivities": {
        "simple_name": "Operating Cash Flow",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "NetCashProvidedByUsedInInvestingActivities": {
        "simple_name": "Investing Cash Flow",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "NetCashProvidedByUsedInFinancingActivities": {
        "simple_name": "Financing Cash Flow",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "PaymentsToAcquirePropertyPlantAndEquipment": {
        "simple_name": "Capital Expenditures",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "DepreciationDepletionAndAmortization": {
        "simple_name": "Depreciation and Amortization",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "Depreciation": {
        "simple_name": "Depreciation",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "PaymentsOfDividends": {
        "simple_name": "Dividends Paid",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "PaymentsOfDividendsCommonStock": {
        "simple_name": "Common Dividends Paid",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "PaymentsForRepurchaseOfCommonStock": {
        "simple_name": "Stock Repurchases",
        "category": "cash_flow",
        "subcategory": "financing",
    },

    # ============== SHARES ==============
    "CommonStockSharesOutstanding": {
        "simple_name": "Shares Outstanding",
        "category": "shares",
        "subcategory": "common",
    },
    "CommonStockSharesIssued": {
        "simple_name": "Shares Issued",
        "category": "shares",
        "subcategory": "common",
    },
    "WeightedAverageNumberOfSharesOutstandingBasic": {
        "simple_name": "Weighted Avg Shares Basic",
        "category": "shares",
        "subcategory": "weighted",
    },
    "WeightedAverageNumberOfDilutedSharesOutstanding": {
        "simple_name": "Weighted Avg Shares Diluted",
        "category": "shares",
        "subcategory": "weighted",
    },
}

# Simple lookup: XBRL tag -> human-readable name
XBRL_SIMPLE_MAPPING = {
    tag: info["simple_name"]
    for tag, info in XBRL_TAG_MAPPING.items()
}

# Reverse mapping: simple name -> XBRL tag
SIMPLE_TO_XBRL = {
    info["simple_name"]: tag
    for tag, info in XBRL_TAG_MAPPING.items()
}

# Tags grouped by category
XBRL_BY_CATEGORY = {}
for tag, info in XBRL_TAG_MAPPING.items():
    category = info["category"]
    if category not in XBRL_BY_CATEGORY:
        XBRL_BY_CATEGORY[category] = {}
    XBRL_BY_CATEGORY[category][tag] = info

# Priority list of tags to extract (most important first)
PRIORITY_TAGS = [
    # Core financials
    "Revenues",
    "NetIncomeLoss",
    "GrossProfit",
    "OperatingIncomeLoss",

    # Balance sheet
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "CashAndCashEquivalentsAtCarryingValue",
    "LongTermDebt",

    # Cash flow
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "DepreciationDepletionAndAmortization",

    # Per share
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",

    # Shares
    "CommonStockSharesOutstanding",
]

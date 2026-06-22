"""
XBRL US-GAAP taxonomy tag mappings to human-readable field names.

This module provides comprehensive mappings for SEC EDGAR XBRL data,
converting complex GAAP tags to simple, standardized field names.

Updated with alternative tags used by different companies (e.g., Apple, Microsoft, Tesla).
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
    "AssetsNoncurrent": {
        "simple_name": "Non-Current Assets",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    # Cash - Multiple alternative tags
    "CashAndCashEquivalentsAtCarryingValue": {
        "simple_name": "Cash and Cash Equivalents",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "Cash": {
        "simple_name": "Cash",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "CashCashEquivalentsAndShortTermInvestments": {
        "simple_name": "Cash and Short-Term Investments",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "CashEquivalentsAtCarryingValue": {
        "simple_name": "Cash Equivalents",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    # Investments
    "ShortTermInvestments": {
        "simple_name": "Short-Term Investments",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "MarketableSecuritiesCurrent": {
        "simple_name": "Marketable Securities Current",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "AvailableForSaleSecuritiesCurrent": {
        "simple_name": "Available For Sale Securities",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "LongTermInvestments": {
        "simple_name": "Long-Term Investments",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "MarketableSecuritiesNoncurrent": {
        "simple_name": "Marketable Securities Non-Current",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    # Receivables - Multiple alternatives
    "AccountsReceivableNetCurrent": {
        "simple_name": "Accounts Receivable",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "AccountsReceivableNet": {
        "simple_name": "Accounts Receivable Net",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "ReceivablesNetCurrent": {
        "simple_name": "Receivables Net",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "AccountsNotesAndLoansReceivableNetCurrent": {
        "simple_name": "Notes and Loans Receivable",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    # Inventory
    "InventoryNet": {
        "simple_name": "Inventory",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "InventoryFinishedGoods": {
        "simple_name": "Finished Goods Inventory",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "InventoryRawMaterials": {
        "simple_name": "Raw Materials Inventory",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "InventoryWorkInProcess": {
        "simple_name": "Work In Process Inventory",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    # Property, Plant & Equipment
    "PropertyPlantAndEquipmentNet": {
        "simple_name": "Property Plant and Equipment Net",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "PropertyPlantAndEquipmentGross": {
        "simple_name": "Property Plant and Equipment Gross",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment": {
        "simple_name": "Accumulated Depreciation PPE",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    # Intangibles
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
    "GoodwillAndIntangibleAssetsNet": {
        "simple_name": "Goodwill and Intangibles",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "FiniteLivedIntangibleAssetsNet": {
        "simple_name": "Finite-Lived Intangibles",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "IndefiniteLivedIntangibleAssetsExcludingGoodwill": {
        "simple_name": "Indefinite-Lived Intangibles",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    # Other Assets
    "PrepaidExpenseAndOtherAssetsCurrent": {
        "simple_name": "Prepaid Expenses",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "OtherAssetsCurrent": {
        "simple_name": "Other Current Assets",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "OtherAssetsNoncurrent": {
        "simple_name": "Other Non-Current Assets",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "DeferredTaxAssetsNetCurrent": {
        "simple_name": "Deferred Tax Assets Current",
        "category": "balance_sheet",
        "subcategory": "assets",
    },
    "DeferredTaxAssetsNetNoncurrent": {
        "simple_name": "Deferred Tax Assets Non-Current",
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
    "LiabilitiesNoncurrent": {
        "simple_name": "Non-Current Liabilities",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    # Payables
    "AccountsPayableCurrent": {
        "simple_name": "Accounts Payable",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "AccountsPayableAndAccruedLiabilitiesCurrent": {
        "simple_name": "Accounts Payable and Accrued",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "AccruedLiabilitiesCurrent": {
        "simple_name": "Accrued Liabilities",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    # Debt - Multiple alternatives
    "LongTermDebt": {
        "simple_name": "Long-Term Debt",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "LongTermDebtNoncurrent": {
        "simple_name": "Long-Term Debt Non-Current",
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
    "ShortTermBorrowings": {
        "simple_name": "Short-Term Borrowings",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "LongTermDebtAndCapitalLeaseObligations": {
        "simple_name": "Long-Term Debt and Leases",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "LongTermDebtAndCapitalLeaseObligationsCurrent": {
        "simple_name": "Current Debt and Leases",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "DebtAndCapitalLeaseObligations": {
        "simple_name": "Total Debt and Leases",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "FinanceLeaseLiability": {
        "simple_name": "Finance Lease Liability",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "OperatingLeaseLiability": {
        "simple_name": "Operating Lease Liability",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "ConvertibleDebt": {
        "simple_name": "Convertible Debt",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "CommercialPaper": {
        "simple_name": "Commercial Paper",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    # Deferred
    "DeferredRevenueCurrent": {
        "simple_name": "Deferred Revenue Current",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "DeferredRevenueNoncurrent": {
        "simple_name": "Deferred Revenue Non-Current",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "ContractWithCustomerLiabilityCurrent": {
        "simple_name": "Contract Liability Current",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "DeferredTaxLiabilitiesNoncurrent": {
        "simple_name": "Deferred Tax Liabilities",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    # Other Liabilities
    "OtherLiabilitiesCurrent": {
        "simple_name": "Other Current Liabilities",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },
    "OtherLiabilitiesNoncurrent": {
        "simple_name": "Other Non-Current Liabilities",
        "category": "balance_sheet",
        "subcategory": "liabilities",
    },

    # ============== BALANCE SHEET - EQUITY ==============
    "StockholdersEquity": {
        "simple_name": "Total Stockholders Equity",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {
        "simple_name": "Total Equity Including NCI",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "CommonStockValue": {
        "simple_name": "Common Stock",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "CommonStocksIncludingAdditionalPaidInCapital": {
        "simple_name": "Common Stock and APIC",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "AdditionalPaidInCapital": {
        "simple_name": "Additional Paid-In Capital",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "AdditionalPaidInCapitalCommonStock": {
        "simple_name": "APIC Common Stock",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "RetainedEarningsAccumulatedDeficit": {
        "simple_name": "Retained Earnings",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "AccumulatedOtherComprehensiveIncomeLossNetOfTax": {
        "simple_name": "Accumulated Other Comprehensive Income",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "TreasuryStockValue": {
        "simple_name": "Treasury Stock",
        "category": "balance_sheet",
        "subcategory": "equity",
    },
    "MinorityInterest": {
        "simple_name": "Non-Controlling Interest",
        "category": "balance_sheet",
        "subcategory": "equity",
    },

    # ============== INCOME STATEMENT - REVENUE ==============
    # Primary Revenue Tags (companies use different ones)
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
    "RevenueFromContractWithCustomerIncludingAssessedTax": {
        "simple_name": "Gross Revenue",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "SalesRevenueNet": {
        "simple_name": "Net Sales Revenue",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "SalesRevenueGoodsNet": {
        "simple_name": "Product Revenue",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "SalesRevenueServicesNet": {
        "simple_name": "Service Revenue",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "RevenueFromSaleOfGoods": {
        "simple_name": "Revenue From Goods",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "RevenueFromRenderingOfServices": {
        "simple_name": "Revenue From Services",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    # Other Income
    "InterestAndDividendIncomeOperating": {
        "simple_name": "Interest and Dividend Income",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "NoninterestIncome": {
        "simple_name": "Non-Interest Income",
        "category": "income_statement",
        "subcategory": "revenue",
    },

    # ============== INCOME STATEMENT - COSTS & EXPENSES ==============
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
    "CostOfGoodsSold": {
        "simple_name": "COGS",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "CostOfServices": {
        "simple_name": "Cost of Services",
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
    "SellingAndMarketingExpense": {
        "simple_name": "Sales and Marketing Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "GeneralAndAdministrativeExpense": {
        "simple_name": "G&A Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "DepreciationAndAmortization": {
        "simple_name": "Depreciation and Amortization",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "AmortizationOfIntangibleAssets": {
        "simple_name": "Amortization of Intangibles",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "ImpairmentOfLongLivedAssetsHeldForUse": {
        "simple_name": "Impairment Charges",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "RestructuringCharges": {
        "simple_name": "Restructuring Charges",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "OtherOperatingIncomeExpenseNet": {
        "simple_name": "Other Operating Income/Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },

    # ============== INCOME STATEMENT - PROFITABILITY ==============
    "OperatingIncomeLoss": {
        "simple_name": "Operating Income",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": {
        "simple_name": "Pre-Tax Income",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments": {
        "simple_name": "Income Before Tax",
        "category": "income_statement",
        "subcategory": "profit",
    },
    # Interest - Comprehensive coverage for different reporting styles
    "InterestExpense": {
        "simple_name": "Interest Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestExpenseDebt": {
        "simple_name": "Interest Expense on Debt",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestExpenseBorrowings": {
        "simple_name": "Interest Expense Borrowings",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestExpenseRelatedParty": {
        "simple_name": "Interest Expense Related Party",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestExpenseOther": {
        "simple_name": "Interest Expense Other",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestAndDebtExpense": {
        "simple_name": "Interest and Debt Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestCostsIncurred": {
        "simple_name": "Interest Costs Incurred",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestPaidNet": {
        "simple_name": "Interest Paid Net",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "InterestPaid": {
        "simple_name": "Interest Paid",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "InterestIncomeExpenseNet": {
        "simple_name": "Net Interest Income/Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestIncomeExpenseNonoperatingNet": {
        "simple_name": "Net Non-Operating Interest",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "InterestIncome": {
        "simple_name": "Interest Income",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "InterestIncomeOther": {
        "simple_name": "Other Interest Income",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    "InterestIncomeOperating": {
        "simple_name": "Interest Income Operating",
        "category": "income_statement",
        "subcategory": "revenue",
    },
    # Other Income/Expense
    "OtherNonoperatingIncomeExpense": {
        "simple_name": "Other Non-Operating Income/Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    "NonoperatingIncomeExpense": {
        "simple_name": "Non-Operating Income/Expense",
        "category": "income_statement",
        "subcategory": "expenses",
    },
    # Taxes
    "IncomeTaxExpenseBenefit": {
        "simple_name": "Income Tax Expense",
        "category": "income_statement",
        "subcategory": "taxes",
    },
    "CurrentIncomeTaxExpenseBenefit": {
        "simple_name": "Current Income Tax",
        "category": "income_statement",
        "subcategory": "taxes",
    },
    "DeferredIncomeTaxExpenseBenefit": {
        "simple_name": "Deferred Income Tax",
        "category": "income_statement",
        "subcategory": "taxes",
    },
    "EffectiveIncomeTaxRateContinuingOperations": {
        "simple_name": "Effective Tax Rate",
        "category": "income_statement",
        "subcategory": "taxes",
    },
    # Net Income
    "NetIncomeLoss": {
        "simple_name": "Net Income",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "NetIncomeLossAvailableToCommonStockholdersBasic": {
        "simple_name": "Net Income to Common",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "NetIncomeLossAttributableToParent": {
        "simple_name": "Net Income Attributable to Parent",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "ProfitLoss": {
        "simple_name": "Profit/Loss",
        "category": "income_statement",
        "subcategory": "profit",
    },
    "ComprehensiveIncomeNetOfTax": {
        "simple_name": "Comprehensive Income",
        "category": "income_statement",
        "subcategory": "profit",
    },

    # ============== INCOME STATEMENT - PER SHARE ==============
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
    "CommonStockDividendsPerShareDeclared": {
        "simple_name": "Dividends Per Share",
        "category": "income_statement",
        "subcategory": "per_share",
    },
    "CommonStockDividendsPerShareCashPaid": {
        "simple_name": "Dividends Per Share Paid",
        "category": "income_statement",
        "subcategory": "per_share",
    },

    # ============== CASH FLOW STATEMENT ==============
    # Operating Activities
    "NetCashProvidedByUsedInOperatingActivities": {
        "simple_name": "Operating Cash Flow",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations": {
        "simple_name": "Operating Cash Flow Continuing",
        "category": "cash_flow",
        "subcategory": "operating",
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
    "DepreciationAmortizationAndAccretionNet": {
        "simple_name": "Depreciation and Amortization",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "DepreciationAmortizationAndOther": {
        "simple_name": "Depreciation and Amortization",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "DepreciationNonproduction": {
        "simple_name": "Depreciation",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "DeferredIncomeTaxesAndTaxCredits": {
        "simple_name": "Deferred Taxes",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "ShareBasedCompensation": {
        "simple_name": "Stock-Based Compensation",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "IncreaseDecreaseInAccountsReceivable": {
        "simple_name": "Change in Accounts Receivable",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "IncreaseDecreaseInInventories": {
        "simple_name": "Change in Inventories",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "IncreaseDecreaseInAccountsPayable": {
        "simple_name": "Change in Accounts Payable",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    # Investing Activities
    "NetCashProvidedByUsedInInvestingActivities": {
        "simple_name": "Investing Cash Flow",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations": {
        "simple_name": "Investing Cash Flow Continuing",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "PaymentsToAcquirePropertyPlantAndEquipment": {
        "simple_name": "Capital Expenditures",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "PaymentsToAcquireProductiveAssets": {
        "simple_name": "CapEx Productive Assets",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "PaymentsToAcquireBusinessesNetOfCashAcquired": {
        "simple_name": "Acquisitions",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "PaymentsToAcquireInvestments": {
        "simple_name": "Investment Purchases",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "ProceedsFromSaleOfAvailableForSaleSecuritiesDebt": {
        "simple_name": "Investment Sales",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "ProceedsFromMaturitiesPrepaymentsAndCallsOfAvailableForSaleSecurities": {
        "simple_name": "Investment Maturities",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    "PaymentsForProceedsFromOtherInvestingActivities": {
        "simple_name": "Other Investing Activities",
        "category": "cash_flow",
        "subcategory": "investing",
    },
    # Financing Activities
    "NetCashProvidedByUsedInFinancingActivities": {
        "simple_name": "Financing Cash Flow",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations": {
        "simple_name": "Financing Cash Flow Continuing",
        "category": "cash_flow",
        "subcategory": "financing",
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
    "PaymentsForRepurchaseOfEquity": {
        "simple_name": "Equity Repurchases",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "ProceedsFromIssuanceOfCommonStock": {
        "simple_name": "Stock Issuance Proceeds",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "ProceedsFromStockOptionsExercised": {
        "simple_name": "Stock Option Proceeds",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "ProceedsFromIssuanceOfLongTermDebt": {
        "simple_name": "Debt Issuance Proceeds",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "RepaymentsOfLongTermDebt": {
        "simple_name": "Debt Repayments",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    "ProceedsFromRepaymentsOfCommercialPaper": {
        "simple_name": "Commercial Paper Net",
        "category": "cash_flow",
        "subcategory": "financing",
    },
    # Free Cash Flow Components
    "FreeCashFlow": {
        "simple_name": "Free Cash Flow",
        "category": "cash_flow",
        "subcategory": "operating",
    },
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect": {
        "simple_name": "Net Change in Cash",
        "category": "cash_flow",
        "subcategory": "operating",
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
    "CommonStockSharesAuthorized": {
        "simple_name": "Shares Authorized",
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
    "PreferredStockSharesOutstanding": {
        "simple_name": "Preferred Shares Outstanding",
        "category": "shares",
        "subcategory": "preferred",
    },
    "TreasuryStockShares": {
        "simple_name": "Treasury Shares",
        "category": "shares",
        "subcategory": "treasury",
    },

    # ============== RATIOS & METRICS ==============
    "EntityCommonStockSharesOutstanding": {
        "simple_name": "Entity Shares Outstanding",
        "category": "shares",
        "subcategory": "common",
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
# Includes alternatives for maximum coverage
PRIORITY_TAGS = [
    # Revenue - Multiple alternatives (Apple uses RevenueFromContractWithCustomerExcludingAssessedTax)
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",

    # Core Profitability
    "NetIncomeLoss",
    "GrossProfit",
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",

    # Costs
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "OperatingExpenses",
    "ResearchAndDevelopmentExpense",
    "SellingGeneralAndAdministrativeExpense",

    # Balance Sheet - Assets
    "Assets",
    "AssetsCurrent",
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsAndShortTermInvestments",
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
    "AccountsReceivableNetCurrent",
    "InventoryNet",
    "PropertyPlantAndEquipmentNet",
    "Goodwill",
    "IntangibleAssetsNetExcludingGoodwill",

    # Balance Sheet - Liabilities
    "Liabilities",
    "LiabilitiesCurrent",
    "AccountsPayableCurrent",
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "DebtCurrent",
    "ShortTermBorrowings",
    "CommercialPaper",
    "DeferredRevenueCurrent",
    "ContractWithCustomerLiabilityCurrent",

    # Balance Sheet - Equity
    "StockholdersEquity",
    "RetainedEarningsAccumulatedDeficit",
    "AccumulatedOtherComprehensiveIncomeLossNetOfTax",
    "TreasuryStockValue",
    "AdditionalPaidInCapital",

    # Cash Flow
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInInvestingActivities",
    "NetCashProvidedByUsedInFinancingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "DepreciationDepletionAndAmortization",
    "ShareBasedCompensation",
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfDividends",
    "PaymentsForRepurchaseOfCommonStock",
    "ProceedsFromIssuanceOfLongTermDebt",
    "RepaymentsOfLongTermDebt",

    # Per Share
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
    "CommonStockDividendsPerShareDeclared",

    # Shares
    "CommonStockSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfDilutedSharesOutstanding",

    # Interest & Taxes - Multiple alternatives for coverage
    "InterestExpense",
    "InterestExpenseDebt",
    "InterestAndDebtExpense",
    "InterestPaidNet",
    "InterestPaid",
    "InterestIncome",
    "InterestIncomeExpenseNet",
    "IncomeTaxExpenseBenefit",
]

# Alternative tag groups - when primary tag is missing, try alternatives
ALTERNATIVE_TAGS = {
    "Revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "Cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
        "Cash",
        "CashEquivalentsAtCarryingValue",
    ],
    "Debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "DebtAndCapitalLeaseObligations",
        "LongTermDebtAndCapitalLeaseObligations",
    ],
    "Receivables": [
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
        "ReceivablesNetCurrent",
    ],
    "COGS": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
    ],
    "NetIncome": [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLossAttributableToParent",
        "ProfitLoss",
    ],
    "OperatingCashFlow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "CapEx": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "Dividends": [
        "PaymentsOfDividends",
        "PaymentsOfDividendsCommonStock",
    ],
    "Buybacks": [
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
    ],
    "InterestExpense": [
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestAndDebtExpense",
        "InterestExpenseBorrowings",
        "InterestCostsIncurred",
        "InterestPaidNet",
        "InterestPaid",
    ],
}

"""Calculated financial metrics for DCF modeling and investment analysis.

Provides automatic calculation of derived metrics from SEC/Yahoo data:
- Free Cash Flow (FCF)
- EBITDA
- ROIC (Return on Invested Capital)
- Net Debt
- Interest Coverage Ratio
- Working Capital
- Enterprise Value
- And more
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .metric_utils import field_value


class CalculatedMetrics:
    """
    Calculator for derived financial metrics.

    Takes raw financial data from SEC EDGAR and Yahoo Finance
    and computes key metrics for DCF modeling and investment analysis.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize the calculator.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)

    def calculate_all(
        self,
        financials: Dict[str, Any],
        market_data: Optional[Dict[str, Any]] = None,
        valuation: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Calculate all derived metrics from financial data.

        Args:
            financials: Dictionary of financial statement data (from SEC)
            market_data: Optional market data (from Yahoo)
            valuation: Optional valuation metrics (from Yahoo)

        Returns:
            Dictionary with calculated metrics
        """
        metrics = {
            "calculated_at": datetime.now().isoformat(),
        }

        # Core profitability metrics
        metrics["ebitda"] = self._calculate_ebitda(financials)

        # Use Yahoo EBITDA as fallback if SEC calculation failed
        if metrics["ebitda"] is None and valuation:
            yahoo_ebitda = valuation.get("ebitda")
            if yahoo_ebitda:
                metrics["ebitda"] = yahoo_ebitda
                metrics["ebitda_source"] = "yahoo"
        metrics["ebit"] = self._calculate_ebit(financials)
        metrics["nopat"] = self._calculate_nopat(financials)

        # Cash flow metrics
        metrics["free_cash_flow"] = self._calculate_fcf(financials)
        metrics["fcf_margin"] = self._calculate_fcf_margin(financials, metrics["free_cash_flow"])
        metrics["levered_fcf"] = self._calculate_levered_fcf(financials)

        # Capital structure metrics
        metrics["net_debt"] = self._calculate_net_debt(financials)
        metrics["total_debt"] = self._calculate_total_debt(financials)
        metrics["working_capital"] = self._calculate_working_capital(financials)
        metrics["invested_capital"] = self._calculate_invested_capital(financials)

        # Return metrics
        metrics["roic"] = self._calculate_roic(financials, metrics["nopat"], metrics["invested_capital"])
        metrics["roa"] = self._calculate_roa(financials)
        metrics["roe"] = self._calculate_roe(financials)

        # Coverage ratios
        metrics["interest_coverage"] = self._calculate_interest_coverage(financials)
        metrics["debt_to_ebitda"] = self._calculate_debt_to_ebitda(
            metrics["total_debt"], metrics["ebitda"]
        )

        # Efficiency ratios
        metrics["asset_turnover"] = self._calculate_asset_turnover(financials)
        metrics["inventory_turnover"] = self._calculate_inventory_turnover(financials)
        metrics["receivables_turnover"] = self._calculate_receivables_turnover(financials)
        metrics["days_sales_outstanding"] = self._calculate_dso(metrics["receivables_turnover"])
        metrics["days_inventory_outstanding"] = self._calculate_dio(metrics["inventory_turnover"])

        # Margin analysis
        metrics["gross_margin"] = self._calculate_gross_margin(financials)
        metrics["operating_margin"] = self._calculate_operating_margin(financials)
        metrics["net_margin"] = self._calculate_net_margin(financials)
        metrics["ebitda_margin"] = self._calculate_ebitda_margin(financials, metrics["ebitda"])

        # Valuation metrics (if market data available)
        if market_data:
            metrics["enterprise_value"] = self._calculate_ev(
                market_data, metrics["net_debt"]
            )
            metrics["ev_to_ebitda"] = self._calculate_ev_to_ebitda(
                metrics["enterprise_value"], metrics["ebitda"]
            )
            metrics["ev_to_revenue"] = self._calculate_ev_to_revenue(
                metrics["enterprise_value"], financials
            )
            metrics["ev_to_fcf"] = self._calculate_ev_to_fcf(
                metrics["enterprise_value"], metrics["free_cash_flow"]
            )
            metrics["fcf_yield"] = self._calculate_fcf_yield(
                metrics["free_cash_flow"], market_data
            )

        return metrics

    def calculate_historical(
        self,
        annual_financials: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Calculate metrics for multiple years of historical data.

        Args:
            annual_financials: Dictionary keyed by fiscal year

        Returns:
            Dictionary with calculated metrics for each year
        """
        historical_metrics = {}

        for year, financials in annual_financials.items():
            try:
                metrics = self.calculate_all(financials)
                historical_metrics[year] = metrics
            except Exception as e:
                self.logger.warning(f"Error calculating metrics for {year}: {e}")
                historical_metrics[year] = {"error": str(e)}

        return historical_metrics

    # ========== Core Profitability Metrics ==========

    def _calculate_ebitda(self, financials: Dict[str, Any]) -> Optional[float]:
        """
        Calculate EBITDA = Operating Income + Depreciation & Amortization.

        Falls back to: Net Income + Interest + Taxes + D&A
        """
        operating_income = self._get_value(financials, ["operating_income"])
        da = self._get_value(financials, ["depreciation_amortization"])

        if operating_income is not None and da is not None:
            return operating_income + da

        # Fallback calculation
        net_income = self._get_value(financials, ["net_income"])
        interest = self._get_value(financials, ["interest_expense"])
        taxes = self._get_value(financials, ["income_tax_expense"])

        if all(v is not None for v in [net_income, da]):
            result = net_income + (da or 0)
            if interest is not None:
                result += interest
            if taxes is not None:
                result += taxes
            return result

        return None

    def _calculate_ebit(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate EBIT (Operating Income)."""
        return self._get_value(financials, ["operating_income"])

    def _calculate_nopat(self, financials: Dict[str, Any]) -> Optional[float]:
        """
        Calculate NOPAT = Operating Income * (1 - Tax Rate).

        NOPAT (Net Operating Profit After Tax) used in ROIC calculation.
        """
        operating_income = self._get_value(financials, [
            "operating_income"
        ])

        if operating_income is None:
            return None

        # Calculate effective tax rate
        pretax_income = self._get_value(financials, ["pretax_income"])
        tax_expense = self._get_value(financials, ["income_tax_expense"])

        if pretax_income and tax_expense and pretax_income > 0:
            tax_rate = tax_expense / pretax_income
            tax_rate = max(0, min(tax_rate, 0.5))  # Reasonable bounds
        else:
            tax_rate = 0.21  # Default corporate tax rate

        return operating_income * (1 - tax_rate)

    # ========== Cash Flow Metrics ==========

    def _calculate_fcf(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Free Cash Flow = Operating Cash Flow - CapEx."""
        ocf = self._get_value(financials, ["operating_cash_flow"])
        capex = self._get_value(financials, ["capex"])

        if ocf is not None and capex is not None:
            # CapEx is typically negative in cash flow statement
            return ocf - abs(capex)
        elif ocf is not None:
            return ocf  # No CapEx data

        return None

    def _calculate_fcf_margin(
        self, financials: Dict[str, Any], fcf: Optional[float]
    ) -> Optional[float]:
        """Calculate FCF Margin = FCF / Revenue."""
        if fcf is None:
            return None

        revenue = self._get_value(financials, ["revenue"])

        if revenue and revenue > 0:
            return fcf / revenue
        return None

    def _calculate_levered_fcf(self, financials: Dict[str, Any]) -> Optional[float]:
        """
        Calculate Levered FCF = FCF - Interest Expense.

        Levered FCF represents cash available to equity holders.
        """
        fcf = self._calculate_fcf(financials)
        if fcf is None:
            return None

        interest = self._get_value(financials, ["interest_expense"])

        if interest is not None:
            return fcf - abs(interest)
        return fcf

    # ========== Capital Structure Metrics ==========

    def _calculate_net_debt(self, financials: Dict[str, Any]) -> Optional[float]:
        """
        Calculate Net Debt = Total Debt - Cash & Equivalents.

        Negative value means net cash position.
        """
        total_debt = self._calculate_total_debt(financials)
        cash = self._get_value(financials, ["cash_and_equivalents"])

        # Also include marketable securities
        marketable = self._get_value(financials, ["short_term_investments"])

        total_cash = (cash or 0) + (marketable or 0)

        if total_debt is not None:
            return total_debt - total_cash
        return None

    def _calculate_total_debt(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Total Debt = Long-term Debt + Short-term Debt."""
        long_term = self._get_value(financials, ["long_term_debt"])
        short_term = self._get_value(financials, ["short_term_debt"])
        commercial_paper = self._get_value(financials, ["commercial_paper"])

        total = 0
        has_data = False

        if long_term is not None:
            total += long_term
            has_data = True
        if short_term is not None:
            total += short_term
            has_data = True
        if commercial_paper is not None:
            total += commercial_paper
            has_data = True

        return total if has_data else None

    def _calculate_working_capital(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Working Capital = Current Assets - Current Liabilities."""
        current_assets = self._get_value(financials, ["current_assets"])
        current_liabilities = self._get_value(financials, ["current_liabilities"])

        if current_assets is not None and current_liabilities is not None:
            return current_assets - current_liabilities
        return None

    def _calculate_invested_capital(self, financials: Dict[str, Any]) -> Optional[float]:
        """
        Calculate Invested Capital = Total Equity + Total Debt - Cash.

        Or alternatively: Total Assets - Non-operating Assets - Current Liabilities (excl. debt)
        """
        equity = self._get_value(financials, ["total_equity"])
        total_debt = self._calculate_total_debt(financials)
        cash = self._get_value(financials, ["cash_and_equivalents"])

        if equity is not None and total_debt is not None:
            invested = equity + total_debt - (cash or 0)
            return invested if invested > 0 else None

        return None

    # ========== Return Metrics ==========

    def _calculate_roic(
        self,
        financials: Dict[str, Any],
        nopat: Optional[float],
        invested_capital: Optional[float]
    ) -> Optional[float]:
        """
        Calculate ROIC = NOPAT / Invested Capital.

        Key metric for comparing return vs cost of capital.
        """
        if nopat is not None and invested_capital and invested_capital > 0:
            return nopat / invested_capital
        return None

    def _calculate_roa(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Return on Assets = Net Income / Total Assets."""
        net_income = self._get_value(financials, ["net_income"])
        total_assets = self._get_value(financials, ["total_assets"])

        if net_income is not None and total_assets and total_assets > 0:
            return net_income / total_assets
        return None

    def _calculate_roe(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Return on Equity = Net Income / Stockholders Equity."""
        net_income = self._get_value(financials, ["net_income"])
        equity = self._get_value(financials, [
            "total_equity"
        ])

        if net_income is not None and equity and equity > 0:
            return net_income / equity
        return None

    # ========== Coverage Ratios ==========

    def _calculate_interest_coverage(self, financials: Dict[str, Any]) -> Optional[float]:
        """
        Calculate Interest Coverage Ratio = EBIT / Interest Expense.

        Higher is better; below 1.5 is concerning.
        """
        ebit = self._get_value(financials, [
            "operating_income"
        ])
        interest = self._get_value(financials, ["interest_expense"])

        if ebit is not None and interest and interest > 0:
            return ebit / interest
        return None

    def _calculate_debt_to_ebitda(
        self,
        total_debt: Optional[float],
        ebitda: Optional[float]
    ) -> Optional[float]:
        """
        Calculate Debt/EBITDA ratio.

        Lower is better; typically want < 3-4x for investment grade.
        """
        if total_debt is not None and ebitda and ebitda > 0:
            return total_debt / ebitda
        return None

    # ========== Efficiency Ratios ==========

    def _calculate_asset_turnover(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Asset Turnover = Revenue / Total Assets."""
        revenue = self._get_value(financials, ["revenue"])
        total_assets = self._get_value(financials, ["total_assets"])

        if revenue is not None and total_assets and total_assets > 0:
            return revenue / total_assets
        return None

    def _calculate_inventory_turnover(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Inventory Turnover = COGS / Average Inventory."""
        cogs = self._get_value(financials, ["cost_of_revenue"])
        inventory = self._get_value(financials, ["inventory"])

        if cogs is not None and inventory and inventory > 0:
            return cogs / inventory
        return None

    def _calculate_receivables_turnover(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Receivables Turnover = Revenue / Accounts Receivable."""
        revenue = self._get_value(financials, ["revenue"])
        receivables = self._get_value(financials, ["accounts_receivable"])

        if revenue is not None and receivables and receivables > 0:
            return revenue / receivables
        return None

    def _calculate_dso(self, receivables_turnover: Optional[float]) -> Optional[float]:
        """Calculate Days Sales Outstanding = 365 / Receivables Turnover."""
        if receivables_turnover and receivables_turnover > 0:
            return 365 / receivables_turnover
        return None

    def _calculate_dio(self, inventory_turnover: Optional[float]) -> Optional[float]:
        """Calculate Days Inventory Outstanding = 365 / Inventory Turnover."""
        if inventory_turnover and inventory_turnover > 0:
            return 365 / inventory_turnover
        return None

    # ========== Margin Analysis ==========

    def _calculate_gross_margin(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Gross Margin = Gross Profit / Revenue."""
        gross_profit = self._get_value(financials, ["gross_profit"])
        revenue = self._get_value(financials, ["revenue"])

        # If no direct Gross Profit, calculate from Revenue - Cost of Revenue
        if gross_profit is None and revenue:
            cost_of_revenue = self._get_value(financials, ["cost_of_revenue"])
            if cost_of_revenue is not None:
                gross_profit = revenue - cost_of_revenue

        if gross_profit is not None and revenue and revenue > 0:
            return gross_profit / revenue
        return None

    def _calculate_operating_margin(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Operating Margin = Operating Income / Revenue."""
        operating_income = self._get_value(financials, [
            "operating_income"
        ])
        revenue = self._get_value(financials, ["revenue"])

        if operating_income is not None and revenue and revenue > 0:
            return operating_income / revenue
        return None

    def _calculate_net_margin(self, financials: Dict[str, Any]) -> Optional[float]:
        """Calculate Net Margin = Net Income / Revenue."""
        net_income = self._get_value(financials, ["net_income"])
        revenue = self._get_value(financials, ["revenue"])

        if net_income is not None and revenue and revenue > 0:
            return net_income / revenue
        return None

    def _calculate_ebitda_margin(
        self, financials: Dict[str, Any], ebitda: Optional[float]
    ) -> Optional[float]:
        """Calculate EBITDA Margin = EBITDA / Revenue."""
        if ebitda is None:
            return None

        revenue = self._get_value(financials, ["revenue"])

        if revenue and revenue > 0:
            return ebitda / revenue
        return None

    # ========== Valuation Metrics ==========

    def _calculate_ev(
        self, market_data: Dict[str, Any], net_debt: Optional[float]
    ) -> Optional[float]:
        """
        Calculate Enterprise Value = Market Cap + Net Debt.

        EV represents total value of the business.
        """
        market_cap = market_data.get("market_cap")

        if market_cap is not None:
            return market_cap + (net_debt or 0)
        return None

    def _calculate_ev_to_ebitda(
        self, ev: Optional[float], ebitda: Optional[float]
    ) -> Optional[float]:
        """Calculate EV/EBITDA ratio."""
        if ev is not None and ebitda and ebitda > 0:
            return ev / ebitda
        return None

    def _calculate_ev_to_revenue(
        self, ev: Optional[float], financials: Dict[str, Any]
    ) -> Optional[float]:
        """Calculate EV/Revenue ratio."""
        if ev is None:
            return None

        revenue = self._get_value(financials, ["revenue"])

        if revenue and revenue > 0:
            return ev / revenue
        return None

    def _calculate_ev_to_fcf(
        self, ev: Optional[float], fcf: Optional[float]
    ) -> Optional[float]:
        """Calculate EV/FCF ratio."""
        if ev is not None and fcf and fcf > 0:
            return ev / fcf
        return None

    def _calculate_fcf_yield(
        self, fcf: Optional[float], market_data: Dict[str, Any]
    ) -> Optional[float]:
        """Calculate FCF Yield = FCF / Market Cap."""
        market_cap = market_data.get("market_cap")

        if fcf is not None and market_cap and market_cap > 0:
            return fcf / market_cap
        return None

    # ========== Helper Methods ==========

    def _get_value(
        self, data: Dict[str, Any], keys: List[str]
    ) -> Optional[float]:
        """Get value from data dictionary, trying multiple possible keys."""
        return field_value(data, keys)

    def format_metrics_summary(self, metrics: Dict[str, Any]) -> str:
        """
        Format metrics as a readable summary string.

        Args:
            metrics: Dictionary of calculated metrics

        Returns:
            Formatted string summary
        """
        lines = ["=== Calculated Financial Metrics ===\n"]

        # Profitability
        lines.append("PROFITABILITY:")
        if metrics.get("ebitda"):
            lines.append(f"  EBITDA: ${metrics['ebitda']/1e9:.2f}B")
        if metrics.get("ebitda_margin"):
            lines.append(f"  EBITDA Margin: {metrics['ebitda_margin']*100:.1f}%")
        if metrics.get("gross_margin"):
            lines.append(f"  Gross Margin: {metrics['gross_margin']*100:.1f}%")
        if metrics.get("operating_margin"):
            lines.append(f"  Operating Margin: {metrics['operating_margin']*100:.1f}%")
        if metrics.get("net_margin"):
            lines.append(f"  Net Margin: {metrics['net_margin']*100:.1f}%")

        # Cash Flow
        lines.append("\nCASH FLOW:")
        if metrics.get("free_cash_flow"):
            lines.append(f"  Free Cash Flow: ${metrics['free_cash_flow']/1e9:.2f}B")
        if metrics.get("fcf_margin"):
            lines.append(f"  FCF Margin: {metrics['fcf_margin']*100:.1f}%")
        if metrics.get("levered_fcf"):
            lines.append(f"  Levered FCF: ${metrics['levered_fcf']/1e9:.2f}B")

        # Returns
        lines.append("\nRETURNS:")
        if metrics.get("roic"):
            lines.append(f"  ROIC: {metrics['roic']*100:.1f}%")
        if metrics.get("roe"):
            lines.append(f"  ROE: {metrics['roe']*100:.1f}%")
        if metrics.get("roa"):
            lines.append(f"  ROA: {metrics['roa']*100:.1f}%")

        # Capital Structure
        lines.append("\nCAPITAL STRUCTURE:")
        if metrics.get("net_debt"):
            lines.append(f"  Net Debt: ${metrics['net_debt']/1e9:.2f}B")
        if metrics.get("total_debt"):
            lines.append(f"  Total Debt: ${metrics['total_debt']/1e9:.2f}B")
        if metrics.get("working_capital"):
            lines.append(f"  Working Capital: ${metrics['working_capital']/1e9:.2f}B")
        if metrics.get("invested_capital"):
            lines.append(f"  Invested Capital: ${metrics['invested_capital']/1e9:.2f}B")

        # Coverage
        lines.append("\nCOVERAGE:")
        if metrics.get("interest_coverage"):
            lines.append(f"  Interest Coverage: {metrics['interest_coverage']:.1f}x")
        if metrics.get("debt_to_ebitda"):
            lines.append(f"  Debt/EBITDA: {metrics['debt_to_ebitda']:.1f}x")

        # Valuation
        if metrics.get("enterprise_value"):
            lines.append("\nVALUATION:")
            lines.append(f"  Enterprise Value: ${metrics['enterprise_value']/1e12:.2f}T")
            if metrics.get("ev_to_ebitda"):
                lines.append(f"  EV/EBITDA: {metrics['ev_to_ebitda']:.1f}x")
            if metrics.get("ev_to_fcf"):
                lines.append(f"  EV/FCF: {metrics['ev_to_fcf']:.1f}x")
            if metrics.get("fcf_yield"):
                lines.append(f"  FCF Yield: {metrics['fcf_yield']*100:.1f}%")

        return "\n".join(lines)

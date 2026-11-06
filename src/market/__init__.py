"""The market data (Milestone 4): FRED's corporate equities and GDP, Shiller's monthly S&P data with its CAPE, and
the Treasury's daily yield curve, behind the market page.

``xls`` reads Shiller's legacy Excel workbook, ``sources`` parses and fetches the three sources (cached on disk),
``collect`` stores them (``market_series``, ``market_fetches``) and is the CLI. See
docs/superpowers/specs/2026-10-24-valuation-tools-market-design.md ("The market data").
"""

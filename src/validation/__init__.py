"""Data-quality validation for standardized financials."""

from .quality import Finding, assess_annual

__all__ = ["Finding", "assess_annual"]

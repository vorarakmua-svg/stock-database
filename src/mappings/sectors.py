"""Sector classification from SEC SIC codes.

Banks, insurers, and REITs report fundamentally different financial statements,
so they need their own canonical line items and their own "required field" sets
for quality scoring. We classify each company from the SIC code already collected
via ``SECHandler.get_submissions`` (``submissions["sic"]``).

Utilities and energy are tagged for reporting/grouping but use the GENERAL schema
(their statements fit the standard operating-company shape).
"""

from typing import Any, Optional

# Sector classes
GENERAL = "general"
BANK = "bank"
INSURANCE = "insurance"
REIT = "reit"
UTILITY = "utility"
ENERGY = "energy"

ALL_SECTORS = (GENERAL, BANK, INSURANCE, REIT, UTILITY, ENERGY)

# Sectors that get dedicated canonical line items (statements truly differ).
SPECIALIZED_SECTORS = (BANK, INSURANCE, REIT)


def classify_sic(sic: Optional[Any]) -> str:
    """Map a SIC code to a sector class.

    Args:
        sic: SIC code as int or string (e.g. 6021, "6798"). None/unparseable -> GENERAL.

    Returns:
        One of the sector constants. Unknown/operating companies -> ``GENERAL``.
    """
    code = _to_int(sic)
    if code is None:
        return GENERAL

    # Depository institutions, holding companies, broker-dealers, finance.
    if 6020 <= code <= 6299 or code == 6712:
        return BANK
    # Insurance carriers and agents (life, health, P&C, title, surety).
    if 6300 <= code <= 6411:
        return INSURANCE
    # Real estate investment trusts.
    if code == 6798:
        return REIT
    # Electric, gas, water, sanitary services.
    if 4900 <= code <= 4991:
        return UTILITY
    # Oil & gas extraction and petroleum refining.
    if 1300 <= code <= 1399 or code == 2911:
        return ENERGY
    return GENERAL


def classify_submissions(submissions: Optional[dict]) -> str:
    """Convenience: classify from a SEC submissions dict (uses its ``sic``)."""
    if not submissions:
        return GENERAL
    return classify_sic(submissions.get("sic"))


def _to_int(value: Optional[Any]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None

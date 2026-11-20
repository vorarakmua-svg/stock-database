"""SIC codes and industry groups (Milestone 5; docs/superpowers/specs/2026-10-25-universe-automation-design.md, "SIC
codes and industries").

- A company's SIC code is the SEC's, its submissions feed's ``sic``, stored as four ASCII digits (``normalize_sic``)
  with the feed's ``sic_description`` beside it (``normalize_sic_description``).
- Its industry group, among the companies given (the ones being ranked; ``industry_groups``): its 3-digit prefix
  when at least MIN_INDUSTRY_SIZE of them share it, else its 2-digit prefix (the SIC major group) when at least that
  many share it, else none. A group's members (``industry_members``) are the companies whose group it is when
  MIN_INDUSTRY_SIZE or more: a 3-digit group's every company whose code starts with it, a 2-digit group's the
  companies left over outside its 3-digit groups. A 2-digit group with fewer left over takes in its 3-digit groups'
  companies, as the major group does (``group_members``), so a group always has MIN_INDUSTRY_SIZE members or more
  (BUG-238).
- A group's label is drawn from the data (``group_label``): "SIC 283: " and its members' most common SEC
  descriptions, so no SIC table is needed; a 2-digit group with 3-digit groups in it says which it leaves out or
  takes in (``group_scope``): "SIC 28 (outside 283): ", "SIC 38 (with 382, 384): ".
- The screener's industry choices (``industry_options``): every 2- and 3-digit prefix with a company, its size and
  its label.
"""
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: The companies a prefix needs to be an industry group (the spec's 10): fewer, and its companies take the 2-digit
#: group, or none.
MIN_INDUSTRY_SIZE = 10
#: The SEC descriptions a group's label names, the most common first.
LABEL_DESCRIPTIONS = 3
#: A SIC code as read: up to four ASCII digits (never Unicode digits), zero-padded to four.
SIC_PATTERN = re.compile(r"[0-9]{1,4}")
#: A group's prefixes, the first that's large enough taken: the 3-digit industry group, then the 2-digit major group.
GROUP_DIGITS: Tuple[int, ...] = (3, 2)


def normalize_sic(value: Any) -> Optional[str]:
    """*value* (the SEC's "3571", an int, " 3571 ") as a 4-digit code ("100" as "0100"), or None when it's missing or
    isn't up to four ASCII digits (the feed's "" for a company without one)."""
    if value is None:
        return None
    text = str(value).strip()
    return text.zfill(4) if SIC_PATTERN.fullmatch(text) else None


def normalize_sic_description(value: Any) -> Optional[str]:
    """The SEC's description of a code as it writes it, without the spaces around it; None when empty."""
    if value is None:
        return None
    return str(value).strip() or None


def industry_groups(codes: Dict[str, Optional[str]], min_size: int = MIN_INDUSTRY_SIZE) -> Dict[str, Optional[str]]:
    """Each company's industry group among *codes* (ticker -> SIC code, the companies being ranked): its 3-digit
    prefix when at least *min_size* of them share it, else its 2-digit prefix when at least *min_size* share it, else
    None; None too without a code (which counts toward no group)."""
    normal = {ticker: normalize_sic(code) for ticker, code in codes.items()}
    shared = Counter(code[:digits] for code in normal.values() if code is not None for digits in GROUP_DIGITS)
    groups: Dict[str, Optional[str]] = {}
    for ticker, code in normal.items():
        prefixes = [code[:digits] for digits in GROUP_DIGITS] if code is not None else []
        groups[ticker] = next((prefix for prefix in prefixes if shared[prefix] >= min_size), None)
    return groups


def group_members(codes: Dict[str, Optional[str]], group: str) -> List[str]:
    """The companies of *codes* in *group*: every one whose code starts with it, in *codes*' order."""
    return [ticker for ticker, code in codes.items()
            if (normal := normalize_sic(code)) is not None and normal.startswith(group)]


def industry_members(codes: Dict[str, Optional[str]], groups: Dict[str, Optional[str]],
                     min_size: int = MIN_INDUSTRY_SIZE) -> Dict[str, List[str]]:
    """Each group's members among *codes* (*groups*: ``industry_groups``'), in *codes*' order: the companies whose group
    it is, when *min_size* or more (a 3-digit group's are every company whose code starts with it; a 2-digit group's,
    its companies left over outside its 3-digit groups); else every company whose code starts with it, its 3-digit
    groups' companies included (a 2-digit group with fewer left over), so a group always has *min_size* members or
    more (BUG-238)."""
    out: Dict[str, List[str]] = {}
    for group in sorted({g for g in groups.values() if g is not None}):
        own = [ticker for ticker in codes if groups.get(ticker) == group]
        out[group] = own if len(own) >= min_size else group_members(codes, group)
    return out


def group_scope(group: str, groups: Dict[str, Optional[str]], members: Iterable[str]) -> Optional[str]:
    """What a 2-digit *group*'s *members* (``industry_members``) leave out or take in of the 3-digit groups formed
    within it: "outside 283" when they are its left-over companies, "with 382, 384" when those groups' companies are
    among them; None for a 3-digit group or a 2-digit one without a 3-digit group in it (BUG-238)."""
    within = sorted({g for g in groups.values() if g is not None and len(g) > len(group) and g.startswith(group)})
    if not within:
        return None
    taken_in = any(groups.get(ticker) in within for ticker in members)
    return ("with " if taken_in else "outside ") + ", ".join(within)


def group_label(group: str, descriptions: Iterable[Optional[str]], scope: Optional[str] = None) -> str:
    """"SIC 283: " and the members' most common SEC *descriptions*, up to LABEL_DESCRIPTIONS, by count, then
    alphabetically, joined with "; " (a description can hold a comma: "Biological Products, (No Diagnostic
    Substances)"); "SIC 283" alone without any. A *scope* (``group_scope``) follows the code in brackets: "SIC 28
    (outside 283): ..."."""
    counts = Counter(text for text in (normalize_sic_description(d) for d in descriptions) if text is not None)
    named = sorted(counts, key=lambda text: (-counts[text], text))[:LABEL_DESCRIPTIONS]
    code = f"SIC {group} ({scope})" if scope else f"SIC {group}"
    return f"{code}: {'; '.join(named)}" if named else code


def sic_label(code: Any, description: Optional[str]) -> Optional[str]:
    """A company's code for the DES tab: "SIC 3571 Electronic Computers", "SIC 3571" without a description, None
    without a code."""
    sic = normalize_sic(code)
    if sic is None:
        return None
    text = normalize_sic_description(description)
    return f"SIC {sic} {text}" if text else f"SIC {sic}"


def industry_options(companies: Iterable[Tuple[Any, Optional[str]]]) -> List[Dict[str, Any]]:
    """The screener's industry choices from the companies' (code, description) pairs: every 2- and 3-digit prefix
    with a company, in order (a major group before its industry groups), each with its size (the companies whose
    code starts with it) and its label (``group_label``)."""
    coded = [(sic, description) for sic, description in ((normalize_sic(c), d) for c, d in companies)
             if sic is not None]
    prefixes = sorted({sic[:digits] for sic, _ in coded for digits in GROUP_DIGITS})
    options: List[Dict[str, Any]] = []
    for prefix in prefixes:
        members = [description for sic, description in coded if sic.startswith(prefix)]
        options.append({"prefix": prefix, "size": len(members), "label": group_label(prefix, members)})
    return options

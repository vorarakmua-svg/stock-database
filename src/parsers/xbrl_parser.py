"""XBRL data parser for SEC EDGAR financial data."""

import logging
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from ..mappings.canonical import CANONICAL_FIELDS, DURATION, INSTANT, SIGN_ABS, SIGN_NET
from .share_scale import _TOLERANCE as _SCALE_TOLERANCE
from .share_scale import _best_scale

# Duration bounds (in days) used to distinguish annual vs quarterly periods.
# A "full year" span is ~365 days; a quarter is ~91 days. Bounds are generous
# to absorb 52/53-week fiscal calendars, whose quarters run 12 to 17 weeks: a
# 12-12-12-16-week year ends on a 16- or 17-week fourth quarter (COST, PEP: 112
# and 119 days; BUG-095). Two quarters, a gap in the rungs, run 160 or more.
_FULL_YEAR_MIN_DAYS = 350
_FULL_YEAR_MAX_DAYS = 380
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 120

# Forms that carry quarterly and annual statement facts. A foreign issuer's annual
# report (20-F, 40-F) counts as annual; its 6-K interim reports aren't read.
_QUARTERLY_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
#: Forms of filers reporting as foreign private issuers.
_FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A", "6-K"}
_FRAME_YEAR_RE = re.compile(r"CY(\d{4})")
_FRAME_QUARTER_RE = re.compile(r"^CY(\d{4})Q(\d)")
#: Fields reported in dollars: a filer reporting in another currency resolves none.
_DOLLAR_FIELDS = frozenset(f.key for f in CANONICAL_FIELDS if f.xbrl_unit.startswith("USD"))
_FIELDS = {f.key: f for f in CANONICAL_FIELDS}


def _in_dollars(periods: Iterable[Dict[str, Any]]) -> bool:
    """Whether any period holds a dollar figure, not just share counts."""
    return any(_DOLLAR_FIELDS & period.keys() for period in periods)


def _reporting_currency(us_gaap: Dict[str, Any]) -> Optional[str]:
    """The currency most of the filer's monetary facts are in.

    A filer reporting in another currency also tags a US$ convenience
    translation of some figures (TSM: 4,624 TWD facts beside 1,010 USD ones),
    so a few USD facts don't make a dollar record.
    """
    counts: Dict[str, int] = defaultdict(int)
    for tag in us_gaap.values():
        for unit, entries in tag.get("units", {}).items():
            if re.fullmatch(r"[A-Z]{3}", unit):
                counts[unit] += len(entries)
    return max(counts, key=lambda unit: counts[unit]) if counts else None


def _foreign_filings(us_gaap: Dict[str, Any]) -> Dict[str, str]:
    """The filings (accessions) reported in a currency other than dollars, with it:
    those with no more USD facts than facts in another currency. Such a filing's few
    USD facts are a convenience translation (TSM: 4,624 TWD facts beside 1,010 USD
    ones; BUG-065). Decided filing by filing, since a filer can change its reporting
    currency (the Yandex-to-Nebius path: rubles, then dollars; BUG-077)."""
    counts: Dict[str, Counter] = defaultdict(Counter)
    for tag in us_gaap.values():
        for unit, entries in tag.get("units", {}).items():
            if re.fullmatch(r"[A-Z]{3}", unit):
                for entry in entries:
                    counts[entry.get("accn") or ""][unit] += 1
    foreign = {}
    for accn, by_currency in counts.items():
        others = [(n, currency) for currency, n in by_currency.items() if currency != "USD"]
        if others and by_currency.get("USD", 0) <= max(others)[0]:
            foreign[accn] = max(others)[1]
    return foreign


def _dollar_filings(us_gaap: Dict[str, Any]) -> Dict[str, Any]:
    """The filer's facts less those of its filings in another currency (see
    ``_foreign_filings``): a filer that switched to dollars keeps its filings since,
    and one that left dollars its filings before."""
    foreign = _foreign_filings(us_gaap)
    if not foreign:
        return us_gaap
    kept: Dict[str, Any] = {}
    for name, tag in us_gaap.items():
        units = {unit: [e for e in entries if (e.get("accn") or "") not in foreign]
                 for unit, entries in tag.get("units", {}).items()}
        units = {unit: entries for unit, entries in units.items() if entries}
        if units:
            kept[name] = {**tag, "units": units}
    return kept


def _net_outflows_as_paid(us_gaap: Dict[str, Any]) -> Dict[str, Any]:
    """*us_gaap* with its net outflows (``SIGN_NET``: acquisitions, net of the cash acquired)
    as paid: positive a payment, negative cash brought in, as the taxonomy has them. A filing
    whose gross outflows (capex, dividends, buybacks, debt repaid) are mostly below zero signs
    its outflows negative (LLY's and TXN's 2008 10-Qs), and its net outflows are turned back.
    Read by size, a net inflow was an outflow: WFC's FY2008 -$11.2B with Wachovia (BUG-127)."""
    votes: Dict[str, int] = defaultdict(int)
    for field in CANONICAL_FIELDS:
        if field.sign != SIGN_ABS:
            continue
        for tag in field.tags:
            for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
                value = entry.get("val")
                if entry.get("start") and entry.get("accn") and isinstance(value, (int, float)) and value:
                    votes[entry["accn"]] += 1 if value < 0 else -1
    negated = {accn for accn, vote in votes.items() if vote > 0}
    if not negated:
        return us_gaap

    def as_paid(entry: Dict[str, Any]) -> Dict[str, Any]:
        value = entry.get("val")
        if entry.get("accn") in negated and isinstance(value, (int, float)) and value:
            return {**entry, "val": -value}
        return entry

    turned = dict(us_gaap)
    for field in CANONICAL_FIELDS:
        if field.sign != SIGN_NET:
            continue
        for tag in field.tags:
            units = us_gaap.get(tag, {}).get("units", {})
            if field.xbrl_unit in units:
                entries = [as_paid(entry) for entry in units[field.xbrl_unit]]
                turned[tag] = {**us_gaap[tag], "units": {**units, field.xbrl_unit: entries}}
    return turned


#: Tags read only on the dates a filing's balance sheet covers, those it tags the balance
#: sheet's total on (a note's total assets can stand at earlier year ends: PH's 2024 10-K at
#: 2022's): a cash flow statement reconciles cash with restricted cash (ASU 2016-18), so the
#: total also stands at each earlier year end the statement covers; and a note's commercial
#: paper on another day isn't the year's (MCO's FY2016 10-K tags $566M at 2017-02-22, which
#: took FY2017 once its year end's paper was set aside; JNJ's 2024-05-15 made a quarter of its
#: own; BUG-143).
_BALANCE_SHEET_ONLY = ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CommercialPaper")
_BALANCE_SHEET_TOTAL = "LiabilitiesAndStockholdersEquity"


def _on_balance_sheet_dates(us_gaap: Dict[str, Any]) -> Dict[str, Any]:
    """*us_gaap* with the ``_BALANCE_SHEET_ONLY`` tags' facts kept on their filing's balance-sheet
    dates. Cash reads the restricted-cash total last, for balance sheets that tag nothing else
    (GE's FY2025; BUG-139); read from a cash flow statement's opening balances too, it made
    cash-only years (DIS's FY2016) and, in the vintages, replaced a balance sheet's own cash in
    the as-of view (PH's FY2022: $0.536B, its 2024 10-K's $6.648B with the Meggitt escrow)."""
    if not any(tag in us_gaap for tag in _BALANCE_SHEET_ONLY):
        return us_gaap
    dates: Dict[str, Set[str]] = defaultdict(set)
    for entry in us_gaap.get(_BALANCE_SHEET_TOTAL, {}).get("units", {}).get("USD", []):
        if entry.get("accn") and entry.get("end"):
            dates[entry["accn"]].add(entry["end"])
    kept = dict(us_gaap)
    for tag in _BALANCE_SHEET_ONLY:
        if tag in us_gaap:
            units = us_gaap[tag].get("units", {})
            kept[tag] = {**us_gaap[tag], "units": {
                unit: [e for e in entries if e.get("end") in dates.get(e.get("accn") or "", ())]
                for unit, entries in units.items()}}
    return kept


#: LongTermDebt, a total with the current maturities, and the noncurrent figures long-term debt
#: reads after it (``canonical.py``).
_DEBT_TOTAL = "LongTermDebt"
_DEBT_NONCURRENT = ("LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable", "UnsecuredLongTermDebt",
                    "ConvertibleLongTermNotesPayable")


def _by_filing(us_gaap: Dict[str, Any], tag: str) -> Dict[Tuple[str, str], float]:
    """A tag's dollar figures at an instant, by filing and date: ``{(accn, end): value}``."""
    return {(e["accn"], e["end"]): e["val"] for e in us_gaap.get(tag, {}).get("units", {}).get("USD", [])
            if e.get("accn") and e.get("end") and not e.get("start") and isinstance(e.get("val"), (int, float))}


def _debt_totals_set_aside(us_gaap: Dict[str, Any]) -> Dict[str, Any]:
    """*us_gaap* less the LongTermDebt a filing tags beside a noncurrent figure, where its current
    debt holds the current maturities, so the total would count them twice (BA's FY2025 $53.85B
    beside $8.46B of current debt, where its balance sheet has $45.64B), or where it is below the
    noncurrent figure, a note's narrower one (MU's notes without their finance leases; AMT's
    FY2019 $1.9M against $21.13B). Current debt holds them as DebtCurrent or as the maturities,
    above zero, or as short-term borrowings on the same line (AMT's); otherwise the total keeps
    them, as ABBV's FY2021 $75.96B does $12.48B due within a year (BUG-141)."""
    if _DEBT_TOTAL not in us_gaap:
        return us_gaap
    noncurrent: Dict[Tuple[str, str], float] = {}
    for tag in reversed(_DEBT_NONCURRENT):
        noncurrent.update(_by_filing(us_gaap, tag))  # the first tag's figure
    current, borrowings = _by_filing(us_gaap, "DebtCurrent"), _by_filing(us_gaap, "ShortTermBorrowings")
    maturities = _by_filing(us_gaap, "LongTermDebtCurrent")

    def holds_maturities(key: Tuple[str, str]) -> bool:
        if key in current:
            return current[key] > 0
        if key in borrowings:
            return borrowings[key] > 0 and borrowings[key] == maturities.get(key)
        return maturities.get(key, 0) > 0

    aside = {key for key, total in _by_filing(us_gaap, _DEBT_TOTAL).items()
             if key in noncurrent and (total < noncurrent[key] or holds_maturities(key))}
    if not aside:
        return us_gaap
    units = us_gaap[_DEBT_TOTAL].get("units", {})
    kept = [e for e in units.get("USD", []) if (e.get("accn"), e.get("end")) not in aside]
    return {**us_gaap, _DEBT_TOTAL: {**us_gaap[_DEBT_TOTAL], "units": {**units, "USD": kept}}}


#: Short-term debt a refinancing reclassified to long-term debt (ASC 470-10-45-14).
_REFINANCED = "ShortTermDebtRefinancedAmount"
#: The current maturities of long-term debt: a DebtCurrent below them is a part of the
#: balance sheet's current debt, not its total (IFF's FY2022, PH's FY2023).
_MATURITIES = ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent")
#: How far a short-term total can fall short of the commercial paper it holds: a note can tag
#: the paper at its face value, above its carrying amount (FDX's $750M in $745M of borrowings).
#: The nearest figure below the paper otherwise is 5% short of it (IFF's FY2021, a line of its own).
_PAPER_ROUNDING = 0.02
#: The records the extractions build, each reading a commercial paper fact beside the short-term figure it reads
#: itself on the paper's date (``_current_debt_set_aside``): the annual record, from the annual reports; the
#: quarterly checkpoints, from the 10-Qs and 10-Ks; and each filing's vintage, from that filing (BUG-256, BUG-257).
_ANNUAL_VIEW, _QUARTERLY_VIEW, _VINTAGE_VIEW = "annual", "quarterly", "vintage"


def _current_debt_set_aside(us_gaap: Dict[str, Any], view: str) -> Dict[str, Any]:
    """*us_gaap* less a filing's DebtCurrent on a date where it is the short-term debt before a
    refinancing, and with 0.0 for the CommercialPaper the short-term total on its date holds, as
    the record of *view* reads them.

    CVX's DebtCurrent is its note's short-term debt before the part it reclassifies to long-term
    debt, which its long-term debt then holds: it tags that part ShortTermDebtRefinancedAmount, and
    its balance sheet's short-term debt, DebtCurrent less it, ShortTermBorrowings (FY2024: $12.656B
    less $8.25B, $4.406B; BUG-142). DebtCurrent and ShortTermBorrowings hold the paper, which total
    debt added again (PG's FY2026 $38.98B for $34.14B; BUG-143): held, it reads 0.0, no paper
    outside the short-term figure. The total that holds it is the one the record reads beside it,
    the latest-filed DebtCurrent on the paper's date, else ShortTermBorrowings: the annual reports'
    for the annual record (a later 10-K can show an earlier one's paper inside its borrowings: KO's
    FY2019 $10.0B), the 10-Qs' and 10-Ks' for a quarter (a 10-Q's comparative DebtCurrent can hold a
    10-K's paper: NEE's 2011-12-31; BUG-257), and a vintage's own filing's, as known then (KO's
    FY2019 10-K shows its paper apart; BUG-256). The as-of view fills a vintage's gaps from earlier
    ones (BUG-110), so a vintage that shows no short-term figure on the date reads its paper beside
    the latest earlier annual report's (QCOM's FY2016 10-K tags as paper the line its FY2015 10-K
    tags as borrowings), and one that shows a short-term figure but no paper states the latest paper
    an earlier annual report filed there, against its own figure (HON's FY2015 10-K, whose borrowings
    hold its FY2014 10-K's paper: 0.0; AEP's FY2021 10-K, which shows only the maturities). A
    DebtCurrent below its filing's current maturities is a part that holds none (IFF's FY2022);
    DebtCurrent is read as filed, before a refinancing sets it aside. Paper the total is smaller
    than (``_PAPER_ROUNDING``) is still added: a line of its own (AAPL's, beside its term debt),
    above the borrowings (HON's FY2013) or alone (WMB's)."""
    if _REFINANCED not in us_gaap and "CommercialPaper" not in us_gaap:
        return us_gaap
    current, borrowings = _by_filing(us_gaap, "DebtCurrent"), _by_filing(us_gaap, "ShortTermBorrowings")
    maturities: Dict[Tuple[str, str], float] = {}
    for tag in _MATURITIES:
        for key, value in _by_filing(us_gaap, tag).items():
            maturities[key] = max(maturities.get(key, value), value)
    # the part refinanced as an instant on the date or a period ending on it (CVX tags the year's)
    refinanced = {(e["accn"], e["end"]): e["val"] for e in us_gaap.get(_REFINANCED, {}).get("units", {}).get("USD", [])
                  if e.get("accn") and e.get("end") and isinstance(e.get("val"), (int, float))}
    before = {key for key, debt in current.items() if refinanced.get(key, 0) > 0 and key in borrowings
              and _agree(debt - refinanced[key], borrowings[key])}
    # each filing's form and filing date, to read a date's short-term total as the records resolve it
    short_term = _FIELDS["short_term_debt"].tags
    filings = {e["accn"]: (e.get("form") or "", e.get("filed") or "")
               for tag in short_term + ("CommercialPaper",)
               for e in us_gaap.get(tag, {}).get("units", {}).get("USD", []) if e.get("accn")}

    def holder(key: Tuple[str, str]) -> Optional[float]:
        # DebtCurrent as filed: CVX's holds its paper, which the refinancing moves partly to long-term debt
        if key in current:
            return current[key] if current[key] >= maturities.get(key, 0) * (1 - _AGREE) else None
        return borrowings.get(key)

    def holds(key: Optional[Tuple[str, str]], paper: float) -> bool:
        return key is not None and paper > 0 and (holder(key) or 0) >= paper * (1 - _PAPER_ROUNDING)

    def latest(end: str, forms: Set[str]) -> Optional[Tuple[str, str]]:
        # the latest-filed DebtCurrent on *end* among *forms*, else their latest ShortTermBorrowings
        for figures in (current, borrowings):
            keys = [key for key in figures if key[1] == end and filings[key[0]][0] in forms]
            if keys:
                return max(keys, key=lambda k: filings[k[0]][1])
        return None

    papers = _by_filing(us_gaap, "CommercialPaper")
    held: Set[Tuple[str, str]] = set()
    stated: List[Dict[str, Any]] = []
    if view != _VINTAGE_VIEW:
        forms = _ANNUAL_FORMS if view == _ANNUAL_VIEW else _QUARTERLY_FORMS
        held = {key for key, paper in papers.items() if holds(latest(key[1], forms), paper)}
    else:
        # each annual report in filing order (the as-of view's), on each date it shows a paper or a short-term figure
        shows = {key for tag in short_term for key in _by_filing(us_gaap, tag)}
        dates: Dict[str, Set[str]] = defaultdict(set)
        for accn, end in set(papers) | shows:
            if filings[accn][0] in _ANNUAL_FORMS:
                dates[end].add(accn)
        for end in sorted(dates):
            paper: Optional[float] = None  # the latest paper filed on the date
            source: Optional[Tuple[str, str]] = None  # and short-term figure, the as-of view's
            for accn in sorted(dates[end], key=lambda a: (filings[a][1], a)):
                key = (accn, end)
                source = key if key in shows else source
                if key in papers:
                    if holds(source, papers[key]):
                        held.add(key)
                    paper = papers[key]
                elif key in shows and paper is not None and paper > 0:
                    stated.append({"accn": accn, "end": end, "form": filings[accn][0], "filed": filings[accn][1],
                                   "val": 0.0 if holds(key, paper) else paper})
    if not before and not held and not stated:
        return us_gaap
    kept = dict(us_gaap)
    if before:
        units = us_gaap["DebtCurrent"].get("units", {})
        kept["DebtCurrent"] = {**us_gaap["DebtCurrent"], "units": {**units, "USD": [
            e for e in units.get("USD", []) if (e.get("accn"), e.get("end")) not in before]}}
    if held or stated:  # stated first: a filing's own paper in the same year, read after them, wins
        units = us_gaap["CommercialPaper"].get("units", {})
        kept["CommercialPaper"] = {**us_gaap["CommercialPaper"], "units": {**units, "USD": stated + [
            {**e, "val": 0.0} if (e.get("accn"), e.get("end")) in held else e for e in units.get("USD", [])]}}
    return kept


def _filer_facts(facts: Dict[str, Any], view: str) -> Dict[str, Any]:
    """A filer's us-gaap facts as the extractions of *view* read them: its dollar filings', net
    outflows as paid, the balance-sheet-only tags on their balance sheets' dates, LongTermDebt set
    aside where its noncurrent figure should stand (judged on DebtCurrent as filed), then DebtCurrent
    before a refinancing set aside, and 0.0 for the CommercialPaper the short-term total that view's
    record reads on its date holds."""
    return _current_debt_set_aside(_debt_totals_set_aside(_net_outflows_as_paid(_on_balance_sheet_dates(
        _dollar_filings(facts.get("facts", {}).get("us-gaap", {}))))), view)


#: A later filing's figure for a year can be a tagging error of the figure the earlier
#: filings agree on: its negative, a zero, or it times 10^3 or more (STX's FY2024 10-K put
#: FY2022's 2.77 dividend a share at 2,770,000; BUG-061). x10 and x100 are left alone, as
#: a 10:1 split restates by exactly that.
_MIN_SCALE_ERROR = 3
#: Years on each side whose figures decide between the later filing and the earlier ones.
_NEIGHBOUR_YEARS = 2
#: Figures within this fraction of each other agree (filings round differently).
_AGREE = 0.005
#: A year's cash-flow sections, which its net change in cash checks.
_CASH_SECTIONS = ("operating_cash_flow", "investing_cash_flow", "financing_cash_flow")
#: Totals no company reports as zero (BAC's 2011 10-K put FY2008's total assets at 0).
#: A later zero for a line item can be a restatement: T-Mobile's history replaced
#: MetroPCS's after the reverse merger, and Prologis's FY2018 lease liability was off the
#: balance sheet before ASC 842.
_NEVER_ZERO = frozenset({"total_assets", "current_assets", "total_liabilities",
                         "current_liabilities", "total_equity"})
#: Two of a year's revenue figures this far apart can't both be its revenue (BUG-099); closer
#: ones are a restatement or a presentation, which the tag order settles.
_REVENUE_GAP = 1.25
#: Contract revenue including excise taxes only fills a year with no other revenue figure
#: (PM's $75.0B for FY2016 beside $26.7B net of the taxes).
_REVENUE_WITH_TAXES = "RevenueFromContractWithCustomerIncludingAssessedTax"


def _agree(a: float, b: float) -> bool:
    return abs(a - b) <= _AGREE * max(abs(a), abs(b))


def _tagging_error(earlier: float, latest: float, key: str) -> Optional[str]:
    """What a later filing's figure for *key* is of the one the earlier filings agree
    on, when it can be a tagging error: "sign flip", "zero" (of a total) or
    "scale x10^k"; else None."""
    if earlier == 0 or _agree(earlier, latest):
        return None
    if latest == 0:
        return "zero" if key in _NEVER_ZERO else None
    if _agree(earlier, -latest):
        return "sign flip"
    ratio = latest / earlier
    if ratio > 0:
        k = round(math.log10(ratio))
        if abs(k) >= _MIN_SCALE_ERROR and _agree(ratio, 10.0 ** k):
            return f"scale x10^{k}"
    return None


def _years_side_with_earlier(kind: str, earlier: float, latest: float,
                             neighbours: List[float]) -> bool:
    """Whether the years around a conflicting figure side with the earlier filings.

    For a sign flip, every one of them has the earlier figure's sign; for a zero or a
    scale, the earlier figure is within a factor of ten of their median and the later
    one isn't. Fewer than two years decide nothing.
    """
    if len(neighbours) < 2:
        return False
    if kind == "sign flip":
        return all(v != 0 and (v > 0) == (earlier > 0) for v in neighbours)
    if kind == "zero" and 0 in neighbours:
        return False  # the figure is zero in other years too
    logs = [math.log10(abs(v)) for v in neighbours if v != 0]
    if len(logs) < 2:
        return False
    mid = statistics.median(logs)
    return abs(math.log10(abs(earlier)) - mid) <= 1 and (
        latest == 0 or abs(math.log10(abs(latest)) - mid) > 1)


def _cash_adds_up(sections: List[float], period: Dict[str, Any]) -> bool:
    """Whether a year's cash-flow sections sum to its net change in cash, within 1% of
    the largest, with or without the FX effect and discontinued operations (filers
    differ on which the sections include)."""
    fx = period.get("fx_effect_on_cash") or 0.0
    discontinued = period.get("cash_from_discontinued_operations") or 0.0
    scale = max(abs(s) for s in sections) or 1.0
    return any(abs(sum(sections) + extra - period["net_change_in_cash"]) <= 0.01 * scale
               for extra in (0.0, fx, discontinued, fx + discontinued))


#: Units a share split restates: per-share figures and share counts (BUG-082).
_SPLIT_UNITS = ("USD/shares", "shares")
#: The EPS whose rungs, with net income's, tell a weighted count's scale (BUG-092).
_SCALE_ORACLES = {"weighted_avg_shares_diluted": "eps_diluted", "weighted_avg_shares_basic": "eps_basic"}
#: A split's ratio, new shares per old, is a simple fraction of 3:2 or more either way,
#: up to 50:1 (CMG's of 2024). A split restates each count by it exactly, so two filings'
#: counts for a period give it within 0.5%, their rounding; a reverse merger's restated
#: history doesn't (TMUS's, 1.46 to 1.49 times MetroPCS's counts, isn't 3:2).
_SPLIT_RANGE = (1.45, 52.0)
_SPLIT_TOLERANCE = 0.005


def _split_ratio(ratio: float) -> Optional[float]:
    """The share split a later filing's count over an earlier one's for the same period
    reads as (new shares per old), or None."""
    for x, inverse in ((ratio, False), (1 / ratio, True)):
        if _SPLIT_RANGE[0] <= x <= _SPLIT_RANGE[1]:
            fraction = float(Fraction(x).limit_denominator(4))
            if abs(fraction / x - 1) <= _SPLIT_TOLERANCE:
                return 1 / fraction if inverse else fraction
    return None


def _split_restatements(us_gaap: Dict[str, Any]) -> List[Tuple[str, str, float]]:
    """The share splits the filer's own filings restated: ``[(the last filing date on the
    old basis, the first on the new one, new shares per old share)]``.

    A split restates every share count and per-share figure the filings after it report,
    so a count two filings give for one period at a split's ratio shows one between them.
    Two such counts in overlapping windows make a split: on the cached filers, 37 of the
    38 found are Yahoo's splits, and the other is Arconic's 1-for-3 of 2016, which Yahoo's
    history lacks. A lone ratio is a correction or a mis-tag, which restates nothing else.
    """
    pairs: List[Tuple[str, str, float]] = []
    for field in CANONICAL_FIELDS:
        if field.xbrl_unit != "shares":
            continue
        for tag in field.tags:
            by_period: Dict[Tuple[Any, Any], Dict[str, float]] = defaultdict(dict)
            for entry in us_gaap.get(tag, {}).get("units", {}).get("shares", []):
                val, filed = entry.get("val"), entry.get("filed")
                if entry.get("form", "") in _QUARTERLY_FORMS and filed and isinstance(val, (int, float)) and val > 0:
                    by_period[(entry.get("start"), entry.get("end"))].setdefault(filed, float(val))
            for by_filed in by_period.values():
                history = sorted(by_filed.items())
                for (before, old), (after, new) in zip(history, history[1:]):
                    ratio = _split_ratio(new / old)
                    if ratio:
                        pairs.append((before, after, ratio))
    splits: List[List[Any]] = []  # [last old-basis filing, first new-basis filing, ratio, counts]
    for before, after, ratio in sorted(pairs, key=lambda p: (p[1], p[0])):
        for split in splits:
            if split[2] == ratio and before < split[1] and split[0] < after:
                split[0], split[1], split[3] = max(split[0], before), min(split[1], after), split[3] + 1
                break
        else:
            splits.append([before, after, ratio, 1])
    return [(before, after, ratio) for before, after, ratio, counts in splits if counts >= 2]


def explain_no_annual_financials(facts: Optional[Dict[str, Any]],
                                 first_filing: Optional[str] = None) -> str:
    """Why a filer's SEC data yields no annual financials, for the run's warnings.

    An empty record otherwise reads like a short history: XOM's new holding
    company had filed only 10-Qs, and TSM reports in IFRS. ``first_filing`` is
    the date its filings are listed from, when that is known.
    """
    taxonomies = (facts or {}).get("facts", {})

    def forms(taxonomy: str) -> List[str]:
        return sorted({entry["form"] for tag in taxonomies.get(taxonomy, {}).values()
                       for unit in tag.get("units", {}).values() for entry in unit if entry.get("form")})

    if not taxonomies.get("us-gaap"):
        if taxonomies.get("ifrs-full"):
            return f"it reports in IFRS ({', '.join(forms('ifrs-full'))}), which isn't mapped"
        return "SEC has no XBRL financial statements for it"
    us_gaap_forms = set(forms("us-gaap"))
    if not us_gaap_forms & _ANNUAL_FORMS:
        report = "20-F" if us_gaap_forms & _FOREIGN_FORMS else "10-K"
        since = f" (its filings listed from {first_filing})" if first_filing else ""
        return f"it has filed no annual report ({report}) yet{since}"
    if not _dollar_filings(taxonomies["us-gaap"]):
        return (f"it reports in {_reporting_currency(taxonomies['us-gaap'])} ({', '.join(sorted(us_gaap_forms))}), "
                "which isn't converted to dollars")
    return "its annual-report facts give no full fiscal year of a mapped concept"


def explain_stale_annual_financials(facts: Optional[Dict[str, Any]],
                                    latest_period_end: Optional[str]) -> Optional[str]:
    """Why a filer's annual record stops at the period ending *latest_period_end*,
    when its later annual reports are in IFRS (a US-GAAP 20-F filer like TSM, or a
    10-K filer, that moved to IFRS) or in another currency (BUG-077): the record
    otherwise reads as current. None when no such annual report covers a later period
    (comparatives don't count)."""
    if not latest_period_end:
        return None
    taxonomies = (facts or {}).get("facts", {})

    def later(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [e for e in entries if e.get("form") in _ANNUAL_FORMS and str(e.get("end") or "") > latest_period_end]

    ifrs = sorted({e["form"] for tag in taxonomies.get("ifrs-full", {}).values()
                   for unit in tag.get("units", {}).values() for e in later(unit)})
    if ifrs:
        return f"later annual reports are in IFRS ({', '.join(ifrs)}), which isn't mapped"
    us_gaap = taxonomies.get("us-gaap", {})
    foreign = _foreign_filings(us_gaap)
    reports = {(e["form"], foreign[e.get("accn") or ""]) for tag in us_gaap.values()
               for unit in tag.get("units", {}).values() for e in later(unit) if (e.get("accn") or "") in foreign}
    if not reports:
        return None
    currencies = sorted({currency for _, currency in reports})
    forms = sorted({form for form, _ in reports})
    return f"later annual reports are in {', '.join(currencies)} ({', '.join(forms)}), which isn't converted to dollars"


class XBRLParser:
    """
    Parser for XBRL financial data from SEC EDGAR Company Facts API.

    Converts raw XBRL data to structured, human-readable format.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize XBRL parser.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger or logging.getLogger(__name__)

    # ========== Period helpers ==========

    @staticmethod
    def _day_after(value: Optional[str]) -> Optional[str]:
        """The ISO day after *value* (``None`` if it doesn't parse)."""
        day = XBRLParser._parse_iso_date(value)
        return (day + timedelta(days=1)).isoformat() if day is not None else None

    @staticmethod
    def _parse_iso_date(value: Optional[str]) -> Optional[date]:
        """Parse an ISO ``YYYY-MM-DD`` string into a date, or None."""
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def _period_year(self, entry: Dict[str, Any]) -> Optional[int]:
        """Determine the fiscal year a fact *covers*, from the period-**end** year.

        In SEC ``companyfacts`` each fact carries the ``fy`` of the *filing*, not the
        period it represents, so we must derive the year from the fact itself. We use
        the ``end`` date's year because it is consistent across income-statement
        (duration) and balance-sheet (instant) facts of the same fiscal year — both
        end on the fiscal-year-end date. The ``frame`` (e.g. ``CY2025``) is *not*
        used as the primary signal: SEC assigns frames by calendar year, so a
        non-December fiscal year (e.g. a retailer ending Jan 31) would split its
        income statement (``CY2025``) from its balance sheet into different buckets.
        ``frame`` is only a fallback when ``end`` is missing.
        """
        end = self._parse_iso_date(entry.get("end"))
        if end is not None:
            return end.year

        frame = entry.get("frame")
        if frame:
            match = _FRAME_YEAR_RE.search(frame)
            if match:
                return int(match.group(1))
        return None

    def _fiscal_year_from_end(self, end_iso: Optional[str]) -> Optional[int]:
        """Fiscal year a period belongs to, from its period-end date.

        A period's fiscal year is its period-end calendar year, except for a 52/53-week
        filer whose year-end lands in early January (month == 1, day <= 7): that belongs
        to the prior (December) fiscal year. January-end retailers (day 28-31) keep the
        end year. SEC's ``fy`` field is deliberately NOT used — filers mis-stamp it (e.g.
        HON tagged its FY2021 facts as fy=2020).
        """
        end = self._parse_iso_date(end_iso)
        if end is None:
            return None
        if end.month == 1 and end.day <= 7:
            return end.year - 1
        return end.year

    def _span_days(self, entry: Dict[str, Any]) -> Optional[int]:
        """Number of days the fact spans, or None for instant facts (no ``start``)."""
        start = self._parse_iso_date(entry.get("start"))
        end = self._parse_iso_date(entry.get("end"))
        if start is None or end is None:
            return None
        return (end - start).days

    def _is_full_year(self, entry: Dict[str, Any]) -> bool:
        """True for instant facts and for duration facts spanning ~one year.

        Instant facts (balance sheet - no ``start``) always belong to their period
        end year. Duration facts (income/cash flow) are kept only when they cover a
        full year, which excludes the quarterly sub-periods also present in 10-Ks.
        """
        span = self._span_days(entry)
        if span is None:
            return True  # instant fact
        return _FULL_YEAR_MIN_DAYS <= span <= _FULL_YEAR_MAX_DAYS

    @staticmethod
    def _matches_kind(field: Any, entry: Dict[str, Any]) -> bool:
        """A flow (duration) field needs a fact that covers a period.

        ``_is_full_year`` accepts instant facts for the balance sheet, but under a
        duration tag an instant is a single event, not the year's value: BAC tags
        each quarterly dividend declaration that way, and the last one (0.24)
        outranked the full-year 0.92 filed under the fallback tag.
        """
        return field.kind != DURATION or bool(entry.get("start"))

    def _is_quarter(self, entry: Dict[str, Any]) -> bool:
        """True for instant facts and for duration facts spanning ~one quarter."""
        span = self._span_days(entry)
        if span is None:
            return True  # instant fact
        return _QUARTER_MIN_DAYS <= span <= _QUARTER_MAX_DAYS

    def extract_annual_financials(
        self,
        facts: Dict[str, Any],
        years_back: Optional[int] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract standardized annual (10-K) financials, keyed by fiscal year.

        Each period dict uses canonical keys (see ``mappings/canonical.py``) so the
        same concept is comparable across companies regardless of which XBRL tag a
        filer used. A ``_source_tags`` map records the tag each value came from, and
        a ``_repairs`` list any later filing's tagging error put right (see
        ``_restore_agreed_figures``).

        Args:
            facts: Raw company facts
            years_back: Number of fiscal years to keep (most recent first).
                ``None`` (default) keeps all available history.

        Returns:
            Dictionary organized by fiscal year (string keys).
        """
        us_gaap = _filer_facts(facts, _ANNUAL_VIEW)
        if not us_gaap:
            return {}

        data = self._annual_periods(us_gaap)
        if not _in_dollars(data.values()):
            return {}  # share counts alone aren't a record; the run explains the gap

        sorted_years = sorted(data.keys(), reverse=True)
        if years_back:
            sorted_years = sorted_years[:years_back]
        return {str(y): data[y] for y in sorted_years}

    def extract_annual_vintages(
        self,
        facts: Dict[str, Any],
        years_back: Optional[int] = None,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Every filing's view of each annual period: ``{fiscal_year: {accn: period}}``.

        Unlike :meth:`extract_annual_financials` (which collapses to the latest-filed
        value per period), this keeps one period dict per *filing* (accession), so a
        restated year retains both its original and restated vintages. Reads the
        already-fetched ``companyfacts`` (every historical instance carries its own
        ``accn``/``filed``); no extra network. Annual reports (10-K, 20-F, 40-F) only.
        """
        us_gaap = _filer_facts(facts, _VINTAGE_VIEW)
        if not us_gaap:
            return {}

        data: Dict[Any, Dict[str, Any]] = {}
        best: Dict[Any, Tuple[int, str]] = {}
        period_frames: Dict[Any, set] = {}
        set_aside = self._revenue_set_aside(us_gaap)

        for field in CANONICAL_FIELDS:
            for priority, tag in enumerate(field.tags):
                tag_data = us_gaap.get(tag)
                if not tag_data:
                    continue
                for entry in tag_data.get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in _ANNUAL_FORMS:
                        continue
                    if not self._matches_kind(field, entry) or not self._is_full_year(entry):
                        continue
                    fy = self._fiscal_year_from_end(entry.get("end")) or self._period_year(entry)
                    accn = entry.get("accn")
                    if fy is None or not accn:
                        continue
                    period = (fy, accn)
                    frame = entry.get("frame")
                    if frame:
                        period_frames.setdefault(period, set()).add(frame)
                    seed = {
                        "fiscal_year": fy, "accn": accn,
                        "filed_date": entry.get("filed"), "period_end": entry.get("end"),
                        "form": entry.get("form"),
                    }
                    rank = priority + len(field.tags) if (field.key, tag, fy) in set_aside else priority
                    self._assign_if_better(data, best, period, seed, field,
                                           rank, tag, entry)

        self._apply_calendar(data, period_frames, quarterly=False)
        if not _in_dollars(data.values()):
            return {}

        nested: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for (fy, accn), period_dict in data.items():
            nested.setdefault(str(fy), {})[accn] = period_dict
        if years_back:
            keep = sorted(nested.keys(), reverse=True)[:years_back]
            nested = {y: nested[y] for y in keep}
        return nested

    def extract_quarterly_financials(
        self,
        facts: Dict[str, Any],
        quarters_back: Optional[int] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extract standardized **discrete** quarterly financials, keyed by period-end.

        Income statements and cash-flow statements are reported cumulative-to-date in
        10-Qs (and full-year in the 10-K). We recover true 3-month figures by
        differencing the cumulative ladder anchored at the fiscal-year start
        (``discrete[k] = YTD[k] - YTD[k-1]``), which also yields Q4
        (annual - 9-month YTD). Balance-sheet (instant) facts are captured as
        per-quarter checkpoints. See :meth:`_collect_discrete_flows` /
        :meth:`_collect_instant_checkpoints`.

        Args:
            facts: Raw company facts
            quarters_back: Number of quarters to keep (most recent first).
                ``None`` (default) keeps all available history.

        Returns:
            Dictionary organized by period end date.
        """
        us_gaap = _filer_facts(facts, _QUARTERLY_VIEW)
        if not us_gaap:
            return {}

        periods: Dict[str, Dict[str, Any]] = {}
        frames: Dict[str, set] = {}
        # A year-end figure the annual record puts back (see _restore_agreed_figures) is
        # read the same here: {(field, period end): (the later filing's figure, the one kept)}.
        restored = {(r["field"], r["period_end"]): (r["dropped"], r["kept"])
                    for period in self._annual_periods(us_gaap).values() for r in period.get("_repairs", [])}
        # The split basis a filing's figures are on: the product of the splits it came
        # after. Each quarter's per-share figures and counts go onto their year's, and
        # its basis is kept in ``_split_levels`` for the trailing years (BUG-082).
        splits = _split_restatements(us_gaap)
        level: Optional[Callable[[str], float]] = (
            (lambda filed: math.prod(ratio for _, after, ratio in splits if after <= filed)) if splits else None)

        instants_filed = self._collect_instant_checkpoints(us_gaap, periods, frames, restored)
        self._collect_discrete_flows(us_gaap, periods, frames, restored, level, self._revenue_set_aside(us_gaap))
        if level is not None:
            self._instants_on_year_basis(periods, instants_filed, level)
        self._apply_quarter_calendar(periods, frames)

        sorted_ends = sorted(periods.keys(), reverse=True)
        if quarters_back:
            sorted_ends = sorted_ends[:quarters_back]
        return {e: periods[e] for e in sorted_ends}

    def _collect_instant_checkpoints(
        self, us_gaap: Dict[str, Any], periods: Dict[str, Dict[str, Any]],
        frames: Dict[str, set], restored: Dict[Tuple[str, str], Tuple[float, float]],
    ) -> Dict[Tuple[str, str], str]:
        """Balance-sheet (instant) values at every quarter-end (10-Q and 10-K), with the
        year-end figures the annual record puts back (*restored*). Returns when each share
        count was filed: ``{(period end, field): filed}``."""
        best: Dict[Tuple[str, str], Tuple[int, str]] = {}
        for field in CANONICAL_FIELDS:
            if field.kind != INSTANT:
                continue
            for priority, tag in enumerate(field.tags):
                for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in _QUARTERLY_FORMS:
                        continue
                    if entry.get("start"):  # instant facts have no start
                        continue
                    end = entry.get("end")
                    if not end:
                        continue
                    if entry.get("frame"):
                        frames.setdefault(end, set()).add(entry["frame"])
                    filed = entry.get("filed") or ""
                    bkey = (end, field.key)
                    cur = best.get(bkey)
                    if cur is not None and not (
                        priority < cur[0] or (priority == cur[0] and filed >= cur[1])
                    ):
                        continue
                    best[bkey] = (priority, filed)
                    pd_ = periods.setdefault(end, {"period_end": end})
                    pd_[field.key] = entry.get("val")
                    pd_.setdefault("_source_tags", {})[field.key] = tag
        for (key, end), (dropped, kept) in restored.items():
            value = periods.get(end, {}).get(key)
            if isinstance(value, (int, float)) and _agree(value, dropped):
                periods[end][key] = kept
        split_keys = {f.key for f in CANONICAL_FIELDS if f.kind == INSTANT and f.xbrl_unit == "shares"}
        return {(end, key): filed for (end, key), (_, filed) in best.items() if key in split_keys}

    def _instants_on_year_basis(self, periods: Dict[str, Dict[str, Any]],
                                filed_at: Dict[Tuple[str, str], str], level: Callable[[str], float]) -> None:
        """Put each quarter-end share count on its year's split basis, that of the year's
        latest-filed one: its year end's, which the annual record has (BUG-082)."""
        years: Dict[Tuple[Any, str], List[str]] = defaultdict(list)
        for (end, key) in filed_at:
            fy = periods.get(end, {}).get("fiscal_year")
            if fy is not None:
                years[(fy, key)].append(end)
        for (_, key), ends in years.items():
            basis = level(max(filed_at[(end, key)] for end in ends))
            for end in ends:
                value = periods[end].get(key)
                if isinstance(value, (int, float)):
                    periods[end][key] = value * basis / level(filed_at[(end, key)])
                    periods[end].setdefault("_split_levels", {})[key] = basis

    def _year_basis(self, chosen: Dict[Tuple[str, str], Tuple[int, str, Any]],
                    level: Callable[[str], float]) -> Dict[Tuple[str, str], float]:
        """The split basis each of a field's figures goes onto: its year's, that of the
        latest filing of the year's full-year figure (else of its latest), which is the
        annual record's (BUG-082). A figure no year-to-date ladder reaches keeps its own."""
        ladders: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for (start, end), (_p, filed, _v) in chosen.items():
            ladders[start].append((end, filed))
        year_filed: Dict[str, str] = {}  # period end -> its year's latest filing
        for start, rungs in ladders.items():
            if len(rungs) < 2:
                continue
            full_year = [filed for end, filed in rungs if (self._span(start, end) or 0) >= _FULL_YEAR_MIN_DAYS]
            anchor = max(full_year) if full_year else max(filed for _, filed in rungs)
            for end, _ in rungs:
                year_filed[end] = max(year_filed.get(end, ""), anchor)
        return {(start, end): level(year_filed.get(end, filed)) for (start, end), (_p, filed, _v) in chosen.items()}

    @staticmethod
    def _rungs_on_one_scale(chosen: Dict[Tuple[str, str], Tuple[int, str, Any]],
                            income: Dict[Tuple[str, str], Any], eps: Dict[Tuple[str, str], Any]) -> None:
        """Put a weighted count's rungs on one scale (BUG-092): a rung whose own net income
        over EPS implies it times a power of 1000 is rescaled by that power (share_scale's
        rule) when another rung of its year, as filed, is near the result, since an EPS
        can be the mis-scaled figure (TMO's nine months of 2008 at 1,580,000 a share); a
        rung without a usable EPS takes the scale its year's checked rungs have. A rung no
        year checks is left as filed."""
        ladder_of_end: Dict[str, str] = {}  # a period end -> its year's ladder (start)
        ends_by_start: Dict[str, List[str]] = defaultdict(list)
        for start, end in chosen:
            ends_by_start[start].append(end)
        for start, ends in ends_by_start.items():
            if len(ends) >= 2:
                for end in ends:
                    ladder_of_end.setdefault(end, start)
        filed_by_year: Dict[str, List[Tuple[Tuple[str, str], float]]] = defaultdict(list)
        for key, (_p, _f, val) in chosen.items():
            if isinstance(val, (int, float)) and val > 0:
                filed_by_year[ladder_of_end.get(key[1], key[0])].append((key, val))
        checked: Dict[str, List[float]] = defaultdict(list)
        unchecked: List[Tuple[str, str]] = []
        for key, (priority, filed, val) in list(chosen.items()):
            if not isinstance(val, (int, float)) or val <= 0:
                continue
            year = ladder_of_end.get(key[1], key[0])
            net_income, per_share = income.get(key), eps.get(key)
            if isinstance(net_income, (int, float)) and isinstance(per_share, (int, float)) \
                    and net_income and per_share:
                implied = abs(net_income / per_share)
                scale = _best_scale(val, implied)
                if scale is not None and any(0.5 <= val * scale / other <= 2.0
                                             for other_key, other in filed_by_year[year] if other_key != key):
                    val *= scale
                    chosen[key] = (priority, filed, val)
                if abs(val / implied - 1.0) <= _SCALE_TOLERANCE:
                    checked[year].append(val)
                    continue
            unchecked.append(key)
        for key in unchecked:
            reference = checked.get(ladder_of_end.get(key[1], key[0]))
            priority, filed, val = chosen[key]
            if reference:
                scale = _best_scale(val, statistics.median(reference))
                if scale is not None:
                    chosen[key] = (priority, filed, val * scale)

    def _collect_discrete_flows(
        self, us_gaap: Dict[str, Any], periods: Dict[str, Dict[str, Any]],
        frames: Dict[str, set], restored: Dict[Tuple[str, str], Tuple[float, float]],
        level: Optional[Callable[[str], float]] = None,
        set_aside: FrozenSet[Tuple[str, str, Any]] = frozenset(),
    ) -> None:
        """Discrete quarterly flows via cumulative-ladder differencing.

        For each flow (duration) field, dedup facts per (start, end), then group by
        the fiscal-period ``start``. Within each cumulative ladder (a start group with
        >= 2 points) difference consecutive year-to-date values into 3-month figures,
        guarding that each increment spans ~one quarter so missing rungs don't produce
        multi-quarter blobs. A full-year rung reads the figure the annual record puts
        back (*restored*), so the fourth quarter is the restored year less nine months.
        With *level* (the split basis of a filing date), per-share figures and counts go
        onto their year's basis first. A full-year figure in *set_aside* (``(field, tag,
        fiscal year)``, see ``_revenue_set_aside``) ranks after every other tag. An outflow
        stored as a size differences the totals' sizes, and a total that shrinks gives no
        quarter (BUG-111); a net outflow's totals, as paid, difference as filed (BUG-127), but
        a total of payments that falls by half or more gives none (BUG-140). A
        year's ladder is one rung until its second 10-Q, its first quarter's own 3-month
        figure, which fills that quarter after every ladder (BUG-124).
        """
        oracle_rungs: Dict[str, Dict[Tuple[str, str], Any]] = {}  # net income's and EPS's, for the counts' scale
        for field in CANONICAL_FIELDS:
            if field.kind != DURATION:
                continue
            # Dedup per (start, end): higher-priority tag, then most-recently-filed.
            chosen: Dict[Tuple[str, str], Tuple[int, str, Any]] = {}
            closed: Set[Tuple[str, str]] = set()  # the spans a 10-K reports: periods that are over
            for priority, tag in enumerate(field.tags):
                for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
                    if entry.get("form", "") not in _QUARTERLY_FORMS:
                        continue
                    start, end = entry.get("start"), entry.get("end")
                    if not start or not end:
                        continue
                    span = self._span(start, end)
                    if span is None or span > _FULL_YEAR_MAX_DAYS:
                        continue  # ignore multi-year facts
                    if entry.get("frame"):
                        frames.setdefault(end, set()).add(entry["frame"])
                    rank = priority
                    if span >= _FULL_YEAR_MIN_DAYS and (field.key, tag, self._annual_fy(entry)) in set_aside:
                        rank += len(field.tags)  # a revenue figure that isn't the year's (never a count)
                    filed = entry.get("filed") or ""
                    key = (start, end)
                    if entry.get("form", "").startswith("10-K"):
                        closed.add(key)
                    cur = chosen.get(key)
                    if cur is not None and not (
                        rank < cur[0] or (rank == cur[0] and filed >= cur[1])
                    ):
                        continue
                    chosen[key] = (rank, filed, entry.get("val"))
            for (start, end), (priority, filed, val) in list(chosen.items()):
                fix = restored.get((field.key, end))
                span = self._span(start, end)
                if fix is None or not isinstance(val, (int, float)) or span is None or span < _FULL_YEAR_MIN_DAYS:
                    continue
                dropped, kept = fix  # magnitudes for a SIGN_ABS field; the rung keeps its sign
                if field.sign == SIGN_ABS and _agree(abs(val), dropped):
                    chosen[(start, end)] = (priority, filed, math.copysign(kept, val))
                elif field.sign != SIGN_ABS and _agree(val, dropped):
                    chosen[(start, end)] = (priority, filed, kept)

            # A split restates only the filings after it, so a year's rungs can sit on two
            # bases: AMZN's FY2020 from a 10-K after its 20:1 split, its nine months from a
            # 10-Q before, and Q4 read -25.63 (BUG-082). Each goes onto its year's basis.
            shares = field.xbrl_unit == "shares"
            basis: Dict[Tuple[str, str], float] = {}
            if level is not None and field.xbrl_unit in _SPLIT_UNITS:
                basis = self._year_basis(chosen, level)
                for key, year_level in basis.items():
                    priority, filed, val = chosen[key]
                    if isinstance(val, (int, float)):
                        moved = year_level / level(filed)
                        chosen[key] = (priority, filed, val * moved if shares else val / moved)

            # A later filing can re-tag a year's count in thousands or millions beside its
            # other rungs in units (MCD's FY2021 as 751.8, its nine months 751.9M), and the
            # fourth quarter, the year's share-days less the nine months', read -2,223.0M
            # (BUG-092): each rung goes onto the scale its own net income and EPS imply
            # (share_scale's rule), and one without them onto its year's.
            if field.key in _SCALE_ORACLES:
                self._rungs_on_one_scale(chosen, oracle_rungs.get("net_income", {}),
                                         oracle_rungs.get(_SCALE_ORACLES[field.key], {}))
            if field.key in ("net_income", "eps_basic", "eps_diluted"):
                oracle_rungs[field.key] = {key: val for key, (_p, _f, val) in chosen.items()}
            if shares:  # a count at or below zero (a filed zero) is no count, and no baseline
                chosen = {key: rung for key, rung in chosen.items()
                          if not isinstance(rung[2], (int, float)) or rung[2] > 0}

            # Group by fiscal-period start -> cumulative ladders.
            ladders: Dict[str, List[Tuple[str, Any]]] = defaultdict(list)
            for (start, end), (_p, _f, val) in chosen.items():
                ladders[start].append((end, val))

            # A weighted-average share count isn't additive (BUG-074): a quarter's is its
            # own 3-month figure, which 10-Qs tag, and otherwise (Q4: 10-Ks don't tag it)
            # the year-to-date share-days less the previous rung's, over the quarter's days.
            own: Dict[str, Tuple[Any, str, Optional[float]]] = {}
            for (start, end), (priority, _f, val) in chosen.items() if shares else ():
                days = self._span(start, end)
                if isinstance(val, (int, float)) and val > 0 and days is not None \
                        and _QUARTER_MIN_DAYS <= days <= _QUARTER_MAX_DAYS:
                    own[end] = (val, field.tags[priority], basis.get((start, end)))

            def assign(end: str, value: Any, source: str, start: str, ladder_fy: Optional[int],
                       split_level: Optional[float]) -> None:
                pd_ = periods.setdefault(end, {"period_end": end})
                if field.key not in pd_:
                    pd_[field.key] = value
                    pd_.setdefault("_source_tags", {})[field.key] = source
                    if split_level is not None:
                        pd_.setdefault("_split_levels", {})[field.key] = split_level
                pd_.setdefault("fiscal_quarter", self._fiscal_quarter_from_span(self._span(start, end)))
                pd_.setdefault("fiscal_year", ladder_fy)

            # A year still open belongs to the year starting on its start day: as its own full-year
            # figure dates it, or the year after the one ending the day before. Its latest end's
            # calendar year put a January year's quarters before its 10-K in the year before theirs,
            # beside that year's own (BUG-124). A period a 10-K closes keeps its end's year, as
            # before: NOW's July-December 2011 between its June and calendar years.
            year_of_start: Dict[str, Optional[int]] = {}
            year_spans = [(start, end) for start, end in chosen
                          if (self._span(start, end) or 0) >= _FULL_YEAR_MIN_DAYS]
            for start, end in year_spans:
                year_of_start[start] = self._fiscal_year_from_end(end)
            for start, end in year_spans:
                year, after = self._fiscal_year_from_end(end), self._day_after(end)
                if year is not None and after is not None:
                    year_of_start.setdefault(after, year + 1)

            for start, points in ladders.items():
                if len(points) < 2:
                    continue  # not a cumulative ladder (isolated 3-month fact)
                points.sort()
                # The fiscal year this ladder belongs to: a closed period's latest end's (the date
                # rule), an open one's start's.
                ladder_fy = None if (start, points[-1][0]) in closed else year_of_start.get(start)
                if ladder_fy is None:
                    ladder_fy = self._fiscal_year_from_end(points[-1][0])
                prev_end, prev_val = start, 0.0
                for end, val in points:
                    inc_span = self._span(prev_end, end)
                    if end in own:
                        own_val, own_tag, own_level = own[end]
                        assign(end, own_val, own_tag, start, ladder_fy, own_level)
                    elif isinstance(val, (int, float)) and inc_span is not None \
                            and _QUARTER_MIN_DAYS <= inc_span <= _QUARTER_MAX_DAYS:
                        prev = prev_val if isinstance(prev_val, (int, float)) else 0.0
                        if shares:
                            ytd_days, prev_days = self._span(start, end) or 0, self._span(start, prev_end) or 0
                            discrete = (val * ytd_days - prev * prev_days) / inc_span
                            keep = discrete > 0  # never a count at or below zero (BUG-092)
                        elif field.sign == SIGN_ABS:
                            # An outflow can be tagged either sign, even within one year (LLY's 10-Qs
                            # negative, its 10-K positive), so its quarter is what the year-to-date
                            # total's size grew by. A total that shrinks is a later filing's other
                            # basis (INTU's 10-K puts capitalized software apart): no quarter (BUG-111).
                            discrete = abs(val) - abs(prev)
                            keep = discrete >= 0
                        elif field.sign == SIGN_NET and 0 <= val < prev / 2:
                            # A net outflow differences as filed, a total below zero an inflow (BUG-127),
                            # but a total of payments that falls by half or more is two bases (GE's FY2017,
                            # restated to $2,722M, beside nine months as first filed, $6,053M) or a
                            # filing's own rungs (JNJ's Q1 2026 $17,500M, six months $263M): no quarter,
                            # as BUG-111 left it. A smaller fall is a true-up (ABBV's 2012) (BUG-140).
                            discrete, keep = val - prev, False
                        else:
                            discrete, keep = val - prev, True
                        if keep:
                            assign(end, discrete, "derived:ytd_diff", start, ladder_fy, basis.get((start, end)))
                    prev_end, prev_val = end, val  # (a gap resets the baseline)
            if not shares:
                # A year's ladder is one rung until its second 10-Q: the first quarter's own
                # 3-month figure, from the day a year starts. It fills that quarter where the
                # filings hold it (its balance sheet), after every ladder, so it displaces no
                # derived figure (BUG-124); other lone 3-month figures stay unread.
                for start, points in ladders.items():
                    if len(points) != 1 or year_of_start.get(start) is None:
                        continue
                    end, val = points[0]
                    span = self._span(start, end)
                    if end in periods and isinstance(val, (int, float)) and span is not None \
                            and _QUARTER_MIN_DAYS <= span <= _QUARTER_MAX_DAYS:
                        assign(end, abs(val) if field.sign == SIGN_ABS else val, field.tags[chosen[(start, end)][0]],
                               start, year_of_start[start], basis.get((start, end)))
            for end, (value, tag, own_level) in own.items():  # quarters no year-to-date ladder reaches
                if end in periods and field.key not in periods[end]:
                    periods[end][field.key] = value
                    periods[end].setdefault("_source_tags", {})[field.key] = tag
                    if own_level is not None:
                        periods[end].setdefault("_split_levels", {})[field.key] = own_level

    def _apply_quarter_calendar(
        self, periods: Dict[str, Dict[str, Any]], frames: Dict[str, set],
    ) -> None:
        """Attach calendar_year/calendar_quarter (+frame) to each quarter-end."""
        for end, pd_ in periods.items():
            year, quarter, chosen = self._calendar_from_frames(frames.get(end, set()),
                                                               quarterly=True)
            if year is None:
                year = self._calendar_year_from_end(end)
            if quarter is None:
                quarter = self._calendar_quarter_from_end(end)
            pd_["calendar_year"] = year
            pd_["calendar_quarter"] = quarter
            if chosen:
                pd_["frame"] = chosen

    @staticmethod
    def _fiscal_quarter_from_span(cum_span: Optional[int]) -> Optional[int]:
        """Fiscal quarter (1-4) from a cumulative period's day span (~91/182/273/365)."""
        if cum_span is None:
            return None
        return max(1, min(4, round(cum_span / 91.31)))

    def _calendar_quarter_from_end(self, end: Optional[str]) -> Optional[int]:
        """Fallback calendar quarter from a period-end month (used when no frame)."""
        end_date = self._parse_iso_date(end)
        if end_date is None:
            return None
        return (end_date.month - 1) // 3 + 1

    def _span(self, start: Optional[str], end: Optional[str]) -> Optional[int]:
        """Day count between two ISO dates, or None."""
        s, e = self._parse_iso_date(start), self._parse_iso_date(end)
        if s is None or e is None:
            return None
        return (e - s).days

    def _assign_if_better(
        self,
        data: Dict[Any, Dict[str, Any]],
        best: Dict[Any, Tuple[int, str]],
        period: Any,
        seed: Dict[str, Any],
        field: Any,
        priority: int,
        tag: str,
        entry: Dict[str, Any],
    ) -> bool:
        """Store ``entry``'s value for ``field`` under ``period`` iff it beats the
        current best (higher-priority tag, or the same tag filed at least as late).
        Seeds a new period dict from ``seed``. Returns True when it won, so the caller
        can run any period-level metadata update."""
        filed = entry.get("filed") or ""
        bkey = (period, field.key)
        cur = best.get(bkey)
        if cur is not None and not (
            priority < cur[0] or (priority == cur[0] and filed >= cur[1])
        ):
            return False
        best[bkey] = (priority, filed)
        value = entry.get("val")
        if field.sign == SIGN_ABS and isinstance(value, (int, float)):
            value = abs(value)
        period_dict = data.setdefault(period, dict(seed))
        period_dict[field.key] = value
        period_dict.setdefault("_source_tags", {})[field.key] = tag
        return True

    def _resolve_canonical(
        self,
        us_gaap: Dict[str, Any],
        form_set: set,
        valid_fn,
        period_key_fn,
        quarterly: bool,
        set_aside: FrozenSet[Tuple[str, str, Any]] = frozenset(),
    ) -> Dict[Any, Dict[str, Any]]:
        """Resolve every canonical field for every period from raw us-gaap facts.

        For each canonical field, candidate tags are tried in priority order; the
        highest-priority tag with data for a period wins, and within a tag the
        most-recently-filed value wins (so restatements supersede originals). A
        ``(field, tag, period)`` in *set_aside* ranks after every other tag.
        """
        data: Dict[Any, Dict[str, Any]] = {}
        # (period, canonical_key) -> (tag_priority, filed). Tracks the basis for the
        # currently stored value so a better candidate can replace it.
        best: Dict[Any, tuple] = {}
        # period -> filed date of the entry that set period-level metadata.
        meta_filed: Dict[Any, str] = {}
        # period -> set of SEC calendar `frame` strings seen across all of the
        # period's facts (the authoritative calendar mapping; see _apply_calendar).
        period_frames: Dict[Any, set] = {}

        for field in CANONICAL_FIELDS:
            unit_key = field.xbrl_unit
            for priority, tag in enumerate(field.tags):
                tag_data = us_gaap.get(tag)
                if not tag_data:
                    continue

                for entry in tag_data.get("units", {}).get(unit_key, []):
                    if entry.get("form", "") not in form_set:
                        continue
                    if not self._matches_kind(field, entry) or not valid_fn(entry):
                        continue

                    period = period_key_fn(entry)
                    if period is None:
                        continue

                    # Capture the SEC frame from every contributing fact (it may be
                    # present only on a comparative instance, not the one that wins).
                    frame = entry.get("frame")
                    if frame:
                        period_frames.setdefault(period, set()).add(frame)

                    seed = self._init_period_meta(period, quarterly)
                    rank = priority + len(field.tags) if (field.key, tag, period) in set_aside else priority
                    if not self._assign_if_better(
                        data, best, period, seed, field, rank, tag, entry
                    ):
                        continue

                    # Period-level metadata follows the latest-filed contributing entry.
                    period_dict = data[period]
                    filed = entry.get("filed") or ""
                    if filed >= meta_filed.get(period, ""):
                        meta_filed[period] = filed
                        period_dict["filed_date"] = entry.get("filed")
                        period_dict["period_end"] = entry.get("end")
                        period_dict["form"] = entry.get("form")
                        period_dict["fiscal_period"] = entry.get("fp")
                        if quarterly:
                            period_dict["fiscal_year"] = entry.get("fy")

        self._apply_calendar(data, period_frames, quarterly)
        return data

    def _annual_fy(self, entry: Dict[str, Any]) -> Optional[int]:
        """The fiscal year an annual report's fact covers (see ``_fiscal_year_from_end``)."""
        return self._fiscal_year_from_end(entry.get("end")) or self._period_year(entry)

    def _annual_periods(self, us_gaap: Dict[str, Any]) -> Dict[Any, Dict[str, Any]]:
        """Every fiscal year's figures from the annual reports, keyed by fiscal year,
        with a later filing's tagging errors put right."""
        data = self._resolve_canonical(
            us_gaap,
            form_set=_ANNUAL_FORMS,
            valid_fn=self._is_full_year,
            period_key_fn=self._annual_fy,
            quarterly=False,
            set_aside=self._revenue_set_aside(us_gaap),
        )
        self._restore_agreed_figures(us_gaap, data, self._annual_fy)
        return data

    def _annual_filings(self, us_gaap: Dict[str, Any], field: Any, tag: str,
                        period_key_fn) -> Dict[Any, List[Tuple[str, float, str]]]:
        """Each annual report's figure for *field* under *tag*, as ``_resolve_canonical``
        reads them: ``{period: [(filed, value, end), ...]}`` in filing order."""
        filings: Dict[Any, List[Tuple[str, float, str]]] = defaultdict(list)
        for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
            value = entry.get("val")
            if entry.get("form", "") not in _ANNUAL_FORMS or not isinstance(value, (int, float)):
                continue
            if not self._matches_kind(field, entry) or not self._is_full_year(entry):
                continue
            period = period_key_fn(entry)
            if period is not None:
                filings[period].append((entry.get("filed") or "",
                                        float(abs(value) if field.sign == SIGN_ABS else value),
                                        entry.get("end") or ""))
        for values in filings.values():
            values.sort(key=lambda filing: filing[0])
        return filings

    def _revenue_set_aside(self, us_gaap: Dict[str, Any]) -> FrozenSet[Tuple[str, str, Any]]:
        """The revenue figures, as ``("revenue", tag, fiscal year)``, that aren't the year's
        revenue (BUG-099).

        Revenue takes a year's first tag, and a figure named here ranks after every other. It gives
        way to one 25% or more away from it that the year's income statement adds up to while
        it doesn't (operating income plus operating expenses, or cost of revenue plus gross
        profit): BLK's FY2022-24 ``Revenues``, $11-13B, beside the $17.9-20.4B its operating
        income and expenses make, while MA's net revenue stays beside the gross it tags as
        contract revenue. Where the statement adds up to none of the year's figures, one at or
        below zero gives way to a positive one (ETN's FY2011-16 zeros), and one below the year's
        cost of revenue to one that isn't (PG's FY2012-14 ``Revenues`` of $28-29B beside net
        sales of $74-82B and a cost of sales of $39-41B). Figures closer than that are a
        restatement or a presentation, which the tag order settles.
        """
        def year_figures(key: str, tags: Iterable[str]) -> Dict[Any, Dict[str, float]]:
            figures: Dict[Any, Dict[str, float]] = defaultdict(dict)
            for tag in tags:
                for fy, filings in self._annual_filings(us_gaap, _FIELDS[key], tag, self._annual_fy).items():
                    figures[fy][tag] = filings[-1][1]  # the latest filing's
            return figures

        def first(key: str) -> Dict[Any, float]:  # each year's figure under its first tag
            return {fy: next(iter(by_tag.values())) for fy, by_tag in year_figures(key, _FIELDS[key].tags).items()}

        revenue = year_figures("revenue", (t for t in _FIELDS["revenue"].tags if t != _REVENUE_WITH_TAXES))
        cost, gross, income, expenses = (first(key) for key in ("cost_of_revenue", "gross_profit",
                                                                "operating_income", "operating_expenses"))
        aside: Set[Tuple[str, str, Any]] = set()
        for fy, figures in revenue.items():
            totals = [a + b for a, b in ((income.get(fy), expenses.get(fy)), (cost.get(fy), gross.get(fy)))
                      if a is not None and b is not None]
            adds_up = {tag for tag, value in figures.items() if any(_agree(value, total) for total in totals)}
            floor = cost.get(fy)
            for tag, value in figures.items():
                for other, against in figures.items():
                    if not (value <= 0 < against or 0 < value * _REVENUE_GAP <= against
                            or 0 < against * _REVENUE_GAP <= value):
                        continue
                    if (other in adds_up and tag not in adds_up if adds_up else
                            value <= 0 < against or (floor is not None and value < floor <= against)):
                        aside.add(("revenue", tag, fy))
        return frozenset(aside)

    def _restore_agreed_figures(self, us_gaap: Dict[str, Any],
                                data: Dict[Any, Dict[str, Any]], period_key_fn) -> None:
        """Put back the figure the earlier filings agree on where a later filing's
        comparative reads as a tagging error of it (see ``_tagging_error``), and record
        each in the period's ``_repairs``.

        The latest filing wins otherwise, as a restatement should. A year's own net
        change in cash decides for its cash-flow sections (AMD's 2024 10-K flipped
        FY2021's investing and financing cash flows, and the year no longer added up);
        the years around it decide the rest, so a later filing that corrects an earlier
        error keeps its figure (BRK-B's FY2010 share count, LLY's FY2008 D&A).
        """
        conflicts: List[Tuple[Any, str, str, float, float, str, str]] = []
        for field in CANONICAL_FIELDS:
            years_by_tag: Dict[str, List[Any]] = defaultdict(list)
            for fy, period in data.items():
                if isinstance(period.get(field.key), (int, float)):
                    years_by_tag[period["_source_tags"][field.key]].append(fy)
            for tag, years in years_by_tag.items():
                filings = self._annual_filings(us_gaap, field, tag, period_key_fn)
                for fy in years:
                    history = filings.get(fy, [])
                    latest = float(data[fy][field.key])
                    filed, _, end = history[-1] if history else ("", 0.0, "")
                    earlier = [value for when, value, _ in history if when < filed]
                    if not earlier or not all(_agree(value, latest) for when, value, _ in history
                                              if when == filed):
                        continue
                    if not all(_agree(value, earlier[-1]) for value in earlier):
                        continue  # the earlier filings disagree among themselves
                    kind = _tagging_error(earlier[-1], latest, field.key)
                    if kind:
                        conflicts.append((fy, field.key, kind, earlier[-1], latest, filed, end))

        agreed_cash: Dict[Any, Dict[str, float]] = defaultdict(dict)
        for fy, key, _, earlier_value, *_ in conflicts:
            if key in _CASH_SECTIONS:
                agreed_cash[fy][key] = earlier_value
        restored = []
        for conflict in conflicts:
            fy, key, kind, earlier_value, latest, *_ = conflict
            period = data[fy]
            sections = [period.get(k) for k in _CASH_SECTIONS]
            if key in _CASH_SECTIONS and all(isinstance(v, (int, float))
                                             for v in sections + [period.get("net_change_in_cash")]):
                agreed = [agreed_cash[fy].get(k, v) for k, v in zip(_CASH_SECTIONS, sections)]
                if not _cash_adds_up(sections, period) and _cash_adds_up(agreed, period):
                    restored.append(conflict)
                continue
            neighbours = [data[y][key] for y in range(fy - _NEIGHBOUR_YEARS, fy + _NEIGHBOUR_YEARS + 1)
                          if y != fy and isinstance(data.get(y, {}).get(key), (int, float))]
            if _years_side_with_earlier(kind, earlier_value, latest, neighbours):
                restored.append(conflict)
        for fy, key, kind, earlier_value, latest, filed, end in restored:
            data[fy][key] = earlier_value
            data[fy].setdefault("_repairs", []).append({"field": key, "kind": kind, "kept": earlier_value,
                                                        "dropped": latest, "filed": filed, "period_end": end})

    @staticmethod
    def _init_period_meta(period: Any, quarterly: bool) -> Dict[str, Any]:
        """Seed a period dict with its identifying metadata."""
        if quarterly:
            return {"period_end": period}
        return {"fiscal_year": period}

    def _apply_calendar(self, data: Dict[Any, Dict[str, Any]],
                        period_frames: Dict[Any, set], quarterly: bool) -> None:
        """Set SEC-authoritative ``calendar_year`` (+quarter, +frame) on each period.

        Uses the SEC ``frame`` when present (e.g. a June-fiscal-year-end maps to its
        calendar year, a January FYE to the prior calendar year), falling back to the
        period-end month rule for the rare period with no frame on any instance.
        """
        for period, period_dict in data.items():
            year, quarter, chosen = self._calendar_from_frames(
                period_frames.get(period, set()), quarterly
            )
            if year is None:
                year = self._calendar_year_from_end(period_dict.get("period_end"))
            period_dict["calendar_year"] = year
            if chosen:
                period_dict["frame"] = chosen
            if quarterly:
                period_dict["calendar_quarter"] = quarter

    @staticmethod
    def _parse_frame(frame: str):
        """Parse an SEC frame like ``CY2024`` / ``CY2024Q4I`` -> (year, quarter|None)."""
        match = re.match(r"^CY(\d{4})(?:Q(\d))?I?$", frame or "")
        if not match:
            return None, None
        year = int(match.group(1))
        quarter = int(match.group(2)) if match.group(2) else None
        return year, quarter

    def _calendar_from_frames(self, frames: set, quarterly: bool):
        """Choose the best frame for a period -> (year, quarter, frame_string).

        For annual periods prefer a full-year frame (``CY2024``); for quarterly prefer
        a frame carrying a quarter (``CY2024Q4I``). Returns (None, None, None) if no
        parseable frame is available.
        """
        parsed = []
        for frame in frames:
            year, quarter = self._parse_frame(frame)
            if year is not None:
                parsed.append((year, quarter, frame))
        if not parsed:
            return None, None, None
        if quarterly:
            with_q = [p for p in parsed if p[1] is not None]
            return (with_q or parsed)[0]
        full_year = [p for p in parsed if p[1] is None]
        return (full_year or parsed)[0]

    def _calendar_year_from_end(self, end: Optional[str]):
        """Fallback calendar-year rule (replicates SEC's convention).

        A period ending in Jun-Dec maps to its end year; Jan-May maps to the prior
        year (i.e. the calendar year holding most of the 12-month period).
        """
        end_date = self._parse_iso_date(end)
        if end_date is None:
            return None
        return end_date.year if end_date.month >= 6 else end_date.year - 1

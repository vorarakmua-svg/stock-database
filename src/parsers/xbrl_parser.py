"""XBRL data parser for SEC EDGAR financial data."""

import logging
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from fractions import Fraction
from itertools import combinations
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from ..mappings.canonical import CANONICAL_FIELDS, DURATION, INSTANT, SIGN_ABS, SIGN_NET
from .share_scale import _SCALES, _best_scale
from .share_scale import _TOLERANCE as _SCALE_TOLERANCE

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


#: Net income's tag for the figure basic EPS divides: after preferred dividends and participating securities.
_TO_COMMON = "NetIncomeLossAvailableToCommonStockholdersBasic"
#: The (EPS, weighted count) pairs a filing's figure for common is checked with, basic first (its own).
_EPS_PAIRS = (("eps_basic", "weighted_avg_shares_basic"), ("eps_diluted", "weighted_avg_shares_diluted"))


def _fact_key(entry: Dict[str, Any]) -> Tuple[Any, Any, Any]:
    """A fact's filing and span: what one filing says for one period."""
    return entry.get("accn"), entry.get("start"), entry.get("end")


def _eps_gap(figure: float, eps: float, count: float) -> float:
    """How far *count*, on the power of 1000 nearest (share_scale's), is from *figure* / *eps*: inf where they
    differ in sign or there's nothing to divide."""
    if not eps or count <= 0 or figure / eps <= 0:
        return math.inf
    implied = figure / eps
    return min(abs(count * scale / implied - 1.0) for scale in _SCALES)


def _by_fact(us_gaap: Dict[str, Any], key: str, tags: Iterable[str]) -> Dict[Tuple[Any, Any, Any], float]:
    """Each filing's figure for each period under *key*'s first of *tags* that has one."""
    values: Dict[Tuple[Any, Any, Any], float] = {}
    for tag in tags:
        for entry in us_gaap.get(tag, {}).get("units", {}).get(_FIELDS[key].xbrl_unit, []):
            if isinstance(entry.get("val"), (int, float)) and entry.get("start"):
                values.setdefault(_fact_key(entry), float(entry["val"]))
    return values


def _eps_pairs(us_gaap: Dict[str, Any]) -> List[Tuple[Dict[Tuple[Any, Any, Any], float],
                                                     Dict[Tuple[Any, Any, Any], float]]]:
    """Each filing's EPS and weighted count for each period, as ``_EPS_PAIRS`` pairs them."""
    return [(_by_fact(us_gaap, eps, _FIELDS[eps].tags), _by_fact(us_gaap, count, _FIELDS[count].tags))
            for eps, count in _EPS_PAIRS]


#: The field of net income to common alone, which the valuation history's P/E divides by (Q16).
_TO_COMMON_FIELD = "net_income_to_common"
#: An EPS filed to the cent checks a figure only from this size: a smaller one is 5% off or more by its rounding alone
#: (MCHP's nine months to 2025-12-31: $2.4M to common, EPS $0.00; Ford's FY2019 $47M at $0.01).
_MIN_CHECKING_EPS = 0.10


def _to_common_contradicted(us_gaap: Dict[str, Any]) -> FrozenSet[Tuple[str, str, Any]]:
    """The to-common figures, as ``("net_income_to_common", tag, (accn, start, end))``, that their own filing's EPS x
    count contradicts (Q16): the field of their own doesn't read them at all.

    The test is BUG-252's (``_to_common_set_aside``): its basic pair, else its diluted, more than share_scale's 10%
    from the figure, a count on its power of 1000. Net income ranks such a figure last only where another tag is the
    EPS's figure; this field has no other tag, and a figure that isn't the one EPS divides isn't to common: EG's
    FY2016-20 $792.4M ... where their EPS make $985.7M .... A filing without EPS for the period, or with one under
    _MIN_CHECKING_EPS, can't contradict it.
    """
    figures = _by_fact(us_gaap, "net_income", (_TO_COMMON,))
    if not figures:
        return frozenset()
    pairs = _eps_pairs(us_gaap)
    unread: Set[Tuple[str, str, Any]] = set()
    for key, figure in figures.items():
        pair = next(((eps[key], counts[key]) for eps, counts in pairs if key in eps and key in counts), None)
        if pair is not None and abs(pair[0]) >= _MIN_CHECKING_EPS and _eps_gap(figure, *pair) > _SCALE_TOLERANCE:
            unread.add((_TO_COMMON_FIELD, _TO_COMMON, key))
    return frozenset(unread)


def _unread(unread: FrozenSet[Tuple[str, str, Any]], field: Any, tag: str, entry: Dict[str, Any]) -> bool:
    """Whether *entry*, a fact under *tag*, is one *field* doesn't read (``_to_common_contradicted``). Net income's
    set-aside figures (``_to_common_set_aside``) are keyed alike: they rank last, and are still read."""
    return field.key == _TO_COMMON_FIELD and (field.key, tag, _fact_key(entry)) in unread


def _to_common_set_aside(us_gaap: Dict[str, Any]) -> FrozenSet[Tuple[str, str, Any]]:
    """The to-common figures, as ``("net_income", tag, (accn, start, end))``, that aren't the figure their own
    filing's EPS divides (BUG-252).

    Net income takes that figure where a period has no ``NetIncomeLoss``: by definition basic EPS x the basic
    count, the year's net income less preferred dividends and what participating securities take. One gives way
    where its filing's EPS x count for the period (its basic pair, else its diluted; a count in thousands or
    millions on its power of 1000) is more than share_scale's 10% from it, and within 10% of the same filing's
    figure under a later tag: EG's FY2016-20 10-Ks tag $792.4M ... $262M to common beside ProfitLoss of $996.3M ...
    $514M, which their EPS make (and their 10-Qs likewise); PNC's 10-K of 2021 tags FY2018-20 figures 16-62% below
    its EPS's, L3Harris' of 2019 FY2017's 16% above. A figure after preferred dividends stays (EPS x count makes
    it), and so does one that an EPS rounded near zero checks no figure with (Ford's FY2019 at $0.01).
    """
    field = _FIELDS["net_income"]
    later = field.tags[field.tags.index(_TO_COMMON) + 1:]
    figures = _by_fact(us_gaap, "net_income", (_TO_COMMON,))
    if not figures:
        return frozenset()
    others = [_by_fact(us_gaap, "net_income", (tag,)) for tag in later]
    pairs = _eps_pairs(us_gaap)
    aside: Set[Tuple[str, str, Any]] = set()
    for key, figure in figures.items():
        pair = next(((eps[key], counts[key]) for eps, counts in pairs if key in eps and key in counts), None)
        if pair is None or _eps_gap(figure, *pair) <= _SCALE_TOLERANCE:
            continue
        if any(key in other and _eps_gap(other[key], *pair) <= _SCALE_TOLERANCE for other in others):
            aside.add(("net_income", _TO_COMMON, key))
    return frozenset(aside)


def _ranks_last(set_aside: FrozenSet[Tuple[str, str, Any]], field: Any, tag: str, period: Any,
                entry: Dict[str, Any]) -> bool:
    """Whether *entry*, a fact under *tag* for *period*, ranks after every other tag of *field*: its period's
    figure is set aside (``_revenue_set_aside``) or the fact itself is (``_to_common_set_aside``)."""
    return (field.key, tag, period) in set_aside or (field.key, tag, _fact_key(entry)) in set_aside


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


#: The total with restricted cash, and the cash equivalents cash reads after it, which most filers tag only in a note,
#: as a part of their cash line (``canonical.py``, BUG-161).
_RESTRICTED_CASH = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
_CASH_EQUIVALENTS = "CashEquivalentsAtCarryingValue"
#: What a cash flow statement's total adds to the balance sheet's cash line: restricted cash, and deposits a clearing
#: house counts with it (ICE's margin deposits); not ``Deposits``, a bank's deposit liabilities.
_BESIDE_THE_CASH_LINE = ("Restricted", "DepositsAssets")


def _cash_totals_set_aside(us_gaap: Dict[str, Any]) -> Dict[str, Any]:
    """*us_gaap* less the restricted-cash total a filing tags on a date where it is that filing's cash equivalents
    plus restricted cash (or such deposits) it tags there: the equivalents are then its balance sheet's cash line, and
    the total the cash flow statement's. ICE's 10-Ks for FY2018-22 tag their cash line as the equivalents, beside short-
    and long-term restricted cash and, from FY2021, $82-146B of margin deposits the total adds: read first, the total
    made FY2020-21's cash $83.6B and $148.0B for $0.583B and $0.607B (BUG-161)."""
    totals, equivalents = _by_filing(us_gaap, _RESTRICTED_CASH), _by_filing(us_gaap, _CASH_EQUIVALENTS)
    dates = totals.keys() & equivalents.keys()
    if not dates:
        return us_gaap
    beside: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for tag in us_gaap:
        if tag.startswith(_BESIDE_THE_CASH_LINE):
            for key, value in _by_filing(us_gaap, tag).items():
                if key in dates:
                    beside[key].append(value)
    aside = {key for key in dates if any(_agree(sum(parts), totals[key] - equivalents[key])
                                         for n in range(1, len(beside[key]) + 1)
                                         for parts in combinations(beside[key], n))}
    if not aside:
        return us_gaap
    body = us_gaap[_RESTRICTED_CASH]
    return {**us_gaap, _RESTRICTED_CASH: {**body, "units": {
        unit: [e for e in entries if (e.get("accn"), e.get("end")) not in aside]
        for unit, entries in body.get("units", {}).items()}}}


#: Cash, which cash reads before the total with restricted cash, and the figures for cash and its equivalents without
#: restricted cash that show a date's cash line apart from it (``_cash_parts_set_aside``).
_CASH = "Cash"
_CASH_LINES = ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments",
               "CashAndCashEquivalentsFairValueDisclosure")


def _cash_parts_set_aside(us_gaap: Dict[str, Any]) -> Dict[str, Any]:
    """*us_gaap* less the Cash a filing tags beside the restricted-cash total where Cash is a part of that total: where
    on one of the filing's dates the total agrees with a figure for cash and its equivalents above Cash (a
    ``_CASH_LINES`` figure, or Cash and the cash equivalents together) that this filing or an earlier one tags there,
    the total holds no restricted cash and is the balance sheet's cash line, and the filing's Cash is set aside on each
    of its dates (one balance sheet tags a line alike in each column). CF's 10-Ks tag the line, $1.614B for FY2024, as
    the total and in the fair-value note, its 10-Qs only as the total, beside a note's $0.168B of Cash; TGT's tag it as
    the total and with its short-term investments, $4.762B for FY2025, beside $0.276B; KLAC's FY2020 10-K tags the total
    its earlier filings tag as CashAndCashEquivalentsAtCarryingValue. Where Cash is the line (ES's, HIG's, PGR's), the
    total adds restricted cash, and no such figure agrees with it (BUG-260)."""
    totals, cash = _by_filing(us_gaap, _RESTRICTED_CASH), _by_filing(us_gaap, _CASH)
    if not totals.keys() & cash.keys():
        return us_gaap
    filed = {e["accn"]: e.get("filed") or "" for tag in (_RESTRICTED_CASH, _CASH)
             for e in us_gaap[tag].get("units", {}).get("USD", []) if e.get("accn")}
    lines = [_by_filing(us_gaap, tag) for tag in _CASH_LINES]
    equivalents = _by_filing(us_gaap, _CASH_EQUIVALENTS)
    shown: Dict[str, List[Tuple[str, float]]] = defaultdict(list)  # each date's lines above Cash, by filing day
    for (accn, end), value in cash.items():
        figures = [line[(accn, end)] for line in lines if (accn, end) in line]
        if equivalents.get((accn, end)):
            figures.append(value + equivalents[(accn, end)])
        shown[end].extend((filed[accn], figure) for figure in figures if figure > value and not _agree(figure, value))
    parts = {accn for (accn, end), total in totals.items()
             if any(day <= filed[accn] and _agree(figure, total) for day, figure in shown.get(end, ()))}
    if not parts:
        return us_gaap
    body = us_gaap[_CASH]
    return {**us_gaap, _CASH: {**body, "units": {
        unit: [e for e in entries if e.get("accn") not in parts] for unit, entries in body.get("units", {}).items()}}}


#: LongTermDebt, a total with the current maturities, and the noncurrent figures long-term debt
#: reads after it (``canonical.py``).
_DEBT_TOTAL = "LongTermDebt"
_DEBT_NONCURRENT = ("LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable", "UnsecuredLongTermDebt",
                    "ConvertibleLongTermNotesPayable")


def _by_filing(us_gaap: Dict[str, Any], tag: str) -> Dict[Tuple[str, str], float]:
    """A tag's dollar figures at an instant, by filing and date: ``{(accn, end): value}``."""
    return {(e["accn"], e["end"]): e["val"] for e in us_gaap.get(tag, {}).get("units", {}).get("USD", [])
            if e.get("accn") and e.get("end") and not e.get("start") and isinstance(e.get("val"), (int, float))}


def _noncurrent_debt(us_gaap: Dict[str, Any], tags: Tuple[str, ...] = _DEBT_NONCURRENT) -> Dict[Tuple[str, str], float]:
    """Each filing date's noncurrent long-term debt, its figure under the first of *tags* it tags. A LongTermDebt total
    is weighed against the figures long-term debt reads after it (``_DEBT_NONCURRENT``): a filing that tags
    LongTermDebtNoncurrent reads that before any total. The filer's own total debt is weighed against the figure the
    record reads beside the short-term debt, LongTermDebtNoncurrent first (``_current_debt_combinations``)."""
    noncurrent: Dict[Tuple[str, str], float] = {}
    for tag in reversed(tags):
        noncurrent.update(_by_filing(us_gaap, tag))  # the first tag's figure
    return noncurrent


#: Concepts a balance sheet's noncurrent debt line can carry that long-term debt doesn't read (Corpay's and KMB's
#: LongTermNotesAndLoans, FICO's SeniorLongTermNotes, ADP's OtherLongTermDebtNoncurrent, JKHY's LongTermLoansPayable;
#: BUG-290); and the principal due within a year in a filing's maturity schedule, the current maturities of a filing
#: that tags them only there (FICO's FY2014: $71M inside its $170M of current debt).
_DEBT_LINES = ("LongTermNotesAndLoans", "SeniorLongTermNotes", "OtherLongTermDebtNoncurrent", "LongTermLoansPayable")
_NEXT_MATURITIES = "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"


def _debt_lines(us_gaap: Dict[str, Any], totals: Dict[Tuple[str, str], float],
                read: Set[Tuple[str, str]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """``{(accn, end): its fact}``: the noncurrent line under a ``_DEBT_LINES`` concept on each filing date with a
    LongTermDebt total and no noncurrent figure long-term debt reads (*read*), the first that makes up the total with a
    current figure of the filing on the date: its DebtCurrent, its current maturities or its maturity schedule's next
    twelve months, within rounding (Corpay's FY2022 $4.723B and $2.314B of maturities, its $7.037B total). A line
    that makes up no total is a part of it (Equinix's FY2024 loans of $0.644B beside $15.334B; BUG-290)."""
    currents = [_by_filing(us_gaap, tag) for tag in ("DebtCurrent",) + _MATURITIES + (_NEXT_MATURITIES,)]
    lines: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for tag in _DEBT_LINES:
        for e in us_gaap.get(tag, {}).get("units", {}).get("USD", []):
            key = (e.get("accn"), e.get("end"))
            if key in lines or key in read or key not in totals or e.get("start") or not isinstance(
                    e.get("val"), (int, float)):
                continue
            if any(figures.get(key, 0) > 0 and _agree(e["val"] + figures[key], totals[key]) for figures in currents):
                lines[key] = e
    return lines


def _debt_totals_set_aside(us_gaap: Dict[str, Any]) -> Dict[str, Any]:
    """*us_gaap* less the LongTermDebt a filing tags beside a noncurrent figure, where its current
    debt holds the current maturities, so the total would count them twice (BA's FY2025 $53.85B
    beside $8.46B of current debt, where its balance sheet has $45.64B), or where it is below the
    noncurrent figure, a note's narrower one (MU's notes without their finance leases; AMT's
    FY2019 $1.9M against $21.13B). Current debt holds them as DebtCurrent or as the maturities,
    above zero, or as short-term borrowings on the same line (AMT's); otherwise the total keeps
    them, as ABBV's FY2021 $75.96B does $12.48B due within a year (BUG-141). Short-term debt now
    reads the maturities beside the borrowings, or tagged LongTermDebtAndCapitalLeaseObligationsCurrent
    alone (BUG-159: ABBV's $12.48B), so they hold them there too, where the noncurrent figure and the
    current debt make up the total (``_MADE_UP``; EXC's FY2023 notes of $0.2B beside its $41.3B).
    A filing that tags no such figure there, nor LongTermDebtNoncurrent, weighs its noncurrent line under a concept
    long-term debt doesn't read (``_debt_lines``), which stands in the total's place as the date's
    LongTermDebtNoncurrent where the total gives way to it (Corpay's FY2021-25: $4.723B at FY2022, not its $7.037B
    total, which counted the $2.314B of current maturities twice; BUG-290)."""
    if _DEBT_TOTAL not in us_gaap:
        return us_gaap
    noncurrent = _noncurrent_debt(us_gaap)
    current, borrowings = _by_filing(us_gaap, "DebtCurrent"), _by_filing(us_gaap, "ShortTermBorrowings")
    maturities = _by_filing(us_gaap, "LongTermDebtCurrent")
    either = _maturities(us_gaap)
    totals = _by_filing(us_gaap, _DEBT_TOTAL)
    lines = _debt_lines(us_gaap, totals, set(noncurrent) | set(_by_filing(us_gaap, "LongTermDebtNoncurrent")))
    noncurrent.update({key: e["val"] for key, e in lines.items()})

    def holds_maturities(key: Tuple[str, str]) -> bool:
        if key in current:
            return current[key] > 0
        if key in borrowings and borrowings[key] > 0 and borrowings[key] == maturities.get(key):
            return True
        if key not in borrowings and maturities.get(key, 0) > 0:
            return True
        return either.get(key, 0) > 0 and (noncurrent[key] + borrowings.get(key, 0) + either[key]
                                           >= (1 - _MADE_UP) * totals[key])

    aside = {key for key, total in totals.items()
             if key in noncurrent and (total < noncurrent[key] or holds_maturities(key))}
    if not aside:
        return us_gaap
    units = us_gaap[_DEBT_TOTAL].get("units", {})
    kept = [e for e in units.get("USD", []) if (e.get("accn"), e.get("end")) not in aside]
    out = {**us_gaap, _DEBT_TOTAL: {**us_gaap[_DEBT_TOTAL], "units": {**units, "USD": kept}}}
    placed = [dict(e) for key, e in sorted(lines.items()) if key in aside]
    if placed:
        body = us_gaap.get("LongTermDebtNoncurrent", {"units": {}})
        ltdnc = body.get("units", {})
        out["LongTermDebtNoncurrent"] = {**body, "units": {**ltdnc, "USD": ltdnc.get("USD", []) + placed}}
    return out


#: Short-term debt a refinancing reclassified to long-term debt (ASC 470-10-45-14).
_REFINANCED = "ShortTermDebtRefinancedAmount"
#: The current maturities of long-term debt: a DebtCurrent below them is a part of the
#: balance sheet's current debt, not its total (IFF's FY2022, PH's FY2023).
_MATURITIES = ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent")


def _maturities(us_gaap: Dict[str, Any]) -> Dict[Tuple[str, str], float]:
    """Each filing date's current maturities: the larger of its ``_MATURITIES`` figures."""
    out: Dict[Tuple[str, str], float] = {}
    for tag in _MATURITIES:
        for key, value in _by_filing(us_gaap, tag).items():
            out[key] = max(out.get(key, value), value)
    return out


#: How far a short-term total can fall short of the commercial paper it holds: a note can tag
#: the paper at its face value, above its carrying amount (FDX's $750M in $745M of borrowings).
#: The nearest figure below the paper otherwise is 5% short of it (IFF's FY2021, a line of its own).
_PAPER_ROUNDING = 0.02
#: The records the extractions build, each reading a commercial paper fact beside the short-term figure it reads
#: itself on the paper's date (``_current_debt_set_aside``): the annual record, from the annual reports; the
#: quarterly checkpoints, from the 10-Qs and 10-Ks; and each filing's vintage, from that filing (BUG-256, BUG-257).
_ANNUAL_VIEW, _QUARTERLY_VIEW, _VINTAGE_VIEW = "annual", "quarterly", "vintage"


def _current_debt_set_aside(us_gaap: Dict[str, Any], view: str,
                            combined: FrozenSet[Tuple[str, str]] = frozenset()) -> Dict[str, Any]:
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
    DebtCurrent is read as filed, before a refinancing sets it aside. The borrowings and maturities
    *combined* into a DebtCurrent for that view's records (``_current_debt_combinations``) are among
    the DebtCurrent figures, held by their borrowings as filed: NEE's FY2012 10-K shows $0 of
    borrowings and $0.808B of maturities at 2011-12-31, after the 10-Qs whose DebtCurrent holds the
    paper, so the year end reads the paper beside that combination (BUG-267). Paper the total is
    smaller than (``_PAPER_ROUNDING``) is still added: a line of its own (AAPL's, beside its term
    debt), above the borrowings (HON's FY2013) or alone (WMB's)."""
    if _REFINANCED not in us_gaap and "CommercialPaper" not in us_gaap:
        return us_gaap
    current, borrowings = _by_filing(us_gaap, "DebtCurrent"), _by_filing(us_gaap, "ShortTermBorrowings")
    maturities = _maturities(us_gaap)
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
        # the latest-filed DebtCurrent on *end* among *forms*, combinations too, else their latest ShortTermBorrowings
        for figures in (current.keys() | combined, borrowings):
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


#: How much of a LongTermDebt total the noncurrent figure and the current debt must make up for the total to
#: give way to them (NRG's notes run 1-3% above its balance sheet's lines); else the noncurrent figure is a note's
#: (EXC's FY2023 LongTermNotesPayable, $0.2B beside $41.3B; OMC's FY2011 notes without its convertibles).
_MADE_UP = 0.05
#: Short-term borrowings' own parts a filing can tag beside them: borrowings that are the maturities and these
#: hold the maturities (IBM's short-term loans and paper, EBAY's other borrowings); borrowings that are these alone
#: are a line of their own (DOW's FY2022 notes payable, which equal its maturities), unless the maturities are the
#: same figure too: one line tagged three ways (ICE's FY2015 paper, AMD's 2016 line of credit; BUG-267).
_BORROWING_PARTS = ("CommercialPaper", "OtherShortTermBorrowings", "ShortTermBankLoansAndNotesPayable",
                    "ShortTermNonBankLoansAndNotesPayable", "LinesOfCreditCurrent", "BankOverdrafts")
#: The filer's own total debt; and its debt and lease obligations, often the long-term debt with its maturities
#: alone (PNW's, HSY's), so it only ever tells that the borrowings are a line of their own (DUK's FY2018).
_OWN_TOTAL, _OWN_DEBT_AND_LEASES = "DebtLongtermAndShorttermCombinedAmount", "DebtAndCapitalLeaseObligations"
#: Borrowings at most this much above the maturities are the maturities and a little more (IBM's FY2024 $5.089B
#: beside $5.059B; the same figure tagged twice, APTV's); separate lines that happen to agree stay as filed (ATO's
#: FY2018 $576M and $575M).
_HOLD_MARGIN = 0.01
#: The current liabilities a balance sheet shows as lines apart from its debt, which the current-liabilities bound
#: reads beside the borrowings and the maturities: trade payables (the larger of these), accrued compensation and
#: income taxes (the larger of these), else the payables with accruals and income taxes; or the payables with
#: accruals alone (BUG-267).
_TRADE_PAYABLES = ("AccountsPayableCurrent", "AccountsPayableTradeCurrent")
_INCOME_TAXES = ("TaxesPayableCurrent", "AccruedIncomeTaxesCurrent")
_EMPLOYEE_ACCRUALS = "EmployeeRelatedLiabilitiesCurrent"
_PAYABLES_AND_ACCRUALS = "AccountsPayableAndAccruedLiabilitiesCurrent"
#: How far borrowings can fall short of the maturities they hold: a line rounded to a tenth of a million (BE's $1.4M
#: of borrowings beside its $1.424M of maturities, 1.7% less).
_MATURITIES_ROUNDING = 0.02


def _kept_totals(us_gaap: Dict[str, Any], view: str) -> Tuple[Set[Tuple[str, str]], Set[str]]:
    """Where long-term debt may read a LongTermDebt total that holds the maturities: the filing dates whose total
    BUG-141 keeps (no LongTermDebtNoncurrent there, and the total isn't the filing's noncurrent figure under the
    total's tag, as ODFL's FY2016 is), and the year ends where an annual report's is kept and no annual report tags
    LongTermDebtNoncurrent: the annual record and the quarterly checkpoints read their long-term and short-term debt
    from different filings (EXC's FY2023 10-K tags the total, its FY2024 10-K the borrowings and maturities; its 10-Qs'
    comparative LongTermDebtNoncurrent the annual record never reads; ADBE's 10-Q totals no annual record reads at all).
    A vintage reads its own filing alone, so no year end is kept there: XEL's FY2024 10-K's vintage read $29.114B as
    known then and $28.011B once its FY2025 10-K had tagged the year end's noncurrent line as LongTermDebt (BUG-267)."""
    ltdnc = _by_filing(us_gaap, "LongTermDebtNoncurrent")
    annual_ltdnc = {e["end"] for e in us_gaap.get("LongTermDebtNoncurrent", {}).get("units", {}).get("USD", [])
                    if e.get("end") and not e.get("start") and e.get("form") in _ANNUAL_FORMS}
    noncurrent = _noncurrent_debt(us_gaap)
    keys: Set[Tuple[str, str]] = set()
    dates: Set[str] = set()
    for e in us_gaap.get(_DEBT_TOTAL, {}).get("units", {}).get("USD", []):
        if not e.get("accn") or not e.get("end") or e.get("start") or not isinstance(e.get("val"), (int, float)):
            continue
        key, form = (e["accn"], e["end"]), e.get("form") or ""
        if form not in _QUARTERLY_FORMS and form not in _ANNUAL_FORMS:
            continue  # (an 8-K's figures are no record's)
        if key in ltdnc or (key in noncurrent and _agree(e["val"], noncurrent[key])):
            continue
        keys.add(key)
        if form in _ANNUAL_FORMS and e["end"] not in annual_ltdnc:
            dates.add(e["end"])
    if view == _VINTAGE_VIEW:
        dates.clear()
    return keys, dates


def _beside_current_debt(us_gaap: Dict[str, Any]) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """``{(accn, end): (its LiabilitiesCurrent, the least its other lines come to)}`` on each filing date that tags its
    current liabilities: its trade payables, accrued compensation and income taxes, else its payables with accruals
    and income taxes, or those payables alone (``_TRADE_PAYABLES``). Lines of the balance sheet apart from its debt:
    over the cached filings they never exceed the current liabilities on any of the 52,124 dates that tag them, nor
    beside a DebtCurrent on the 11,520 such dates but CVX's and CSCO's 18 (a DebtCurrent before a refinancing,
    BUG-142's, and a mis-tag). Lines above the current liabilities are no part of them (a tagging error)."""
    figures = {tag: _by_filing(us_gaap, tag)
               for tag in _TRADE_PAYABLES + _INCOME_TAXES + (_EMPLOYEE_ACCRUALS, _PAYABLES_AND_ACCRUALS)}

    def line(tag: str, key: Tuple[str, str]) -> float:
        return max(figures[tag].get(key, 0.0), 0.0)

    out: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for key, current in _by_filing(us_gaap, "LiabilitiesCurrent").items():
        trade, taxes = max(line(t, key) for t in _TRADE_PAYABLES), max(line(t, key) for t in _INCOME_TAXES)
        lines = max(trade + line(_EMPLOYEE_ACCRUALS, key) + taxes if trade
                    else line(_PAYABLES_AND_ACCRUALS, key) + taxes, line(_PAYABLES_AND_ACCRUALS, key))
        if current > 0:
            out[key] = (current, lines if lines <= current else 0.0)
    return out


def _current_debt_combinations(as_filed: Dict[str, Any], view: str) -> Tuple[
        Dict[Tuple[str, str], Dict[str, Any]], Set[Tuple[Any, Any]]]:
    """The DebtCurrent facts *view*'s records read where a filing tags short-term borrowings and current maturities on
    a date but no DebtCurrent *as_filed* (before BUG-142 sets CVX's aside, whose borrowings hold them): one of the two
    together, ``{(accn, end): fact}``, read first by short-term debt (VZ's FY2023 $12.973B of maturities beside $0 of
    borrowings, LIN's FY2025 $4.510B and $1.796B; BUG-159); and the filing dates whose
    LongTermDebtAndCapitalLeaseObligationsCurrent a long-term total holds (``_kept_totals``), which short-term debt
    doesn't read again. One maturities figure that is the other and the borrowings is the figure (VZ's FY2020
    LongTermDebtCurrent). Every figure is read as filed: BUG-143 sets aside IBM's paper inside its borrowings.

    Not where the borrowings hold the maturities: the filer's own total debt is the noncurrent figure and the borrowings
    alone (GL's); the current liabilities can't hold both lines beside the filing's other ones
    (``_beside_current_debt``: COO's FY2016, PEP's and YUM's quarters, BUG-267); the borrowings are the maturities and
    their own parts (IBM's FY2021 $6.787B, $6.764B and $22M of loans) or the maturities and at most ``_HOLD_MARGIN``
    more (IBM's FY2024, and one line tagged as the borrowings, a part of them and the maturities: ICE's FY2015 paper,
    AMD's 2016 line of credit). Borrowings that are their own parts are a line of their own (DOW's FY2022 notes
    payable, $362M beside $362M due within one year, apart in its 10-K's other column). A quarter its own evidence
    leaves undecided takes its latest annual report's word, on the latest year end at or before its date (IBM's Q1
    2026 10-Q): an annual report there shows the borrowings holding the maturities, or tags as DebtCurrent the line the
    10-Q tags as its borrowings there (PEP's 10-Ks since FY2011); no older year's (PEP's quarters took its FY2010 10-K's
    undecided entry for "separate"), and only where the borrowings can hold them, at least the maturities less
    ``_MATURITIES_ROUNDING`` (ATO's Q1 FY2019: $0 beside $575M).

    The annual record and the quarterly checkpoints read several filings: the latest one of the record's forms that
    tags a DebtCurrent, or borrowings with maturities, on a date decides it (HWM's FY2020 10-K, whose borrowings are its
    maturities, over its FY2019 10-K's combination: $5.940B, not $6.943B). A vintage reads its own filing's."""
    kept_keys, kept_dates = _kept_totals(as_filed, view)

    def total_holds(key: Tuple[str, str]) -> bool:
        return key in kept_keys or key[1] in kept_dates

    held_by_totals = {(e.get("accn"), e.get("end")) for e in as_filed.get(_MATURITIES[1], {}).get("units", {}).get(
        "USD", []) if total_holds((e.get("accn"), e.get("end")))} if kept_keys or kept_dates else set()
    current = _by_filing(as_filed, "DebtCurrent")
    mats = [_by_filing(as_filed, tag) for tag in _MATURITIES]
    parts: Dict[Tuple[str, str], float] = defaultdict(float)
    for tag in _BORROWING_PARTS:
        for key, value in _by_filing(as_filed, tag).items():
            parts[key] += max(value, 0)
    own, own_debt_and_leases = _by_filing(as_filed, _OWN_TOTAL), _by_filing(as_filed, _OWN_DEBT_AND_LEASES)
    noncurrent = _noncurrent_debt(as_filed, ("LongTermDebtNoncurrent",) + _DEBT_NONCURRENT)
    beside = _beside_current_debt(as_filed)
    borrowings = _by_filing(as_filed, "ShortTermBorrowings")

    def maturities(key: Tuple[str, str]) -> float:
        return max(m.get(key, 0.0) for m in mats)

    def apart(key: Tuple[str, str]) -> bool:
        # the same filing's other columns show the borrowings as their own parts beside other maturities
        return any(other[0] == key[0] and other[1] != key[1] and parts.get(other, 0.0) > 0 and _agree(b, parts[other])
                   and 0 < maturities(other) != b for other, b in borrowings.items())

    def holds(key: Tuple[str, str], b: float, m: float) -> Optional[bool]:
        """Whether the borrowings hold the maturities, where the filing tells (else None)."""
        if key in noncurrent:  # its own total debt, where it is one of the two within rounding: the nearer
            for total, both_only in ((own.get(key), False), (own_debt_and_leases.get(key), True)):
                if total is None:
                    continue
                alone, both = abs(total - noncurrent[key] - b), abs(total - noncurrent[key] - b - m)
                if min(alone, both) <= _AGREE * abs(total) and (both < alone or not both_only):
                    return alone < both
        if key in beside and b + m + beside[key][1] > beside[key][0] * (1 + _AGREE):
            return True  # the current liabilities can't hold both lines beside the filing's other ones
        if parts[key] > 0 and _agree(b, parts[key]):
            if b != m or apart(key):
                return False  # its own parts: a line of their own
        if parts[key] > 0 and _agree(b, m + parts[key]):
            return True
        return True if 0 <= b - m <= _HOLD_MARGIN * b else None

    decided: Dict[Tuple[str, str], Tuple[Optional[bool], Dict[str, Any], float]] = {}
    for e in as_filed.get("ShortTermBorrowings", {}).get("units", {}).get("USD", []):
        if not e.get("accn") or not e.get("end") or e.get("start") or not isinstance(e.get("val"), (int, float)):
            continue
        key, b = (e["accn"], e["end"]), e["val"]
        ms = [m[key] for m in mats if m.get(key, 0) > 0]
        if key in decided or key in current or b < 0 or not ms or total_holds(key):
            continue
        hi, lo = max(ms), min(ms)
        decided[key] = (holds(key, b, hi), e, hi if lo != hi and _agree(hi, b + lo) else b + hi)
    filings = {e["accn"]: (e.get("form") or "", e.get("filed") or "") for tag in ("DebtCurrent", "ShortTermBorrowings")
               for e in as_filed.get(tag, {}).get("units", {}).get("USD", []) if e.get("accn")}
    # what the annual reports say on each year end, for the quarters that end on or after it
    year_ends = sorted({e["end"] for e in as_filed.get(_BALANCE_SHEET_TOTAL, {}).get("units", {}).get("USD", [])
                        if e.get("form") in _ANNUAL_FORMS and e.get("end") and not e.get("start")})
    annual_held = {key[1] for key, (held, e, _) in decided.items() if held is True and e.get("form") in _ANNUAL_FORMS}
    annual_current: Dict[str, List[float]] = defaultdict(list)
    for key, value in current.items():
        if filings[key[0]][0] in _ANNUAL_FORMS:
            annual_current[key[1]].append(value)

    def year_end_holds(key: Tuple[str, str]) -> bool:
        ends = [end for end in year_ends if end <= key[1]]
        if not ends:
            return False
        end = max(ends)
        line = borrowings.get((key[0], end))  # this 10-Q's borrowings on its latest year end
        return end in annual_held or (line is not None and any(_agree(v, line) for v in annual_current[end]))

    combined: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for key, (held, e, value) in decided.items():
        if held is None and e.get("form") not in _ANNUAL_FORMS:
            held = e["val"] >= maturities(key) * (1 - _MATURITIES_ROUNDING) and year_end_holds(key)
        if not held:
            combined[key] = {**e, "val": value}
    if view != _VINTAGE_VIEW:  # each date as the latest filing of the record's forms that tells it
        forms = _QUARTERLY_FORMS if view == _QUARTERLY_VIEW else _ANNUAL_FORMS
        latest: Dict[str, Tuple[str, str]] = {}
        for key in current.keys() | decided.keys():
            if filings[key[0]][0] in forms:
                latest[key[1]] = max(latest.get(key[1], ("", "")), (filings[key[0]][1], key[0]))
        combined = {key: fact for key, fact in combined.items() if latest.get(key[1], ("", ""))[1] == key[0]}
    return combined, held_by_totals


def _current_debt_combined(us_gaap: Dict[str, Any], combined: Dict[Tuple[str, str], Dict[str, Any]],
                           held_by_totals: Set[Tuple[Any, Any]]) -> Dict[str, Any]:
    """*us_gaap* with the borrowings and maturities *combined* into DebtCurrent facts, and less the
    LongTermDebtAndCapitalLeaseObligationsCurrent a long-term total holds (*held_by_totals*), so short-term debt
    doesn't read them again (``_current_debt_combinations``, BUG-159)."""
    out = dict(us_gaap)
    if _MATURITIES[1] in out and held_by_totals:
        units = out[_MATURITIES[1]].get("units", {})
        out[_MATURITIES[1]] = {**out[_MATURITIES[1]], "units": {**units, "USD": [
            e for e in units.get("USD", []) if (e.get("accn"), e.get("end")) not in held_by_totals]}}
    if combined:
        body = out.get("DebtCurrent", {"units": {}})
        units = body.get("units", {})
        out["DebtCurrent"] = {**body, "units": {**units, "USD": units.get("USD", []) + list(combined.values())}}
    return out


#: The depreciation alone, which D&A reads after its totals (``canonical.py``), and those totals in its order.
_DEPRECIATION = "Depreciation"
_DA_TOTALS = tuple(tag for tag in _FIELDS["depreciation_amortization"].tags if tag != _DEPRECIATION)
#: A filing's amortization of intangibles beside its depreciation: the cash flow statement's add-back, which can hold
#: the amortization in cost of revenue too (INTU's acquired technology), else the intangibles' own figure.
_AMORTIZATION = ("AdjustmentForAmortization", "AmortizationOfIntangibleAssets")
#: The tag the two enter as: the last-ranked total, so a filing's own total of another tag outranks it, and of two
#: filings' DepreciationAndAmortization for a year the later one stands, as for any restated figure.
_DA_COMBINED = "DepreciationAndAmortization"


def _by_period(us_gaap: Dict[str, Any], tag: str) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """A tag's dollar facts over a period, by filing and period: ``{(accn, start, end): fact}``."""
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for e in us_gaap.get(tag, {}).get("units", {}).get("USD", []):
        if e.get("accn") and e.get("start") and e.get("end") and isinstance(e.get("val"), (int, float)):
            out.setdefault((e["accn"], e["start"], e["end"]), e)
    return out


def _span_at_least_a_year(key: Tuple[str, str, str]) -> bool:
    """Whether a ``(accn, start, end)`` period is a year's (at least ``_FULL_YEAR_MIN_DAYS``), not a 10-Q's to date."""
    start, end = date.fromisoformat(key[1][:10]), date.fromisoformat(key[2][:10])
    return (end - start).days >= _FULL_YEAR_MIN_DAYS


def _depreciation_combined(us_gaap: Dict[str, Any]) -> Dict[str, Any]:
    """*us_gaap* with one DepreciationAndAmortization fact of a filing's depreciation and its amortization of
    intangibles on each period it tags them on lines of their own, with no total that can hold them.

    D&A reads a period's first total, else Depreciation, and AbbVie's cash flow statement shows its depreciation ($762M
    for FY2025) and its amortization of intangible assets ($7.377B) apart, with no total: its D&A was the depreciation
    alone (BUG-170). The amortization is the cash flow statement's (``_AMORTIZATION``); a figure at or below zero is
    none. A total at least as large as it is the filing's D&A and stays, as most are (DHR's FY2023 $2.166B, its $675M of
    depreciation and $1.491B of amortization). A total no larger can't hold it: it is the depreciation line under a
    total's tag (CME's $107.5M beside $223.4M of amortization; EME's, equal to its depreciation), and is set aside, so
    another total of the filing that can hold it is read (MCK's), and with none the D&A is the depreciation, else that
    line, plus the amortization. No other amortization joins: capitalized software's is inside the depreciation at one
    filer (EXPE's) and apart at another. Each filing's periods are judged alone, so the annual record, the quarters
    (the 10-Qs' figures to date) and each vintage read the same figures.

    Where both amortizations are tagged and no total exceeds the cash flow statement's, the intangibles' is the test:
    a total holds it only as large as the depreciation and it together. ALL's FY2021 total ($1.086B) holds its
    depreciation ($411M) and its intangibles' amortization ($376M) beside the statement's broader $1.281B (BUG-299);
    CSGP's 2012 six months ($4.7M beside $4.5M and $4.2M) is its depreciation line.

    A total larger than the amortization is the depreciation line too where the filings show it (BUG-274). With the
    amortization it makes another total of the period: in the same filing (SWK's FY2023 $432.4M and $625.1M, with
    $192.7M), or in an earlier one on the same basis (the same revenue: HLT's 2016 restated after its spin-offs isn't)
    and for an amortization at least 5% of it, beyond what a restatement's change can match by chance (MRSH's FY2023
    10-K tagged $713M, its later 10-Ks $370M beside $343M; not DAL's FY2016 $1.886B and $17M beside $1.902B). Or it is
    below the filing's Depreciation, or below the depreciation and amortization its 10-Qs gave for part of the
    period (as first reported), and is no gross total; but a net add-back is the cash flow statement's own figure
    (BMY's "Depreciation and amortization, net", $382M beside $448M), so such a total is a line only where the
    filings, by then, show its tag to carry one (HUM's FY2024 $839M beside $908M) or the filing tags a total that
    holds the two (BA's FY2009 $1.066B beside $1.459B, $207M and $1.678B). Only filings filed by then are read, so a
    vintage reads what was known. Beside such a line another total holds the two only if it is as large as the
    depreciation and the amortization together (HD's FY2023 $2.455B, an income statement's line, doesn't hold its
    $2.796B line and $179M); with none, the two must come to more than the line, else it held them (LMT's FY2019
    $906M beside $794M and $111M).

    A total that with the amortization makes another is the depreciation line under its tag in the filing's other
    periods too, one line of its statements, where it doesn't differ from their Depreciation: HD's 10-K of March 2025
    tags FY2023's $2.796B and FY2025's $3.336B, beside $425M. Known only so, it sets aside the period's smaller totals
    (HD's $3.034B) and gives way to a larger one, which holds it (SBUX's FY2018 $1.306B beside its $1.247B line).

    Beside a line, the filing's D&A enters under the line's tag, so a later filing's figure stands over an earlier
    one's as for any restatement (DDOG's FY2021 $22.9M over its first 10-K's $18.5M line). A year's 10-Qs are read so
    only where its latest 10-K is, so its quarters stay on one basis (PSTG's FY2022)."""
    amortization: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    figures: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
    for tag in _AMORTIZATION:
        for key, e in _by_period(us_gaap, tag).items():
            amortization.setdefault(key, e)
            if e["val"] > 0:
                figures[key].append(e["val"])
    amortization = {key: e for key, e in amortization.items() if e["val"] > 0}
    least = {key: min(figures[key]) for key in amortization}
    if not amortization:
        return us_gaap
    depreciation = _by_period(us_gaap, _DEPRECIATION)
    totals = {tag: _by_period(us_gaap, tag) for tag in _DA_TOTALS}
    # every filing's totals and its depreciation with the amortization, by period, with the filing's date
    filed_totals: Dict[Tuple[str, str], List[Tuple[str, str, float]]] = defaultdict(list)
    for by_key in totals.values():
        for (accn, start, end), e in by_key.items():
            filed_totals[(start, end)].append((accn, e.get("filed") or "", e["val"]))
    first: Dict[Tuple[str, str], str] = {}  # when a period was first reported: its own year's basis
    for by_key in (*totals.values(), depreciation, amortization):
        for (_, start, end), e in by_key.items():
            first[(start, end)] = min(first.get((start, end), e.get("filed") or ""), e.get("filed") or "")
    first_totals: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for span, entries in filed_totals.items():
        first_totals[span] = [val for _, when, val in entries if when == first[span]]
    to_date: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)
    for key in amortization:  # a depreciation tagged as a total too may hold the amortization (BBY's)
        d = depreciation.get(key)
        if d is not None and not any(key in t and _agree(t[key]["val"], d["val"]) for t in totals.values()):
            to_date[key[1]].append((key[2], d.get("filed") or "", d["val"] + least[key]))
    revenue = [_by_period(us_gaap, tag) for tag in _FIELDS["revenue"].tags]

    def revenue_of(key: Tuple[str, str, str]) -> Optional[float]:
        return next((by_key[key]["val"] for by_key in revenue if key in by_key), None)

    def same_basis(accn: str, key: Tuple[str, str, str]) -> bool:
        # another filing gives the period the revenue this one does: a restated period (HLT's 2016 after its spin-offs)
        # is on another basis, and its totals show nothing
        mine, theirs = revenue_of(key), revenue_of((accn, key[1], key[2]))
        return mine is not None and theirs is not None and _agree(mine, theirs)

    def a_part(key: Tuple[str, str, str], total: float) -> bool:
        # with the amortization it makes another total of the period filed by then: another filing's only on the same
        # basis and for an amortization well beyond the agreement's band, which a restatement's change can match
        # (DAL's FY2016 $1.886B and $17M make its first 10-K's $1.902B)
        filed = amortization[key].get("filed") or ""
        return any(when <= filed and _agree(other, total + a) and not _agree(total + a, total)
                   and (accn == key[0] or (a >= 10 * _AGREE * total and same_basis(accn, key)))
                   for accn, when, other in filed_totals[key[1:]] for a in figures[key])

    periods: Dict[Tuple[str, str, str], Tuple[List[Tuple[str, Dict[str, Any]]], float]] = {}
    tagged_lines: Dict[str, List[str]] = defaultdict(list)  # a total's tag the filings show to carry a line, when
    for key, fact in amortization.items():
        here = [(tag, by_key[key]) for tag, by_key in totals.items() if key in by_key]
        # the amortization a total must exceed to hold it: the cash flow statement's, unless no total does (ALL's)
        bar = fact["val"] if any(e["val"] > fact["val"] for _, e in here) else least[key]
        periods[key] = (here, bar)
        for tag, e in here:
            if e["val"] > bar and a_part(key, e["val"]):
                tagged_lines[tag].append(fact.get("filed") or "")
    shown: Set[Tuple[str, str]] = set()  # (filing, tag): a filing's total shown to be its depreciation line
    judged: List[Tuple[Tuple[str, str, str], float, Optional[Dict[str, Any]], List[Tuple[str, Dict[str, Any]]],
                       List[Tuple[str, Dict[str, Any]]], List[Tuple[str, Dict[str, Any]]]]] = []
    for key, fact in amortization.items():
        part, filed = fact["val"], fact.get("filed") or ""
        here, bar = periods[key]
        dep = depreciation.get(key)

        def a_line(tag: str, total: float) -> bool:
            if a_part(key, total):
                return True
            # below the filing's depreciation, or below the depreciation and amortization its 10-Qs gave for part of
            # the period (judged only as the report that first gave the period gave it: a later comparative can be on
            # another basis, GE's FY2013 after GE Capital left), it is no gross total
            short = (dep is not None and total < dep["val"] and not _agree(total, dep["val"])) or (
                any(_agree(total, val) for val in first_totals[key[1:]]) and not _agree(total + least[key], total)
                and any(end < key[2] and when <= filed and both > total and not _agree(both, total)
                        for end, when, both in to_date[key[1]]))
            # but a net add-back (BMY's "Depreciation and amortization, net") is the cash flow statement's own figure:
            # it is a line only where the filings, by then, show the tag to carry one (HUM's FY2024 $839M beside $908M:
            # its 10-K of 2024 shows FY2022's $709M the line of $845M) or the filing tags a total that holds the two
            # (BA's FY2009 $1.066B beside $1.459B of depreciation, $207M and a $1.678B total)
            base = (dep["val"] if dep is not None else total) + least[key]
            return short and (any(when <= filed for when in tagged_lines[tag]) or any(
                e["val"] > total and (e["val"] >= base or _agree(e["val"], base)) for _, e in here))

        low = [(tag, e) for tag, e in here if 0 < e["val"] <= bar]
        if bar < part and dep is not None:
            # against the intangibles' own figure a total holds it only as large as the depreciation and it together
            # (ALL's $1.086B beside $411M and $376M; CSGP's 2012 six months, $4.7M beside $4.5M and $4.2M, doesn't)
            both = dep["val"] + bar
            low += [(tag, e) for tag, e in here if bar < e["val"] < both and not _agree(e["val"], both)]
        lines = [(tag, e) for tag, e in here if (tag, e) not in low and e["val"] > bar and a_line(tag, e["val"])]
        # a total that with the amortization makes another is the line under its tag throughout its filing
        shown.update((key[0], tag) for tag, e in lines if a_part(key, e["val"]))
        judged.append((key, part, dep, here, low, lines))
    final: Dict[Tuple[str, str, str], Tuple[float, Optional[Dict[str, Any]], List[Tuple[str, Dict[str, Any]]],
                                            List[Tuple[str, Dict[str, Any]]], List[Tuple[str, Dict[str, Any]]]]] = {}
    for key, part, dep, here, low, lines in judged:
        # the filing's same line in its other periods (HD's FY2025), giving way to a larger total (SBUX's FY2018)
        same = [(tag, e) for tag, e in here if (key[0], tag) in shown and e["val"] > 0
                and (tag, e) not in low + lines and (dep is None or _agree(e["val"], dep["val"]))]
        if same and not lines:
            least_line = min(e["val"] for _, e in same)
            lines = same + [(tag, e) for tag, e in here if (tag, e) not in low + same and e["val"] < least_line
                            and not _agree(e["val"], least_line)]
        elif lines:
            lines += same
            base = (dep or lines[0][1])["val"] + least[key]
            lines += [(tag, e) for tag, e in here if (tag, e) not in low + lines
                      and e["val"] < base and not _agree(e["val"], base)]
        final[key] = (part, dep, here, low, lines)
    # a year's 10-Qs are read so only where its latest 10-K is, so its quarters stay on one basis (PSTG's FY2022: nine
    # months of $47.2M and $12.4M make the 10-Q's $59.6M, but the 10-K's $65.9M beside $16.8M is no $83.2M)
    latest: Dict[str, Tuple[str, bool]] = {}
    for key, (_, _, _, _, lines) in final.items():
        when = amortization[key].get("filed") or ""
        if _span_at_least_a_year(key) and when >= latest.get(key[1], ("", False))[0]:
            latest[key[1]] = (when, bool(lines))
    years = {start for start, (_, lined) in latest.items() if lined}
    aside: Dict[str, Set[Tuple[str, str, str]]] = defaultdict(set)
    added: List[Dict[str, Any]] = []
    for key, (part, dep, here, low, lines) in final.items():
        if lines and not _span_at_least_a_year(key) and key[1] not in years:
            lines = []
        if lines and len(low) + len(lines) == len(here):
            # the depreciation and the amortization must come to more than the line they replace, else it held them
            # (LMT's FY2019: $794M and $111M beside a $906M line)
            both = (dep or lines[0][1])["val"] + part
            if any(e["val"] >= both or _agree(e["val"], both) for _, e in lines):
                lines = []
        for tag, _ in low + lines:
            aside[tag].add(key)
        # beside a line the filings show, the filing's D&A enters under its tag too, so a later filing's figure stands
        # over an earlier one's as for any restated figure (DDOG's FY2021: not its first 10-K's $18.5M line)
        under = min((t for t, _ in lines), key=_DA_TOTALS.index) if lines else _DA_COMBINED
        if len(low) + len(lines) < len(here):
            held = [(tag, e) for tag, e in here if (tag, e) not in low + lines]
            if lines and _DA_TOTALS.index(under) < _DA_TOTALS.index(held[0][0]):
                added.append({**held[0][1], "_tag": under})
            continue  # a total that can hold the amortization
        line = dep or (lines[0][1] if lines else low[0][1] if low else None)
        if line is not None:
            added.append({**line, "val": line["val"] + part, "_tag": under})
    if not aside and not added:
        return us_gaap
    out = dict(us_gaap)
    for tag, keys in aside.items():
        units = out[tag].get("units", {})
        out[tag] = {**out[tag], "units": {**units, "USD": [
            e for e in units.get("USD", []) if (e.get("accn"), e.get("start"), e.get("end")) not in keys]}}
    by_tag: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in added:
        by_tag[e.pop("_tag")].append(e)
    for tag, entries in by_tag.items():
        body = out.get(tag, {"units": {}})
        units = body.get("units", {})
        out[tag] = {**body, "units": {**units, "USD": units.get("USD", []) + entries}}
    return out


def _filer_facts(facts: Dict[str, Any], view: str) -> Dict[str, Any]:
    """A filer's us-gaap facts as the extractions of *view* read them, in this order: its dollar filings', the
    balance-sheet-only tags on their balance sheets' dates, less the total with restricted cash where a filing's cash
    equivalents and restricted cash make it up, less Cash where it is a part of a total without restricted cash, net
    outflows as paid, LongTermDebt set aside where its noncurrent
    figure should stand (judged on DebtCurrent as filed); then, judged on those facts as filed, the borrowings and the
    maturities that view's records read as one DebtCurrent where a filing tags none (``_current_debt_combinations``),
    DebtCurrent before a refinancing set aside, 0.0 for the CommercialPaper the short-term total that view's record
    reads on its date holds (those combinations among them), the combinations themselves, and last the depreciation
    and the amortization of intangibles as one D&A figure where the filing tags no total that can hold them."""
    totals = _debt_totals_set_aside(_net_outflows_as_paid(_cash_parts_set_aside(_cash_totals_set_aside(
        _on_balance_sheet_dates(_dollar_filings(facts.get("facts", {}).get("us-gaap", {})))))))
    combined, held_by_totals = _current_debt_combinations(totals, view)
    return _depreciation_combined(_current_debt_combined(_current_debt_set_aside(totals, view, frozenset(combined)),
                                                         combined, held_by_totals))


#: The quarterly reports, whose balance sheets are a quarter's: a 10-K's year end is the annual record's.
_QUARTERLY_REPORTS = {"10-Q", "10-Q/A"}
#: The concepts a 10-Q's long-term debt line can carry that long-term debt's own don't (``_long_term_debt_lines``): a
#: debt concept, but no current or short-term one, and none of these figures about the debt: its fair value, face or
#: principal, repayments and capacity, issuance costs, discounts and premiums, a receivable, an asset, an equity
#: component, an acquisition's, the liabilities other than it.
_DEBT_CONCEPT = re.compile(r"(Debt|Notes|Loans?|Borrowing|Bonds?|Debenture|LineOfCredit|CapitalLease|FinanceLease)")
_NOT_A_LINE = re.compile(r"(Receivable|FairValue|Face|Principal|Repayment|Capacity|Available|Unused|Remaining|"
                         r"Acquired|Assumed|Asset|Investment|Securities|OperatingLease|Lessee|Equity|Component|"
                         r"Interest|Issuance|Discount|Premium|Unamortized|LiabilitiesOtherThan)")
#: Debt figures that are no long-term debt line: the filer's own totals with its short-term debt, and an
#: instrument's carrying amount, often one note's (SLB's $0.5B at 2020-12-31; BUG-125).
_NOT_LONG_TERM = (_OWN_TOTAL, _OWN_DEBT_AND_LEASES, "DebtInstrumentCarryingAmount")


def _line_concept(tag: str) -> bool:
    """Whether *tag* can carry a 10-Q's long-term debt line (``_DEBT_CONCEPT``). "...Current" isn't "...Noncurrent"."""
    if tag in _NOT_LONG_TERM or tag.startswith("ShortTerm") or tag.endswith("Current"):
        return False
    return bool(_DEBT_CONCEPT.search(tag)) and not _NOT_A_LINE.search(tag)


def _noncurrent_concept(tag: str) -> bool:
    """Whether *tag* names a noncurrent figure, which holds no current maturities: "...Noncurrent", or "LongTerm..." but
    not one including them (LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities)."""
    return tag.endswith("Noncurrent") or (tag.startswith("LongTerm") and "IncludingCurrent" not in tag)


def _long_term_debt_lines(us_gaap: Dict[str, Any], annual: Dict[Any, Dict[str, Any]],
                          quarters: Dict[str, Dict[str, Any]]) -> Dict[str, Tuple[float, str]]:
    """``{quarter end: (long-term debt, tag)}`` for the quarterly checkpoints with no long-term debt, from a 10-Q's line
    under a concept long-term debt's own don't include (``_line_concept``): one it tags at its year-end column with the
    figure an annual report filed there under the tag the annual record reads, which makes it the same line (ORCL's
    10-Qs tag their notes as LongTermNotesAndLoans, its 10-Ks as LongTermNotesPayable; CVX's 10-Qs as
    LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities; BUG-171). Beside the quarter's short-term debt a
    line is read only where it holds no current maturities: a noncurrent concept (``_noncurrent_concept``), or the
    annual record's noncurrent figure at a year end where an annual report shows current maturities above zero (CVX's
    FY2024 $20.135B beside $4.012B); Kenvue's line, its 10-K's total at a year end without maturities, holds the $750M
    its DebtCurrent holds too on 2024-09-29. A year end the annual record has no long-term debt on carries no line.
    Lines that disagree on the date are no line, and the latest 10-Q whose line shows the date decides it (TGT's 10-Qs
    show the year-earlier quarter). A 10-Q whose line is under its own concept, which the company facts don't carry,
    has none to read (TMUS's since 2024, CAT's)."""
    # the figures the rule reads: debt concepts (long-term debt's own among them) and the current maturities
    read = [tag for tag in us_gaap if _line_concept(tag) or tag in _MATURITIES]
    columns: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(dict)  # {accn: {end: {tag: figure}}}
    for tag in read:
        for (accn, end), value in _by_filing(us_gaap, tag).items():
            columns[accn].setdefault(end, {})[tag] = value
    filings = {e["accn"]: (e.get("form") or "", e.get("filed") or "") for tag in read
               for e in us_gaap[tag].get("units", {}).get("USD", []) if e.get("accn")}
    figures: Dict[str, Set[float]] = {}  # each year end's long-term debt as the annual reports filed it
    without_maturities: Set[str] = set()  # year ends where it is a noncurrent figure beside maturities above zero
    for period in annual.values():
        if not period.get("long_term_debt"):
            continue  # no line carries a year's debt that the annual record has none of (NVDA's FY2016, restated to 0)
        end, tag = period["period_end"], period["_source_tags"]["long_term_debt"]
        filed = [cols[end] for accn, cols in columns.items() if filings[accn][0] in _ANNUAL_FORMS and end in cols]
        figures[end] = {col[tag] for col in filed if col.get(tag, 0) > 0}
        if tag in ("LongTermDebtNoncurrent",) + _DEBT_NONCURRENT and any(
                col.get(m, 0) > 0 for col in filed for m in _MATURITIES):
            without_maturities.add(end)
    found: Dict[str, Tuple[Tuple[str, str], float, str]] = {}
    for accn, cols in columns.items():
        if filings[accn][0] not in _QUARTERLY_REPORTS:
            continue
        lines: Dict[str, Set[str]] = defaultdict(set)  # {tag: the year ends it carries the annual figure on}
        for end, col in cols.items():
            for tag, value in col.items():
                if value in figures.get(end, ()) and _line_concept(tag):
                    lines[tag].add(end)
        for end in cols.keys() & quarters.keys():
            col, quarter = cols[end], quarters[end]
            if quarter.get("long_term_debt") is not None:
                continue
            short_term = quarter.get("short_term_debt")
            tags = sorted(tag for tag, ends in lines.items() if tag in col and (
                not short_term or _noncurrent_concept(tag) or ends & without_maturities))
            if len({col[tag] for tag in tags}) == 1 and (filings[accn][1], accn) > found.get(end, (("", ""),))[0]:
                found[end] = ((filings[accn][1], accn), col[tags[0]], tags[0])
    return {end: (value, tag) for end, (_, value, tag) in found.items()}


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
        set_aside = self._aside(us_gaap)

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
                    if _unread(set_aside, field, tag, entry):
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
                    rank = priority + len(field.tags) if _ranks_last(set_aside, field, tag, fy, entry) else priority
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
        :meth:`_collect_instant_checkpoints`. A quarter whose 10-Q tags its long-term
        debt under a concept the field doesn't map reads the 10-Q's line where its
        year-end column is the annual record's figure (``_long_term_debt_lines``, BUG-171).

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
        annual = self._annual_periods(us_gaap)
        restored = {(r["field"], r["period_end"]): (r["dropped"], r["kept"])
                    for period in annual.values() for r in period.get("_repairs", [])}
        # The split basis a filing's figures are on: the product of the splits it came
        # after. Each quarter's per-share figures and counts go onto their year's, and
        # its basis is kept in ``_split_levels`` for the trailing years (BUG-082).
        splits = _split_restatements(us_gaap)
        level: Optional[Callable[[str], float]] = (
            (lambda filed: math.prod(ratio for _, after, ratio in splits if after <= filed)) if splits else None)

        instants_filed = self._collect_instant_checkpoints(us_gaap, periods, frames, restored)
        for end, (value, tag) in _long_term_debt_lines(us_gaap, annual, periods).items():
            periods[end]["long_term_debt"] = value
            periods[end]["_source_tags"]["long_term_debt"] = tag
        self._collect_discrete_flows(us_gaap, periods, frames, restored, level, self._aside(us_gaap))
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
                    if not start or not end or _unread(set_aside, field, tag, entry):
                        continue
                    span = self._span(start, end)
                    if span is None or span > _FULL_YEAR_MAX_DAYS:
                        continue  # ignore multi-year facts
                    if entry.get("frame"):
                        frames.setdefault(end, set()).add(entry["frame"])
                    rank = priority
                    if span >= _FULL_YEAR_MIN_DAYS and (field.key, tag, self._annual_fy(entry)) in set_aside:
                        rank += len(field.tags)  # a revenue figure that isn't the year's (never a count)
                    elif (field.key, tag, _fact_key(entry)) in set_aside:
                        rank += len(field.tags)  # a to-common figure its filing's EPS doesn't divide
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
                    if _unread(set_aside, field, tag, entry):
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
                    rank = priority + len(field.tags) if _ranks_last(set_aside, field, tag, period, entry) else priority
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
            set_aside=self._aside(us_gaap),
        )
        self._restore_agreed_figures(us_gaap, data, self._annual_fy)
        return data

    def _annual_filings(self, us_gaap: Dict[str, Any], field: Any, tag: str, period_key_fn,
                        unread: FrozenSet[Tuple[str, str, Any]] = frozenset()
                        ) -> Dict[Any, List[Tuple[str, float, str]]]:
        """Each annual report's figure for *field* under *tag*, as ``_resolve_canonical``
        reads them: ``{period: [(filed, value, end), ...]}`` in filing order, without the facts
        *unread* names (``_to_common_contradicted``; BUG-316)."""
        filings: Dict[Any, List[Tuple[str, float, str]]] = defaultdict(list)
        for entry in us_gaap.get(tag, {}).get("units", {}).get(field.xbrl_unit, []):
            value = entry.get("val")
            if entry.get("form", "") not in _ANNUAL_FORMS or not isinstance(value, (int, float)):
                continue
            if _unread(unread, field, tag, entry):
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

    def _aside(self, us_gaap: Dict[str, Any]) -> FrozenSet[Tuple[str, str, Any]]:
        """``_set_aside``'s figures and the ones not read at all (``_to_common_contradicted``)."""
        return self._set_aside(us_gaap) | _to_common_contradicted(us_gaap)

    def _set_aside(self, us_gaap: Dict[str, Any]) -> FrozenSet[Tuple[str, str, Any]]:
        """The figures that rank after every other tag of their field: ``_revenue_set_aside``'s and
        ``_to_common_set_aside``'s."""
        return self._revenue_set_aside(us_gaap) | _to_common_set_aside(us_gaap)

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
        unread = _to_common_contradicted(us_gaap)  # a figure no record reads can't be put back either (BUG-316)
        for field in CANONICAL_FIELDS:
            years_by_tag: Dict[str, List[Any]] = defaultdict(list)
            for fy, period in data.items():
                if isinstance(period.get(field.key), (int, float)):
                    years_by_tag[period["_source_tags"][field.key]].append(fy)
            for tag, years in years_by_tag.items():
                filings = self._annual_filings(us_gaap, field, tag, period_key_fn, unread)
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

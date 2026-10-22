"""The gurus whose 13F-HR holdings are collected (Milestone 3): value investors and the other managers GuruFocus
follows, each with the SEC filers that report its portfolio
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md).

Every CIK was checked against its submissions feed on 2026-10-22: the filer's name, and its 13F-HR filings
(qa/evidence/ownership-m3-4-gurus-check.txt). A guru's quarter is the union of its filers' 13F-HRs for that
period: Pershing Square Inc. reports Bill Ackman's portfolio from Q2 2026, when Pershing Square Capital Management's
own filing became a notice pointing to it (13F-NT), and DME Capital Management has filed David Einhorn's since
Greenlight Capital's last 13F (Q4 2023). The list is the user's to change (NEEDS-USER Q13).
"""
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Guru:
    """A guru: its current 13F filer's CIK (its id) and name as the SEC lists it, the investor or firm the list
    follows, and any earlier filers of the same portfolio, each (CIK, name)."""

    cik: str
    name: str
    manager: str
    former: Tuple[Tuple[str, str], ...] = ()

    @property
    def filers(self) -> Tuple[Tuple[str, str], ...]:
        """Every filer whose 13F-HRs make up the guru's holdings, (CIK, name), the current one first."""
        return ((self.cik, self.name),) + self.former

    @property
    def ciks(self) -> Tuple[str, ...]:
        """The filers' CIKs, the current one first."""
        return tuple(cik for cik, _ in self.filers)


GURUS: Tuple[Guru, ...] = (
    Guru("1067983", "BERKSHIRE HATHAWAY INC", "Warren Buffett"),
    Guru("1061768", "BAUPOST GROUP LLC/MA", "Seth Klarman"),
    Guru("2026053", "PERSHING SQUARE INC.", "Bill Ackman",
         former=(("1336528", "Pershing Square Capital Management, L.P."),)),
    Guru("1489933", "DME Capital Management, LP", "David Einhorn", former=(("1079114", "GREENLIGHT CAPITAL INC"),)),
    Guru("949509", "OAKTREE CAPITAL MANAGEMENT LP", "Howard Marks"),
    Guru("1096343", "MARKEL GROUP INC.", "Tom Gayner"),
    Guru("1112520", "AKRE CAPITAL MANAGEMENT LLC", "Chuck Akre"),
    Guru("860643", "GARDNER RUSSO & QUINN LLC", "Tom Russo"),
    Guru("1720792", "Ruane, Cunniff & Goldfarb L.P.", "Ruane, Cunniff & Goldfarb"),
    Guru("807985", "SOUTHEASTERN ASSET MANAGEMENT INC/TN/", "Mason Hawkins"),
    Guru("732905", "Tweedy, Browne Co LLC", "Tweedy, Browne"),
    Guru("905567", "YACKTMAN ASSET MANAGEMENT LP", "Donald Yacktman"),
    Guru("883965", "WEITZ INVESTMENT MANAGEMENT, INC.", "Wally Weitz"),
    Guru("1325447", "First Eagle Investment Management, LLC", "First Eagle"),
    Guru("200217", "DODGE & COX", "Dodge & Cox"),
    Guru("1036325", "DAVIS SELECTED ADVISERS", "Chris Davis"),
    Guru("1027796", "PZENA INVESTMENT MANAGEMENT LLC", "Richard Pzena"),
    Guru("1709323", "Himalaya Capital Management LLC", "Li Lu"),
    Guru("1345471", "TRIAN FUND MANAGEMENT, L.P.", "Nelson Peltz"),
    Guru("1418814", "ValueAct Holdings, L.P.", "ValueAct"),
    Guru("1040273", "Third Point LLC", "Dan Loeb"),
    Guru("1656456", "Appaloosa LP", "David Tepper"),
    Guru("921669", "ICAHN CARL C", "Carl Icahn"),
    # Scion's last 13F-HR covers Q3 2025: Michael Burry deregistered it.
    Guru("1649339", "Scion Asset Management, LLC", "Michael Burry"),
    Guru("1536411", "Duquesne Family Office LLC", "Stanley Druckenmiller"),
    Guru("1056831", "FAIRHOLME CAPITAL MANAGEMENT LLC", "Bruce Berkowitz"),
    Guru("915191", "FAIRFAX FINANCIAL HOLDINGS LTD/ CAN", "Prem Watsa"),
    Guru("1569205", "Fundsmith LLP", "Terry Smith"),
    Guru("1647251", "TCI Fund Management Ltd", "Chris Hohn"),
    Guru("813917", "HARRIS ASSOCIATES L P", "Bill Nygren"),
    Guru("1553733", "Brave Warrior Advisors, LLC", "Glenn Greenberg"),
    Guru("1549575", "Dalal Street, LLC", "Mohnish Pabrai"),
    Guru("783412", "DAILY JOURNAL CORP", "Daily Journal (Charlie Munger's)"),
    Guru("1166559", "GATES FOUNDATION TRUST", "Gates Foundation Trust"),
)

_BY_CIK: Dict[str, Guru] = {cik: g for g in GURUS for cik in g.ciks}


def guru_by_cik(cik: str) -> Optional[Guru]:
    """The guru whose filers include *cik*, or None."""
    return _BY_CIK.get(str(int(cik)) if str(cik).isdigit() else str(cik))

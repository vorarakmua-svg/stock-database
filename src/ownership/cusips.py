"""Our companies' CUSIPs (Milestone 3): the 13F CUSIPs of the securities in our universe, curated from the
information tables' issuer names and checked against OpenFIGI's tickers
(docs/superpowers/specs/2026-10-22-sec-ownership-design.md). Each share class keeps its own ticker (GOOG beside
GOOGL, BRK-A beside BRK-B). Every other CUSIP is mapped through OpenFIGI (``openfigi``).

Built on 2026-10-22 from the 1,941 CUSIPs of the gurus' 13F filings for Q4 2024 to Q2 2026: each one's OpenFIGI
listing (the US composite; a code starting with a letter asked as a CINS), kept where its ticker is one of ours,
each match read against its 13F issuer name (qa/evidence/ownership-m3-5-universe-review.txt). Preferred shares and
convertible notes of our companies keep their own OpenFIGI tickers. No guru held ANET, CRWD, DUK, ETN, HWM, MRVL,
PRU or SO in that window.
"""
from typing import Dict

#: CUSIP -> ticker, for the securities of our universe that the gurus' 13F filings hold.
UNIVERSE_CUSIPS: Dict[str, str] = {
    "037833100": "AAPL",  # APPLE INC
    "00287Y109": "ABBV",  # ABBVIE INC
    "002824100": "ABT",  # ABBOTT LABORATORIES
    "00724F101": "ADBE",  # ADOBE INC
    "032654105": "ADI",  # ANALOG DEVICES INC
    "020002101": "ALL",  # ALLSTATE CORP
    "038222105": "AMAT",  # APPLIED MATLS INC
    "007903107": "AMD",  # ADVANCED MICRO DEVICES INC
    "031162100": "AMGN",  # AMGEN INC
    "03027X100": "AMT",  # AMERICAN TOWER CORP NEW
    "023135106": "AMZN",  # AMAZON COM INC
    "032095101": "APH",  # AMPHENOL CL A ORD
    "03831W108": "APP",  # APPLOVIN CORP
    "11135F101": "AVGO",  # BROADCOM INC
    "025816109": "AXP",  # AMERICAN EXPRESS CO
    "097023105": "BA",  # BOEING CO
    "060505104": "BAC",  # BANK AMER CORP
    "09857L108": "BKNG",  # BOOKING HOLDINGS INC
    "09290D101": "BLK",  # BLACKROCK INC
    "110122108": "BMY",  # BRISTOL-MYERS SQUIBB CO
    "084670702": "BRK-B",  # BERKSHIRE HATHAWAY CL B ORD
    "101137107": "BSX",  # Boston Scientific Corp.
    "09260D107": "BX",  # BLACKSTONE INC
    "172967424": "C",  # CITIGROUP INC
    "149123101": "CAT",  # CATERPILLAR INC
    "H1467J104": "CB",  # CHUBB LIMITED
    "21037T109": "CEG",  # CONSTELLATION ENERGY CORP
    "14040H105": "COF",  # CAPITAL ONE FINANCIAL ORD
    "20825C104": "COP",  # CONOCOPHILLIPS
    "22160K105": "COST",  # COSTCO WHOLESALE CORPORATION
    "79466L302": "CRM",  # SALESFORCE COM INC
    "17275R102": "CSCO",  # CISCO SYS INC
    "126650100": "CVS",  # CVS CORP.
    "166764100": "CVX",  # CHEVRON CORP NEW
    "25746U109": "D",  # DOMINION ENERGY INC
    "244199105": "DE",  # DEERE & CO
    "24703L202": "DELL",  # DELL TECHNOLOGIES INC
    "235851102": "DHR",  # DANAHER CORP DEL
    "254687106": "DIS",  # DISNEY WALT CO
    "29444U700": "EQIX",  # EQUINIX INC
    "34959E109": "FTNT",  # FORTINET INC
    "369604301": "GE",  # GE AEROSPACE
    "36828A101": "GEV",  # GE VERNOVA INC
    "375558103": "GILD",  # GILEAD SCIENCES INC
    "219350105": "GLW",  # CORNING INC
    "02079K305": "GOOGL",  # ALPHABET INC
    "38141G104": "GS",  # GOLDMAN SACHS GROUP INC
    "437076102": "HD",  # HOME DEPOT INC
    "438516205": "HON",  # Honeywell International, Inc.
    "459200101": "IBM",  # I B M
    "458140100": "INTC",  # INTEL CORP
    "461202103": "INTU",  # INTUIT
    "46120E602": "ISRG",  # Intuitive Surgical, Inc.
    "478160104": "JNJ",  # JOHNSON & JOHNSON
    "46625H100": "JPM",  # J.P. Morgan Chase
    "48251W104": "KKR",  # KKR & CO INC
    "482480100": "KLAC",  # KLA CORP
    "191216100": "KO",  # COCA COLA CO
    "G54950103": "LIN",  # LINDE ORD
    "532457108": "LLY",  # ELI LILLY & CO
    "539830109": "LMT",  # LOCKHEED MARTIN CORP
    "548661107": "LOW",  # LOWES COS INC
    "512807306": "LRCX",  # LAM RESEARCH CORP
    "57636Q104": "MA",  # MASTERCARD CL A ORD
    "580135101": "MCD",  # MCDONALDS CORP
    "59156R108": "MET",  # METLIFE INC
    "30303M102": "META",  # META PLATFORMS CL A ORD
    "02209S103": "MO",  # ALTRIA GROUP INC
    "58933Y105": "MRK",  # MERCK & CO INC
    "617446448": "MS",  # MORGAN STANLEY
    "594918104": "MSFT",  # MICROSOFT CORP
    "595112103": "MU",  # MICRON TECHNOLOGY INC
    "65339F101": "NEE",  # NextEra Energy, Inc.
    "64110L106": "NFLX",  # NETFLIX INC
    "81762P102": "NOW",  # SERVICENOW INC
    "67066G104": "NVDA",  # NVIDIA CORPORATION
    "756109104": "O",  # Realty Income Corp.
    "68389X105": "ORCL",  # ORACLE CORP
    "697435105": "PANW",  # PALO ALTO NETWORKS INC
    "713448108": "PEP",  # PEPSICO INC
    "717081103": "PFE",  # PFIZER INC
    "742718109": "PG",  # PROCTER & GAMBLE CO
    "743315103": "PGR",  # PROGRESSIVE CORP
    "701094104": "PH",  # PARKER-HANNIFIN CORP
    "74340W103": "PLD",  # Prologis, Inc.
    "69608A108": "PLTR",  # PALANTIR TECHNOLOGIES INC
    "718172109": "PM",  # PHILIP MORRIS INTL INC
    "74460D109": "PSA",  # Public Storage, Inc.
    "747525103": "QCOM",  # QUALCOMM INC
    "75513E101": "RTX",  # RTX CORPORATION
    "855244109": "SBUX",  # STARBUCKS CORP
    "84265V105": "SCCO",  # Southern Copper Corp
    "808513105": "SCHW",  # Charles Schwab Corp
    "806857108": "SLB",  # SCHLUMBERGER LTD
    "80004C200": "SNDK",  # SANDISK CORP
    "84615Q103": "SPCX",  # SPACE EXPLORATION TECHN CORP
    "828806109": "SPG",  # Simon Property Group, Inc.
    "78409V104": "SPGI",  # S&P GLOBAL INC
    "G7997R103": "STX",  # Seagate Technology Hldngs Pl
    "863667101": "SYK",  # STRYKER CORPORATION
    "00206R102": "T",  # AT&T INC
    "872540109": "TJX",  # TJX COS INC NEW
    "883556102": "TMO",  # THERMO FISHER SCIENTIFIC INC
    "872590104": "TMUS",  # T-MOBILE US INC
    "89417E109": "TRV",  # The Travelers Co., Inc.
    "88160R101": "TSLA",  # TESLA INC
    "882508104": "TXN",  # TEXAS INSTRS INC
    "90353T100": "UBER",  # UBER TECHNOLOGIES INC
    "91324P102": "UNH",  # UNITEDHEALTH GROUP
    "907818108": "UNP",  # UNION PAC CORP
    "902973304": "USB",  # U.S. Bancorp
    "92826C839": "V",  # VISA CL A ORD
    "92532F100": "VRTX",  # VERTEX PHARMACEUTICALS
    "92343V104": "VZ",  # VERIZON COMMUNICATIONS
    "958102105": "WDC",  # Western Digital Corp
    "95040Q104": "WELL",  # Welltower, Inc.
    "949746101": "WFC",  # WELLS FARGO & CO
    "931142103": "WMT",  # WALMART INC
    "30233Q108": "XOM",  # Exxon Mobil Corp
    # Checked by hand:
    "02079K107": "GOOG",  # Alphabet's Class C beside our GOOGL (Class A)
    "084670108": "BRK-A",  # Berkshire's Class A beside our BRK-B
    "30231G102": "XOM",  # Exxon Mobil Corp's, before ExxonMobil Holdings took XOM (OpenFIGI: EXMOC)
    "438516106": "HON",  # Honeywell's before its 2026 reverse split (OpenFIGI: HONGBP)
    "200021014": "ALL",  # a filer's typo of Allstate's 020002101
}

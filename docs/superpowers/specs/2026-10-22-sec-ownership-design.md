# SEC ownership: design (Milestone 3)

- Date: 2026-10-22
- Status: designed by the build loop (task M3-0) from the approved plan's outline in
  `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` ("Milestones 2-5").
- Decisions left to the user are NEEDS-USER, each with a default that changes no existing number.

## Goal

GuruFocus shows each company's insider trades (Form 4 since 2003, with CEO/CFO buys and cluster buys) and what its
140+ "gurus" hold (from 13F). Today the app shows Yahoo's last two years of insider rows as free text, and
Yahoo's top-10 holders. This milestone reads both from the SEC itself:
- **Insiders**: every Form 4 of our companies for the last three years, parsed from its XML: who, their role,
  the transaction code, shares, price, the holding after, direct or indirect, the 10b5-1 flag.
- **Gurus**: the 13F-HR holdings of a curated list of about 30 value investors, quarter by quarter, mapped to
  tickers by CUSIP.
Nothing in the valuations, the history or the scores changes.

## Facts that shape the design (verified)

- The SEC cache holds every company's submissions feed (`data/cache/submissions_CIK*.json`), cut to its latest
  100 filings: 6,507 Form 4s across 129 companies (median 54), back to about February 2026 (median). Three years
  is therefore about 80 a year per company, some 30,000 filings: at a polite 5 requests a second, 1.7 hours.
- The feed's `recent` block holds up to 1,000 filings, older ones in `files` pages; the cached copy keeps 100
  (`SECHandler._extract_recent_filings`).
- Every cached Form 4's primary document is the rendered path `xslF345X05/...` (2,083) or `xslF345X06/...`
  (4,424); the raw XML is the same file without that prefix.
- `SECHandler` already rate-limits (`fetchers/rate_limiter.py`), sends the configured User-Agent and caches JSON
  payloads; it lists Form 4 filings (`get_insider_transactions`) without parsing them.
- Yahoo's `insider_transactions` (14,539 rows, 2024-07 to 2026-10) has free-text descriptions; the INS tab shows
  them. `holders` holds Yahoo's top-10 institutional and fund holders; the HDS tab shows them.
- The submissions feed carries a SIC code for all 129 cached companies, which M5 stores.

## Form 4 (`src/ownership/form4.py`)

- **Fetch**: for each company, its submissions feed in full (`recent` plus the `files` pages reaching back three
  years), then each Form 4 and 4/A's XML: `Archives/edgar/data/{issuer CIK}/{accession}/{primary document}`, the
  raw XML (the primary document without its `xslF345X0n/` rendering prefix). Every XML is cached on disk
  (`data/cache/form4/{accession}.xml`), so a later run fetches only new filings. Through `SECHandler`'s session
  and limiter.
- **Parse** (`ownershipDocument`, the SEC's schema): the issuer; each reporting owner (CIK, name, director,
  officer and title, ten-percent owner, other); `periodOfReport`; `aff10b5One`; each non-derivative transaction:
  security title, date, transaction code, shares, price, acquired or disposed, shares owned after, direct or
  indirect. Derivative transactions are kept with their code, shares, exercise price and underlying. Footnote
  references stay as their ids. A malformed document is skipped and logged, never guessed.
- **Only the company's own filings** (BUG-208): a feed also lists the Form 4s the company filed as an owner of
  another issuer's shares (PFE's of Cerevel, Berkshire's of OXY); a filing is kept only when its issuer CIK is
  the company's, the rest counted as "other issuer". A successor company (`PREDECESSOR_CIKS`: XOM from Exxon
  Mobil Corp, BLK from the old BlackRock) also reads its predecessor's own Form 4s filed before its first
  (BUG-210).
- **Amendments** (BUG-209): a 4/A amends the same owner's filing made on the day it names as its original's (a
  4 or an earlier 4/A, a chain; the period of report only chooses between several) and replaces only the trades
  it restates: the same table, code and security on the same date or with the same shares and price, or the same
  date, shares and price under another code (a code correction), matched one for one. The amended filing's other
  trades stand (both of PFE's 4/As add an omitted trade). A restated trade is kept, marked
  `insider_trades.superseded_by`; `insider_filings.superseded_by` notes the amending 4/A.

## 13F (`src/ownership/thirteenf.py`)

- **The gurus**: `src/ownership/gurus.py`, a tuple of `Guru(cik, name, manager, former)` kept in the code
  (nothing under `data/` is committed): the current 13F filer's CIK (the guru's id) and name as the SEC lists
  it, the investor followed, and any earlier filers of the same portfolio, each (CIK, name). 34 gurus (Berkshire
  Hathaway,
  Baupost, Pershing Square, Einhorn's DME Capital, Oaktree, Markel, Akre, Gardner Russo, Ruane Cunniff,
  Southeastern, Tweedy Browne, Yacktman, Weitz, First Eagle, Dodge & Cox, Davis, Pzena, Himalaya, Trian,
  ValueAct, Third Point, Appaloosa, Icahn, Scion, Duquesne, Fairholme, Fairfax, Fundsmith, TCI, Harris,
  Brave Warrior, Dalal Street, Daily Journal, the Gates Foundation Trust). Each CIK was checked against its
  submissions feed (name, 13F-HR filings) on 2026-10-22 (`qa/evidence/ownership-m3-4-gurus-check.txt`).
  - A guru's quarter is the union of its filers' 13F-HRs. Pershing Square Inc. (2026053) reports Bill Ackman's
    portfolio from Q2 2026, when Pershing Square Capital Management's own filing (1336528) became a 13F-NT
    notice pointing to it; before, the two reported different holdings. DME Capital Management (1489933) has
    filed David Einhorn's since Greenlight Capital's (1079114) last 13F, for Q4 2023.
  - Scion's last 13F-HR covers Q3 2025: Michael Burry deregistered it.
- **Fetch**: each filer's 13F-HR and 13F-HR/A whose period of report falls in the last eight quarters before
  the current one (13F-NT notices hold no holdings and are skipped). Each filing's full submission text
  (`{accession}.txt`) carries the cover and the INFORMATION TABLE, each under its `<TYPE>`: one request per
  filing, cached like Form 4 (`index.json` lists a filing's files without their document types).
- **Parse** (`src/ownership/thirteenf.py`): each entry's issuer, class, CUSIP, value, shares or principal,
  put/call, discretion, and the cover's period, form, report type, amendment type and totals.
  - **Units**: values are in thousands of dollars in filings made before 2023-01-03 and in dollars from that
    day (the SEC's rule goes by the filing date), stored in dollars. The holdings' prices overrule the date
    where it gives impossible ones: when the median price of a filing's share rows (value / shares) falls
    outside $1 to $100,000 under the date's units and inside them at 1,000 times more or less, the filing
    states the other units. Baupost and Duquesne still state thousands after the switch (their 14 filings in
    the window). `guru_filings.multiplier` keeps the units used. A pre-2023 filing stated in dollars is caught
    only when its median price is above $100; none falls in the window.
  - **Checksum**: the cover's `tableEntryTotal` must equal the parsed entries, and its `tableValueTotal` their
    sum within rounding. Each entry's value and the total are rounded to the unit, so they may differ by half
    a unit per entry plus half a unit: 13 of the first 248 filings differ by 1 to 4 units. A filing that fails
    is stored with a flag and kept out of the aggregates.
- **CUSIP to ticker**: `cusip_map` (cusip PK, ticker, name, source, security_type, mapped_at; source
  'universe', 'openfigi' or 'none').
  - Our companies' codes are curated in `src/ownership/cusips.py`: 124 codes for 121 tickers. Each 13F code's
    OpenFIGI listing (the US composite) is kept where its ticker is ours, and read against its 13F issuer name
    (`qa/evidence/ownership-m3-5-universe-review.txt`).
  - Added by hand: the other common class (GOOG, BRK-A), the codes that changed in the window (Honeywell's
    before its 2026 reverse split, Exxon Mobil Corp's before ExxonMobil Holdings took XOM), and a filer's
    typo of Allstate's. Our preferred shares and convertible notes keep their own OpenFIGI tickers.
  - Every other code goes to OpenFIGI's mapping API without a key (10 a request, 25 requests a minute),
    cached on disk. A code starting with a letter is a CINS (a foreign issuer: Chubb, Linde, Seagate), asked
    as `ID_CINS`; as `ID_CUSIP` OpenFIGI finds nothing. "No identifier found" and "Invalid idValue format"
    (a malformed code: a filer's bad check digit) are answers, mapped 'none'; another job error fails only its
    own code; three failed requests in a row stop the mapping until the next run. Unmapped codes keep their
    issuer name.
- **A guru's quarter** (`src/ownership/portfolio.py`), from the filings that pass the checksum:
  - per filer and period, the latest of the original 13F-HR and its RESTATEMENTs, plus the NEW HOLDINGS
    amendments filed after it; a filer with no usable filing leaves the quarter incomplete;
  - a NEW HOLDINGS amendment's rows that repeat its base's (same CUSIP, put/call, shares and value), matched
    one for one, are left out: First Eagle's Q2 2026 amendment repeats all 614 rows of its 13F-HR and adds 2,
    while Berkshire's Q1 2025 one adds 202 Lennar B shares to a code the base holds;
  - the guru's filers summed (Pershing Square Inc. with Pershing Square Capital Management);
  - positions keyed by security (the mapped ticker, else the CUSIP, so a changed code doesn't read as a sale
    and a purchase) and put/call (options apart), rows of one key summed; weights by value.
- **Changes**, against the previous quarter only: new, added, reduced, sold out, unchanged, by shares. The
  earlier quarter's shares are adjusted for our tickers' share splits between the two period ends (simple
  ratios: Yahoo's spin-off price factors change no holder's count, and stock dividends such as SCCO's 1% read
  as small additions); other securities have no split data. HON's 2026 reverse split is inside a combined
  factor, so its change reads -50% (BUG-184).

## Storage (`src/exporters/sqlite_store.py`)

- `insider_filings`: accession (PK), ticker, form, filed_date, period_of_report, owner_cik, owner_name,
  is_director, is_officer, officer_title, is_ten_percent, is_other, aff10b5one, superseded_by.
- `insider_trades`: (accession, seq) PK, ticker, owner_cik, table ('nonderivative' or 'derivative'),
  security_title, date, code, shares, price, acquired_disposed, owned_after, direct_indirect, exercise_price.
- `guru_filers` (filer_cik PK, guru_cik, name, manager): the curated list, one row per filer.
- `guru_filings` (accession PK, guru_cik, filer_cik, form, period, filed, report_type, amendment_type,
  entry_total and value_total as the cover states them, rows and rows_value as parsed, checksum_ok,
  confidential_omitted, collected_at, multiplier), and `guru_holdings` ((accession, row) PK, guru_cik, cusip, issuer,
  title_of_class, value_usd, shares, share_type, put_call, discretion), as filed: one CUSIP may take several
  rows. Values are in dollars.
- `cusip_map` (M3-5).
- Each company's or guru's rows are replaced in one transaction.

## Aggregates

- **Open-market net buying** over 3, 6 and 12 months: purchases (code P) less sales (code S), in shares and
  dollars, from non-superseded filings.
- **CEO and CFO buys**: code P by an officer whose title names the chief executive or financial officer.
- **Cluster buys**: three or more distinct insiders buying (code P) within 30 days.
- **10b5-1 sales**: the share of sales under a plan (where the flag is filed).
  The insiders' are computed at read time.
- **The gurus' quarters are materialized when collecting** (M3-6): the holders tab, the aggregated portfolio
  and the screener all need every guru's latest quarter, too slow to rebuild per request, and an SQL screen
  can't call Python. After mapping, `--gurus` stores each guru's quarters (`guru_quarters`: total, positions,
  complete, whether the prior quarter is there, the accessions) and positions with their changes
  (`guru_positions`: one row per security and put/call, put/call '' for shares, plus a 'sold_out' row for each
  position of the quarter before that's gone); `--positions` rebuilds them offline.
- **Gurus holding** a stock: over each guru's latest quarter, its shares (and options) of the stock or its
  other class (GOOGL with GOOG, BRK-B with BRK-A), with the value, weight and the quarter's change, then the
  quarter's sellers out. The **aggregated guru portfolio**: per security, the gurus holding it, their summed
  shares and value, and the buyers and sellers of the latest quarter.

## Web

- **INS tab**: the SEC trades (date, insider, role, code with its meaning, shares, price, value, holding after,
  plan), the aggregates above, and Yahoo's rows only where there's no SEC data.
- **HDS tab**: a "Gurus" section above Yahoo's holders: each guru's latest quarter, its quarter shown per row
  (Scion's stops at Q3 2025), then the quarter's sellers out. Hidden until a quarter is stored.
- **Pages**: `/gurus` (the list, each with its latest quarter's value, positions, new and sold out),
  `/gurus/{cik}` (`?period=`: holdings with weights and changes, the changes summary, the filings linked to
  EDGAR), `/gurus/portfolio` (the aggregated portfolio); a Gurus link in the nav.
- **API**: `/api/stocks/{t}/form4` (the existing `/api/stocks/{t}/insiders` keeps serving Yahoo's rows),
  `/api/stocks/{t}/gurus`, `/api/gurus`, `/api/gurus/portfolio`, `/api/gurus/{cik}`.
- **Screener**: net open-market buying (12 months, dollars), CEO/CFO buys (12 months), cluster buys (12 months);
  `gurus_holding` and `gurus_buying` (new or added) in their latest quarters, each guru counted once over the
  stock's share classes, shown as one "Gurus (buying)" column.

## Collection

- `python -m src.ownership.collect --insiders [--gurus] [--years 3] [--quarters 8] [--db PATH] [TICKERS...]`, a
  module like
  the backfill (the `stock-data` command's positional tickers leave no room for a subcommand): incremental
  through the caches, with
  progress logged; a failed fetch is retried once, then reported, never stored as empty.
- The live step runs it into the user's DB after a backup, in the background.

## Validation

- Form 4 against Yahoo's insider rows where both cover the same months: dates, insiders and shares, with every
  miss explained (Yahoo's rows are per transaction day and summed).
- 13F's own checksums on every filing; Berkshire's latest holdings against its filing's totals.
- Fixtures: a handful of real Form 4 and 13F documents (a director's purchase, an officer's 10b5-1 sale, a gift, a
  4/A, an option exercise; a pre-2023 and a post-2023 information table) saved under `tests/fixtures/ownership/`.

## Edge cases

| Case | Handling |
|---|---|
| A filing with several reporting owners | One filing row per owner |
| Footnoted prices (weighted averages) | The price as filed; the footnote id kept |
| Shares owned after missing | NULL, never derived |
| A company with no Form 4 in three years | No rows; the INS tab falls back to Yahoo's |
| A guru filing a confidential-treatment amendment | Stored as filed; the amendment's rows added |
| Options and warrants in 13F (put/call) | Kept with their flag; out of the guru portfolio's share totals |
| One CUSIP in several rows of a 13F (other managers, discretion) | Summed per filing and CUSIP |
| A Form 4 trade code the aggregates don't use (A, M, F, G, ...) | Stored and shown with its meaning; only P and S count as open-market |

## NEEDS-USER

- **Bulk data sets**: the SEC's Insider Transactions and 13F data set ZIPs (hundreds of MB) would give history
  to 2006/2013. Default: per-filing fetches only, three years and eight quarters.
- **The guru list**: the default list above; the user may add or drop filers.

## Tasks

M3-1 Form 4 parser with fixtures; M3-2 Form 4 collection and storage; M3-3 insider aggregates, the INS tab, the
API and screener fields; M3-4 13F guru list, information-table parser with checksums, storage; M3-5 CUSIP map and
quarter-over-quarter changes; M3-6 guru pages, the HDS section, the API; M3-7 docs; M3-8 milestone close (code
review 17, the live collection into the user's DB after a backup, verification against Yahoo and the checksums).

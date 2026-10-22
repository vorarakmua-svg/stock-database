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
- **Amendments**: a 4/A replaces the original filing of the same owner and period of report; the original is
  kept, marked superseded.

## 13F (`src/ownership/thirteenf.py`)

- **The gurus**: `src/ownership/gurus.py`, a tuple of (CIK, name, manager) kept in the code (nothing under
  `data/` is committed). About 30 value investors (Berkshire Hathaway,
  Baupost, Pershing Square, Greenlight, Oaktree, Markel, Akre, Gardner Russo, Ruane Cunniff, Southeastern,
  Tweedy Browne, Yacktman, Weitz, First Eagle, Dodge & Cox, Davis, Pzena, Himalaya, Trian, ValueAct, Third Point,
  Appaloosa, Icahn, Scion, Duquesne, ...). Each CIK is checked against its submissions feed (name, 13F-HR filer)
  before it's added.
- **Fetch**: each guru's 13F-HR and 13F-HR/A for the last eight quarters; the information table from the
  filing's index (`index.json`, the document of type INFORMATION TABLE), cached like Form 4.
- **Parse**: each entry's issuer, class, CUSIP, value, shares or principal, put/call, discretion. Values are in
  thousands of dollars before 2023 and in dollars after (the SEC's change for filings from 2023-01-03): stored in
  dollars. **Checksum**: the primary document's `tableEntryTotal` and `tableValueTotal` must equal the parsed
  count and sum; a filing that disagrees is stored with a flag and kept out of the aggregates.
- **CUSIP to ticker**: `cusip_map` (CUSIP, ticker, name, source). Our 127 companies' CUSIPs are curated from the
  information tables' issuer names, checked by hand-listed name matches; the others through OpenFIGI's mapping
  API without a key (25 requests a minute, 10 CUSIPs each), cached. Unmapped CUSIPs keep their issuer name.
- **Changes**: per guru and CUSIP, quarter over quarter: new, added, reduced, sold out, unchanged (shares).

## Storage (`src/exporters/sqlite_store.py`)

- `insider_filings`: accession (PK), ticker, form, filed_date, period_of_report, owner_cik, owner_name,
  is_director, is_officer, officer_title, is_ten_percent, is_other, aff10b5one, superseded_by.
- `insider_trades`: (accession, seq) PK, ticker, owner_cik, table ('nonderivative' or 'derivative'),
  security_title, date, code, shares, price, acquired_disposed, owned_after, direct_indirect, exercise_price.
- `guru_filers`, `guru_filings` (accession PK, cik, period, filed, form, entries, value_total, checksum_ok),
  `guru_holdings` (accession, row) PK, cusip, issuer, class, value_usd, shares, share_type, put_call),
  `cusip_map`.
- Each company's or guru's rows are replaced in one transaction.

## Aggregates (computed at read time)

- **Open-market net buying** over 3, 6 and 12 months: purchases (code P) less sales (code S), in shares and
  dollars, from non-superseded filings.
- **CEO and CFO buys**: code P by an officer whose title names the chief executive or financial officer.
- **Cluster buys**: three or more distinct insiders buying (code P) within 30 days.
- **10b5-1 sales**: the share of sales under a plan (where the flag is filed).
- **Gurus holding** a stock: which, how many shares, the value, the quarter's change; and the aggregated guru
  portfolio: per stock, the number of gurus and the summed value.

## Web

- **INS tab**: the SEC trades (date, insider, role, code with its meaning, shares, price, value, holding after,
  plan), the aggregates above, and Yahoo's rows only where there's no SEC data.
- **HDS tab**: a "Gurus" section above Yahoo's holders.
- **Pages**: `/gurus` (the list, each with its latest quarter's value and positions), `/gurus/{cik}` (holdings
  and changes), `/gurus/portfolio` (the aggregated portfolio).
- **API**: `/api/stocks/{t}/insiders`, `/api/stocks/{t}/gurus`, `/api/gurus`, `/api/gurus/{cik}`.
- **Screener**: net open-market buying (12 months, dollars), CEO/CFO buys (12 months), cluster buys (12 months),
  gurus holding.

## Collection

- `python -m src.ownership.collect --insiders [--gurus] [--years 3] [--db PATH] [TICKERS...]`, a module like
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

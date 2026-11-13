# The universe

The tickers the app collects, kept in the repo (never under `data/`). Each file is a tickers file for
`stock-data --tickers-file PATH` (repeatable, beside positional tickers). Tickers are in Yahoo's form: a class share's
dot is a dash (`BRK-B`, `BF-B`). Design: `docs/superpowers/specs/2026-10-25-universe-automation-design.md` ("The
universe").

| File | What it is |
|---|---|
| `core.txt` | The 127 companies in the app's database on 2026-10-25, collected by hand before Milestone 5: a ticker a line, sorted, `#` comments. A company that leaves the S&P 500 stays here: this list isn't the index (SCCO and SPCX aren't members). |
| `sp500-2026-10-25.csv` | The S&P 500's members on 2026-10-25, one company a CIK: `ticker,name,cik,gics_sector,gics_sub_industry,date_added` (the CIK 10 digits, the date added an ISO date). 500 rows: the page's 503 less GOOG, FOX and NWS, the second share classes of Alphabet, Fox and News Corp (GOOGL, FOXA and NWSA are kept). |

## Collecting them

    stock-data --tickers-file universe/core.txt
    stock-data --tickers-file universe/sp500-2026-10-25.csv --skip-existing

`--skip-existing` leaves out the tickers already in the database the run writes to (`--db`, else
`<output-dir>/output/stock.db`): on 2026-10-25, 125 of the list's 500 were, so it collects the other 375.

## The S&P 500 list's source and license

- Source: Wikipedia, "List of S&P 500 companies", <https://en.wikipedia.org/wiki/List_of_S%26P_500_companies>, its
  first table (Symbol, Security, GICS Sector, GICS Sub-Industry, Date added, CIK; its Headquarters Location and
  Founded columns aren't kept), read on 2026-10-25 (revision 1381317423, last edited on 2026-10-21).
- Checked when it was saved: 503 rows read; each CIK equal to the SEC's own ticker map's (`company_tickers.json`) for
  all 503; every ticker in Yahoo's form; every date added an ISO date.
- License: the list's facts are taken from that Wikipedia page, whose text is available under the
  [Creative Commons Attribution-ShareAlike 4.0 License](https://creativecommons.org/licenses/by-sa/4.0/). The file is
  attributed to the page and its contributors
  (<https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=history>) and shared under the same
  license. It isn't S&P Dow Jones Indices' official list of the index's members.

## Refreshing the list

    python -m src.universe.sp500

writes `universe/sp500-<today>.csv` (`--out PATH` names another file; an existing file is never overwritten, and the
page isn't fetched then) with one request to Wikipedia, its User-Agent naming the app without an email, and prints the
rows read, kept and dropped. A page that changed fails loudly and writes nothing: no table with those header cells, a
column missing, a ticker or a CIK that doesn't read, or a row count outside 480 to 520. Then update this README's
date and counts, keep the earlier file (it records the index on its day), and collect the new members with
`--skip-existing`.

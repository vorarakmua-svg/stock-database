# Golden Basket and Oracles

- **Golden scratch DB:** `%LOCALAPPDATA%\Temp\stockdb-qa\golden\output\stock.db` (live-collected in iteration 1, 2026-10-20; JSON/CSV alongside)
- **Real-DB copy:** `%LOCALAPPDATA%\Temp\stockdb-qa\realcopy\stock.db` (SQLite backup API from a `mode=ro` connection; integrity_check ok; 127 companies; 135 MB)
- Batch logs: `qa/evidence/iter01-golden-batch{1..4}.log` (4 batches, `--workers 2`). All 4 batches exit 0 (128 s, 77 s, 77 s, 52 s); per-ticker results recorded in iteration 2.
- Re-collect a golden ticker only when testing gathering changes (prompt §7).

## Basket

| Ticker | Why it's here | Iteration-1 collection |
|---|---|---|
| AAPL | September FY; buyback-depleted equity | ok, 1 warning |
| MSFT | June FY | ok, 0 warnings |
| NVDA | January FY ("FY2022" = calendar 2021); hyper-growth | ok, 4 warnings (to triage) |
| WMT | January-end retailer (keeps the end year) | ok, 0 warnings |
| JNJ | 52/53-week year ending in early January | ok, 1 warning |
| JPM | Bank: sector rules; DCF/Lynch/owner earnings not applicable | ok, 0 warnings |
| PGR | Insurer: loss and combined ratios; P/B multiples | ok, 2 warnings |
| PLD | REIT: FFO/AFFO, P/FFO multiples | ok, 6 warnings (to triage) |
| XOM | Energy: cyclical, energy-specific inventory tags | ok, 1 warning; **lead:** sources lack `calculated_metrics` (every other ticker has it); investigate in GATHER-01 / VALID |
| NEE | Utility | ok, 6 warnings (to triage) |
| KO | Steady dividends (DDM) | ok, 3 warnings |
| GOOGL | Older filings lack cash-flow D&A tags (owner earnings not applicable) | ok, 0 warnings |
| BRK-B | Class share with punctuation; insurer conglomerate | ok, 6 warnings (to triage) |
| MCD | Negative equity | ok, 9 warnings (to triage) |
| TSLA | No dividend; high growth | ok, 5 warnings (to triage) |
| ARM | Recent IPO (short history) | ok, 0 warnings; **lead:** no `calculated_metrics` in sources |
| TSM | Foreign filer (20-F, IFRS taxonomy) | ok, 0 warnings; **lead:** no `calculated_metrics` in sources (IFRS 20-F filer: expected?) |
| SPY | ETF (no company fundamentals) | **lead:** Yahoo 404 "No fundamentals data" ×3, yet recorded as 0 warnings / 0 errors (silent gap; GATHER-02/06) |
| ZZZZ | Invalid ticker (must fail gracefully) | errors=1 as expected (Yahoo 404, no SEC); **lead:** batch 4 still exited 0 with a failed ticker (CLI-01 exit-code semantics) |

## Tolerances

| Comparison | Tolerance |
|---|---|
| SEC-reported values vs DB (after unit normalization) | exact |
| Derived sums (identities, quarterly vs annual) | ±1% |
| Cross-source market data (Yahoo vs SEC / computed) | ±5% |
| Stored valuations vs `qa/oracle/valuation_oracle.py` | ±0.5% |

## Oracles (to build in discovery units)

- `qa/oracle/valuation_oracle.py`: independent re-implementation of the six models from
  `docs/superpowers/specs/2026-07-13-valuation-layer-design.md` and `2026-07-14-owner-earnings-design.md`.
  Reads raw inputs from the DB; never imports `src.valuation`.
- `qa/oracle/sec_oracle.py`: independent parse of SEC companyfacts (requests + json) for revenue, net income,
  assets, liabilities, equity, OCF, capex and diluted shares; compared with the DB.
- Hand checks: 3 companies against their filed 10-K numbers (source URLs and dates recorded below).

## Reference values

_To be filled by VAL-13 (hand-worked valuations) and MATCH-02 (cross-source fundamentals), with sources and dates._

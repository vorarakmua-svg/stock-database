# Golden Basket and Oracles

- **Golden scratch DB:** `%LOCALAPPDATA%\Temp\stockdb-qa\golden\output\stock.db` (live-collected in iteration 1, 2026-10-20; JSON/CSV alongside)
- **Real-DB copy:** `%LOCALAPPDATA%\Temp\stockdb-qa\realcopy\stock.db` (SQLite backup API from a `mode=ro` connection; integrity_check ok; 127 companies; 135 MB)
- Batch logs: `qa/evidence/iter01-golden-batch{1..4}.log` (4 batches, `--workers 2`). Rows still _pending_ were running at the bootstrap commit; the next iteration records them.
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
| NEE | Utility | _pending_ |
| KO | Steady dividends (DDM) | _pending_ |
| GOOGL | Older filings lack cash-flow D&A tags (owner earnings not applicable) | _pending_ |
| BRK-B | Class share with punctuation; insurer conglomerate | _pending_ |
| MCD | Negative equity | _pending_ |
| TSLA | No dividend; high growth | _pending_ |
| ARM | Recent IPO (short history) | _pending_ |
| TSM | Foreign filer (20-F, IFRS taxonomy) | _pending_ |
| SPY | ETF (no company fundamentals) | _pending_ |
| ZZZZ | Invalid ticker (must fail gracefully) | _pending_ |

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

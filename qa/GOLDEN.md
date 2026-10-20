# Golden Basket and Oracles

- **Golden scratch DB:** `%LOCALAPPDATA%\stockdb-qa\golden\output\stock.db` (re-collected live in iteration 11, 2026-10-20, after the `Temp` copy was deleted: 4 batches, all exit 0 (87, 79, 106 and 79 s), 19 companies, errors only for the invalid ZZZZ, and SPY's three Yahoo 404s again counted as 0 warnings (lead L2); JSON/CSV alongside; logs `qa/evidence/iter11-golden-batch{1..4}.log`)
- **Real-DB copy:** `%LOCALAPPDATA%\stockdb-qa\realcopy\stock.db` (rebuilt in iteration 11; SQLite backup API from a `mode=ro` connection; integrity_check ok; 127 companies; 135 MB)
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

## Oracles

- `qa/oracle/valuation_oracle.py` (built in iteration 4; run with `--db PATH [--level 1|2|both] [TICKERS]`): independent re-implementation of the five models from
  `docs/superpowers/specs/2026-07-13-valuation-layer-design.md` and `2026-07-14-owner-earnings-design.md`.
  Reads raw inputs from the DB; never imports `src.valuation`. Level 1 = formula from stored assumptions; Level 2 = inputs re-derived from raw tables. Owner earnings isn't covered yet (VAL-06).
- `qa/oracle/sec_oracle.py`: independent parse of SEC companyfacts (requests + json) for revenue, net income,
  assets, liabilities, equity, OCF, capex and diluted shares; compared with the DB.
- Hand checks: 3 companies against their filed 10-K numbers (source URLs and dates recorded below).

## Reference values

### VAL-13: hand-worked valuations (iteration 46)

Golden collection of 2026-10-20 (`SCRATCH/golden`, valued by 144dcdf's code), worked from the raw tables by
`iter46_handworked.py`, which follows the specs' text and imports nothing from `src` or the oracle (every step:
`qa/evidence/iter46-val13-handworked.txt`). **Every model's bear, base and bull value equals the stored one (0.000%
difference), and so do the summaries.**

Readings where the specs are silent (each is what the stored values follow, recorded since iteration 4):
- EPS = reported diluted EPS (it nets out preferred dividends: JPM), on today's split basis. A year's figure
  reflects a split only if it was last filed after it (AAPL's FY2016 EPS 8.31, last filed 2018-11-05, before the
  4:1 split of 2020-08-31: 2.0775).
- BVPS = total equity ÷ diluted weighted shares (lead L8: JPM's equity includes preferred; on common equity alone
  its BVPS would be about $124, not $130.30).
- Scenario discount = base ± 1pp, not re-clamped to 8–14%: the spec floors and caps only the growth spread, so
  KO's bull case runs at 7% (re-clamped it would be 8%: DCF bull $16.24, not $19.87).
- CAGR over the last ≤ 10 FYs with a value, exponent = the fiscal-year span; the dividend CAGR over complete
  calendar years before the TTM anchor's year. TTM dividends = the 365 days to the latest snapshot's date.
- Discounting at year ends; years 6–10 fade linearly (year t grows at g + (2.5% − g)(t − 5)/5); Gordon terminal
  value at year 10 (DCF, owner earnings) or year 5 (DDM).

Shared inputs (latest snapshot, 2026-10-20; the key-less rate of the golden collection, BUG-041's note):

| | AAPL (general) | JPM (bank) | KO (general, dividend payer) |
|---|---|---|---|
| Price | $343.03 | $334.3357 | $93.555 |
| Beta; risk-free | 1.069; 4.19% | 1.015; 4.19% | 0.318; 4.19% |
| r = clamp(rf + β × 4.5%, 8%, 14%) | 9.0005% | 8.7575% | 5.621% → 8.000% |
| Shares (snapshot) | 14,594,180,000 | 2,655,600,000 | 4,302,549,243 |
| Analyst growth (Q2) | 28.7% | 9.9% | 16.9% |

**AAPL** (bear / base / bull):
- DCF: basis = median FCF FY2023–25 (95.651, 108.807, 98.767B) = $98.767B; FCF CAGR FY2016–25 =
  (98.767 / 52.041)^(1/9) − 1 = 7.379% < analyst → g 7.379%. (g, r): (4.379%, 10.0005%), (7.379%, 9.0005%),
  (10.379%, 8.0005%) → **$102.6023 / $140.5251 / $198.3826**.
- DDM: TTM 0.26 + 0.26 + 0.27 + 0.27 = $1.06; dividends 2016 $0.5575 → 2025 $1.03: CAGR 7.059% → g 7.059%;
  bull growth capped at 10% → **$15.4743 / $20.3007 / $27.2519**.
- Graham: EPS FY2023–25 6.13, 6.08, 7.46; BVPS = $73.733B ÷ 15.0047B = 4.9140; √(22.5 × EPS × BVPS) with EPS
  6.08 / 7.46 / 7.46 → **$25.9275 / $28.7196 / $28.7196**.
- Lynch: EPS FY2016 2.0775 → FY2025 7.46: CAGR 15.263% → capped at 15%; fair P/E 12 / 15 / 15 × 7.46 →
  **$89.52 / $111.90 / $111.90**.
- Multiples (P/E at FY-end closes): FY2021 143.30/5.61 = 25.544, FY2022 147.55/6.11 = 24.148, FY2023
  168.93/6.13 = 27.557, FY2024 225.90/6.08 = 37.155, FY2025 254.52/7.46 = 34.118; min / median / max × 7.46 →
  **$180.1477 / $205.5763 / $277.1737**.
- Owner earnings: NI + D&A − min(D&A, |capex|), FY2016–25 from $45.687B to $112.010B, positive in 10 of 10;
  basis = median FY2023–25 = $97.555B; CAGR 10.477% → g 10.477%; r = max(4.19%, 7%) = 7% in every scenario →
  **$204.5367 / $243.5793 / $289.3445**; buy below 0.70 × base = $170.5055.
- Summary (five models): **$89.52 / $111.90 / $111.90**; upside (111.90 − 343.03) / 343.03 = −67.38%;
  price above the bull median → looks expensive.

**JPM** (bank: DCF, Lynch and owner earnings not applicable):
- DDM: TTM 1.50 + 1.50 + 1.50 + 1.65 = $6.15; dividends 2016 $1.84 → 2025 $5.55: CAGR 13.051% → min with
  analyst 9.9% → g 9.9%. (g, r): (6.9%, 9.7575%), (9.9%, 8.7575%), (10% cap, 7.7575%) →
  **$104.5554 / $137.8756 / $165.6302**.
- Graham: EPS FY2023–25 16.23, 19.75, 20.02; BVPS = $362.438B ÷ 2.7815B = 130.3031 →
  **$218.1363 / $242.2706 / $242.2706**.
- Multiples (P/B): FY2021 139.87/97.1807 = 1.439, FY2022 122.19/98.4283 = 1.241, FY2023 159.61/111.4057 = 1.433,
  FY2024 230.31/119.7492 = 1.923, FY2025 316.13/130.3031 = 2.426; × 130.3031 →
  **$161.7618 / $187.5467 / $316.1314**.
- Summary (three models): **$161.7618 / $187.5467 / $242.2706**; upside −43.90%; looks expensive.

**KO**:
- DCF: basis = median FCF FY2023–25 (8.220, 3.085, 3.642B) = $3.642B; FCF CAGR FY2016–25 =
  (3.642 / 5.797)^(1/9) − 1 = −5.033% → g floored at 0%. (g, r): (0%, 9%), (0%, 8%), (3%, 7%) →
  **$11.5756 / $13.6264 / $19.8668**.
- DDM: TTM 0.51 + 0.53 + 0.53 + 0.53 = $2.10; dividends 2016 $1.40 → 2025 $2.04: CAGR 4.272% → g 4.272% →
  **$31.3935 / $42.2933 / $59.0243**.
- Graham: EPS FY2023–25 2.47, 2.46, 3.04; BVPS = $32.169B ÷ 4.313B = 7.4586 → **$20.3183 / $22.5869 / $22.5869**.
- Lynch: EPS FY2016 1.49 → FY2025 3.04: CAGR 8.245% → fair P/E 5.245 / 8.245 / 11.245 × 3.04 →
  **$15.9462 / $25.0662 / $34.1862**.
- Multiples (P/E): FY2021 51.55/2.25 = 22.909, FY2022 57.01/2.19 = 26.034, FY2023 54.49/2.47 = 22.059,
  FY2024 59.32/2.46 = 24.115, FY2025 68.58/3.04 = 22.559; × 3.04 → **$67.0602 / $69.6443 / $79.1438**.
- Owner earnings: FY2016–25 from $6.527B to $13.107B (FY2017 $1.248B), positive in 10 of 10; basis = median
  FY2023–25 = $10.714B; CAGR 8.055% → g 8.055%; r 7% → **$66.0509 / $78.8145 / $93.8138**; buy below $55.1701.
- Summary (five models): **$20.3183 / $25.0662 / $34.1862**; upside −73.21%; looks expensive.

MATCH-02 (cross-source fundamentals against the filed 10-Ks, with sources and dates) is still to come.

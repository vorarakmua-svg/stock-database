# Quality scores and ranks: design (Milestone 2)

- Date: 2026-10-21
- Status: designed by the build loop (task M2-0) from the approved plan's outline in
  `docs/superpowers/plans/2026-10-21-gurufocus-roadmap.md` ("Milestones 2-5").
- Decisions left to the user are NEEDS-USER, each with a default that changes no existing number.

## Goal

GuruFocus shows a GF Score (0-100) from five ranks, a Business Predictability rank, Piotroski F, Altman Z,
Beneish M, Sloan, ROIC vs WACC, and a list of warning signs. This milestone builds our counterparts. Every
formula and weight is published here and in the README, every score is reproducible from stored data, and
the existing models, median and verdicts are untouched. The package is `src/scores/`.

## Inputs (`src/scores/fundamentals.py`)

- **Annual records** come from `valuation.inputs.load_inputs` (the FY records, with `shares` and EPS on
  today's split basis, `split_factor` applied), joined per fiscal year with `financials_annual` (revenue,
  cost_of_revenue, gross_profit, operating_income, sga_expense, interest_expense, income_tax_expense,
  pretax_income, total_assets, current_assets, current_liabilities, total_liabilities, long_term_debt,
  short_term_debt, cash_and_equivalents, short_term_investments, accounts_receivable, inventory, ppe_net,
  goodwill, retained_earnings, total_equity, operating_cash_flow, investing_cash_flow, capex,
  depreciation_amortization, dividends_paid, shares_outstanding) and `metrics_annual` (ebitda, ebit, roic,
  operating_margin, interest_coverage). The latest 11 fiscal years are used.
- **Per-share series**, per fiscal year, on today's split basis (the FY record's `shares`): revenue/share,
  EPS (the record's diluted EPS), EBITDA/share, FCF/share (`free_cash_flow`), book/share (total equity).
  The FY records fold a share-count jump by a declared share split, by its own ratio on a split's marks (EPS
  against the count, revenue not with it, the EPS-implied count moving with it), or when the older year ends
  before the listing or in its year (pre-listing counts leave out converted preferred). Otherwise mergers,
  exchange offers, swinging counts and spin-off price factors fold nothing (BUG-185, as BUG-175 for the
  history). A year that ended before the first price bar and that only the listing's jump folds has the
  private count (`FYRecord.private_count`): a merger around the listing jumps the same way (DELL's EMC),
  so the scores give that year no per-share figures and keep its totals. The listing's own year keeps the
  public count, as the history's listing quarter does (BUG-188).
- **Debt** (`read_debt`, BUG-189, BUG-190). A year where no debt tag holds a positive amount:
  - with interest expense: the debt is in a tag the mapping doesn't read (PGR's
    `DebtLongtermAndShorttermCombinedAmount`), so total debt, net debt and ROIC are None and the row is
    `debt_unknown`: no WACC or earnings yield, no cash/debt or debt/revenue;
  - without, outside banks and insurers: no debt (ISRG, ANET, PANW, PLTR, VRTX): total debt 0, net debt
    = -(cash + short-term investments), and ROIC by `CalculatedMetrics` on zero debt;
  - a bank or an insurer keeps its debt as filed (KKR tags neither debt nor interest).
  A missing interest figure alone decides nothing: many filers with debt tag their interest under an
  unmapped name. `metrics_annual` itself is unchanged.
- **CAGRs** over 3, 5 and 10 years: `(last / first) ** (1 / years) - 1`. Both ends must be positive, and the
  start year must exist exactly `years` fiscal years back; otherwise None.

## Scores (`src/scores/scores.py`)

All on the latest fiscal year (t) against the one before (t-1), unless said otherwise. Banks and insurers
are N/A where the sector rules suppress an input (`sector_metrics`), with a reason.

- **Piotroski F**, 0-9. Each of the nine tests is stored with its pass/fail. N/A for banks and insurers
  (the current ratio and gross margin don't exist for them). A test whose inputs are missing counts as a
  fail, and the number of tests that could run is stored (`piotroski_tested`), so F is "out of N tested".
  - ROA = NI / total assets > 0.
  - CFO > 0.
  - ROA rose.
  - CFO > NI.
  - LTD / total assets fell. Untested when long-term debt is missing in either year.
  - The current ratio rose.
  - Shares outstanding did not rise: the FY records' split-normalized count, within 0.5% (rounding).
  - The gross margin rose.
  - Asset turnover rose.
- **Altman Z**, the original model: 1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA.
  - MVE is the latest snapshot's market cap. WC = current assets - current liabilities. EBIT = operating
    income.
  - Zones: below 1.81 distress, 1.81-2.99 grey, above 2.99 safe.
  - N/A for banks, insurers, REITs and utilities. A regulated utility's heavy assets and slow turnover read
    as distress under the original model: D, DUK, NEE and SO all scored below 1.2 on the real data.
  - Z'' for non-manufacturers needs the SIC code, which M5 stores. Until then the original applies to the
    other non-financials.
- **Beneish M** (8 variables):
  `M = -4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI + 0.115·DEPI - 0.172·SGAI + 4.679·TATA - 0.327·LVGI`.
  - The variables:
    - DSRI = (AR/Sales)_t / (AR/Sales)_t-1.
    - GMI = GM_t-1 / GM_t.
    - AQI = (1 - (CA+PPE)/TA)_t / (1 - (CA+PPE)/TA)_t-1.
    - SGI = Sales_t / Sales_t-1.
    - DEPI = (D&A/(D&A+PPE))_t-1 / (D&A/(D&A+PPE))_t.
    - SGAI = (SGA/Sales)_t / (SGA/Sales)_t-1.
    - LVGI = ((CL+LTD)/TA)_t / ((CL+LTD)/TA)_t-1.
    - TATA = (NI - CFO)_t / TA_t.
  - SGAI and DEPI are 1 (neutral) when their inputs are missing. A missing long-term debt reads as zero in
    LVGI: debt-free filers tag none (ANET, ISRG, PANW, PLTR). Any other missing variable means N/A.
  - Above -1.78 flags a likely manipulator.
  - N/A for banks, insurers and REITs.
- **Sloan ratio**: (NI - CFO - CFI) / total assets.
  - Within ±10% is safe, 10-25% is a warning, and beyond ±25% is flagged.
- **ROIC vs WACC**:
  - ROIC is `metrics_annual.roic`, or the same formula on zero debt for a debt-free year (see Debt). Where it
    is None, `na_reasons.roic` says why: not meaningful for banks, insurers and REITs (the sector rules
    suppress it), unknown debt, no operating income, no equity or debt figure, or invested capital of 0 or
    less.
  - WACC = E/(D+E)·Re + D/(D+E)·Rd·(1-t), where:
    - E is the market cap and D is the total debt;
    - Re is the DCF's CAPM cost of equity (`valuation.assumptions`, with its floor and cap);
    - Rd = interest expense / total debt, clamped to [rf, rf + 10%];
    - t is the effective tax rate, clamped to 0-35% (21% when unknown).
  - The spread is ROIC - WACC. N/A for banks and insurers, and WACC is N/A when the debt is unknown.
- **Predictability**, 1-5 stars in half steps, from the last 10 fiscal years. It's our own published
  formula; GuruFocus's is unpublished.
  - Fewer than 10 years is N/A. Any year with revenue or EBITDA per share ≤ 0 is 1 star (as GuruFocus
    treats an operating loss).
  - Otherwise, fit ln(series) against the year for revenue/share and EBITDA/share. If either slope is ≤ 0,
    1 star. Else stars = 1 + 4·min(R²_revenue, R²_EBITDA), rounded down to the half star.
  - For banks and insurers, EPS takes EBITDA's place (EBITDA is suppressed).

## Warning signs (`src/scores/signs.py`)

Each sign has a code, a severity (severe or medium), a message stating the figure, the value and the
threshold. Only the signs that fire are stored.

| Code | Severity | Fires when |
|---|---|---|
| altman_distress | severe | Altman Z < 1.81 |
| beneish_flag | severe | Beneish M > -1.78 |
| sloan_high | severe / medium | abs(Sloan) > 25% / > 10% |
| piotroski_low | medium | F ≤ 3 (out of 9 tested, at least 7 tested) |
| interest_coverage_low | severe / medium | coverage < 1.5 / < 3 |
| revenue_decline | medium | revenue fell in each of the last 3 years |
| margin_decline | medium | operating margin fell by more than 5 points over 5 years |
| fcf_negative | medium | FCF < 0 in 2 of the last 3 years |
| dilution | medium | split-normalized shares up more than 3% a year over 3 years |
| debt_outgrows_revenue | medium | total-debt CAGR exceeds revenue CAGR by 10+ points over 3 years, debt > 0 |
| receivables_outgrow_revenue | medium | DSO up more than 20% over 3 years |
| inventory_outgrows_revenue | medium | inventory/revenue up more than 20% over 3 years |
| assets_outgrow_revenue | medium | asset CAGR exceeds revenue CAGR by 10+ points over 3 years |
| goodwill_heavy | medium | goodwill > 50% of total equity (equity > 0) |
| negative_equity | medium | total equity < 0 |
| payout_over_fcf | medium | dividends paid > FCF > 0, or dividends paid > 0 ≥ FCF |
| roic_below_wacc | medium | ROIC < WACC |
| price_far_above_value | medium | price / historical value > 1.3 (M1) |
| pe_at_high | medium | P/E percentile in its 10 years ≥ 95% (M1) |
| gross_margin_decline | medium | gross margin down more than 5 points over 5 years |

## Ranks and the composite (`src/scores/ranks.py`)

Ranks are computed across the universe after every ticker's scores are stored, so one ticker's collection
re-ranks everyone.

- Each **component** becomes a percentile in (0, 1], computed within the company's `sector_class` when the
  class has at least 10 companies with the component, and within the whole universe otherwise. It is
  oriented so that higher is better.
- A **rank** is the mean of its available components' percentiles (at least two for financial strength,
  profitability and growth; valuation and momentum have one component each), else N/A, mapped to 1-10 as
  ceil(10·p), with a minimum of 1.

| Rank | Components |
|---|---|
| Financial strength | interest coverage (capped at 100), Altman Z, equity/assets, cash/debt (capped at 10), debt/revenue (lower is better) |
| Profitability | operating margin, its 10-year mean, its 5-year slope, Piotroski F, predictability stars, 10-year median ROIC, profitable years out of 10 |
| Growth | revenue/share CAGR over 3 and 5 years, EBITDA/share CAGR over 5 years (EPS for banks and insurers) |
| Valuation | price / historical value (lower is better; M1), else the P/E percentile in its 10 years (lower is better) |
| Momentum | ((P[-1m]/P[-12m] - 1) + (P[-1m]/P[-6m] - 1)) / 2 / max(beta, 0.5), from the bars and the snapshot's beta |

- **Composite**, 0-100: 10 × the mean of the five ranks, with at least four available. The weights are
  **equal by default**. Choosing them is the user's call (NEEDS-USER, Q11). GuruFocus weighs Profitability
  and Growth most, with unpublished weights.
- **Magic Formula** (Greenblatt):
  - earnings yield = EBIT / EV, where EV = market cap + net debt (N/A when the debt is unknown);
  - return on capital = EBIT / (net working capital + net PPE), where net working capital = current
    assets - cash - (current liabilities - short-term debt), floored at 0: a business its customers fund
    (MA, V, BKNG) ties up only its PPE (BUG-191);
  - `na_reasons.magic_formula` says why a company has no rank: no operating income (EBIT), no market cap,
    unknown debt, an enterprise value of 0 or less, no current assets or liabilities, no net PP&E figure, no
    capital employed, or no annual financials;
  - the combined rank is the sum of the two ranks across the universe, excluding banks, insurers,
    utilities and REITs; a tie in the sum goes to the higher earnings yield.

## Storage

- `per_share_annual`: (ticker, fiscal_year) → revenue_ps, eps, ebitda_ps, fcf_ps, book_ps.
- `quality_scores`: one row per ticker, holding:
  - fiscal_year;
  - the CAGRs: rev_ps, eps, ebitda_ps, fcf_ps and book_ps, over 3, 5 and 10 years where defined;
  - Piotroski: piotroski_f, piotroski_tested, piotroski_tests (JSON);
  - Altman: altman_z, altman_zone;
  - beneish_m and sloan_ratio;
  - roic, wacc, roic_minus_wacc, and debt_to_ebitda on the scores' debt (for the Buffett-Munger screen);
  - predictability_stars, plus the R² of each series;
  - ncav_per_share (for the Graham net-net screen), earnings_yield, return_on_capital;
  - momentum;
  - the ranks: rank_strength, rank_profitability, rank_growth, rank_valuation, rank_momentum;
  - composite_score and magic_formula_rank;
  - na_reasons (JSON) and computed_at.
- `warning_signs`: (ticker, code) → severity, message, value, threshold, computed_at.
- Each ticker's rows are replaced in one transaction. The rank pass updates the rank columns for all
  tickers in one transaction.

## Engine

`valuation.engine.compute_and_store` gains two hooks, each in its own try/except:
- after each ticker's history: `scores.engine.compute_ticker(conn, ticker)` and its store;
- after the loop: `scores.ranks.rank_universe(db_path)`.

Nothing in the valuations or the history changes. The backfill covers it as before.

## Web

- **API**: `GET /api/stocks/{t}/scores` returns the scores, the tests, the ranks, the composite, the growth
  table and the warning signs. 404 for an unknown ticker; "not computed yet" when absent.
- **STAT tab**: a lazily loaded "Quality" section (`/ui/stocks/{t}/scores`) holding:
  - the composite with a five-axis radar (Plotly `scatterpolar`);
  - the five ranks;
  - the scores: Piotroski with its nine tests, Altman's zone, Beneish, Sloan, ROIC vs WACC, predictability
    stars;
  - the growth table;
  - the warning signs, severe first.
- **Screener**: new fields from `quality_scores`, through a LEFT JOIN made only when the table exists (as
  M1 does): piotroski_f, altman_z, beneish_m, sloan_ratio, predictability_stars, rev_ps_cagr_5y,
  eps_cagr_5y, roic_minus_wacc, the five ranks, composite_score, momentum, magic_formula_rank, plus a count
  of warning signs, and scores_debt_to_ebitda (the stored debt_to_ebitda).
- **Preset screens**, a dropdown that fills the filters:
  - Buffett-Munger: predictability ≥ 4, ROIC - WACC > 0, scores_debt_to_ebitda ≤ 3 (a debt-free filer's is 0,
    where metrics_annual's debt_to_ebitda is empty), price / historical value ≤ 1.1.
  - Peter Lynch: the Lynch model's base above the price, via a `lynch_upside_pct` expression.
  - Graham net-net: price / NCAV per share ≤ 0.67.
  - Magic Formula: sorted by magic_formula_rank, with financials, utilities and REITs excluded.
  - Undervalued-predictable: predictability ≥ 4 and price / historical value < 0.9.

## Validation

- **Independent oracle**: `qa/oracle/scores_oracle.py`, importing nothing from `src`, recomputes from the
  DB's raw tables:
  - Piotroski (each test), Altman, Beneish, Sloan, the CAGRs and predictability: all must agree;
  - the ranks' percentiles, recomputed from the stored components.
  - It puts shares on today's split basis its own way: each year's count from its latest filing (the diluted
    weighted count; else net income / diluted EPS; else shares outstanding; a count 100x from its reference is
    in other units), times the declared share splits after that filing. So it also checks the FY records'
    seam folding, and found BUG-185.
  - Years ending before the first price bar + 1 year are not compared: their folded count is the spec's
    approximation of the converted preferred, which nothing in the filings states.
- **Real-data distributions**, recorded as evidence: the F-score histogram, Altman zones, the number of
  Beneish flags, predictability stars, the composite. Every severe warning sign gets explained or traced to
  the data.

## Tasks

M2-1 fundamentals; M2-2 scores; M2-3 warning signs; M2-4 storage, engine hooks and ranks; M2-5 oracle and
real-data validation; M2-6 API and STAT section (browser-checked); M2-7 screener fields and presets; M2-8
docs; M2-9 milestone close (code review 16, live backfill).

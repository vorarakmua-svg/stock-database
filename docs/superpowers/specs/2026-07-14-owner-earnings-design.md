# Owner Earnings ("Buffett mode") — Design

**Date:** 2026-07-14
**Status:** Approved design, pending implementation plan
**Builds on:** `2026-07-13-valuation-layer-design.md` (the five-model valuation layer)

## Problem

The valuation layer's DCF is a textbook academic model: free cash flow (operating
cash flow minus **total** capex) discounted at a CAPM cost of equity (risk-free +
beta x 4.5% ERP, floored at 8%). Driving it against the real 50-ticker database
exposed how much that method choice drives the answer:

| | median fair value / price | DCF above today's price |
|---|---|---|
| CAPM discount (8-14%) | 0.68x | 4 of 27 |
| Risk-free discount (4.56%) | 1.98x | 23 of 27 |

**The discount rate alone swings the median fair value by 170% and flips the
market from "expensive" to "cheap."** So "41 of 50 look expensive" is not an
objective fact about the market — it substantially encodes an 8% required-return
assumption. A user cannot see that from a single number.

Two further divergences from how Buffett describes his method:

- **Total capex** is subtracted, so a company investing heavily to grow is
  penalized as though that spending were merely keeping the lights on.
- **Beta** sets the discount rate, a measure Buffett rejects outright.

## Goals

- Add an owner-earnings model that follows Buffett's stated method, so the user
  can see how much of any verdict is *method* rather than *business*.
- Keep the honest-output rule: refuse to value what cannot be forecast.
- Never let the new model silently move the existing verdict.

## Non-goals

- Replacing the CAPM DCF. It is the industry-standard baseline and stays.
- A configurable/interactive hurdle rate. The value must be storable so the
  screener can rank on it; a calculator has no stored value.
- Claiming to reproduce Buffett's judgment. Maintenance capex is a judgment call
  he makes company by company; we use a documented, auditable proxy.

## The model

Model key: **`owner_earnings`**. One more entry in the existing five-model suite,
with one deliberate carve-out (below).

### Owner earnings

Per Buffett's 1986 shareholder-letter appendix, adapted to what filings give us:

```
owner_earnings    = net_income + depreciation_amortization - maintenance_capex
maintenance_capex = min(depreciation_amortization, abs(capex))
```

Growth capex — the excess of capex over D&A — is **not** subtracted. That is the
entire point of the model: stop treating expansion spending as a cost of standing
still.

Rationale for the proxy: depreciation *is* the accounting estimate of asset
consumption, i.e. the spend required to stand still. Capping at actual capex
prevents claiming more maintenance than the company actually spent (12 of 35
tickers with both figures spend less than D&A). D&A is available for 48 of 50
collected tickers.

**Stock-based compensation is NOT added back**, though it is a non-cash charge and
a naive reading of "owner earnings" would add it. It is real compensation and
Buffett has been explicit that pretending otherwise is dishonest. This makes the
model deliberately stricter than the textbook formula, and the choice is recorded
in the assumptions (`sbc_added_back: false`).

Basis = **median owner earnings of the last 3 fiscal years** (same shape as the
existing DCF's FCF basis).

### Discount rate

```
discount = max(risk_free_rate, 0.07)
```

The 10-year Treasury (already collected from FRED) is the rate Buffett describes
using. The **7% floor** honours his stated refusal to discount at absurdly low
rates when yields collapse — a pure risk-free discount would value almost anything
as infinitely cheap at a 1% yield. **Beta is not used at all.**

### Projection

Identical machinery to the existing DCF, reusing `dcf_per_share` so there is one
discounting implementation in the codebase, not two: years 1-5 at the base growth,
years 6-10 fading linearly to the 2.5% terminal rate, Gordon terminal at year 10.

Growth is derived by the existing conservative rule (`derive_growth`) on the
owner-earnings history: `min(historical CAGR, analyst estimate)`, clamped to
`[0, 15%]`. Bear/base/bull come from the existing `growth_scenarios` spread
(+/- 3pp), with the discount rate held **fixed** across scenarios — under this
method the rate is an observable market fact (the Treasury yield), not a risk
knob to be flexed.

### Margin of safety

The safety lives in the **price**, as Buffett describes it — not smuggled into the
discount rate.

```
MARGIN_OF_SAFETY = 0.30
buy_below = intrinsic_value_base x (1 - MARGIN_OF_SAFETY)
```

Verdict (its own, on its own terms):

- `price < buy_below`            -> **cheap** ("below intrinsic value with a margin of safety")
- `buy_below <= price <= base`   -> **fair**
- `price > base`                 -> **expensive**

Both the discount rate and the margin of safety are stored in the assumptions, so
any verdict can be traced to what produced it.

### Predictability gate

The most Buffett-like part of the method: he stays inside his circle of competence
and declines to forecast what he cannot predict.

**Require owner earnings positive in at least 8 of the last 10 fiscal years.**
Fewer -> N/A with the reason `owner earnings too erratic to forecast`.

**AMENDED 2026-07-14 after live verification — the original claim here was false.**
This gate was specified as "deliberately refuses to value cyclicals (SLB, COP and
similar)". Driving it against the real 50-ticker database disproved that: SLB, COP,
CVX and CAT are all valued. The gate tests the **sign** of owner earnings, not their
stability — and for capital-heavy cyclicals the large, steady D&A add-back genuinely
smooths owner earnings relative to net income.

Two stability metrics were then tested against the real data and both misclassify: a
log-linear trend fit refuses KO, PG, JNJ and WMT (archetypal predictable businesses)
while keeping SLB and COP; a collapse-year filter refuses ABBV, PFE and T while keeping
CVX, XOM and CAT. No simple mechanical gate reproduces the qualitative
circle-of-competence judgment.

So the model does not pretend to make it. The positivity gate stays — it genuinely
refuses loss-making histories — and the **volatility evidence is stored and shown**
instead (`assumptions.volatility`: `collapse_years`, `worst_drop`, `positive_years`,
`total_years`). The VAL tab states it plainly ("owner earnings were positive in 8 of 10
years; they fell by half or more against their recent normal in 2 of them; the worst
single-year fall was 100%") and says explicitly that the judgment is the reader's.
Showing the evidence is honest; faking a judgment no formula can make is not.

### N/A reasons (exact, user-facing copy)

- `not applicable to sector '<sector>'` — banks, insurers, REITs (as with the DCF;
  owner earnings are meaningless where capex and D&A are not the capital cycle)
- `insufficient history (need >= 4 fiscal years)`
- `insufficient history for the predictability test (need >= 10 fiscal years)` —
  fewer than ten years of owner earnings, so the 8-of-10 test cannot be run. A short
  history is not an erratic one, and saying otherwise would be a false reason.
- `owner earnings too erratic to forecast` — the predictability gate proper: a full
  ten-year window exists and fewer than eight of those years are positive
- `median 3-year owner earnings is not positive`
- `shares outstanding unavailable`

## The carve-out: excluded from the median

`intrinsic_summary` computes the cross-model medians that drive the existing
verdict, upside, and screener column. **`owner_earnings` is excluded from that
median.**

Mixing a risk-free-discounted model into a median with CAPM-discounted models
averages two incompatible philosophies and would silently drag every existing
verdict toward "cheap" without the user understanding why. The new model gets its
own verdict instead.

This carve-out is load-bearing and easy to break by accident (the engine's model
list is the obvious place a future model gets appended). It gets:
- a named constant `MEDIAN_MODELS` (the five), separate from the full model list;
- a test asserting `owner_earnings` never affects `valuation_summary` medians.

## Storage

**No schema change.** Reuses the existing `valuations` table with
`model = 'owner_earnings'`, so it carries the same bear/base/bull, assumptions
JSON, na_reason and basis_fiscal_year as every other model. `valuation_summary`
is unchanged in shape — only its *inputs* are restricted to `MEDIAN_MODELS`.

## Webapp

- **VAL tab** — a dedicated "Owner Earnings (Buffett)" section BELOW the
  five-model range chart, showing:
  - intrinsic value (bear/base/bull), the `buy_below` threshold, and its verdict chip;
  - a side-by-side comparison line making the method gap explicit:
    `Academic DCF $131 · Owner earnings $260 · Price $317`;
  - assumptions: maintenance capex, growth capex added back, discount rate (and
    that beta is unused), margin of safety, years of positive owner earnings.
  - When N/A, the reason — especially the erratic-earnings refusal, which is a
    feature, not a gap.
- **API** — the existing `/api/stocks/{ticker}/valuation` payload already returns
  every model row, so `owner_earnings` appears automatically. Add a top-level
  `owner_earnings_verdict` (its own verdict is not derivable from the shared
  medians).
- **Screener** — one new sortable column `Buffett upside %`
  (`(owner_earnings base - price) / price`) and a filter on its verdict, kept
  separate from the existing `Upside %`. Follows the existing whitelist/fixed-clause
  SQL pattern exactly; the injection invariant must not be weakened.

## Testing

- **Unit:** owner-earnings arithmetic against hand-computed fixtures (incl. the
  `min(D&A, capex)` cap in both directions); the discount floor; the margin-of-safety
  verdict boundaries; every N/A path incl. the 8-of-10 predictability gate.
- **Carve-out:** a test proving `owner_earnings` does not move `valuation_summary`
  medians — the one invariant a future model is likely to break.
- **Property:** bear <= base <= bull; higher growth => higher value.
- **Integration:** backfill on a fixture DB produces the sixth row per ticker.
- **Webapp:** VAL section, API field, screener column/filter.
- Constraints: ruff, mypy, Python 3.9.

## Verification

Backfill against the real 50-ticker database and confirm:
- cyclicals (SLB, COP) are refused with `owner earnings too erratic to forecast`;
- heavy growth investors (GOOGL, MSFT) show materially higher intrinsic values than
  their CAPM DCF, since their growth capex is no longer treated as a cost;
- the existing five-model verdicts are **byte-identical** to before (proving the
  median carve-out holds).

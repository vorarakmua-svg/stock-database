# Web Fetch & Update — Design

**Date:** 2026-07-14
**Status:** Approved design, pending implementation plan
**Builds on:** the valuation layer (PR #23/#24) and the existing collection job machinery
(`src/webapp/jobs.py`, `src/webapp/routes/collection_api.py`, `templates/collect.html`).

## Problem

The user updates all ~50 tickers quarterly and wants everything doable from the web —
including fetching a company that is not in the database yet — without touching the
terminal. Today:

- The webapp HAS a collect page and a background job manager that runs the same
  fetcher as `python -m src.main`, but it is gated behind `allow_collection`, which
  defaults to **off**, so the user has never seen it.
- **Bug:** `CollectionJobManager._run` exports via `fetcher.export(results,
  formats=["sqlite"])`, bypassing `fetch_and_export` — which is where the valuation
  recompute hook lives. A web-triggered fetch therefore updates fundamentals but
  leaves valuations stale, recreating exactly the "valuation tab is empty/stale"
  problem the user just hit.
- An unknown ticker (`/stocks/PGR` before PGR is collected) is a bare 404 with no
  path forward; the Ctrl+K palette offers nothing for a query with no matches.
- The workstation has no update affordance at all — the DES REFRESH button only
  re-fetches the quote snapshot.

## Goals

- One button on every stock page that updates that company end to end (fundamentals
  + market data + valuations), using the existing job machinery.
- A path to fetch a brand-new ticker from inside the web UI (palette and direct URL).
- Web fetches compute valuations exactly like terminal runs — one behavior, not two.

## Non-goals

- Batch "update all 50" UI. The collect page already accepts a ticker list, and the
  quarterly terminal run also works; both already recompute valuations.
- A separate "recompute valuations only" button. UPDATE DATA covers it (every fetch
  recomputes); dropped per the user's "maybe this is just one button".
- Auth/multi-user concerns. This is a personal, local tool; the `allow_collection`
  flag remains the single gate.

## Design

### 1. Job runner computes valuations (bug fix)

In `CollectionJobManager._run`, after `fetcher.export(results, formats=["sqlite"])`
succeeds, run the valuation engine for the job's tickers:

- lazy module import (`from ..valuation import engine as valuation_engine`) exactly
  like the `fetch_and_export` hook, so tests can monkeypatch and no import cycle
  forms at module load;
- `compute_and_store(db_path, tickers=[...successfully fetched tickers...])`;
- wrapped so a valuation failure NEVER fails the job (log + continue) — the job's
  collected data must still land.

The job status detail gains a `valuations_computed: int` field so the UI can show it.

### 2. `UPDATE DATA` button on the stock page

- Placement: the workstation header (`stock.html`), next to the existing watchlist
  star — visible from every tab, styled like the DES REFRESH button.
- Behavior: `hx-post` to a new UI fragment route `POST /ui/stocks/{ticker}/update`
  which submits a single-ticker job via the existing `CollectionJobManager` and
  returns the existing job-status poll fragment (`job_status.html` pattern —
  reuse it; do not build a second poller).
- On completion the poll fragment triggers a reload of the ACTIVE tab (htmx event),
  so whatever panel the user is on refreshes with new data and new valuations.
- Gated by `allow_collection` like every other collection surface: the button is
  simply not rendered when collection is disabled.
- The VAL tab's empty state drops "run collection or `python -m src.valuation.backfill`"
  and instead says "No valuations computed yet — click UPDATE DATA above."

### 3. Fetching a ticker that is not in the database

Two entry points, one destination:

- **Unknown-ticker page.** `GET /stocks/{ticker}` for an unknown ticker returns a
  rendered page (still HTTP 404) instead of the bare error page, when the ticker is
  plausibly a symbol (regex `^[A-Z][A-Z0-9.\-]{0,5}$` after uppercasing) AND
  collection is enabled: "PGR is not in the database — [Fetch PGR]". The fetch
  button submits the same single-ticker job; the poll fragment redirects to
  `/stocks/PGR` on success (htmx `HX-Redirect` or a meta refresh on the completed
  state). For implausible paths (`/stocks/;drop`) or when collection is disabled,
  the existing plain 404 stands.
- **Ctrl+K palette.** When a query produces zero search results and matches the
  ticker regex, append a synthetic action item "Fetch <QUERY> from SEC/Yahoo" whose
  href is `/stocks/<QUERY>` (the unknown-ticker page above — palette stays a pure
  navigator; no new palette POST logic). Enter navigates there; the user clicks
  Fetch. Only shown when collection is enabled (the palette config already has
  access to app state via the page; pass a flag through the template).

### 4. `allow_collection` defaults to on

`WebSettings.allow_collection` flips its default from `False` to `True`
(`STOCK_WEB_ALLOW_COLLECTION=0` disables). Rationale: personal local tool;
"everything in the web" is the point. The Collect nav link and all gated surfaces
light up by default. Existing 409-when-disabled behavior and its tests stay —
tests construct their settings explicitly.

## Error handling

- Fetch fails for the ticker (bad symbol, SEC/Yahoo down): the job completes with
  the error recorded; the poll fragment shows the job's error state (existing
  behavior) instead of redirecting. The unknown-ticker page can be retried.
- Valuation step fails: logged, job still succeeds with `valuations_computed: 0`.
- Concurrent clicks: the job manager already serializes/queues jobs; the button is
  disabled while the poll fragment is live (htmx request indicator).

## Testing

- `jobs.py`: after a (mocked-fetcher) job run, `compute_and_store` was called with
  the job's tickers (monkeypatch the lazy import target, mirroring
  `test_fetch_and_export_triggers_valuations`); a raising valuation step does not
  fail the job.
- Update route: POST submits a job and returns the poll fragment; gated off →
  route absent from markup and POST returns 409/404 consistent with existing
  collection gating.
- Unknown-ticker page: plausible ticker + collection on → 404 status with Fetch
  button; implausible path → plain 404; collection off → plain 404.
- Palette: template flag renders the fetch action config; JS syntax check.
- Settings: default flips to True; `STOCK_WEB_ALLOW_COLLECTION=0` disables.
- Gates: ruff, bare mypy, full pytest, `node --check` on touched JS. Python 3.9.

## Rollout

1. Job-runner valuation hook (the bug fix — valuable alone).
2. UPDATE DATA button + poll-driven tab reload + VAL empty-state copy.
3. Unknown-ticker fetch page + palette action.
4. Default flip + docs (README/USAGE_GUIDE: "updating from the web").

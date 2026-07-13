# Webapp "Slate Pro" UI modernization — restyle + UX upgrades

**Date:** 2026-07-10
**Status:** Approved (design), pending implementation plan

## Context

The webapp (FastAPI + Jinja + HTMX + Plotly, PR #21) currently wears a deliberately retro Bloomberg-terminal skin: amber-on-black, all-monospace, zero radius, fixed top command bar with function codes. The user wants a **modern fintech pro** look — keep the dark, dense, keyboard-driven workstation workflow, but restyle it like Koyfin/TradingView-class products, plus targeted UX upgrades.

## Decisions (locked with user, via visual mockup comparison)

- **Direction:** modern fintech pro. Dark, dense, keyboard-driven stays; retro terminal styling goes.
- **Tech depth:** restyle + UX upgrades on the current stack. No frontend rewrite, no framework change.
- **Visual identity:** **Slate Pro** — GitHub-dark/Koyfin-style blue-grey slate surfaces, blue accent, pill badges, 10px radius, gradient-filled charts.
- **Home:** watchlist **cards** layout (sparkline cards), not dense table.
- **Screener:** **filter chips** builder, not sidebar filters.
- **Tabs/palette labels:** friendly + code ("Overview · DES"); palette accepts both `AAPL GP` and `AAPL chart`.
- **Theme:** dark only. Token layer must make a future light theme cheap.
- **UX scope:** command palette ⌘K, analysis-first home, screener overhaul, polish pack (skeletons, empty states, transitions, responsive) — "improve it to the fullest" within the current stack.

## 1. Design system (rewrite `static/app.css` as tokens + components)

### Tokens

```css
--color-bg:        #0d1117;  /* page background */
--color-surface:   #161b22;  /* panels, cards */
--color-surface-2: #1c2128;  /* nested/hover surfaces */
--color-border:    #21262d;  /* hairline panel borders */
--color-border-2:  #30363d;  /* input borders, stronger dividers */
--color-ink:       #e6edf3;
--color-ink-muted: #8b949e;
--color-accent:    #1f6feb;  /* fills (active tab, primary button) */
--color-accent-2:  #388bfd;  /* lines, links, chart strokes */
--color-accent-3:  #79b8ff;  /* chip text */
--color-up:        #2ea043;  /* fills */  --color-up-text:   #3fb950;
--color-down:      #f85149;
--color-warn:      #d29922;
--radius:    10px;  /* panels */
--radius-sm: 6px;   /* inputs, buttons */
--radius-pill: 999px;
--shadow: 0 1px 3px rgba(0,0,0,0.4);        /* subtle, panels */
--shadow-overlay: 0 16px 48px rgba(0,0,0,0.55); /* palette, modals */
```

- **Typography:** Inter for UI text; JetBrains Mono for numerics, tickers, code. Load via Google Fonts CDN (consistent with existing Plotly/HTMX CDN usage) with system fallbacks (`"Segoe UI", system-ui` / `Consolas`). `font-variant-numeric: tabular-nums` on every numeric cell. Base 14px, 1.5 line-height.
- **Color usage rule:** Δ%/signed values get green/red; accent blue is for interactive elements and chart lines; everything else stays neutral.
- **Motion:** 120–160ms ease transitions on hover, tab switch, panel swap (HTMX `htmx-swapping` fade). `prefers-reduced-motion` respected.

### Component classes (used by every page)

Panel, section header (uppercase 10px letterspaced muted label), data table (sticky header, hover row, `.num` right-aligned mono), tabs (friendly + code hint, active = accent underline or fill), chip (removable pill) + add-chip (dashed), pill badge (Δ% up/down tinted background), buttons (primary accent / ghost), inputs & selects (dark, `--radius-sm`, accent focus ring), skeleton loader (shimmer blocks), empty state (icon + one-liner + action link), sparkline (inline SVG), stat cell (label-over-value).

## 2. Navigation + command palette (⌘K)

- **Remove the fixed top command bar.** One top nav remains: brand, links (Home, Companies, Screener, As-Of, Quality, Collect-if-enabled), and a right-aligned "Search or jump to… ⌘K" button-style affordance.
- **Palette overlay** (centered, `--shadow-overlay`, dims page) opens via `⌘K`/`Ctrl+K`, `` ` ``, or `/`. Contents:
  - **Recent tickers** (localStorage, top when query empty).
  - **Fuzzy matches** across tickers/company names (existing `/api/companies/search`), pages, and function codes with friendly aliases.
  - **Grammar preserved:** `AAPL` → workstation; `AAPL GP` or `AAPL chart` → chart tab. Codes: DES/GP/FA/ERN/STAT/HP/DVD/HDS/INS + global SCR/ASOF/QM/COL/HELP, each with a friendly alias (chart, financials, earnings, …).
  - Arrow-key navigation, Enter to go, Esc to close. Each row shows name + code hint + destination.
- Implementation: evolve `static/terminal.js` (vanilla JS, no dependencies). HELP overlay restyled and reachable from palette.

## 3. Home — analysis-first watchlist (replaces dashboard content)

- **Watchlist cards grid** at top: ticker, company name, last price, Δ% pill, 3-month sparkline, P/E, quality score. "+ Add ticker" card opens the palette in add-mode (selecting a ticker adds it to the watchlist instead of navigating). Star/unstar also available on the stock page quote header.
- **Persistence:** watchlist + recently-viewed live in **localStorage** (single-user tool; webapp never writes the DB — single-writer rule preserved).
- **New endpoint** `GET /api/stocks/summary?tickers=A,B,C` (read-only, batch): per ticker — last price, change, change %, P/E, quality score, ~63 trading days of closes for the sparkline. Reader method reuses `quote()`/`price_bars()` queries; unknown tickers are skipped in the response (client prunes them).
- **Below the fold:** Recently viewed (client-rendered from localStorage + same summary endpoint), sector coverage panel (existing data, restyled).
- **Ops health strip** (bottom, one line): companies count, last-collected date, warnings count → links to Quality. Collection-runs table and unmapped-tags worklist move off the home page entirely (Quality page already covers them; add the unmapped-tags table there if missing).
- **Empty state:** "Search a ticker (⌘K) to start your watchlist."

## 4. Stock workstation restyle

- **Quote header:** large mono price, Δ and Δ% as tinted pill, after-hours if present, "as of …" stamp, REFRESH ghost-button (existing quote-job polling), star toggle.
- **Tabs:** sticky under header, label format "Overview · DES" (friendly first, mono code hint muted). Same HTMX fragment routes; no URL changes.
- **Panel loads:** skeleton loaders (shimmer table/chart blocks) via `htmx-indicator` instead of text.
- **Plotly retheme (keep Plotly):** one shared JS layout template applied by the existing render helpers — transparent paper/plot backgrounds, `#21262d` gridlines, Inter hover font, unified hovermode, accent-blue line series with gradient area fill, candles in token green/red, MA/RSI/MACD series recolored to a small categorical set, modebar hidden except zoom-reset, range buttons restyled as a segmented pill control. Decision note: TradingView `lightweight-charts` was considered and rejected — not worth losing Plotly's working indicator subplots.
- 52-week range bar, analyst target-price bar, and recommendation gauge redrawn with tokens.

## 5. Screener — filter chips builder

- **Chip bar panel:** active filters as removable chips (`P/E ≤ 25 ✕`, `Sector: Technology ✕`). "+ Add filter" opens a searchable popover listing metrics grouped (Metrics / Market & Valuation) — picking one shows operator select + value input tuned to the metric (% vs absolute), then becomes a chip.
- **Auto-run:** debounced (~400ms) HTMX GET on chip add/remove/edit; no Run button. Existing `/ui/screen` endpoint and `field_op=value` param format unchanged.
- **Results:** match count, sortable column headers (click toggles sort/dir, re-issues request using existing `sort`/`sort_dir` params), Δ%/signed columns colored, CSV export link carrying current params.
- **Shareable:** chip state serializes to the page URL (`history.replaceState`) so a copied link reproduces the screen; **saved screens** named in localStorage, listed on the screener page.

## 6. Remaining pages + polish pack

- **Companies, As-Of, Quality, Collect, error page:** restyled in place with the component library; no flow changes. Quality gains the unmapped-tags worklist from the old home (if not already present).
- **Responsive:** usable down to ~768px — nav collapses sensibly, card grids stack, tables get horizontal scroll containers. No dedicated mobile design.
- **States:** every async region gets a skeleton; every empty dataset gets a designed empty state ("no data collected — refresh this ticker"); errors render the styled error fragment.

## 7. Explicitly unchanged / out of scope

- All existing routes, JSON APIs, DB schema, collection pipeline, and features keep working. Only addition: the read-only summary endpoint.
- No React/SPA, no light theme (tokens enable one later), no streaming quotes, no auth/multi-user, no server-side watchlist.

## 8. Testing

- Existing route/API tests stay green (markup assertions updated where class names change).
- New: summary endpoint tests (batch, unknown ticker skipped, empty bars → no sparkline), screener param serialization round-trip (URL → chips → request params).
- JS stays dependency-free and untested-by-CI (manual live-smoke per project workflow); keep palette/chips logic in small pure functions where practical.
- Constraints: Python 3.9 compatibility, ruff + mypy clean, live-smoke after implementation.

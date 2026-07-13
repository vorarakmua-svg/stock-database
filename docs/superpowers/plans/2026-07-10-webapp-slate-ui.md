# Webapp "Slate Pro" UI Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the FastAPI/Jinja/HTMX webapp from the retro Bloomberg-terminal skin to the "Slate Pro" modern fintech look, and ship four UX upgrades: ⌘K command palette, watchlist-cards home, chip-based screener, and a polish pack (skeletons, empty states, responsive).

**Architecture:** Restyle-by-redefinition — the new `app.css` keeps every existing CSS class name and redefines its look with design tokens, so most templates need no class changes. Structural changes are limited to: `base.html` (nav + palette), `index.html` (watchlist home), `screener.html` (chips), `stock.html` (tabs/star/skeleton), plus three new vanilla-JS files. One new read-only JSON endpoint (`GET /api/stocks/summary`). No DB writes from the webapp, no new Python or JS dependencies.

**Tech Stack:** FastAPI + Jinja2 + HTMX 2 + Plotly (CDN) + vanilla JS. Fonts: Inter + JetBrains Mono via Google Fonts CDN. SQLite read-only via `src/webapp/repository.py`.

**Spec:** `docs/superpowers/specs/2026-07-10-webapp-modern-ui-design.md`

## Global Constraints

- Python 3.9 compatibility (`from __future__ import annotations` where needed; `typing.List/Optional`, no `X | Y` types).
- Lint/type/test gates (same as CI): `ruff check src/ diagnose.py tests/`, `mypy`, `pytest` — all must pass at every commit.
- No new Python dependencies; no new JS dependencies (vanilla JS matching existing `app.js` style — `var`, IIFEs, no build step).
- The webapp never writes the SQLite DB (single-writer rule). Watchlist/recents/saved-screens live in `localStorage`.
- All existing routes and JSON APIs keep working, **except** `/ui/search` which is deliberately removed in Task 2 (replaced by the palette).
- Dark theme only. All colors via CSS custom properties (tokens) — no hard-coded hex in templates.
- Work on branch `feat/slate-ui` (create via superpowers:using-git-worktrees at execution start).
- Commit messages follow the repo convention `feat(webapp): …` / `fix(webapp): …`, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

### Design tokens (single source of truth for every task)

| Token | Value | Use |
|---|---|---|
| `--color-bg` | `#0d1117` | page background |
| `--color-surface` | `#161b22` | panels, cards |
| `--color-surface-2` | `#1c2128` | nested/hover surfaces, hover rows |
| `--color-border` | `#21262d` | hairline panel borders, chart grids |
| `--color-border-2` | `#30363d` | input borders, stronger dividers |
| `--color-ink` | `#e6edf3` | primary text |
| `--color-ink-muted` | `#8b949e` | secondary text |
| `--color-accent` | `#1f6feb` | fills (primary button, active tab) |
| `--color-accent-2` | `#388bfd` | links, chart lines |
| `--color-accent-3` | `#79b8ff` | chip text |
| `--color-up` / `--color-up-text` | `#2ea043` / `#3fb950` | positive fills / text |
| `--color-down` | `#f85149` | negative |
| `--color-warn` | `#d29922` | warnings, MA50 line |
| `--radius` / `--radius-sm` / `--radius-pill` | `10px` / `6px` / `999px` | panels / inputs / pills |

Old→new color mapping when porting any legacy rule: `#0a0a0a→var(--color-bg)`, `#141414→var(--color-surface)`, `#2a2a2a→var(--color-border)`, `#e6e3dc→var(--color-ink)`, `#8a8a8a→var(--color-ink-muted)`, `#ff9900→var(--color-accent-2)` (links/lines) or `var(--color-accent)` (fills), `#00e676→var(--color-up-text)`, `#ff5252→var(--color-down)`, `radius: 0→var(--radius)` (panels) or `var(--radius-sm)` (controls), IBM Plex Mono→`var(--font-ui)` for UI text, `var(--font-mono)` only for numerics/tickers/code.

---

### Task 1: Slate Pro design system — rewrite `static/app.css`, load fonts

**Files:**
- Modify: `src/webapp/static/app.css` (full rewrite)
- Modify: `src/webapp/templates/base.html` (font links only in this task)

**Interfaces:**
- Produces: every CSS class listed in "Selector coverage" below, styled with the tokens above. Later tasks rely on these class names: `.pill` (+ `.up/.down/.flat/.num-pos/.num-neg` variants), `.skeleton`, `.skeleton-stack`, `.empty-state`, `.wl-grid`, `.wl-card`, `.ops-strip`, `.palette-overlay`, `.palette`, `.palette-input`, `.palette-row`, `.palette-open`, `.filter-chip`, `.chip-add`, `.popover`, `.tab-code`, `.star-btn`, `.mono`.

- [ ] **Step 1: Add font links to `base.html`**

In `src/webapp/templates/base.html`, directly above the existing `<link rel="stylesheet" href="/static/app.css" />` line, add:

```html
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
```

- [ ] **Step 2: Selector coverage inventory**

Run (Git Bash):
```bash
grep -oE '^[^ /@}][^{]*\{' src/webapp/static/app.css | sed 's/ *{$//' | tr ',' '\n' | sed 's/^ *//' | sort -u > /tmp/old-selectors.txt
wc -l /tmp/old-selectors.txt
```
Expected: ~150 selectors. After writing the new file (Step 3), every one of these must still be defined (or intentionally dropped — only the `.cmdbar*` family may be dropped, and only in Task 2, so in this task keep them).

- [ ] **Step 3: Replace `app.css` with the Slate Pro stylesheet**

Replace the entire file with the stylesheet below, **then** port any selector present in `/tmp/old-selectors.txt` but absent below by copying its old rule and applying the color mapping table (Global Constraints). Known families the executor must port this way (they are page-specific and their old rules are fine once recolored): `.asof-*`, `.job-*`, `.form-badge`, `.form-group`, `.form-label`, `.form-input`, `.form-actions`, `.snap-*`, `.snapshot-row`, `.meta-card`, `.label-col`, `.current-ticker`, `.statements-panel`, `.two-col`, `.error-*`, `.range-bar`, `.range-block`, `.range-endpoint`, `.range-marker`, `.range-marker--mean`, `.range-row`, `.chart-error`, `.filter-bar`, `.filter-label`, `.accn`, `.search-*` (kept until Task 2 removes the nav search), `.cmdbar*` (kept until Task 2).

```css
/* Stock DB — Slate Pro design system
   Modern fintech workstation: dark slate surfaces, blue accent, Inter UI +
   JetBrains Mono numerics, 10px panels, subtle motion. Dark only.
   Restyle-by-redefinition: class names are stable; their look lives here. */

/* ---- Tokens ---- */
:root {
  --font-ui: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
  --font-mono: "JetBrains Mono", "Cascadia Mono", Consolas, monospace;
  --color-bg: #0d1117;
  --color-surface: #161b22;
  --color-surface-2: #1c2128;
  --color-border: #21262d;
  --color-border-2: #30363d;
  --color-ink: #e6edf3;
  --color-ink-muted: #8b949e;
  --color-accent: #1f6feb;
  --color-accent-2: #388bfd;
  --color-accent-3: #79b8ff;
  --color-accent-bg: rgba(31, 111, 235, 0.15);
  --color-up: #2ea043;
  --color-up-text: #3fb950;
  --color-up-bg: rgba(46, 160, 67, 0.15);
  --color-down: #f85149;
  --color-down-bg: rgba(248, 81, 73, 0.15);
  --color-warn: #d29922;
  --color-stripe: #10151c;
  --radius: 10px;
  --radius-sm: 6px;
  --radius-pill: 999px;
  --shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
  --shadow-overlay: 0 16px 48px rgba(0, 0, 0, 0.55);
  --nav-h: 52px;
  --cmdbar-h: 34px; /* removed in Task 2 with the command bar */
  --ease: 140ms ease;
}

/* ---- Reset / base ---- */
*, *::before, *::after { box-sizing: border-box; }
html { font-size: 14px; }
body {
  margin: 0; font-family: var(--font-ui); color: var(--color-ink);
  background: var(--color-bg); line-height: 1.5; -webkit-font-smoothing: antialiased;
  padding-top: var(--cmdbar-h); /* removed in Task 2 */
}
a { color: var(--color-accent-2); text-decoration: none; transition: color var(--ease); }
a:hover { color: var(--color-accent-3); }
h1, h2, h3 { font-weight: 600; margin: 0 0 0.5rem; color: var(--color-ink); letter-spacing: -0.01em; }
h1 { font-size: 1.45rem; }
h2 { font-size: 1.05rem; }
p { margin: 0 0 0.75rem; }
code { font-family: var(--font-mono); font-size: 0.85em; color: var(--color-accent-3); background: var(--color-surface-2); padding: 0.1em 0.35em; border-radius: 4px; }
label { color: var(--color-ink-muted); font-size: 0.85rem; }
.mono, .num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.muted, .text-muted { color: var(--color-ink-muted); }
.up, .num-pos { color: var(--color-up-text); }
.down, .num-neg { color: var(--color-down); }
.flat { color: var(--color-ink-muted); }
.mb-2 { margin-bottom: 0.5rem; } .mb-3 { margin-bottom: 1rem; }
.mt-2 { margin-top: 0.5rem; } .mt-4 { margin-top: 1.5rem; } .ml-2 { margin-left: 0.5rem; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}

/* ---- Scrollbars ---- */
* { scrollbar-color: var(--color-border-2) var(--color-bg); scrollbar-width: thin; }
*::-webkit-scrollbar { width: 10px; height: 10px; }
*::-webkit-scrollbar-track { background: var(--color-bg); }
*::-webkit-scrollbar-thumb { background: var(--color-border-2); border-radius: 5px; border: 2px solid var(--color-bg); }
*::-webkit-scrollbar-thumb:hover { background: var(--color-ink-muted); }

/* ---- Nav ---- */
.nav {
  position: sticky; top: var(--cmdbar-h); z-index: 100; height: var(--nav-h);
  background: rgba(13, 17, 23, 0.9); backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--color-border);
}
.nav-inner {
  max-width: 1200px; margin: 0 auto; height: 100%; padding: 0 1.25rem;
  display: flex; align-items: center; gap: 1.25rem;
}
.nav-brand { font-weight: 700; font-size: 1rem; color: var(--color-ink); letter-spacing: -0.02em; }
.nav-brand:hover { color: var(--color-accent-3); }
.nav-links { display: flex; gap: 0.25rem; flex: 1; }
.nav-link {
  color: var(--color-ink-muted); font-size: 0.875rem; font-weight: 500;
  padding: 0.35rem 0.65rem; border-radius: var(--radius-sm); transition: background var(--ease), color var(--ease);
}
.nav-link:hover { color: var(--color-ink); background: var(--color-surface-2); }
.palette-open {
  display: inline-flex; align-items: center; gap: 0.75rem; cursor: pointer;
  background: var(--color-surface); border: 1px solid var(--color-border-2);
  color: var(--color-ink-muted); font-family: var(--font-ui); font-size: 0.8rem;
  padding: 0.35rem 0.4rem 0.35rem 0.75rem; border-radius: var(--radius-sm);
  transition: border-color var(--ease), color var(--ease);
}
.palette-open:hover { border-color: var(--color-accent-2); color: var(--color-ink); }

/* ---- Layout ---- */
.main { max-width: 1200px; margin: 0 auto; padding: 1.5rem 1.25rem 3rem; }
.section { margin: 1.75rem 0; }
.section-heading, .section-title, .help-col-title {
  font-size: 0.7rem; font-weight: 600; color: var(--color-ink-muted);
  text-transform: uppercase; letter-spacing: 0.07em; margin-bottom: 0.6rem;
}
.section-header, .chart-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.6rem; }
.page-header { margin-bottom: 1.25rem; display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.page-title { margin: 0; }
.page-subtitle { color: var(--color-ink-muted); font-size: 0.875rem; }
.hero { margin: 0.5rem 0 1.25rem; }
.hero-title { font-size: 1.45rem; }
.hero-sub { color: var(--color-ink-muted); }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }

/* ---- Panels / cards ---- */
.card, .panel, .chart-section, .meta-card, .job-status-card, .statements-panel {
  background: var(--color-surface); border: 1px solid var(--color-border);
  border-radius: var(--radius); box-shadow: var(--shadow); padding: 1rem 1.15rem;
}
.card-body { padding: 0; }
.card-title { font-size: 1rem; margin-bottom: 0.75rem; }
.panel { margin-top: 0.75rem; min-height: 200px; }

/* ---- Stat cards (home / dashboard) ---- */
.stat-row { display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 1rem 0; }
.stat-card {
  background: var(--color-surface); border: 1px solid var(--color-border);
  border-radius: var(--radius); padding: 0.75rem 1rem; min-width: 120px;
  display: flex; flex-direction: column; gap: 0.15rem;
}
.stat-value { font-family: var(--font-mono); font-size: 1.35rem; font-weight: 600; }
.stat-label { font-size: 0.72rem; color: var(--color-ink-muted); text-transform: uppercase; letter-spacing: 0.05em; }

/* ---- Tables ---- */
.table-wrap, .table-responsive { overflow-x: auto; border: 1px solid var(--color-border); border-radius: var(--radius); background: var(--color-surface); }
.data-table, .table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.data-table thead th, .table thead th {
  text-align: left; padding: 0.5rem 0.75rem; font-size: 0.7rem; font-weight: 600;
  color: var(--color-ink-muted); text-transform: uppercase; letter-spacing: 0.06em;
  border-bottom: 1px solid var(--color-border-2); background: var(--color-surface);
  position: sticky; top: 0;
}
.data-table td, .table td { padding: 0.45rem 0.75rem; border-bottom: 1px solid var(--color-border); }
.data-table tbody tr:last-child td, .table tbody tr:last-child td { border-bottom: none; }
.data-table tbody tr, .table-hover tbody tr { transition: background var(--ease); }
.data-table tbody tr:hover, .table-hover tbody tr:hover { background: var(--color-surface-2); }
.data-table tbody tr:nth-child(even) { background: transparent; }
.data-table tbody tr:nth-child(odd) { background: transparent; }
.data-table .num, .table .num { text-align: right; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.data-table .row-label, .help-table .row-label { color: var(--color-ink); font-weight: 500; }
.table-sm td, .table-sm th { padding: 0.35rem 0.6rem; }
.table-bordered td, .table-bordered th { border: 1px solid var(--color-border); }
.help-table td { color: var(--color-ink-muted); padding: 0.2rem 0.5rem 0.2rem 0; }
.empty-row td { color: var(--color-ink-muted); text-align: center; padding: 1.25rem; }

/* ---- Buttons ---- */
.btn {
  display: inline-flex; align-items: center; gap: 0.35rem; cursor: pointer;
  background: var(--color-surface-2); color: var(--color-ink);
  border: 1px solid var(--color-border-2); border-radius: var(--radius-sm);
  font-family: var(--font-ui); font-size: 0.85rem; font-weight: 500;
  padding: 0.4rem 0.85rem; transition: background var(--ease), border-color var(--ease);
}
.btn:hover { border-color: var(--color-ink-muted); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: var(--color-accent); border-color: var(--color-accent); color: #fff; }
.btn-primary:hover { background: var(--color-accent-2); border-color: var(--color-accent-2); }
.btn-secondary, .btn-outline-secondary { background: transparent; }
.btn-sm { font-size: 0.78rem; padding: 0.25rem 0.6rem; }

/* ---- Forms ---- */
.form-control, .form-input, .metric-select,
input[type="text"], input[type="search"], input[type="number"], input[type="date"], select {
  background: var(--color-bg); color: var(--color-ink);
  border: 1px solid var(--color-border-2); border-radius: var(--radius-sm);
  font-family: var(--font-ui); font-size: 0.85rem; padding: 0.4rem 0.6rem;
  transition: border-color var(--ease), box-shadow var(--ease);
}
input:focus, select:focus, .form-control:focus, .form-input:focus, .metric-select:focus {
  outline: none; border-color: var(--color-accent-2); box-shadow: 0 0 0 3px var(--color-accent-bg);
}
.form-control-sm { font-size: 0.8rem; padding: 0.3rem 0.5rem; }
.form-row { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.form-check-group { display: flex; gap: 1rem; align-items: center; }
.form-check-group label { display: inline-flex; align-items: center; gap: 0.3rem; cursor: pointer; }

/* ---- Pills / chips ---- */
.pill {
  display: inline-flex; align-items: center; gap: 0.25rem;
  font-family: var(--font-mono); font-size: 0.8rem; font-weight: 600;
  padding: 0.1rem 0.55rem; border-radius: var(--radius-pill);
  background: var(--color-surface-2); color: var(--color-ink-muted);
}
.pill.up, .pill.num-pos { background: var(--color-up-bg); color: var(--color-up-text); }
.pill.down, .pill.num-neg { background: var(--color-down-bg); color: var(--color-down); }
.chip-row { display: flex; gap: 0.4rem; flex-wrap: wrap; }
.chip {
  display: inline-flex; align-items: center; cursor: pointer;
  background: var(--color-surface); border: 1px solid var(--color-border-2);
  color: var(--color-ink-muted); border-radius: var(--radius-pill);
  font-size: 0.8rem; font-weight: 500; padding: 0.25rem 0.75rem;
  transition: color var(--ease), border-color var(--ease), background var(--ease);
}
.chip:hover { color: var(--color-ink); border-color: var(--color-ink-muted); }
.chip-active, .chip-active:hover { background: var(--color-accent); border-color: var(--color-accent); color: #fff; }
.filter-chip {
  display: inline-flex; align-items: center; gap: 0.45rem;
  background: var(--color-accent-bg); border: 1px solid rgba(56, 139, 253, 0.4);
  color: var(--color-accent-3); border-radius: var(--radius-pill);
  font-size: 0.8rem; padding: 0.25rem 0.4rem 0.25rem 0.75rem;
}
.filter-chip .filter-chip-x {
  background: none; border: none; cursor: pointer; color: var(--color-ink-muted);
  font-size: 0.85rem; line-height: 1; padding: 0 0.25rem; border-radius: 50%;
}
.filter-chip .filter-chip-x:hover { color: var(--color-down); }
.chip-add {
  display: inline-flex; align-items: center; cursor: pointer;
  background: transparent; border: 1px dashed var(--color-border-2);
  color: var(--color-ink-muted); border-radius: var(--radius-pill);
  font-size: 0.8rem; padding: 0.25rem 0.75rem; transition: color var(--ease), border-color var(--ease);
}
.chip-add:hover { color: var(--color-accent-3); border-color: var(--color-accent-2); }
.ticker-badge {
  font-family: var(--font-mono); font-size: 0.8rem; font-weight: 600;
  background: var(--color-accent-bg); color: var(--color-accent-3);
  padding: 0.15rem 0.6rem; border-radius: var(--radius-sm);
}
.kbd {
  display: inline-block; padding: 0.05rem 0.4rem; border: 1px solid var(--color-border-2);
  border-bottom-width: 2px; border-radius: 4px; color: var(--color-ink-muted);
  font-family: var(--font-mono); font-size: 0.72rem; background: var(--color-surface-2);
}

/* ---- Alerts ---- */
.alert { padding: 0.6rem 0.9rem; border-radius: var(--radius-sm); font-size: 0.85rem; border: 1px solid; margin-bottom: 0.75rem; }
.alert-danger { background: var(--color-down-bg); border-color: rgba(248, 81, 73, 0.4); color: var(--color-down); }
.alert-info { background: var(--color-accent-bg); border-color: rgba(56, 139, 253, 0.4); color: var(--color-accent-3); }

/* ---- Tabs (workstation + statements) ---- */
.tab-bar { display: flex; gap: 0.15rem; border-bottom: 1px solid var(--color-border); margin: 1rem 0 0; overflow-x: auto; }
.tab-btn {
  background: none; border: none; border-bottom: 2px solid transparent; cursor: pointer;
  color: var(--color-ink-muted); font-family: var(--font-ui); font-size: 0.85rem; font-weight: 500;
  padding: 0.5rem 0.8rem; white-space: nowrap; transition: color var(--ease), border-color var(--ease);
}
.tab-btn:hover { color: var(--color-ink); }
.tab-btn.tab-active { color: var(--color-ink); border-bottom-color: var(--color-accent); }
.tab-code { font-family: var(--font-mono); font-size: 0.68rem; color: var(--color-ink-muted); margin-left: 0.25rem; }
.tab-btn.tab-active .tab-code { color: var(--color-accent-3); }

/* ---- Quote header (workstation) ---- */
.company-header { margin-top: 1rem; }
.company-title-row { display: flex; align-items: center; gap: 0.75rem; }
.company-title-row h1 { margin: 0; }
.company-meta { color: var(--color-ink-muted); font-size: 0.85rem; margin-top: 0.15rem; }
.meta-item { color: var(--color-ink-muted); }
.meta-sep { margin: 0 0.35rem; color: var(--color-border-2); }
.star-btn {
  background: none; border: none; cursor: pointer; font-size: 1.15rem; line-height: 1;
  color: var(--color-ink-muted); padding: 0.15rem; transition: color var(--ease), transform var(--ease);
}
.star-btn:hover { transform: scale(1.15); }
.star-btn.starred { color: var(--color-warn); }
.quote-header { margin: 0.75rem 0 1rem; }
.quote-price-row { display: flex; align-items: baseline; gap: 0.75rem; }
.quote-price { font-family: var(--font-mono); font-size: 2rem; font-weight: 650; letter-spacing: -0.02em; }
.quote-change { font-size: 0.95rem; }
.quote-afterhours { font-size: 0.8rem; margin-top: 0.15rem; }
.quote-stamp-row { display: flex; align-items: center; gap: 0.75rem; margin-top: 0.35rem; font-size: 0.75rem; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.5rem; }
.summary-item {
  display: flex; flex-direction: column; gap: 0.1rem; padding: 0.55rem 0.75rem;
  background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-sm);
}
.summary-label { font-size: 0.68rem; color: var(--color-ink-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.summary-value { font-family: var(--font-mono); font-size: 0.9rem; font-weight: 600; }
.gp-controls { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.gp-chart { min-height: 420px; }

/* ---- Watchlist home ---- */
.wl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(215px, 1fr)); gap: 0.75rem; }
.wl-grid--small { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
.wl-card {
  position: relative; display: block; color: inherit;
  background: var(--color-surface); border: 1px solid var(--color-border);
  border-radius: var(--radius); padding: 0.75rem 0.9rem;
  transition: border-color var(--ease), transform var(--ease);
}
.wl-card:hover { border-color: var(--color-border-2); color: inherit; transform: translateY(-1px); }
.wl-top { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
.wl-ticker { font-weight: 700; font-size: 0.95rem; }
.wl-name { color: var(--color-ink-muted); font-size: 0.75rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wl-price { font-size: 1.25rem; font-weight: 600; margin: 0.2rem 0; }
.wl-meta { color: var(--color-ink-muted); font-size: 0.72rem; margin-top: 0.35rem; }
.wl-remove {
  position: absolute; top: 0.4rem; right: 0.4rem; display: none;
  background: var(--color-surface-2); border: none; border-radius: 50%; cursor: pointer;
  color: var(--color-ink-muted); font-size: 0.7rem; width: 1.3rem; height: 1.3rem;
}
.wl-card:hover .wl-remove { display: block; }
.wl-remove:hover { color: var(--color-down); }
.wl-add-tile {
  display: flex; align-items: center; justify-content: center; cursor: pointer; min-height: 120px;
  background: transparent; border: 1px dashed var(--color-border-2); border-radius: var(--radius);
  color: var(--color-ink-muted); font-size: 0.85rem; transition: color var(--ease), border-color var(--ease);
}
.wl-add-tile:hover { color: var(--color-accent-3); border-color: var(--color-accent-2); }
.spark { width: 100%; height: 30px; display: block; }
.ops-strip {
  display: flex; gap: 1.25rem; align-items: center; flex-wrap: wrap;
  margin-top: 2rem; padding: 0.5rem 0.9rem; font-size: 0.78rem; color: var(--color-ink-muted);
  background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-sm);
}

/* ---- Skeletons / empty states ---- */
@keyframes shimmer { 0% { background-position: -400px 0; } 100% { background-position: 400px 0; } }
.skeleton {
  border-radius: var(--radius-sm);
  background: linear-gradient(90deg, var(--color-surface) 25%, var(--color-surface-2) 50%, var(--color-surface) 75%);
  background-size: 800px 100%; animation: shimmer 1.4s infinite linear;
}
.skeleton--bar { height: 0.9rem; margin: 0.5rem 0; }
.skeleton--bar.w60 { width: 60%; }
.skeleton--block { height: 220px; margin-top: 0.75rem; }
.skeleton-stack { display: none; padding: 1rem 0; }
.skeleton-stack.htmx-request { display: block; }
.htmx-indicator { opacity: 0; transition: opacity var(--ease); }
.htmx-request .htmx-indicator, .htmx-request.htmx-indicator { opacity: 1; }
.empty-state {
  padding: 2.5rem 1rem; text-align: center; color: var(--color-ink-muted); font-size: 0.9rem;
  border: 1px dashed var(--color-border-2); border-radius: var(--radius);
}
.empty-state a { font-weight: 500; }

/* ---- HTMX swap fade ---- */
#panel.htmx-swapping, #screen-results.htmx-swapping { opacity: 0; transition: opacity 120ms ease; }
#panel, #screen-results { opacity: 1; transition: opacity 160ms ease; }

/* ---- Command palette ---- */
.palette-overlay {
  position: fixed; inset: 0; z-index: 400; background: rgba(1, 4, 9, 0.7);
  display: flex; align-items: flex-start; justify-content: center; padding: 12vh 1rem;
}
.palette-overlay[hidden] { display: none; }
.palette {
  width: 100%; max-width: 560px; background: var(--color-surface);
  border: 1px solid var(--color-border-2); border-radius: 12px; box-shadow: var(--shadow-overlay);
  overflow: hidden;
}
.palette-input {
  width: 100%; background: transparent; border: none; outline: none;
  color: var(--color-ink); font-family: var(--font-ui); font-size: 1rem;
  padding: 0.9rem 1.1rem; border-bottom: 1px solid var(--color-border);
}
.palette-input::placeholder { color: var(--color-ink-muted); }
.palette-results { max-height: 45vh; overflow-y: auto; }
.palette-row {
  display: flex; align-items: center; gap: 0.6rem; padding: 0.55rem 1.1rem;
  cursor: pointer; font-size: 0.875rem;
}
.palette-row.selected { background: var(--color-accent-bg); }
.palette-row-ticker { font-family: var(--font-mono); font-weight: 700; color: var(--color-accent-3); min-width: 4.5rem; }
.palette-row-name { color: var(--color-ink-muted); flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.palette-row-code { font-family: var(--font-mono); font-size: 0.7rem; color: var(--color-ink-muted); }
.palette-group {
  padding: 0.4rem 1.1rem 0.2rem; font-size: 0.65rem; font-weight: 600;
  color: var(--color-ink-muted); text-transform: uppercase; letter-spacing: 0.07em;
}
.palette-foot {
  display: flex; gap: 1rem; padding: 0.5rem 1.1rem; font-size: 0.72rem;
  color: var(--color-ink-muted); border-top: 1px solid var(--color-border);
}

/* ---- Screener popover ---- */
.popover {
  position: absolute; z-index: 200; min-width: 300px; margin-top: 0.35rem;
  background: var(--color-surface); border: 1px solid var(--color-border-2);
  border-radius: var(--radius); box-shadow: var(--shadow-overlay); padding: 0.6rem;
}
.popover[hidden] { display: none; }
.popover-list { max-height: 260px; overflow-y: auto; margin-top: 0.4rem; }
.popover-item { padding: 0.35rem 0.5rem; border-radius: var(--radius-sm); cursor: pointer; font-size: 0.85rem; }
.popover-item:hover { background: var(--color-surface-2); }
.popover-group { font-size: 0.65rem; font-weight: 600; color: var(--color-ink-muted); text-transform: uppercase; letter-spacing: 0.07em; padding: 0.45rem 0.5rem 0.1rem; }

/* ---- HELP overlay (restyled) ---- */
.help-overlay {
  position: fixed; inset: 0; z-index: 400; background: rgba(1, 4, 9, 0.7);
  display: flex; align-items: flex-start; justify-content: center; padding: 8vh 1rem;
}
.help-overlay[hidden] { display: none; }
.help-panel {
  background: var(--color-surface); border: 1px solid var(--color-border-2); border-radius: 12px;
  box-shadow: var(--shadow-overlay); width: 100%; max-width: 720px;
  padding: 1.25rem 1.5rem; max-height: 80vh; overflow-y: auto;
}
.help-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; }
.help-title { font-weight: 600; font-size: 0.95rem; }
.help-close {
  background: transparent; border: 1px solid var(--color-border-2); border-radius: var(--radius-sm);
  color: var(--color-ink); font-size: 1rem; line-height: 1; cursor: pointer; padding: 0.15rem 0.5rem;
}
.help-close:hover { border-color: var(--color-accent-2); color: var(--color-accent-3); }
.help-hint { color: var(--color-ink-muted); font-size: 0.8rem; margin-bottom: 1rem; }
.help-grid { display: flex; gap: 1.5rem; flex-wrap: wrap; }
.help-col { flex: 1; min-width: 260px; }

/* ---- Command bar (LEGACY — removed in Task 2) ---- */
.cmdbar {
  position: fixed; top: 0; left: 0; right: 0; z-index: 300; height: var(--cmdbar-h);
  background: var(--color-bg); border-bottom: 1px solid var(--color-border);
  display: flex; align-items: center; gap: 0.5rem; padding: 0 0.75rem;
}
.cmdbar-prompt { color: var(--color-accent-2); font-weight: 700; font-family: var(--font-mono); }
.cmdbar-input {
  flex: 1; background: transparent; border: none; outline: none;
  font-family: var(--font-mono); font-size: 0.85rem; color: var(--color-ink);
}
.cmdbar-input::placeholder { color: var(--color-ink-muted); }
.cmdbar-input:focus { box-shadow: none; }
.cmdbar-suggest { position: fixed; top: var(--cmdbar-h); left: 0.75rem; width: 340px; background: var(--color-surface); border: 1px solid var(--color-border-2); border-radius: var(--radius-sm); z-index: 300; }
.cmdbar-suggest:empty { display: none; }
.cmdbar-suggest-item { display: flex; gap: 0.4rem; padding: 0.35rem 0.6rem; font-size: 0.8rem; cursor: pointer; }
.cmdbar-suggest-item:hover { background: var(--color-surface-2); }
.cmdbar-suggest-item:last-child { border-bottom: none; }
.cmdbar-suggest-ticker { color: var(--color-accent-3); font-weight: 700; font-family: var(--font-mono); }
.cmdbar-suggest-name { color: var(--color-ink-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---- Responsive ---- */
@media (max-width: 900px) {
  .two-col { grid-template-columns: 1fr; }
  .nav-links { overflow-x: auto; }
}
@media (max-width: 768px) {
  html { font-size: 13px; }
  .main { padding: 1rem 0.75rem 2.5rem; }
  .nav-inner { padding: 0 0.75rem; gap: 0.6rem; }
  .palette-open span:first-child { display: none; }
  .summary-grid { grid-template-columns: 1fr 1fr; }
  .quote-price { font-size: 1.6rem; }
}
```

- [ ] **Step 4: Verify coverage and the test suite**

```bash
grep -oE '^[^ /@}][^{]*\{' src/webapp/static/app.css | sed 's/ *{$//' | tr ',' '\n' | sed 's/^ *//' | sort -u > /tmp/new-selectors.txt
comm -23 /tmp/old-selectors.txt /tmp/new-selectors.txt
```
Expected: empty output (every old selector still defined). Then run `pytest -q` — expected: all pass (CSS is not asserted by tests). Run `ruff check src/ diagnose.py tests/` and `mypy` — expected: clean (no Python touched beyond none).

- [ ] **Step 5: Visual smoke**

`python -m src.webapp` (or the repo's documented run command), open `http://127.0.0.1:8000/`, click through Home, Companies, a stock page, Screener, As-Of, Quality. Expected: slate theme everywhere, no unstyled/black-on-black areas, charts still render (their retheme comes in Task 6).

- [ ] **Step 6: Commit**

```bash
git add src/webapp/static/app.css src/webapp/templates/base.html
git commit -m "feat(webapp): Slate Pro design system — token-based app.css restyle"
```

---

### Task 2: Command palette (⌘K) — replace the fixed command bar

**Files:**
- Create: `src/webapp/static/palette.js`
- Delete: `src/webapp/static/terminal.js`, `src/webapp/templates/fragments/search_results.html`
- Modify: `src/webapp/templates/base.html` (full body rewrite), `src/webapp/static/app.css` (delete legacy blocks), `src/webapp/routes/pages.py` (remove `/ui/search`), `tests/test_web_smoke.py` (remove `/ui/search` tests, adjust any cmdbar assertions)

**Interfaces:**
- Consumes: `/api/companies/search?q=` → `[{ticker, company_name, sector_class}]`; CSS classes `.palette-*`, `.kbd` from Task 1.
- Produces: `window.Palette = { open(opts), close() }` where `opts` may be `{ mode: 'add', onPick: function(ticker) }` (used by Task 4's watchlist). localStorage keys: `stockdb.recent` (managed here until Task 4 moves helpers into `watchlist.js` — this task inlines a `recordRecent`/`getRecents` pair that Task 4 will reuse as-is).

- [ ] **Step 1: Rewrite `base.html` body**

Replace the `<body>` content (keep the `<head>` from Task 1) with:

```html
<body>
  <nav class="nav">
    <div class="nav-inner">
      <a class="nav-brand" href="/">Stock DB</a>
      <div class="nav-links">
        <a href="/" class="nav-link">Home</a>
        <a href="/companies" class="nav-link">Companies</a>
        <a href="/screener" class="nav-link">Screener</a>
        <a href="/asof" class="nav-link">As-Of</a>
        <a href="/quality" class="nav-link">Quality</a>
        {% if request.app.state.settings.allow_collection %}
        <a href="/collect" class="nav-link">Collect</a>
        {% endif %}
      </div>
      <button id="palette-open" class="palette-open" type="button">
        <span>Search or jump to…</span>
        <span class="kbd">Ctrl K</span>
      </button>
    </div>
  </nav>

  <main class="main">
    {% block content %}{% endblock %}
  </main>

  {% include "fragments/help.html" %}

  <div id="palette-overlay" class="palette-overlay" hidden>
    <div class="palette">
      <input id="palette-input" class="palette-input" type="text"
             placeholder="Search tickers, pages, functions…  (try: AAPL GP)"
             autocomplete="off" spellcheck="false" />
      <div id="palette-results" class="palette-results"></div>
      <div class="palette-foot">
        <span><span class="kbd">↑↓</span> navigate</span>
        <span><span class="kbd">Enter</span> open</span>
        <span><span class="kbd">Esc</span> close</span>
      </div>
    </div>
  </div>

  <script src="/static/app.js"></script>
  <script src="/static/palette.js"></script>
</body>
```

Also remove the `--cmdbar-h` usage: in `app.css` set `body { padding-top: 0; }` (drop the `padding-top: var(--cmdbar-h)` line), change `.nav { top: 0; }`, and delete the whole `/* ---- Command bar (LEGACY …) ---- */` block plus the `.search-wrap/.search-input/.search-dropdown/.search-item/.search-link/.search-list/.search-name/.search-ticker` rules and the `--cmdbar-h` token.

- [ ] **Step 2: Write `palette.js`**

```js
/**
 * Stock DB — palette.js
 * ⌘K command palette: fuzzy search over tickers/pages/function codes,
 * recent tickers, arrow-key navigation, `TICKER CODE` grammar.
 * Vanilla JS, no dependencies. Exposes window.Palette = { open, close }.
 */
(function () {
  'use strict';

  var overlay = document.getElementById('palette-overlay');
  var input = document.getElementById('palette-input');
  var resultsEl = document.getElementById('palette-results');
  var openBtn = document.getElementById('palette-open');
  var helpOverlay = document.getElementById('help-overlay');
  if (!overlay || !input || !resultsEl) return;

  var RKEY = 'stockdb.recent';
  function getRecents() {
    try { return JSON.parse(localStorage.getItem(RKEY)) || []; } catch (e) { return []; }
  }
  function recordRecent(t) {
    var l = getRecents().filter(function (x) { return x !== t; });
    l.unshift(t);
    localStorage.setItem(RKEY, JSON.stringify(l.slice(0, 8)));
  }

  // Function codes on /stocks/{ticker}
  var FUNCTIONS = [
    { label: 'Overview', code: 'DES', suffix: '' },
    { label: 'Chart', code: 'GP', suffix: '?tab=gp' },
    { label: 'Financials', code: 'FA', suffix: '?tab=fa' },
    { label: 'Earnings', code: 'ERN', suffix: '?tab=ern' },
    { label: 'Statistics', code: 'STAT', suffix: '?tab=stat' },
    { label: 'History', code: 'HP', suffix: '?tab=hp' },
    { label: 'Dividends', code: 'DVD', suffix: '?tab=dvd' },
    { label: 'Holders', code: 'HDS', suffix: '?tab=hds' },
    { label: 'Insiders', code: 'INS', suffix: '?tab=ins' },
  ];
  var CODE_ALIASES = { STATS: 'STAT', PRICES: 'HP', HISTORY: 'HP', CHART: 'GP', OVERVIEW: 'DES', FINANCIALS: 'FA', EARNINGS: 'ERN', STATISTICS: 'STAT', DIVIDENDS: 'DVD', HOLDERS: 'HDS', INSIDERS: 'INS' };
  var PAGES = [
    { label: 'Home', code: '', href: '/' },
    { label: 'Companies', code: '', href: '/companies' },
    { label: 'Screener', code: 'SCR', href: '/screener' },
    { label: 'As-Of explorer', code: 'ASOF', href: '/asof' },
    { label: 'Quality monitor', code: 'QM', href: '/quality' },
    { label: 'Collect', code: 'COL', href: '/collect' },
    { label: 'Help — keys & codes', code: 'HELP', href: '#help' },
  ];

  function resolveFunction(token) {
    var up = token.toUpperCase();
    var code = Object.prototype.hasOwnProperty.call(CODE_ALIASES, up) ? CODE_ALIASES[up] : up;
    for (var i = 0; i < FUNCTIONS.length; i++) {
      if (FUNCTIONS[i].code === code) return FUNCTIONS[i];
    }
    return null;
  }

  /** Subsequence fuzzy score: higher is better, -1 = no match. */
  function fuzzyScore(query, text) {
    var q = query.toLowerCase(), t = text.toLowerCase();
    if (!q) return 0;
    var qi = 0, score = 0, streak = 0;
    for (var ti = 0; ti < t.length && qi < q.length; ti++) {
      if (t[ti] === q[qi]) {
        streak += 1;
        score += streak + (ti === 0 || t[ti - 1] === ' ' ? 3 : 0);
        qi += 1;
      } else {
        streak = 0;
      }
    }
    return qi === q.length ? score : -1;
  }

  var items = [];      // [{type:'ticker'|'page'|'function', label, sub, code, href, ticker}]
  var selected = 0;
  var mode = null;     // null | {mode:'add', onPick:fn}
  var debounceTimer = null;
  var reqSeq = 0;

  function open(opts) {
    mode = opts && opts.mode === 'add' ? opts : null;
    overlay.hidden = false;
    input.value = '';
    input.placeholder = mode ? 'Add ticker to watchlist…' : 'Search tickers, pages, functions…  (try: AAPL GP)';
    build('');
    input.focus();
  }
  function close() {
    overlay.hidden = true;
    mode = null;
  }

  function render() {
    resultsEl.textContent = '';
    if (items.length === 0) {
      var empty = document.createElement('div');
      empty.className = 'palette-group';
      empty.textContent = 'No matches';
      resultsEl.appendChild(empty);
      return;
    }
    var lastGroup = null;
    items.forEach(function (item, i) {
      if (item.group && item.group !== lastGroup) {
        var g = document.createElement('div');
        g.className = 'palette-group';
        g.textContent = item.group;
        resultsEl.appendChild(g);
        lastGroup = item.group;
      }
      var row = document.createElement('div');
      row.className = 'palette-row' + (i === selected ? ' selected' : '');
      row.dataset.index = String(i);
      var t = document.createElement('span');
      t.className = 'palette-row-ticker';
      t.textContent = item.ticker || item.label;
      row.appendChild(t);
      var n = document.createElement('span');
      n.className = 'palette-row-name';
      n.textContent = item.sub || '';
      row.appendChild(n);
      if (item.code) {
        var c = document.createElement('span');
        c.className = 'palette-row-code';
        c.textContent = item.code;
        row.appendChild(c);
      }
      resultsEl.appendChild(row);
    });
  }

  function setItems(list) {
    items = list;
    selected = 0;
    render();
  }

  function build(query) {
    var tokens = query.trim().split(/\s+/).filter(Boolean);

    // "TICKER FN" — second token picks a workstation function
    if (!mode && tokens.length >= 2) {
      var tick = tokens[0].toUpperCase();
      var fq = tokens.slice(1).join(' ');
      var fns = FUNCTIONS.map(function (f) {
        var s = Math.max(fuzzyScore(fq, f.label), fuzzyScore(fq, f.code));
        return { f: f, s: s };
      }).filter(function (x) { return x.s >= 0; });
      var direct = resolveFunction(tokens[1]);
      if (direct) fns.unshift({ f: direct, s: 9999 });
      fns.sort(function (a, b) { return b.s - a.s; });
      var seen = {};
      setItems(fns.filter(function (x) {
        if (seen[x.f.code]) return false;
        seen[x.f.code] = true;
        return true;
      }).map(function (x) {
        return { type: 'function', ticker: tick, sub: x.f.label, code: x.f.code, href: '/stocks/' + encodeURIComponent(tick) + x.f.suffix, group: 'Functions' };
      }));
      return;
    }

    var staticRows = [];
    if (!mode) {
      PAGES.forEach(function (p) {
        var s = Math.max(fuzzyScore(query, p.label), p.code ? fuzzyScore(query, p.code) : -1);
        if (query === '' || s >= 0) staticRows.push({ type: 'page', label: p.label, sub: '', code: p.code, href: p.href, group: 'Pages', score: s });
      });
      staticRows.sort(function (a, b) { return b.score - a.score; });
    }

    if (tokens.length === 0) {
      var recentRows = getRecents().map(function (t) {
        return { type: 'ticker', ticker: t, sub: '', code: '', href: '/stocks/' + encodeURIComponent(t), group: 'Recent' };
      });
      setItems(recentRows.concat(mode ? [] : staticRows));
      return;
    }

    // One token: ticker/company search + pages
    var q = tokens[0];
    var seq = ++reqSeq;
    fetch('/api/companies/search?q=' + encodeURIComponent(q))
      .then(function (resp) { return resp.ok ? resp.json() : []; })
      .catch(function () { return []; })
      .then(function (hits) {
        if (seq !== reqSeq) return; // stale response
        var tickerRows = (hits || []).map(function (h) {
          return { type: 'ticker', ticker: h.ticker, sub: h.company_name || '', code: '', href: '/stocks/' + encodeURIComponent(h.ticker), group: 'Tickers' };
        });
        setItems(tickerRows.concat(mode ? [] : staticRows.filter(function (r) { return r.score >= 0; })));
      });
  }

  function execute(item) {
    if (!item) return;
    if (mode && mode.onPick && item.type === 'ticker') {
      var cb = mode.onPick;
      close();
      cb(item.ticker);
      return;
    }
    if (item.href === '#help') {
      close();
      if (helpOverlay) helpOverlay.hidden = false;
      return;
    }
    if (item.type === 'ticker' || item.type === 'function') recordRecent(item.ticker);
    close();
    window.location.href = item.href;
  }

  // ---- Events ----
  input.addEventListener('input', function () {
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(function () { build(input.value); }, 150);
  });
  input.addEventListener('keydown', function (evt) {
    if (evt.key === 'ArrowDown') { evt.preventDefault(); selected = Math.min(selected + 1, items.length - 1); render(); }
    else if (evt.key === 'ArrowUp') { evt.preventDefault(); selected = Math.max(selected - 1, 0); render(); }
    else if (evt.key === 'Enter') { evt.preventDefault(); execute(items[selected]); }
    else if (evt.key === 'Escape') { close(); }
  });
  resultsEl.addEventListener('click', function (evt) {
    var row = evt.target.closest ? evt.target.closest('.palette-row') : null;
    if (row) execute(items[Number(row.dataset.index)]);
  });
  overlay.addEventListener('click', function (evt) {
    if (evt.target === overlay) close();
  });
  if (openBtn) openBtn.addEventListener('click', function () { open(); });

  function isTypingTarget(el) {
    if (!el) return false;
    var tag = el.tagName ? el.tagName.toLowerCase() : '';
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }
  document.addEventListener('keydown', function (evt) {
    if ((evt.key === 'k' || evt.key === 'K') && (evt.ctrlKey || evt.metaKey)) {
      evt.preventDefault();
      if (overlay.hidden) open(); else close();
      return;
    }
    if ((evt.key === '/' || evt.key === '`') && !isTypingTarget(evt.target) && overlay.hidden) {
      evt.preventDefault();
      open();
    }
  });

  // Record direct /stocks/{ticker} visits as recents
  var m = window.location.pathname.match(/^\/stocks\/([^/]+)$/);
  if (m) recordRecent(decodeURIComponent(m[1]));

  // HELP overlay close wiring (was in terminal.js)
  var helpClose = document.getElementById('help-close');
  if (helpClose && helpOverlay) helpClose.addEventListener('click', function () { helpOverlay.hidden = true; });
  if (helpOverlay) {
    helpOverlay.addEventListener('click', function (evt) {
      if (evt.target === helpOverlay) helpOverlay.hidden = true;
    });
    document.addEventListener('keydown', function (evt) {
      if (evt.key === 'Escape' && !helpOverlay.hidden) helpOverlay.hidden = true;
    });
  }

  window.Palette = { open: open, close: close };
})();
```

- [ ] **Step 3: Delete `terminal.js` and the `/ui/search` route**

- `git rm src/webapp/static/terminal.js src/webapp/templates/fragments/search_results.html`
- In `src/webapp/routes/pages.py` delete the `search_fragment` route (the `@router.get("/ui/search", …)` block, currently lines 308–328) and any now-unused imports flagged by ruff.
- Check `src/webapp/templates/fragments/help.html` still makes sense: update its hint text from "` or / to focus the command bar" wording to "Ctrl K, / or ` opens the palette" (edit the `.help-hint` line; keep the code tables).

- [ ] **Step 4: Update tests**

In `tests/test_web_smoke.py`: delete tests hitting `/ui/search`; update any assertion on cmdbar markup (grep the file for `cmd` / `search`). Run:

```bash
pytest tests/test_web_smoke.py -q
```
Expected: PASS. Then full `pytest -q`, `ruff check src/ diagnose.py tests/`, `mypy` — all clean.

- [ ] **Step 5: Manual smoke**

Run the app. Verify: Ctrl+K, `/`, and `` ` `` open the palette; typing `AAA` lists the ticker; `AAA GP` offers Chart · GP; Enter navigates; Esc closes; recents appear on reopen; HELP row opens the help overlay; nav button works.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(webapp): Ctrl+K command palette replaces fixed command bar"
```

---

### Task 3: Batch summary endpoint — `GET /api/stocks/summary`

**Files:**
- Modify: `src/webapp/repository.py` (add `stock_summaries`), `src/webapp/schemas.py` (add `StockSummaryOut`), `src/webapp/routes/stocks_api.py` (add route)
- Test: `tests/test_web_api_stocks.py`

**Interfaces:**
- Consumes: existing `Reader.get_company`, `Reader.quote`; tables `price_bars`, `collection_runs`.
- Produces: `Reader.stock_summaries(tickers: List[str]) -> List[Dict[str, Any]]`; `GET /api/stocks/summary?tickers=A,B` → `[{ticker, company_name, price, change, change_pct, pe_trailing, quality_score, as_of, sparkline: [float]}]` — unknown tickers silently skipped, order follows the request, max 50 tickers honored. Task 4's `watchlist.js` consumes this exact shape.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_web_api_stocks.py`)

```python
# ---------------------------------------------------------------------------
# summary (watchlist batch)
# ---------------------------------------------------------------------------


def test_summary_returns_known_tickers_and_skips_unknown(client):
    resp = client.get("/api/stocks/summary?tickers=AAA,ZZZ")
    assert resp.status_code == 200
    body = resp.json()
    assert [row["ticker"] for row in body] == ["AAA"]
    row = body[0]
    # aaa2 snapshot: current_price=105.0, previous_close=100.0
    assert row["price"] == 105.0
    assert row["change"] == 5.0
    assert row["change_pct"] == 0.05
    assert 0 < len(row["sparkline"]) <= 63
    assert all(isinstance(v, float) for v in row["sparkline"])


def test_summary_normalizes_case_and_whitespace(client):
    resp = client.get("/api/stocks/summary?tickers=%20aaa%20")
    assert resp.status_code == 200
    assert [row["ticker"] for row in resp.json()] == ["AAA"]


def test_summary_empty_param_returns_empty_list(client):
    resp = client.get("/api/stocks/summary?tickers=,,")
    assert resp.status_code == 200
    assert resp.json() == []


def test_summary_includes_latest_quality_score(client, web_db):
    conn = sqlite3.connect(str(web_db))
    conn.execute(
        "INSERT INTO collection_runs (ticker, collected_at, quality_score) "
        "VALUES ('AAA', '2030-01-01T00:00:00', 88)"
    )
    conn.commit()
    conn.close()
    resp = client.get("/api/stocks/summary?tickers=AAA")
    assert resp.json()[0]["quality_score"] == 88
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/test_web_api_stocks.py -k summary -q
```
Expected: 4 failures — `/api/stocks/summary` currently matches the `/{ticker}/quote`-style routes and 404s (or 422s).

- [ ] **Step 3: Implement**

`src/webapp/schemas.py` — append:

```python
class StockSummaryOut(BaseModel):
    """One watchlist-card row from GET /api/stocks/summary."""

    ticker: str
    company_name: Optional[str] = None
    price: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    pe_trailing: Optional[float] = None
    quality_score: Optional[int] = None
    as_of: Optional[str] = None
    sparkline: List[float] = Field(default_factory=list)
```

(Ensure `from pydantic import BaseModel, Field` and `from typing import List, Optional` at the top — extend the existing imports.)

`src/webapp/repository.py` — append to `Reader` (after `profile`):

```python
    def stock_summaries(self, tickers: List[str]) -> List[Dict[str, Any]]:
        """Watchlist-card batch: quote + trailing-quarter sparkline + quality.

        Unknown tickers are skipped; output order follows input order. The
        sparkline is the last ~63 trading days of closes, ascending.
        """
        out: List[Dict[str, Any]] = []
        for ticker in tickers:
            company = self.get_company(ticker)
            if company is None:
                continue
            quote = self.quote(ticker) or {}
            cur = self._conn.execute(
                "SELECT close FROM ("
                "  SELECT date, close FROM price_bars WHERE ticker = ? "
                "  ORDER BY date DESC LIMIT 63"
                ") ORDER BY date ASC",
                (ticker,),
            )
            sparkline = [row["close"] for row in cur.fetchall() if row["close"] is not None]
            run = self._conn.execute(
                "SELECT quality_score FROM collection_runs WHERE ticker = ? "
                "ORDER BY collected_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
            out.append(
                {
                    "ticker": ticker,
                    "company_name": company.get("company_name"),
                    "price": quote.get("current_price"),
                    "change": quote.get("change"),
                    "change_pct": quote.get("change_pct"),
                    "pe_trailing": quote.get("pe_trailing"),
                    "quality_score": run["quality_score"] if run is not None else None,
                    "as_of": quote.get("collected_at"),
                    "sparkline": sparkline,
                }
            )
        return out
```

`src/webapp/routes/stocks_api.py` — add `StockSummaryOut` to the schemas import, and register **above** the first `/{ticker}/…` route (no path conflict, but keeps literal paths grouped):

```python
@router.get("/summary", response_model=List[StockSummaryOut])
def summary(tickers: str, r: Reader = Depends(get_reader)) -> List[Dict[str, Any]]:
    """Batch watchlist summary. ``tickers`` is comma-separated; unknown skipped."""
    wanted = [t.strip().upper() for t in tickers.split(",") if t.strip()][:50]
    return r.stock_summaries(wanted)
```

- [ ] **Step 4: Run to verify pass**

```bash
pytest tests/test_web_api_stocks.py -q && ruff check src/ diagnose.py tests/ && mypy
```
Expected: all PASS/clean.

- [ ] **Step 5: Commit**

```bash
git add src/webapp/repository.py src/webapp/schemas.py src/webapp/routes/stocks_api.py tests/test_web_api_stocks.py
git commit -m "feat(webapp): batch /api/stocks/summary endpoint for watchlist cards"
```

---

### Task 4: Analysis-first home — watchlist cards + `watchlist.js`

**Files:**
- Create: `src/webapp/static/watchlist.js`
- Modify: `src/webapp/templates/index.html` (full rewrite), `src/webapp/routes/pages.py` (`home()` slims), `src/webapp/templates/base.html` (add script tag), `tests/test_web_smoke.py` (home assertions)

**Interfaces:**
- Consumes: `GET /api/stocks/summary` (Task 3 shape); `window.Palette.open({mode:'add', onPick})` (Task 2); CSS `.wl-*`, `.pill`, `.spark`, `.ops-strip`, `.empty-state` (Task 1).
- Produces: `window.Watchlist = { list(), has(t), add(t), remove(t), toggle(t) }` (localStorage `stockdb.watchlist`); global `renderWatchlistHome()`; auto-init of `#watch-star` buttons (consumed by Task 5).

- [ ] **Step 1: Write `watchlist.js`**

```js
/**
 * Stock DB — watchlist.js
 * localStorage watchlist + recents rendering for the home page, and the
 * star-toggle on stock pages. Consumes GET /api/stocks/summary.
 */
(function () {
  'use strict';

  var KEY = 'stockdb.watchlist';
  var RKEY = 'stockdb.recent';

  function read(key) {
    try { return JSON.parse(localStorage.getItem(key)) || []; } catch (e) { return []; }
  }
  function write(key, list) { localStorage.setItem(key, JSON.stringify(list)); }

  window.Watchlist = {
    list: function () { return read(KEY); },
    has: function (t) { return read(KEY).indexOf(t) !== -1; },
    add: function (t) { var l = read(KEY); if (l.indexOf(t) === -1) { l.push(t); write(KEY, l); } },
    remove: function (t) { write(KEY, read(KEY).filter(function (x) { return x !== t; })); },
    toggle: function (t) { if (this.has(t)) { this.remove(t); return false; } this.add(t); return true; },
  };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtPct(p) {
    if (p === null || p === undefined) return '—';
    return (p >= 0 ? '+' : '') + (p * 100).toFixed(2) + '%';
  }
  function fmtNum(v, digits) {
    if (v === null || v === undefined) return '—';
    return Number(v).toFixed(digits === undefined ? 2 : digits);
  }

  function sparklineSVG(values, stroke) {
    if (!values || values.length < 2) return '<div class="spark"></div>';
    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    var span = max - min || 1;
    var pts = values.map(function (v, i) {
      var x = (i / (values.length - 1)) * 100;
      var y = 28 - ((v - min) / span) * 24;
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');
    return '<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none">'
      + '<polyline points="' + pts + '" fill="none" stroke="' + stroke + '" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>';
  }

  function card(row, removable) {
    var hasPct = row.change_pct !== null && row.change_pct !== undefined;
    var dirClass = hasPct ? (row.change_pct >= 0 ? 'up' : 'down') : 'flat';
    var stroke = dirClass === 'down' ? 'var(--color-down)'
      : dirClass === 'up' ? 'var(--color-up-text)' : 'var(--color-ink-muted)';
    var q = row.quality_score === null || row.quality_score === undefined ? '—' : row.quality_score;
    return '<a class="wl-card" href="/stocks/' + encodeURIComponent(row.ticker) + '">'
      + (removable ? '<button class="wl-remove" type="button" data-ticker="' + esc(row.ticker) + '" title="Remove from watchlist">✕</button>' : '')
      + '<div class="wl-top"><span class="wl-ticker mono">' + esc(row.ticker) + '</span>'
      + '<span class="pill ' + dirClass + '">' + fmtPct(row.change_pct) + '</span></div>'
      + '<div class="wl-name">' + esc(row.company_name) + '</div>'
      + '<div class="wl-price mono">' + fmtNum(row.price) + '</div>'
      + sparklineSVG(row.sparkline, stroke)
      + '<div class="wl-meta mono">P/E ' + fmtNum(row.pe_trailing, 1) + ' · Q ' + q + '</div>'
      + '</a>';
  }

  function fetchSummaries(tickers) {
    if (!tickers.length) return Promise.resolve([]);
    return fetch('/api/stocks/summary?tickers=' + encodeURIComponent(tickers.join(',')))
      .then(function (resp) { return resp.ok ? resp.json() : []; })
      .catch(function () { return []; });
  }

  window.renderWatchlistHome = function () {
    var grid = document.getElementById('watchlist-grid');
    var recentRow = document.getElementById('recent-row');
    if (!grid) return;

    function drawWatchlist() {
      var tickers = window.Watchlist.list();
      if (tickers.length === 0) {
        grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1">'
          + 'No tickers yet — press <span class="kbd">Ctrl K</span> or '
          + '<a href="#" id="wl-empty-add">add one</a> to start your watchlist.</div>';
        var link = document.getElementById('wl-empty-add');
        if (link) link.addEventListener('click', function (evt) { evt.preventDefault(); openAdd(); });
        return;
      }
      grid.innerHTML = '<div class="skeleton skeleton--bar"></div>';
      fetchSummaries(tickers).then(function (rows) {
        var known = {};
        rows.forEach(function (r) { known[r.ticker] = true; });
        // prune tickers the DB no longer knows
        tickers.filter(function (t) { return !known[t]; }).forEach(window.Watchlist.remove.bind(window.Watchlist));
        grid.innerHTML = rows.map(function (r) { return card(r, true); }).join('')
          + '<div class="wl-add-tile" id="wl-add-tile">+ Add ticker</div>';
        var tile = document.getElementById('wl-add-tile');
        if (tile) tile.addEventListener('click', openAdd);
        grid.querySelectorAll('.wl-remove').forEach(function (btn) {
          btn.addEventListener('click', function (evt) {
            evt.preventDefault();
            evt.stopPropagation();
            window.Watchlist.remove(btn.dataset.ticker);
            drawWatchlist();
          });
        });
      });
    }

    function openAdd() {
      if (window.Palette) {
        window.Palette.open({ mode: 'add', onPick: function (t) { window.Watchlist.add(t); drawWatchlist(); } });
      }
    }

    var addBtn = document.getElementById('wl-add');
    if (addBtn) addBtn.addEventListener('click', openAdd);

    drawWatchlist();

    if (recentRow) {
      var recents;
      try { recents = JSON.parse(localStorage.getItem(RKEY)) || []; } catch (e) { recents = []; }
      if (recents.length === 0) {
        recentRow.innerHTML = '<p class="muted">Pages you visit show up here.</p>';
      } else {
        fetchSummaries(recents.slice(0, 6)).then(function (rows) {
          recentRow.innerHTML = rows.map(function (r) { return card(r, false); }).join('')
            || '<p class="muted">Pages you visit show up here.</p>';
        });
      }
    }
  };

  // Star toggle on /stocks/{ticker} (markup added in Task 5)
  var star = document.getElementById('watch-star');
  if (star) {
    var t = star.dataset.ticker;
    function paint(on) {
      star.textContent = on ? '★' : '☆';
      star.classList.toggle('starred', on);
      star.title = on ? 'Remove from watchlist' : 'Add to watchlist';
    }
    paint(window.Watchlist.has(t));
    star.addEventListener('click', function () { paint(window.Watchlist.toggle(t)); });
  }
})();
```

- [ ] **Step 2: Add the script to `base.html`**

Insert `<script src="/static/watchlist.js"></script>` between the `app.js` and `palette.js` script tags.

- [ ] **Step 3: Rewrite `index.html`**

```html
{% extends "base.html" %}
{% block title %}Home — Stock DB{% endblock %}

{% block content %}
<div class="page-header">
  <div>
    <h1 class="page-title">Watchlist</h1>
    <p class="page-subtitle">Standardised, comparable financials across all sectors.</p>
  </div>
  <button class="btn btn-primary btn-sm" type="button" id="wl-add">+ Add ticker</button>
</div>

<div id="watchlist-grid" class="wl-grid"></div>

<section class="section">
  <h2 class="section-heading">Recently viewed</h2>
  <div id="recent-row" class="wl-grid wl-grid--small"></div>
</section>

{% if by_sector %}
<section class="section">
  <h2 class="section-heading">Sector coverage</h2>
  <div class="stat-row">
    {% for sec in by_sector %}
    <a class="stat-card" href="/companies?sector={{ sec.sector_class }}">
      <span class="stat-value">{{ sec.n_companies }}</span>
      <span class="stat-label">{{ sec.sector_class }}</span>
      {% if sec.median_quality is not none %}
      <span class="muted mono" style="font-size:0.72rem;">quality {{ sec.median_quality | int }}</span>
      {% endif %}
    </a>
    {% endfor %}
  </div>
</section>
{% endif %}

<div class="ops-strip">
  <span class="mono">● {{ company_count }} companies</span>
  {% if freshness %}
  <span class="mono">{{ freshness.table_counts.get("collection_runs", 0) }} collection runs</span>
  {% if freshness.latest_company_update %}
  <span class="mono">last updated {{ freshness.latest_company_update[:10] }}</span>
  {% endif %}
  {% endif %}
  <a href="/quality">Data quality →</a>
</div>

<script>renderWatchlistHome();</script>
{% endblock %}
```

(One inline `style` for the tiny quality caption is acceptable; if ruff-of-conscience objects, add a `.stat-note` class instead.)

- [ ] **Step 4: Slim the `home()` route**

In `src/webapp/routes/pages.py`, `home()` now only needs:

```python
@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    r: Reader = Depends(get_reader),
) -> Any:
    """Home: watchlist (client-rendered) + sector coverage + ops strip."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "company_count": r.count_companies(),
            "freshness": r.data_freshness(),
            "by_sector": r.coverage_by_sector(),
        },
    )
```

Remove the now-unused `latest_runs`/`unmapped_top`/fill-chart code and any imports ruff flags (`json`, `fmt_pct` if unused elsewhere in the module — check first). The Quality page already renders latest runs, unmapped tags, and the full fill-rate table, so nothing is lost.

- [ ] **Step 5: Update home tests, run gates**

`grep -n "def test" tests/test_web_smoke.py` — fix home-page tests that asserted the removed dashboard sections (collection-runs table, unmapped tags, fill-rate chart). The home body now contains `Watchlist`, `Sector coverage`, and the ops strip. Run:

```bash
pytest -q && ruff check src/ diagnose.py tests/ && mypy
```
Expected: all pass.

- [ ] **Step 6: Manual smoke**

Home shows the empty-state; Ctrl+K add-mode adds AAPL-equivalents from your local DB; cards render price/Δ%/sparkline/P-E/quality; remove ✕ works; recently-viewed populates after visiting a stock page; sector cards link to filtered companies list.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(webapp): analysis-first home with localStorage watchlist cards"
```

---

### Task 5: Stock workstation — friendly+code tabs, star, skeletons, pill header

**Files:**
- Modify: `src/webapp/routes/workstation.py` (TABS shape), `src/webapp/templates/stock.html` (rewrite), `src/webapp/templates/fragments/des.html` (pill + empty-state), `tests/test_web_workstation.py` / `tests/test_web_smoke.py` (only if assertions break)

**Interfaces:**
- Consumes: `.tab-code`, `.star-btn`, `.skeleton-*`, `.pill` CSS (Task 1); star auto-init in `watchlist.js` (Task 4).
- Produces: `TABS: List[Tuple[str, str, str]]` — `(label, code, key)`; template iterates `{% for label, code, key in tabs %}`.

- [ ] **Step 1: Update `TABS` in `workstation.py`**

```python
TABS: List[Tuple[str, str, str]] = [
    ("Overview", "DES", "des"),
    ("Chart", "GP", "gp"),
    ("Financials", "FA", "fa"),
    ("Earnings", "ERN", "ern"),
    ("Statistics", "STAT", "stat"),
    ("History", "HP", "hp"),
    ("Dividends", "DVD", "dvd"),
    ("Holders", "HDS", "hds"),
    ("Insiders", "INS", "ins"),
]
_TAB_KEYS = frozenset(key for _, _, key in TABS)
```

- [ ] **Step 2: Rewrite `stock.html`**

```html
{% extends "base.html" %}
{% block title %}{{ company.ticker }} — Stock DB{% endblock %}

{% block content %}
<div class="company-header">
  <div class="company-title-row">
    <h1>{{ company.company_name or company.ticker }}</h1>
    <span class="ticker-badge">{{ company.ticker }}</span>
    <button id="watch-star" class="star-btn" type="button" data-ticker="{{ ticker }}" aria-label="Toggle watchlist">☆</button>
  </div>
  <div class="company-meta">
    {% if company.sector_class %}<span class="meta-item">{{ company.sector_class }}</span>{% endif %}
    {% if company.sector %}<span class="meta-sep">·</span><span class="meta-item">{{ company.sector }}</span>{% endif %}
    {% if company.industry %}<span class="meta-sep">·</span><span class="meta-item">{{ company.industry }}</span>{% endif %}
  </div>
</div>

<div class="tab-bar" role="tablist">
  {% for label, code, key in tabs %}
  <button
    class="tab-btn{% if key == active_tab %} tab-active{% endif %}"
    hx-get="/ui/stocks/{{ ticker }}/{{ key }}"
    hx-target="#panel"
    hx-indicator="#panel-skeleton"
    hx-trigger="{% if key == active_tab %}click, load{% else %}click{% endif %}"
    role="tab"
  >{{ label }} <span class="tab-code">{{ code }}</span></button>
  {% endfor %}
</div>

<div id="panel-skeleton" class="skeleton-stack" aria-hidden="true">
  <div class="skeleton skeleton--bar"></div>
  <div class="skeleton skeleton--bar w60"></div>
  <div class="skeleton skeleton--block"></div>
</div>
<div id="panel" class="panel"></div>
{% endblock %}
```

Note: the star's dynamic behavior comes from `watchlist.js` (Task 4) which runs on every page via `base.html`. Note the `>FA<` substring older tests assert still exists inside `<span class="tab-code">FA</span>`.

- [ ] **Step 3: Pill the quote change + empty state in `des.html`**

In `src/webapp/templates/fragments/des.html`:
- Replace `<span class="quote-change {{ change_class }}">{{ change_fmt }} ({{ change_pct_fmt }})</span>` with `<span class="pill {{ change_class }} quote-change">{{ change_fmt }} ({{ change_pct_fmt }})</span>` — the CSS maps both `num-pos/num-neg` and `up/down` variants, so whatever `change_class` the route emits is covered.
- If the fragment has a bare "no data" paragraph for missing quotes, wrap it as `<div class="empty-state">No quote collected yet — press REFRESH or run a collection.</div>`. Apply the same `empty-state` treatment to the other fragments' "No … available/collected" paragraphs (`grep -rn "No .*available\|No .*collected" src/webapp/templates/fragments/`).

- [ ] **Step 4: Run gates, fix assertions**

```bash
pytest tests/test_web_workstation.py tests/test_web_smoke.py -q
```
`test_web_workstation.py:49` iterates the code labels DES/GP/… — still present via `.tab-code` spans. Fix anything else that referenced the 2-tuple `tabs` shape (`grep -rn "for label, key" src/webapp/templates/`). Then full `pytest -q`, `ruff check src/ diagnose.py tests/`, `mypy`.

- [ ] **Step 5: Manual smoke**

Stock page: star toggles and persists (check home watchlist gains the ticker); tabs show "Overview · DES" style; switching tabs shows the shimmer skeleton; DES change renders as tinted pill.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(webapp): workstation friendly+code tabs, watchlist star, panel skeletons"
```

---

### Task 6: Plotly Slate retheme — `app.js`

**Files:**
- Modify: `src/webapp/static/app.js`

**Interfaces:**
- Produces: `SLATE` color object + `slateLayout(overrides)` + `slateAxis()` helpers used by every chart renderer. No function signatures change — templates keep calling `renderPlot/renderBar/renderGP/renderERN/renderDVD` as today.

- [ ] **Step 1: Add helpers at the top of `app.js`** (below the header comment)

```js
/** Slate Pro chart theme — single source of chart colors (mirrors app.css tokens). */
var SLATE = {
  grid: '#21262d', ink: '#e6edf3', muted: '#8b949e',
  accent: '#388bfd', accentFill: 'rgba(31, 111, 235, 0.12)',
  up: '#3fb950', down: '#f85149', warn: '#d29922', purple: '#a371f7', pink: '#f778ba',
  fontUI: 'Inter, "Segoe UI", system-ui, sans-serif',
  fontMono: '"JetBrains Mono", "Cascadia Mono", Consolas, monospace',
};

function slateAxis(extra) {
  var ax = {
    gridcolor: SLATE.grid, linecolor: SLATE.grid, zerolinecolor: '#30363d',
    tickfont: { family: SLATE.fontMono, size: 11, color: SLATE.muted },
  };
  return Object.assign(ax, extra || {});
}

function slateLayout(overrides) {
  var base = {
    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
    font: { family: SLATE.fontUI, color: SLATE.ink, size: 12 },
    hoverlabel: { bgcolor: '#1c2128', bordercolor: '#30363d', font: { family: SLATE.fontMono, size: 11, color: SLATE.ink } },
    colorway: [SLATE.accent, SLATE.warn, SLATE.purple, SLATE.pink, SLATE.up, SLATE.down],
    margin: { t: 24, r: 24, b: 40, l: 56 },
    showlegend: false,
  };
  return Object.assign(base, overrides || {});
}
```

- [ ] **Step 2: Apply exact substitutions through the file**

| Location | Old | New |
|---|---|---|
| `renderPlot` trace | `line: { color: '#ff9900', width: 2.5 }, marker: { color: '#ff9900', size: 6 }` | `line: { color: SLATE.accent, width: 2 }, marker: { color: SLATE.accent, size: 5 }, fill: 'tozeroy', fillcolor: SLATE.accentFill` |
| `renderPlot` layout | whole literal | `slateLayout({ title: { text: title, font: { size: 13, color: SLATE.muted, family: SLATE.fontUI } }, margin: { t: 40, r: 24, b: 40, l: 60 }, xaxis: slateAxis({ tickformat: 'd' }), yaxis: slateAxis({ tickformat: '.0%' }) })` |
| `renderBar` trace | `marker: { color: '#ff9900', opacity: 0.85 }` | `marker: { color: SLATE.accent, opacity: 0.9 }` |
| `renderBar` layout | whole literal | `slateLayout({ title: { text: title, font: { size: 13, color: SLATE.muted, family: SLATE.fontUI } }, margin: { t: 36, r: 80, b: 36, l: 200 }, xaxis: slateAxis({ range: [0, 1.05], tickformat: '.0%' }), yaxis: slateAxis({ autorange: 'reversed' }) })` |
| `_gpBaseLayout` | whole function body | `return slateLayout({ margin: { t: 10, r: 24, b: 30, l: 55 }, legend: { orientation: 'h', font: { size: 10, color: SLATE.muted } }, showlegend: true, hovermode: 'x unified' });` |
| GP candle | `increasing: { line: { color: '#00e676' } }, decreasing: { line: { color: '#ff5252' } }` | `increasing: { line: { color: SLATE.up } }, decreasing: { line: { color: SLATE.down } }` |
| GP line close | `line: { color: '#e6e3dc', width: 1.5 }` | `line: { color: SLATE.accent, width: 1.75 }` |
| GP MA50 | `line: { color: '#ff9900', width: 1.25 }` | `line: { color: SLATE.warn, width: 1.25 }` |
| GP MA200 | `line: { color: '#4fc3f7', width: 1.25 }` | `line: { color: SLATE.purple, width: 1.25 }` |
| GP RSI | `line: { color: '#ff9900', width: 1.25 }` | `line: { color: SLATE.warn, width: 1.25 }` |
| GP MACD line | `line: { color: '#e6e3dc', width: 1.25 }` | `line: { color: SLATE.accent, width: 1.25 }` |
| GP MACD signal | `line: { color: '#ff9900', width: 1.25 }` | `line: { color: SLATE.warn, width: 1.25 }` |
| GP MACD hist | `marker: { color: '#4fc3f7' }` | `marker: { color: 'rgba(139, 148, 158, 0.5)' }` |
| every axis literal `gridcolor: '#2a2a2a', linecolor: '#2a2a2a'` | | replace the enclosing axis object with `slateAxis({ …other keys kept… })` |
| `renderERN` traces | Estimate `'#4fc3f7'` / Actual `'#ff9900'` | Estimate `'#6e7681'` / Actual `SLATE.accent` |
| `renderERN`/`renderDVD` layouts | font/axis literals | rebuild via `slateLayout({ barmode: 'group', margin: {…kept…}, legend: {…kept…}, showlegend: true/false, xaxis: slateAxis(), yaxis: slateAxis() })` |
| `renderDVD` bar | `'#ff9900'` | `SLATE.accent` |
| "No data" strings | `'<p style="color:#8a8a8a;font-size:.875rem">No data available.</p>'` | `'<p class="muted">No data available.</p>'` |

After the sweep: `grep -n "ff9900\|2a2a2a\|e6e3dc\|00e676\|ff5252\|4fc3f7\|IBM Plex" src/webapp/static/app.js` — expected: no matches.

- [ ] **Step 3: Verify + manual smoke**

`pytest -q` (templates unaffected; expected pass). Run the app; check: metric charts (FA tab / metric-chart fragment) draw blue with soft area fill; GP line/candle/indicators/compare all render in slate colors; unified hover shows mono numbers on dark tooltip; ERN and DVD bars recolored; home/quality fill-rate bar chart (if visited via Quality) blue.

- [ ] **Step 4: Commit**

```bash
git add src/webapp/static/app.js
git commit -m "feat(webapp): slate Plotly theme via shared slateLayout helpers"
```

---

### Task 7: Screener — filter-chips builder

**Files:**
- Create: `src/webapp/static/screener.js`
- Modify: `src/webapp/templates/screener.html` (full rewrite), `src/webapp/routes/pages.py` (`screener_page` adds `metric_json`), `src/webapp/templates/fragments/screener_results.html` (alert()→inline note, minor classes)

**Interfaces:**
- Consumes: existing `GET /ui/screen` params (`sector`, `{field}_{op}={value}`, `sort`, `sort_dir` — unchanged server-side); CSS `.filter-chip`, `.chip-add`, `.popover`, `.empty-state` (Task 1).
- Produces: URL-synced screener state (`history.replaceState`), saved screens in localStorage `stockdb.screens` (`[{name, qs}]`).

- [ ] **Step 1: Pass the metric catalog as JSON**

In `pages.py` `screener_page`, add to the context (the route already receives `metric_options` and `snapshot_options` as `(label, key)` pairs — reuse those exact variables):

```python
    metric_json = json.dumps(
        [{"key": key, "label": label, "group": "Metrics"} for label, key in metric_options]
        + [{"key": key, "label": label, "group": "Market / Valuation"} for label, key in snapshot_options]
    )
```

and include `"metric_json": metric_json` in the `TemplateResponse` context (ensure `import json` exists in the module).

- [ ] **Step 2: Rewrite `screener.html`**

```html
{% extends "base.html" %}
{% block title %}Screener — Stock DB{% endblock %}
{% block content %}
<div class="page-header">
  <div>
    <h1 class="page-title">Screener</h1>
    <p class="page-subtitle">Filter companies by fundamental metric thresholds.</p>
  </div>
  <div class="form-row">
    <button class="btn btn-sm" type="button" id="scr-save">Save screen</button>
    <button class="btn btn-sm" type="button" id="scr-copy">Copy link</button>
  </div>
</div>

<div class="card mb-3">
  <div class="form-row" id="chip-bar">
    <select id="sector-select" class="form-control form-control-sm">
      <option value="">All sectors</option>
    {% for s in sectors %}
      <option value="{{ s }}">{{ s }}</option>
    {% endfor %}
    </select>
    <span id="chips"></span>
    <span style="position:relative;">
      <button class="chip-add" type="button" id="add-filter">+ Add filter</button>
      <div class="popover" id="filter-popover" hidden>
        <input type="text" class="form-control form-control-sm" id="popover-search"
               placeholder="Search metrics…" autocomplete="off" style="width:100%;" />
        <div class="popover-list" id="popover-list"></div>
        <div class="form-row mt-2" id="popover-editor" hidden>
          <strong id="popover-metric-label" style="font-size:0.8rem;"></strong>
          <select id="popover-op" class="form-control form-control-sm" style="width:64px;">
            <option value="gte">≥</option><option value="lte">≤</option>
            <option value="gt">&gt;</option><option value="lt">&lt;</option>
            <option value="eq">=</option><option value="ne">≠</option>
          </select>
          <input type="number" step="any" id="popover-value" class="form-control form-control-sm"
                 style="width:110px;" placeholder="value" />
          <button class="btn btn-primary btn-sm" type="button" id="popover-apply">Add</button>
        </div>
      </div>
    </span>
  </div>
  <div class="form-row mt-2" id="saved-screens"></div>
  <div id="scr-note" class="muted mt-2" style="font-size:0.8rem;"></div>
</div>

<div id="screen-results"></div>

<script type="application/json" id="screener-metrics">{{ metric_json | safe }}</script>
<script src="/static/screener.js"></script>
{% endblock %}
```

- [ ] **Step 3: Write `screener.js`**

```js
/**
 * Stock DB — screener.js
 * Chip-based filter builder over the existing GET /ui/screen contract:
 * sector, {field}_{op}=value, sort, sort_dir. State lives in the URL
 * (history.replaceState) and saved screens in localStorage.
 */
(function () {
  'use strict';

  var metricsEl = document.getElementById('screener-metrics');
  var chipsEl = document.getElementById('chips');
  if (!metricsEl || !chipsEl) return;

  var METRICS = JSON.parse(metricsEl.textContent);
  var BY_KEY = {};
  METRICS.forEach(function (m) { BY_KEY[m.key] = m; });
  var OPS = { gte: '≥', lte: '≤', gt: '>', lt: '<', eq: '=', ne: '≠' };
  var OP_KEYS = Object.keys(OPS);
  var SKEY = 'stockdb.screens';

  var sectorSel = document.getElementById('sector-select');
  var popover = document.getElementById('filter-popover');
  var popSearch = document.getElementById('popover-search');
  var popList = document.getElementById('popover-list');
  var popEditor = document.getElementById('popover-editor');
  var popLabel = document.getElementById('popover-metric-label');
  var popOp = document.getElementById('popover-op');
  var popValue = document.getElementById('popover-value');
  var note = document.getElementById('scr-note');

  var state = { sector: '', filters: [], sort: '', sort_dir: 'desc' };
  var pendingKey = null;
  var debounceTimer = null;

  // ---- URL <-> state ----
  function parseQS(qs) {
    var s = { sector: '', filters: [], sort: '', sort_dir: 'desc' };
    new URLSearchParams(qs).forEach(function (value, key) {
      if (key === 'sector') { s.sector = value; return; }
      if (key === 'sort') { s.sort = value; return; }
      if (key === 'sort_dir') { s.sort_dir = value; return; }
      for (var i = 0; i < OP_KEYS.length; i++) {
        var suffix = '_' + OP_KEYS[i];
        if (key.length > suffix.length && key.slice(-suffix.length) === suffix) {
          var field = key.slice(0, -suffix.length);
          if (BY_KEY[field]) s.filters.push({ field: field, op: OP_KEYS[i], value: value });
          return;
        }
      }
    });
    return s;
  }
  function buildQS() {
    var p = new URLSearchParams();
    if (state.sector) p.set('sector', state.sector);
    state.filters.forEach(function (f) { p.set(f.field + '_' + f.op, f.value); });
    if (state.sort) { p.set('sort', state.sort); p.set('sort_dir', state.sort_dir); }
    return p.toString();
  }

  // ---- Rendering ----
  function renderChips() {
    chipsEl.textContent = '';
    state.filters.forEach(function (f, i) {
      var m = BY_KEY[f.field];
      var chip = document.createElement('span');
      chip.className = 'filter-chip';
      chip.appendChild(document.createTextNode((m ? m.label : f.field) + ' ' + OPS[f.op] + ' ' + f.value));
      var x = document.createElement('button');
      x.className = 'filter-chip-x';
      x.type = 'button';
      x.textContent = '✕';
      x.title = 'Remove filter';
      x.addEventListener('click', function () {
        state.filters.splice(i, 1);
        sync();
      });
      chip.appendChild(x);
      chipsEl.appendChild(chip);
    });
  }

  function renderSaved() {
    var wrap = document.getElementById('saved-screens');
    if (!wrap) return;
    var screens;
    try { screens = JSON.parse(localStorage.getItem(SKEY)) || []; } catch (e) { screens = []; }
    wrap.textContent = '';
    if (screens.length === 0) return;
    var label = document.createElement('span');
    label.className = 'muted';
    label.style.fontSize = '0.75rem';
    label.textContent = 'Saved:';
    wrap.appendChild(label);
    screens.forEach(function (sc, i) {
      var chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = sc.name;
      chip.title = 'Load this screen';
      chip.addEventListener('click', function () {
        state = parseQS(sc.qs);
        sectorSel.value = state.sector;
        sync();
      });
      var x = document.createElement('button');
      x.className = 'filter-chip-x';
      x.type = 'button';
      x.textContent = '✕';
      x.addEventListener('click', function (evt) {
        evt.stopPropagation();
        screens.splice(i, 1);
        localStorage.setItem(SKEY, JSON.stringify(screens));
        renderSaved();
      });
      chip.appendChild(x);
      wrap.appendChild(chip);
    });
  }

  // ---- Run ----
  function run() {
    var qs = buildQS();
    window.history.replaceState(null, '', '/screener' + (qs ? '?' + qs : ''));
    htmx.ajax('GET', '/ui/screen' + (qs ? '?' + qs : ''), { target: '#screen-results', swap: 'innerHTML' });
  }
  function sync() {
    renderChips();
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(run, 400);
  }

  // Column-header sort links inside the results fragment re-request /ui/screen
  // themselves; capture their sort so chip edits keep it, and sync the URL.
  document.body.addEventListener('htmx:afterSettle', function (evt) {
    if (evt.detail.target && evt.detail.target.id === 'screen-results' && evt.detail.pathInfo) {
      var path = evt.detail.pathInfo.requestPath || '';
      var qi = path.indexOf('?');
      if (qi !== -1) {
        var s = parseQS(path.slice(qi + 1));
        state.sort = s.sort;
        state.sort_dir = s.sort_dir;
        window.history.replaceState(null, '', '/screener?' + path.slice(qi + 1));
      }
    }
  });

  // ---- Popover ----
  function renderPopList(query) {
    popList.textContent = '';
    var lastGroup = null;
    METRICS.forEach(function (m) {
      if (query && m.label.toLowerCase().indexOf(query.toLowerCase()) === -1) return;
      if (m.group !== lastGroup) {
        var g = document.createElement('div');
        g.className = 'popover-group';
        g.textContent = m.group;
        popList.appendChild(g);
        lastGroup = m.group;
      }
      var item = document.createElement('div');
      item.className = 'popover-item';
      item.textContent = m.label;
      item.addEventListener('click', function () {
        pendingKey = m.key;
        popLabel.textContent = m.label;
        popEditor.hidden = false;
        popValue.focus();
      });
      popList.appendChild(item);
    });
  }
  document.getElementById('add-filter').addEventListener('click', function () {
    popover.hidden = !popover.hidden;
    if (!popover.hidden) {
      popEditor.hidden = true;
      pendingKey = null;
      popSearch.value = '';
      renderPopList('');
      popSearch.focus();
    }
  });
  popSearch.addEventListener('input', function () { renderPopList(popSearch.value); });
  document.getElementById('popover-apply').addEventListener('click', function () {
    if (!pendingKey || popValue.value === '') return;
    state.filters.push({ field: pendingKey, op: popOp.value, value: popValue.value });
    popover.hidden = true;
    popValue.value = '';
    sync();
  });
  document.addEventListener('click', function (evt) {
    if (!popover.hidden && !popover.contains(evt.target) && evt.target.id !== 'add-filter') {
      popover.hidden = true;
    }
  });

  // ---- Sector / save / copy ----
  sectorSel.addEventListener('change', function () {
    state.sector = sectorSel.value;
    sync();
  });
  document.getElementById('scr-save').addEventListener('click', function () {
    var qs = buildQS();
    if (!qs) { note.textContent = 'Add at least one filter before saving.'; return; }
    var name = '';
    var existing = state.filters.map(function (f) {
      var m = BY_KEY[f.field];
      return (m ? m.label : f.field) + OPS[f.op] + f.value;
    });
    name = (state.sector ? state.sector + ' · ' : '') + existing.join(', ');
    var screens;
    try { screens = JSON.parse(localStorage.getItem(SKEY)) || []; } catch (e) { screens = []; }
    screens.push({ name: name.slice(0, 60), qs: qs });
    localStorage.setItem(SKEY, JSON.stringify(screens));
    renderSaved();
    note.textContent = 'Saved.';
  });
  document.getElementById('scr-copy').addEventListener('click', function () {
    var url = window.location.origin + '/screener' + (buildQS() ? '?' + buildQS() : '');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { note.textContent = 'Link copied.'; });
    } else {
      note.textContent = url;
    }
  });

  // ---- Init from URL ----
  state = parseQS(window.location.search);
  sectorSel.value = state.sector;
  renderChips();
  renderSaved();
  if (window.location.search) run();
})();
```

- [ ] **Step 4: De-`alert()` the results fragment**

In `fragments/screener_results.html`, replace `alert('Select at least one company to compare.'); return;` inside the compare button's `onclick` with:
`document.getElementById('scr-note').textContent = 'Select at least one company to compare.'; return;`
Also replace the bare `<p class="text-muted">No companies matched your filters.</p>` with `<div class="empty-state">No companies matched your filters.</div>`, and delete the inline `style="cursor:pointer;text-decoration:none;color:inherit"` on the sort links (add a `.data-table` friendly rule instead: sort links already inherit `a` colors — add `class="sort-link"` and in `app.css` `.sort-link { color: inherit; }`).

- [ ] **Step 5: Run gates + manual smoke**

```bash
pytest tests/test_web_screener.py tests/test_web_smoke.py -q && pytest -q && ruff check src/ diagnose.py tests/ && mypy
```
Expected: pass — the server contract is untouched. Manual: add/remove chips re-runs automatically; sector select filters; sort headers work and survive chip edits; URL updates live and a pasted URL reproduces the screen; Save/Load/Copy work; compare-selected note appears without `alert()`.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(webapp): chip-based screener with URL sync and saved screens"
```

---

### Task 8: Remaining pages sweep, inline-style purge, final verification

**Files:**
- Modify: `src/webapp/templates/companies.html`, `asof.html`, `quality.html`, `collect.html`, `error.html`, remaining `fragments/*.html` (class/empty-state sweep only), `src/webapp/static/app.css` (only if a sweep reveals a missing rule)

- [ ] **Step 1: Inline-style purge**

```bash
grep -rn 'style="' src/webapp/templates/
```
For each hit, replace with an existing utility/component class: text sizing → `.muted` + component defaults; spacing → `.mb-2/.mt-2/.ml-2`; width hints on selects/inputs → leave only where functionally required (popover editor widths from Task 7 may stay); colored text → `.up/.down/.muted`. Chart-height wrappers (`style="height:320px;"`) may stay — they are layout, not theme. Expected end state: remaining inline styles are only dimensions.

- [ ] **Step 2: Empty states + `htmx-indicator` sweep**

```bash
grep -rn "Loading…\|Loading\.\.\." src/webapp/templates/
```
Replace text-only indicators with skeleton spans, e.g. the screener's `<span id="screen-spinner" class="htmx-indicator">Loading…</span>` pattern → `<span id="…" class="htmx-indicator skeleton skeleton--bar" style="width:120px;display:inline-block;"></span>` (or drop where Task 7 already removed the Run button flow). Confirm every fragment's no-data message uses `.empty-state` (Task 5 started this; finish the sweep).

- [ ] **Step 3: Per-page visual pass**

Run the app and visit: `/companies` (+ a `?sector=` filter), `/asof` (run a query + vintages), `/quality` (+ sector filter), `/collect` (page render; job form), a 404 (`/stocks/NOPE`), `/screener`, `/`, `/stocks/{ticker}` all 9 tabs. Fix anything unstyled by adding the missing rule to `app.css` using tokens only. Check at ~800px width: nav usable, cards stack, tables scroll horizontally.

- [ ] **Step 4: Legacy leftovers check**

```bash
grep -rn "ff9900\|0a0a0a\|141414\|e6e3dc\|IBM Plex\|cmdbar" src/webapp/ --include="*.css" --include="*.js" --include="*.html"
```
Expected: no matches.

- [ ] **Step 5: Full gates**

```bash
pytest -q && ruff check src/ diagnose.py tests/ && mypy
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(webapp): finish Slate Pro sweep — pages, empty states, responsive"
```

---

## Final verification (after all tasks)

1. `pytest -q && ruff check src/ diagnose.py tests/ && mypy` — clean.
2. Live smoke against the real DB (project convention): start the app on the real `data/` store, click through every page and all workstation tabs with a well-covered ticker (e.g. AAPL) and a sparse one; exercise palette (`Ctrl K`, `TICKER GP`, aliases), watchlist add/remove/star, screener chips + saved screens + copied URL in a fresh tab, quote REFRESH.
3. Use superpowers:requesting-code-review, then superpowers:finishing-a-development-branch (PR to `main`).

## Spec-coverage self-check (done at plan time)

- Design system/tokens/typography/motion → Task 1. Command palette + grammar + recents + nav → Task 2. Summary endpoint → Task 3. Watchlist home + localStorage + ops strip + empty state → Task 4 (unmapped-tags/collection-runs already on Quality — verified, `quality_page` renders both). Tabs friendly+code, star, skeletons, quote pill → Task 5. Plotly retheme + decision note (keep Plotly) → Task 6. Screener chips/auto-run/sortable/CSV/copy-link/saved → Task 7 (sortable headers + CSV already existed server-side). Remaining pages, responsive, states → Tasks 1 + 8. Dark-only, no schema change, routes preserved (minus `/ui/search`) → Global Constraints.

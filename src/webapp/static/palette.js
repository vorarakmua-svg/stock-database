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
    try { localStorage.setItem(RKEY, JSON.stringify(l.slice(0, 8))); } catch (e) { /* storage unavailable */ }
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
    reqSeq++;
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
    var seq = ++reqSeq;
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
  if (m && document.getElementById('watch-star')) recordRecent(decodeURIComponent(m[1]));

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

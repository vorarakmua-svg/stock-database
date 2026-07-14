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
  var verdictSel = document.getElementById('verdict-select');
  var oeVerdictSel = document.getElementById('oe-verdict-select');
  var popover = document.getElementById('filter-popover');
  var popSearch = document.getElementById('popover-search');
  var popList = document.getElementById('popover-list');
  var popEditor = document.getElementById('popover-editor');
  var popLabel = document.getElementById('popover-metric-label');
  var popOp = document.getElementById('popover-op');
  var popValue = document.getElementById('popover-value');
  var note = document.getElementById('scr-note');

  var state = { sector: '', verdict: '', oeVerdict: '', filters: [], sort: '', sort_dir: 'desc' };
  var pendingKey = null;
  var debounceTimer = null;

  // ---- URL <-> state ----
  function parseQS(qs) {
    var s = { sector: '', verdict: '', oeVerdict: '', filters: [], sort: '', sort_dir: 'desc' };
    new URLSearchParams(qs).forEach(function (value, key) {
      if (key === 'sector') { s.sector = value; return; }
      if (key === 'verdict') { s.verdict = value; return; }
      if (key === 'oe_verdict') { s.oeVerdict = value; return; }
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
    if (state.verdict) p.set('verdict', state.verdict);
    if (state.oeVerdict) p.set('oe_verdict', state.oeVerdict);
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
        verdictSel.value = state.verdict;
        oeVerdictSel.value = state.oeVerdict;
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
    if (evt.detail.target && evt.detail.target.id === 'screen-results' && evt.detail.pathInfo
        && (evt.detail.pathInfo.requestPath || '').indexOf('/ui/screen') === 0) {
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

  // ---- Sector / verdict / save / copy ----
  sectorSel.addEventListener('change', function () {
    state.sector = sectorSel.value;
    sync();
  });
  verdictSel.addEventListener('change', function () {
    state.verdict = verdictSel.value;
    sync();
  });
  oeVerdictSel.addEventListener('change', function () {
    state.oeVerdict = oeVerdictSel.value;
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
    var prefix = [state.sector, state.verdict, state.oeVerdict].filter(Boolean).join(' · ');
    name = (prefix ? prefix + ' · ' : '') + existing.join(', ');
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
  verdictSel.value = state.verdict;
  oeVerdictSel.value = state.oeVerdict;
  renderChips();
  renderSaved();
  if (window.location.search) {
    // This script executes at parse time, but htmx is loaded with `defer`
    // (runs before DOMContentLoaded) — delay the auto-run until it exists.
    document.addEventListener('DOMContentLoaded', function () { run(); });
  }
})();

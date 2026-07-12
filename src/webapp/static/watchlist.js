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
  function write(key, list) { try { localStorage.setItem(key, JSON.stringify(list)); } catch (e) { /* storage unavailable */ } }

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
    var q = row.quality_score === null || row.quality_score === undefined ? '—' : String(Math.round(Number(row.quality_score)));
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

  // Resolves [] for an empty ticker list; resolves null on any failure
  // (non-OK response or network error) so callers can tell "no data"
  // apart from "fetch failed" and avoid destructive pruning.
  function fetchSummaries(tickers) {
    if (!tickers.length) return Promise.resolve([]);
    return fetch('/api/stocks/summary?tickers=' + encodeURIComponent(tickers.join(',')))
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .catch(function () { return null; });
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
        if (rows === null) {
          // Fetch failed — show an error state; never prune on failure.
          grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1">'
            + 'Could not load watchlist data — <a href="#" id="wl-retry">retry</a>.</div>';
          var retry = document.getElementById('wl-retry');
          if (retry) retry.addEventListener('click', function (evt) { evt.preventDefault(); drawWatchlist(); });
          return;
        }
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
          recentRow.innerHTML = (rows || []).map(function (r) { return card(r, false); }).join('')
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

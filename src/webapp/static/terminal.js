/**
 * Stock DB — terminal.js
 * Keyboard command bar: `TICKER CODE` grammar, ticker autocomplete, HELP overlay.
 * Vanilla JS, no dependencies. Workstation routes (/stocks/{ticker}) arrive in a
 * later task — navigating there may 404 for now; that is expected.
 */
(function () {
  'use strict';

  var cmd = document.getElementById('cmd');
  var suggestBox = document.getElementById('cmd-suggest');
  var helpOverlay = document.getElementById('help-overlay');
  var helpClose = document.getElementById('help-close');
  if (!cmd) return;

  // CODE -> query-string suffix, applied to /stocks/{ticker}
  var TICKER_CODES = {
    DES: '',
    GP: '?tab=gp',
    FA: '?tab=fa',
    ERN: '?tab=ern',
    STAT: '?tab=stat',
    HP: '?tab=hp',
    DVD: '?tab=dvd',
    HDS: '?tab=hds',
    INS: '?tab=ins',
  };

  // Global codes — no ticker required.
  var GLOBAL_CODES = {
    SCR: '/screener',
    ASOF: '/asof',
    QM: '/quality',
    COL: '/collect',
  };

  var debounceTimer = null;
  var suggestions = [];

  function isTypingTarget(el) {
    if (!el) return false;
    var tag = el.tagName ? el.tagName.toLowerCase() : '';
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }

  function showHelp() {
    if (helpOverlay) helpOverlay.hidden = false;
  }

  function hideHelp() {
    if (helpOverlay) helpOverlay.hidden = true;
  }

  function toggleHelp() {
    if (!helpOverlay) return;
    if (helpOverlay.hidden) showHelp();
    else hideHelp();
  }

  function clearSuggestions() {
    suggestions = [];
    if (suggestBox) suggestBox.innerHTML = '';
  }

  function renderSuggestions(hits) {
    if (!suggestBox) return;
    suggestions = hits || [];
    suggestBox.textContent = '';
    if (suggestions.length === 0) {
      return;
    }
    for (var i = 0; i < suggestions.length; i++) {
      var hit = suggestions[i];
      var row = document.createElement('div');
      row.className = 'cmdbar-suggest-item';
      row.dataset.ticker = hit.ticker;
      var tickerSpan = document.createElement('span');
      tickerSpan.className = 'cmdbar-suggest-ticker';
      tickerSpan.textContent = hit.ticker;
      row.appendChild(tickerSpan);
      var nameSpan = document.createElement('span');
      nameSpan.className = 'cmdbar-suggest-name';
      nameSpan.textContent = hit.company_name ? ' — ' + hit.company_name : '';
      row.appendChild(nameSpan);
      suggestBox.appendChild(row);
    }
  }

  function fetchSuggestions(query) {
    if (!query) {
      clearSuggestions();
      return;
    }
    fetch('/api/companies/search?q=' + encodeURIComponent(query))
      .then(function (resp) {
        return resp.ok ? resp.json() : [];
      })
      .then(renderSuggestions)
      .catch(function () {
        clearSuggestions();
      });
  }

  function selectSuggestion(ticker) {
    cmd.value = ticker + ' ';
    clearSuggestions();
    cmd.focus();
  }

  function navigate(url) {
    window.location.href = url;
  }

  function runCommand(raw) {
    var tokens = raw.trim().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return;

    if (tokens.length === 1) {
      var only = tokens[0].toUpperCase();
      if (only === 'HELP') {
        toggleHelp();
        return;
      }
      if (Object.prototype.hasOwnProperty.call(GLOBAL_CODES, only)) {
        navigate(GLOBAL_CODES[only]);
        return;
      }
      // Not a recognised global code — treat the single token as a ticker (DES).
      navigate('/stocks/' + only);
      return;
    }

    // Two-or-more tokens: TICKER CODE (extra tokens ignored).
    var ticker = tokens[0].toUpperCase();
    var code = tokens[1].toUpperCase();
    var suffix = Object.prototype.hasOwnProperty.call(TICKER_CODES, code) ? TICKER_CODES[code] : '';
    navigate('/stocks/' + ticker + suffix);
  }

  // ---- Global keydown: `/` or backtick focuses the command bar ----
  document.addEventListener('keydown', function (evt) {
    if ((evt.key === '`' || evt.key === '/') && !isTypingTarget(evt.target)) {
      evt.preventDefault();
      cmd.focus();
      cmd.select();
      return;
    }
    if (evt.key === 'Escape') {
      if (helpOverlay && !helpOverlay.hidden) {
        hideHelp();
      }
      if (document.activeElement === cmd) {
        cmd.value = '';
        clearSuggestions();
        cmd.blur();
      }
    }
  });

  // ---- Command bar input: debounced ticker autocomplete ----
  cmd.addEventListener('input', function () {
    var firstToken = cmd.value.trim().split(/\s+/)[0] || '';
    window.clearTimeout(debounceTimer);
    debounceTimer = window.setTimeout(function () {
      fetchSuggestions(firstToken);
    }, 200);
  });

  cmd.addEventListener('keydown', function (evt) {
    if (evt.key === 'Enter') {
      evt.preventDefault();
      runCommand(cmd.value);
    }
  });

  cmd.addEventListener('blur', function () {
    window.setTimeout(function () {
      clearSuggestions();
    }, 150);
  });

  // ---- Suggestion dropdown: click to select ----
  if (suggestBox) {
    suggestBox.addEventListener('click', function (evt) {
      var item = evt.target.closest ? evt.target.closest('.cmdbar-suggest-item') : null;
      if (item && item.dataset.ticker) {
        selectSuggestion(item.dataset.ticker);
      }
    });
  }

  document.addEventListener('click', function (evt) {
    if (evt.target !== cmd && !(suggestBox && suggestBox.contains(evt.target))) {
      clearSuggestions();
    }
  });

  // ---- HELP overlay close button ----
  if (helpClose) {
    helpClose.addEventListener('click', hideHelp);
  }
  if (helpOverlay) {
    helpOverlay.addEventListener('click', function (evt) {
      if (evt.target === helpOverlay) hideHelp();
    });
  }
})();

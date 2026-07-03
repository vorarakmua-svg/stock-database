/**
 * Stock DB — app.js
 * Minimal client-side behaviour: Plotly chart renderer + HTMX tab-active helper.
 */

/**
 * Render a Plotly line chart into the element with the given id.
 * @param {string} elId - The DOM element id to render into.
 * @param {Array<{fiscal_year: number, value: number|null}>} series - Data points.
 * @param {string} title - Chart title (used as y-axis label / legend).
 */
function renderPlot(elId, series, title) {
  if (!series || series.length === 0) {
    const el = document.getElementById(elId);
    if (el) el.innerHTML = '<p style="color:#8a8a8a;font-size:.875rem">No data available.</p>';
    return;
  }

  const x = series.map(function(p) { return p.fiscal_year; });
  const y = series.map(function(p) { return p.value; });

  var trace = {
    x: x,
    y: y,
    type: 'scatter',
    mode: 'lines+markers',
    name: title,
    line: { color: '#ff9900', width: 2.5 },
    marker: { color: '#ff9900', size: 6 },
    hovertemplate: '%{x}: %{y:.2%}<extra></extra>',
  };

  var layout = {
    title: { text: title, font: { size: 14, color: '#e6e3dc', family: '"IBM Plex Mono", "Cascadia Mono", Consolas, monospace' } },
    margin: { t: 40, r: 24, b: 40, l: 60 },
    xaxis: {
      tickformat: 'd',
      gridcolor: '#2a2a2a',
      linecolor: '#2a2a2a',
    },
    yaxis: {
      tickformat: '.0%',
      gridcolor: '#2a2a2a',
      linecolor: '#2a2a2a',
    },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    font: { family: '"IBM Plex Mono", "Cascadia Mono", Consolas, monospace', color: '#e6e3dc' },
    showlegend: false,
  };

  var config = {
    responsive: true,
    displayModeBar: false,
  };

  if (typeof Plotly !== 'undefined') {
    Plotly.newPlot(elId, [trace], layout, config);
  }
}

/**
 * Render a Plotly horizontal bar chart of field fill rates.
 * @param {string} elId - The DOM element id to render into.
 * @param {string[]} labels - Field names (y-axis, top-to-bottom).
 * @param {number[]} values - Fill fractions 0–1 (x-axis).
 * @param {string[]} pct - Pre-formatted percentage strings for hover text.
 * @param {string} title - Chart title.
 */
function renderBar(elId, labels, values, pct, title) {
  var el = document.getElementById(elId);
  if (!el) return;
  if (!labels || labels.length === 0) {
    el.innerHTML = '<p style="color:#8a8a8a;font-size:.875rem">No data available.</p>';
    return;
  }
  var trace = {
    type: 'bar',
    orientation: 'h',
    x: values,
    y: labels,
    text: pct,
    textposition: 'outside',
    marker: { color: '#ff9900', opacity: 0.85 },
    hovertemplate: '%{y}: %{text}<extra></extra>',
  };
  var layout = {
    title: { text: title, font: { size: 13, color: '#e6e3dc', family: '"IBM Plex Mono", "Cascadia Mono", Consolas, monospace' } },
    margin: { t: 36, r: 80, b: 36, l: 200 },
    xaxis: { range: [0, 1.05], tickformat: '.0%', gridcolor: '#2a2a2a', linecolor: '#2a2a2a' },
    yaxis: { autorange: 'reversed', gridcolor: '#2a2a2a', linecolor: '#2a2a2a' },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    font: { family: '"IBM Plex Mono", "Cascadia Mono", Consolas, monospace', color: '#e6e3dc', size: 12 },
    showlegend: false,
  };
  var config = { responsive: true, displayModeBar: false };
  if (typeof Plotly !== 'undefined') {
    Plotly.newPlot(elId, [trace], layout, config);
  }
}

/**
 * GP panel — dark Plotly layout shared by the price figure and the compare figure.
 */
function _gpBaseLayout() {
  return {
    margin: { t: 10, r: 24, b: 30, l: 55 },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    font: { family: '"IBM Plex Mono", "Cascadia Mono", Consolas, monospace', color: '#e6e3dc', size: 11 },
    legend: { orientation: 'h', font: { size: 10 } },
    showlegend: true,
  };
}

/**
 * Compute stacked-subplot y-axis domains for the GP chart.
 * @param {number} extraCount - number of indicator subplots below the main price row (0-2: RSI, MACD).
 * @returns {{main: number[], extras: number[][]}} domain [bottom, top] pairs, main first then top-to-bottom extras.
 */
function _gpComputeDomains(extraCount) {
  if (extraCount === 0) return { main: [0, 1], extras: [] };
  var gap = 0.05;
  var mainHeight = extraCount === 2 ? 0.5 : 0.65;
  var extraHeight = (1 - mainHeight - gap * extraCount) / extraCount;
  var mainBottom = 1 - mainHeight;
  var extras = [];
  var top = mainBottom - gap;
  for (var i = 0; i < extraCount; i++) {
    var bottom = Math.max(top - extraHeight, 0);
    extras.push([bottom, top]);
    top = bottom - gap;
  }
  return { main: [mainBottom, 1], extras: extras };
}

/**
 * Render the main price figure (line or candlestick) with optional MA overlays
 * and RSI/MACD stacked subplots, into el.id via Plotly.
 */
function _gpRenderPriceFigure(el, bars, bundle, chartType, indicators) {
  if (!bars || bars.length === 0) {
    el.innerHTML = '<p class="muted">No price data available.</p>';
    return;
  }
  var dates = bars.map(function (b) { return b.date; });
  var wantsMA50 = indicators.indexOf('ma50') !== -1;
  var wantsMA200 = indicators.indexOf('ma200') !== -1;
  var wantsRSI = indicators.indexOf('rsi') !== -1;
  var wantsMACD = indicators.indexOf('macd') !== -1;

  var traces = [];
  if (chartType === 'candle') {
    traces.push({
      type: 'candlestick', x: dates,
      open: bars.map(function (b) { return b.open; }),
      high: bars.map(function (b) { return b.high; }),
      low: bars.map(function (b) { return b.low; }),
      close: bars.map(function (b) { return b.close; }),
      increasing: { line: { color: '#00e676' } },
      decreasing: { line: { color: '#ff5252' } },
      name: 'Price', xaxis: 'x', yaxis: 'y',
    });
  } else {
    traces.push({
      type: 'scatter', mode: 'lines', x: dates,
      y: bars.map(function (b) { return b.close; }),
      line: { color: '#e6e3dc', width: 1.5 },
      name: 'Close', xaxis: 'x', yaxis: 'y',
    });
  }

  if (bundle && wantsMA50) {
    traces.push({ type: 'scatter', mode: 'lines', x: bundle.dates, y: bundle.ma_50, line: { color: '#ff9900', width: 1.25 }, name: 'MA50', xaxis: 'x', yaxis: 'y' });
  }
  if (bundle && wantsMA200) {
    traces.push({ type: 'scatter', mode: 'lines', x: bundle.dates, y: bundle.ma_200, line: { color: '#4fc3f7', width: 1.25 }, name: 'MA200', xaxis: 'x', yaxis: 'y' });
  }

  var extraPanels = [];
  if (wantsRSI) extraPanels.push('rsi');
  if (wantsMACD) extraPanels.push('macd');
  var domains = _gpComputeDomains(extraPanels.length);

  var layout = _gpBaseLayout();
  layout.xaxis = {
    domain: [0, 1], gridcolor: '#2a2a2a', linecolor: '#2a2a2a',
    rangeslider: { visible: false }, showticklabels: extraPanels.length === 0,
  };
  layout.yaxis = { domain: domains.main, gridcolor: '#2a2a2a', linecolor: '#2a2a2a' };

  extraPanels.forEach(function (kind, i) {
    var n = i + 2;
    var isLast = i === extraPanels.length - 1;
    layout['xaxis' + n] = {
      domain: [0, 1], matches: 'x', gridcolor: '#2a2a2a', linecolor: '#2a2a2a',
      showticklabels: isLast,
    };
    layout['yaxis' + n] = { domain: domains.extras[i], gridcolor: '#2a2a2a', linecolor: '#2a2a2a' };

    if (kind === 'rsi' && bundle) {
      layout['yaxis' + n].range = [0, 100];
      traces.push({ type: 'scatter', mode: 'lines', x: bundle.dates, y: bundle.rsi, line: { color: '#ff9900', width: 1.25 }, name: 'RSI', xaxis: 'x' + n, yaxis: 'y' + n });
    } else if (kind === 'macd' && bundle) {
      traces.push({ type: 'scatter', mode: 'lines', x: bundle.dates, y: bundle.macd.macd, line: { color: '#e6e3dc', width: 1.25 }, name: 'MACD', xaxis: 'x' + n, yaxis: 'y' + n });
      traces.push({ type: 'scatter', mode: 'lines', x: bundle.dates, y: bundle.macd.signal, line: { color: '#ff9900', width: 1.25 }, name: 'Signal', xaxis: 'x' + n, yaxis: 'y' + n });
      traces.push({ type: 'bar', x: bundle.dates, y: bundle.macd.hist, marker: { color: '#4fc3f7' }, name: 'Hist', xaxis: 'x' + n, yaxis: 'y' + n });
    }
  });

  Plotly.newPlot(el.id, traces, layout, { responsive: true, displayModeBar: false });
}

/**
 * Render the compare figure: normalized (%) close series for the primary
 * ticker plus every comparison ticker (including a benchmark like ^GSPC),
 * as line traces on one shared axis.
 */
function _gpRenderCompareFigure(el, compareData, ticker, compareList) {
  var series = (compareData && compareData.series) || {};
  var keys = [ticker].concat(compareList);
  var traces = [];
  keys.forEach(function (key) {
    var s = series[key];
    if (!s) return;
    traces.push({
      type: 'scatter', mode: 'lines', x: s.dates,
      y: s.pct.map(function (p) { return p === null ? null : p * 100; }),
      name: key,
    });
  });
  if (traces.length === 0) {
    el.innerHTML = '<p class="muted">No comparison data available.</p>';
    return;
  }
  var layout = _gpBaseLayout();
  layout.xaxis = { gridcolor: '#2a2a2a', linecolor: '#2a2a2a' };
  layout.yaxis = { ticksuffix: '%', gridcolor: '#2a2a2a', linecolor: '#2a2a2a' };
  Plotly.newPlot(el.id, traces, layout, { responsive: true, displayModeBar: false });
}

/**
 * Render the GP (price chart) panel into #gp-chart.
 * @param {{ticker: string, range: string, chartType: string, indicators: string[], compare: string[]}} cfg
 *
 * Fetches from the existing /api/stocks/{ticker} JSON endpoints (bars always;
 * indicators only if an MA/RSI/MACD checkbox is on; compare-bars only if a
 * comparison is selected) and builds ONE Plotly figure. Stateless: every call
 * re-derives the figure from cfg — no client-side chart state is kept between
 * range/type/indicator/compare changes (the server re-renders this fragment
 * with a fresh inline call instead).
 */
function renderGP(cfg) {
  var el = document.getElementById('gp-chart');
  if (!el || typeof Plotly === 'undefined') return;

  var ticker = cfg.ticker;
  var range = cfg.range || '1Y';
  var chartType = cfg.chartType || 'line';
  var indicators = cfg.indicators || [];
  var compare = cfg.compare || [];
  var needsIndicators = indicators.length > 0;
  var needsCompare = compare.length > 0;

  var barsPromise = fetch('/api/stocks/' + encodeURIComponent(ticker) + '/bars?range=' + encodeURIComponent(range))
    .then(function (resp) { return resp.ok ? resp.json() : []; })
    .catch(function () { return []; });

  var indicatorsPromise = needsIndicators
    ? fetch('/api/stocks/' + encodeURIComponent(ticker) + '/indicators?range=' + encodeURIComponent(range))
        .then(function (resp) { return resp.ok ? resp.json() : null; })
        .catch(function () { return null; })
    : Promise.resolve(null);

  var comparePromise = needsCompare
    ? fetch('/api/stocks/' + encodeURIComponent(ticker) + '/compare-bars?others=' + encodeURIComponent(compare.join(',')) + '&range=' + encodeURIComponent(range))
        .then(function (resp) { return resp.ok ? resp.json() : null; })
        .catch(function () { return null; })
    : Promise.resolve(null);

  Promise.all([barsPromise, indicatorsPromise, comparePromise]).then(function (results) {
    var bars = results[0];
    var bundle = results[1];
    var compareData = results[2];

    if (needsCompare && compareData) {
      _gpRenderCompareFigure(el, compareData, ticker, compare);
    } else {
      _gpRenderPriceFigure(el, bars, bundle, chartType, indicators);
    }
  });
}

/**
 * HTMX tab-active helper.
 * When a tab button triggers an HTMX request, mark it active and remove
 * active from its siblings.
 */
document.addEventListener('htmx:beforeRequest', function(evt) {
  var trigger = evt.detail && evt.detail.elt;
  if (trigger && trigger.classList && trigger.classList.contains('tab-btn')) {
    var bar = trigger.closest('.tab-bar');
    if (bar) {
      bar.querySelectorAll('.tab-btn').forEach(function(btn) {
        btn.classList.remove('tab-active');
      });
      trigger.classList.add('tab-active');
    }
  }
});

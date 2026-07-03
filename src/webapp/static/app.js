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

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
    if (el) el.innerHTML = '<p style="color:#637381;font-size:.875rem">No data available.</p>';
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
    line: { color: '#4f46e5', width: 2.5 },
    marker: { color: '#4f46e5', size: 6 },
    hovertemplate: '%{x}: %{y:.2%}<extra></extra>',
  };

  var layout = {
    title: { text: title, font: { size: 14, color: '#1f2933', family: '-apple-system, Segoe UI, Roboto, sans-serif' } },
    margin: { t: 40, r: 24, b: 40, l: 60 },
    xaxis: {
      tickformat: 'd',
      gridcolor: '#e5e7eb',
      linecolor: '#e5e7eb',
    },
    yaxis: {
      tickformat: '.0%',
      gridcolor: '#e5e7eb',
      linecolor: '#e5e7eb',
    },
    paper_bgcolor: '#ffffff',
    plot_bgcolor: '#ffffff',
    font: { family: '-apple-system, Segoe UI, Roboto, sans-serif', color: '#1f2933' },
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

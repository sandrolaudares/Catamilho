/* timeseries.js — grafico NDVI observado vs curva de referencia (Chart.js) */
let chart = null;

function renderChart(series, cls) {
  const ctx = document.getElementById('ndvi-chart');
  const labels = series.map(s => s.date);
  const obs = series.map(s => s.ndvi);
  const ref = window.REF_CURVES && window.REF_CURVES.milho_safrinha
    ? window.REF_CURVES.milho_safrinha.mensal : null;
  const refData = ref ? series.map(s => ref[+s.date.slice(5, 7) - 1]) : [];

  const datasets = [{
    label: 'NDVI observado (Sentinel-2)',
    data: obs, borderColor: '#4ade80', backgroundColor: '#4ade80',
    pointRadius: 3, pointHoverRadius: 5, tension: 0.25, spanGaps: true,
  }];
  if (ref) datasets.push({
    label: 'Referência soja + milho safrinha',
    data: refData, borderColor: '#fbbf24', borderDash: [6, 4],
    pointRadius: 0, tension: 0.3,
  });

  if (chart) chart.destroy();
  chart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: { min: 0, max: 1, title: { display: true, text: 'NDVI', color: '#9db8a4' },
             ticks: { color: '#9db8a4' }, grid: { color: '#2c4633' } },
        x: { ticks: { color: '#9db8a4', maxTicksLimit: 14 },
             grid: { color: '#1d3022' } },
      },
      plugins: { legend: { labels: { color: '#e7f0e8', boxWidth: 14 } } },
    },
  });
  state.chart = chart;
}

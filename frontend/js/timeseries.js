/* timeseries.js — grafico NDVI observado + suavizado + curva de referencia */
let chart = null;

function renderChart(series, cls, smoothed) {
  const ctx = document.getElementById('ndvi-chart');
  const labels = series.map(s => s.date);
  const obs = series.map(s => s.ndvi);
  const ref = window.REF_CURVES && window.REF_CURVES.milho_safrinha
    ? window.REF_CURVES.milho_safrinha.mensal : null;
  const refData = ref ? series.map(s => ref[+s.date.slice(5, 7) - 1]) : [];

  const datasets = [{
    label: 'NDVI observado (Sentinel-2)',
    data: obs, borderColor: '#4ade80', backgroundColor: '#4ade80',
    pointRadius: 3, pointHoverRadius: 5, tension: 0.15, spanGaps: true,
  }];
  if (ref) datasets.push({
    label: 'Referência (safrinha)',
    data: refData, borderColor: '#fbbf24', borderDash: [6, 4],
    pointRadius: 0, tension: 0.3,
  });

  // curva suavizada Savitzky-Golay (2o dataset com eixo x proprio)
  let allLabels = labels.slice();
  if (smoothed && smoothed.dates && smoothed.ndvi_smooth) {
    // eixo x compartilhado: junta datas e ordena
    const set = new Set(labels);
    smoothed.dates.forEach(d => set.add(d));
    allLabels = Array.from(set).sort();
    // reamostra observado e suavizado nesse eixo
    const obsMap = Object.fromEntries(series.map(s => [s.date, s.ndvi]));
    const smoMap = Object.fromEntries(smoothed.dates.map((d, i) => [d, smoothed.ndvi_smooth[i]]));
    datasets[0].data = allLabels.map(d => obsMap[d] ?? null);
    if (ref) datasets[1].data = allLabels.map(d => ref[+d.slice(5, 7) - 1]);
    datasets.push({
      label: 'Suavizado (Savitzky-Golay)',
      data: allLabels.map(d => smoMap[d] ?? null),
      borderColor: '#60a5fa', backgroundColor: 'transparent',
      pointRadius: 0, borderWidth: 2, tension: 0.25, spanGaps: false,
    });
  }

  if (chart) chart.destroy();
  chart = new Chart(ctx, {
    type: 'line',
    data: { labels: allLabels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        y: { min: 0, max: 1, title: { display: true, text: 'NDVI', color: '#9db8a4' },
             ticks: { color: '#9db8a4' }, grid: { color: '#2c4633' } },
        x: { ticks: { color: '#9db8a4', maxTicksLimit: 14 }, grid: { color: '#1d3022' } },
      },
      plugins: { legend: { labels: { color: '#e7f0e8', boxWidth: 14 } } },
    },
  });
  state.chart = chart;
}

function renderDTW(dtwRes) {
  const block = document.getElementById('dtw-block');
  if (!dtwRes || !dtwRes.ok) { block.hidden = true; return; }
  block.hidden = false;
  const tb = document.querySelector('#dtw-table tbody');
  tb.innerHTML = '';
  dtwRes.ranking.forEach((r, i) => {
    const tr = document.createElement('tr');
    if (i === 0) tr.style.background = '#1d3022';
    tr.innerHTML =
      `<td>${r.rotulo}</td>` +
      `<td>${(r.similaridade * 100).toFixed(1)}%</td>` +
      `<td>${r.distancia.toFixed(3)}</td>` +
      `<td>${r.offset_meses >= 0 ? '+' : ''}${r.offset_meses.toFixed(1)} m</td>`;
    tb.appendChild(tr);
  });
}

function renderMapBiomas(mb, confronto) {
  const block = document.getElementById('mb-block');
  if (!mb) { block.hidden = true; return; }
  block.hidden = false;
  const sum = document.getElementById('mb-summary');
  const tb = document.querySelector('#mb-table tbody');
  tb.innerHTML = '';
  if (!mb.ok) {
    sum.textContent = 'MapBiomas indisponível: ' + (mb.motivo || 'erro') +
      '. Você pode passar uma URL do raster via `mapbiomas_url` na chamada.';
    return;
  }
  const pctDom = (mb.fracao_dominante * 100).toFixed(1);
  const conc = confronto && confronto.comparavel
    ? (confronto.concorda ? '✔ concorda com o classificador' : '✖ diverge do classificador')
    : '';
  sum.textContent = `Dominante: ${mb.classe_dominante_nome} (${pctDom}%) · ` +
    `${mb.pixels_totais} px MapBiomas (30 m). ${conc}`;
  Object.values(mb.composicao)
    .sort((a, b) => b.fracao - a.fracao)
    .forEach(c => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${c.nome}</td><td>${(c.fracao * 100).toFixed(1)}%</td>`;
      tb.appendChild(tr);
    });
}

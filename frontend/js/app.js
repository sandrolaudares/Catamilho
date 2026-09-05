/* app.js — orquestracao: estado, chamadas a API, inicializacao */
const API = window.MILHO_API_URL || '';
const state = {
  layer: null, drawn: null, geometry: null, valid: false,
  series: null, cls: null, chart: null,
};

function setStatus(msg, kind) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status' + (kind ? ' ' + kind : '');
}

function updateAnalyzeBtn() {
  document.getElementById('btn-analyze').disabled = !state.valid;
  document.getElementById('btn-calib').disabled = !state.valid;
  const vb = document.getElementById('btn-vectorize');
  if (vb) vb.disabled = !state.valid;
}

async function analyze() {
  if (!state.valid) return;
  const btn = document.getElementById('btn-analyze');
  btn.disabled = true;
  setStatus('Análise em andamento — veja o gauge de progresso.');
  document.getElementById('results').hidden = true;
  showGauge();

  const body = {
    geometry: state.geometry,
    start: document.getElementById('dt-start').value || null,
    end: document.getElementById('dt-end').value || null,
    cloud_max: +document.getElementById('cloud').value,
    smooth: document.getElementById('opt-smooth').checked,
    use_dtw: document.getElementById('opt-dtw').checked,
    validar_mapbiomas: document.getElementById('opt-mb').checked,
    ano_validacao: +document.getElementById('mb-year').value || null,
    limiares: (typeof getLimiares === 'function') ? getLimiares() : null,
  };
  try {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 300000);
    const r = await fetch(API + '/api/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
    clearTimeout(to);
    if (!r.ok) {
      const e = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(e.detail || 'erro desconhecido');
    }
    const data = await r.json();
    state.series = data.series;
    state.cls = data.classification;
    state.smoothed = data.smoothed;
    state.dtw = data.dtw;
    state.mb = data.mapbiomas;
    renderChart(data.series, data.classification, data.smoothed);
    renderVerdict(data.classification);
    renderDTW(data.dtw);
    renderMapBiomas(data.mapbiomas, data.confronto);
    document.getElementById('results').hidden = false;
    setStatus(`✔ ${data.meta.cenas_processadas} cenas · ${data.meta.datas_validas} datas válidas`, 'ok');
    // abre a analise em NOVA ABA como relatorio dedicado
    openReportTab(data, body);
    completeGauge();
  } catch (err) {
    failGauge();
    setStatus('✖ ' + (err.name === 'AbortError'
      ? 'tempo esgotado — reduza o período' : err.message), 'err');
  } finally {
    btn.disabled = !state.valid;
  }
}

/* Monta o payload do relatorio e abre em nova aba (report.html) */
function openReportTab(data, params) {
  try {
    const areaHa = state.geometry
      ? turf.area(turf.polygon(state.geometry.coordinates)) / 10000 : null;
    const payload = {
      gerado_em: new Date().toISOString(),
      series: data.series,
      classification: data.classification,
      meta: data.meta,
      dtw: data.dtw,
      mapbiomas: data.mapbiomas,
      confronto: data.confronto,
      vectorized: state.vectorized || null,
      carInfo: state.carInfo || null,
      refCurve: (window.REF_CURVES && window.REF_CURVES.milho_safrinha)
        ? window.REF_CURVES.milho_safrinha.mensal : null,
      area_ha: areaHa,
      params: { start: params.start, end: params.end, cloud_max: params.cloud_max },
    };
    sessionStorage.setItem('milho_report', JSON.stringify(payload));
    window.open('report.html', '_blank', 'noopener');
  } catch (e) {
    console.warn('report tab falhou', e);
  }
}

async function calibrar() {
  if (!state.valid) return;
  const btn = document.getElementById('btn-calib');
  const stat = document.getElementById('calib-status');
  btn.disabled = true;
  stat.className = 'status';
  stat.textContent = 'Enviando amostra e calculando série…';
  try {
    const body = {
      classe: document.getElementById('calib-classe').value,
      geometry: state.geometry,
      start: document.getElementById('dt-start').value,
      end: document.getElementById('dt-end').value,
      cloud_max: +document.getElementById('cloud').value,
      safra: document.getElementById('calib-safra').value || null,
      municipio: document.getElementById('calib-mun').value || null,
    };
    const r = await fetch(API + '/api/calibrate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(e.detail || 'erro desconhecido');
    }
    const data = await r.json();
    stat.className = 'status ok';
    const n = (data.curvas[body.classe] && data.curvas[body.classe].n_amostras) || 1;
    stat.textContent = `✔ amostra salva (id ${data.sample.id}) · classe ${body.classe} com ${n} amostra(s) calibrada(s)`;
    // recarrega curvas para o proximo grafico
    fetch(API + '/api/reference-curves').then(x => x.json()).then(c => { window.REF_CURVES = c; });
  } catch (err) {
    stat.className = 'status err';
    stat.textContent = '✖ ' + err.message;
  } finally {
    btn.disabled = !state.valid;
  }
}

function initDates() {
  const end = new Date();
  const start = new Date();
  start.setMonth(start.getMonth() - 18);
  const iso = d => d.toISOString().slice(0, 10);
  document.getElementById('dt-start').value = iso(start);
  document.getElementById('dt-end').value = iso(end);
}

window.addEventListener('DOMContentLoaded', () => {
  initMap();
  loadBoundary();
  initDates();
  initCAR();

  document.getElementById('btn-analyze').addEventListener('click', analyze);
  document.getElementById('btn-calib').addEventListener('click', calibrar);
  document.getElementById('btn-json').addEventListener('click', exportJSON);
  document.getElementById('btn-png').addEventListener('click', exportPNG);
  document.querySelectorAll('.presets button').forEach(b =>
    b.addEventListener('click', () => flyToMunicipio(b.dataset.mun)));
  const cloud = document.getElementById('cloud');
  cloud.addEventListener('input', () =>
    document.getElementById('cloud-val').textContent = cloud.value + '%');
  document.getElementById('opt-mb').addEventListener('change', (e) => {
    document.getElementById('mb-year-lbl').style.display =
      e.target.checked ? 'block' : 'none';
  });

  fetch(API + '/api/reference-curves')
    .then(r => r.json())
    .then(c => { window.REF_CURVES = c; })
    .catch(() => { window.REF_CURVES = null; });

  if ('serviceWorker' in navigator && location.protocol !== 'file:')
    navigator.serviceWorker.register('sw.js').catch(() => {});
});

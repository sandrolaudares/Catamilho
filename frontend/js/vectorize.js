/* vectorize.js — classificacao pixel a pixel (10 m) + vetorizacao do milho
   dentro da propriedade (fluxo ideal: imovel aberto via CAR). */
let vecLayer = null;

function initVectorize() {
  document.getElementById('btn-vectorize').addEventListener('click', vectorizar);
}

async function vectorizar() {
  if (!state.valid) return;
  const btn = document.getElementById('btn-vectorize');
  btn.disabled = true;
  setStatus('Vetorizando milho pixel a pixel (10 m) na propriedade… 2–5 min');
  try {
    let start = document.getElementById('dt-start').value || null;
    let end = document.getElementById('dt-end').value || null;
    // classificacao pixel a pixel usa UMA safra: limita janela a ~12 meses
    if (start && end && (new Date(end) - new Date(start)) / 86400000 > 400) {
      const e = new Date(end);
      e.setFullYear(e.getFullYear() - 1);
      start = e.toISOString().slice(0, 10);
    }
    const body = {
      geometry: state.geometry,
      start, end,
      cloud_max: +document.getElementById('cloud').value,
      max_scenes: 60,
      threshold: 0.72,
      min_area_ha: 2,
      validar_mapbiomas: true,
      ano_mapbiomas: +document.getElementById('mb-year').value || null,
      limiares: (typeof getLimiares === 'function') ? getLimiares() : null,
      refinar: (document.getElementById('refinar') || {}).value || 'slic',
    };
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 480000);
    const r = await fetch(API + '/api/vectorize', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
    clearTimeout(to);
    if (!r.ok) {
      const e = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(e.detail || 'erro desconhecido');
    }
    const data = await r.json();
    state.vectorized = data;
    renderVectorized(data);
    const s = data.stats;
    setStatus(`✔ ${s.area_milho_ha.toLocaleString('pt-BR')} ha de milho vetorizado ` +
              `(${s.pct_milho}% da propriedade) · ${s.cenas_usadas} cenas`, 'ok');
  } catch (err) {
    setStatus('✖ ' + (err.name === 'AbortError'
      ? 'tempo esgotado — reduza a propriedade ou o período' : err.message), 'err');
  } finally {
    btn.disabled = !state.valid;
  }
}

function renderVectorized(data) {
  const block = document.getElementById('vec-block');
  block.hidden = false;
  document.getElementById('results').hidden = false;
  if (vecLayer) { map.removeLayer(vecLayer); vecLayer = null; }
  vecLayer = L.geoJSON(data.geojson, {
    style: { color: '#fbbf24', weight: 1.5, fillColor: '#fbbf24', fillOpacity: 0.35 },
    onEachFeature: (f, l) => l.bindPopup(
      `<b>Milho safrinha</b><br>${f.properties.area_ha.toLocaleString('pt-BR')} ha`),
  }).addTo(map);

  const s = data.stats;
  document.getElementById('vec-summary').textContent =
    `Milho vetorizado: ${s.area_milho_ha.toLocaleString('pt-BR')} ha de ` +
    `${s.area_total_ha.toLocaleString('pt-BR')} ha (${s.pct_milho}%) · ` +
    `${s.n_poligonos} polígonos · grade ${s.resolucao_m} m · ` +
    `${s.cenas_usadas}/${s.cenas_encontradas} cenas` +
    (s.refinamento && s.refinamento.metodo && s.refinamento.metodo !== 'off'
      ? ` · fronteiras refinadas (${s.refinamento.metodo})` : '');

  const el = document.getElementById('vec-acc');
  const a = data.acuracia;
  if (a && a.ok) {
    const pct = (a.acuracia_global * 100).toFixed(1);
    const ok90 = a.acuracia_global >= 0.90;
    el.textContent = `Acurácia vs MapBiomas ${a.ano}: ${pct}% ` +
      (ok90 ? '✔ atinge a meta de ≥90%' : '✖ abaixo da meta de 90% — calibre as curvas') +
      ` · precisão ${(a.precisao * 100).toFixed(0)}% · revocação ${(a.revocacao * 100).toFixed(0)}%` +
      ` · F1 ${(a.f1 * 100).toFixed(0)}% · IoU ${(a.iou * 100).toFixed(0)}%` +
      ` (${a.pixels.toLocaleString('pt-BR')} px)`;
    el.className = 'resumo ' + (ok90 ? 'ok-acc' : 'no-acc');
  } else {
    el.textContent = 'Acurácia vs MapBiomas indisponível' +
      (a && a.motivo ? ` (${a.motivo})` : '') +
      ' — a vetorização segue válida; valide com talhões conhecidos ou o campo mapbiomas_url.';
    el.className = 'resumo';
  }
  block.scrollIntoView({ behavior: 'smooth' });
}

window.addEventListener('DOMContentLoaded', initVectorize);

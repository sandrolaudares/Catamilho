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
  setStatus('Vetorização em andamento — veja o gauge.');
  showGauge();
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
      limiares: (typeof getLimiares === 'function') ? getLimiares() : null,
      refinar: (document.getElementById('refinar') || {}).value || 'slic',
    };
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 600000);
    const r = await fetch(API + '/api/vectorize/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
    if (!r.ok || !r.body) {
      const e = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(e.detail || 'erro desconhecido');
    }
    // stream NDJSON — progresso real da vetorizacao
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '', data = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (!line) continue;
        const ev = JSON.parse(line);
        if (ev.phase === 'cenas_encontradas')
          setGaugeProgress(5, `Classificando ${ev.total} cenas pixel a pixel…`);
        else if (ev.phase === 'processando')
          setGaugeProgress(5 + Math.round(88 * ev.done / Math.max(ev.total, 1)),
            `Cena ${ev.done}/${ev.total} — classificando pixels…`);
        else if (ev.phase === 'classificando')
          setGaugeProgress(95, 'Vetorizando talhões e medindo acurácia…');
        else if (ev.phase === 'concluido') data = ev.data;
        else if (ev.phase === 'erro') throw new Error(ev.mensagem || 'erro no servidor');
      }
    }
    clearTimeout(to);
    if (!data) throw new Error('resposta incompleta do servidor');
    state.vectorized = data;
    renderVectorized(data);
    const s = data.stats;
    setStatus(`✔ ${s.area_milho_ha.toLocaleString('pt-BR')} ha de milho vetorizado ` +
              `(${s.pct_milho}% da propriedade) · ${s.cenas_usadas} cenas`, 'ok');
    openVectorWindow(data);
    completeGauge();
  } catch (err) {
    failGauge();
    setStatus('✖ ' + (err.name === 'AbortError'
      ? 'tempo esgotado — reduza a propriedade ou o período' : err.message), 'err');
  } finally {
    btn.disabled = !state.valid;
  }
}

/* Abre o mapa de milho vetorizado em nova janela centralizada */
function openVectorWindow(data) {
  try {
    const payload = { geojson: data.geojson, stats: data.stats,
      acuracia: data.acuracia, geometry: state.geometry,
      carInfo: state.carInfo || null };
    sessionStorage.setItem('milho_vector', JSON.stringify(payload));
    const w = Math.min(1040, Math.round(screen.width * 0.82));
    const h = Math.min(880, Math.round(screen.height * 0.92));
    const left = Math.round((screen.width - w) / 2);
    const top = Math.round((screen.height - h) / 2);
    window.open('vector.html', 'milho_vector',
      `width=${w},height=${h},left=${left},top=${top}`);
  } catch (e) { console.warn('vector window falhou', e); }
}

function renderVectorized(data) {
  const block = document.getElementById('vec-block');
  block.hidden = false;
  document.getElementById('results').hidden = false;
  if (vecLayer) { map.removeLayer(vecLayer); vecLayer = null; }
  // cada classe com sua cor (CAR multiuso)
  vecLayer = L.geoJSON(data.geojson, {
    style: (f) => ({ color: f.properties.cor || '#fbbf24', weight: 1.5,
                     fillColor: f.properties.cor || '#fbbf24', fillOpacity: 0.38 }),
    onEachFeature: (f, l) => l.bindPopup(
      `<b>${f.properties.rotulo || f.properties.classe}</b><br>` +
      `${f.properties.area_ha.toLocaleString('pt-BR')} ha`),
  }).addTo(map);

  const s = data.stats;
  document.getElementById('vec-summary').textContent =
    `${s.n_zonas || '—'} zonas de uso detectadas · ` +
    `Milho: ${s.area_milho_ha.toLocaleString('pt-BR')} ha (${s.pct_milho}%) · ` +
    `${s.n_poligonos} polígonos · grade ${s.resolucao_m} m · ` +
    `${s.cenas_usadas}/${s.cenas_encontradas} cenas` +
    (s.refinamento && s.refinamento.metodo && s.refinamento.metodo !== 'off'
      ? ` · fronteiras refinadas (${s.refinamento.metodo})` : '');

  // legenda de classes (multiuso)
  const el = document.getElementById('vec-acc');
  el.className = 'resumo';
  if (s.classes && s.classes.length) {
    el.innerHTML = '<b>Composição da propriedade:</b><br>' +
      s.classes.map(c =>
        `<span class="leg-chip" style="background:${c.cor}"></span>` +
        `${c.rotulo}: <b>${c.area_ha.toLocaleString('pt-BR')} ha</b> (${c.n_poligonos} pol.)`
      ).join('<br>');
  } else {
    el.textContent = '';
  }
  block.scrollIntoView({ behavior: 'smooth' });
}

window.addEventListener('DOMContentLoaded', initVectorize);

/* gauge.js — gauge circular animado de progresso da analise (SVG, sem lib) */
let _gauge = null, _timer = null;

function _gaugeHTML() {
  return `
  <div id="gauge-overlay">
    <div class="gauge-box">
      <svg viewBox="0 0 120 120" class="gauge-svg">
        <circle class="gauge-bg" cx="60" cy="60" r="52"></circle>
        <circle class="gauge-fg" id="gauge-arc" cx="60" cy="60" r="52"></circle>
      </svg>
      <div class="gauge-center">
        <div id="gauge-pct">0%</div>
        <div id="gauge-label" class="gauge-label">Preparando…</div>
      </div>
      <div id="gauge-tip" class="gauge-tip">Consultando o catálogo Sentinel-2…</div>
    </div>
  </div>`;
}

const _FASES = [
  [0,   'Consultando o catálogo Sentinel-2…'],
  [20,  'Baixando recortes das bandas (B04/B08/SCL)…'],
  [45,  'Calculando NDVI por data…'],
  [70,  'Aplicando máscara de nuvens…'],
  [85,  'Aplicando regras fenológicas + DTW…'],
  [95,  'Montando o relatório…'],
];

function showGauge() {
  if (!_gauge) {
    document.body.insertAdjacentHTML('beforeend', _gaugeHTML());
    _gauge = document.getElementById('gauge-overlay');
  }
  _gauge.style.display = 'flex';
  const arc = document.getElementById('gauge-arc');
  const pct = document.getElementById('gauge-pct');
  const tip = document.getElementById('gauge-tip');
  const circ = 2 * Math.PI * 52;
  arc.style.strokeDasharray = circ;
  arc.style.strokeDashoffset = circ;

  // progresso estimado: assintotico ate 92% (analise dura 1-3 min)
  const t0 = Date.now();
  clearInterval(_timer);
  _timer = setInterval(() => {
    const el = (Date.now() - t0) / 1000;
    const p = Math.min(92, 92 * (1 - Math.exp(-el / 40))); // ~63% em 40s
    arc.style.strokeDashoffset = circ * (1 - p / 100);
    pct.textContent = Math.round(p) + '%';
    for (const [lim, txt] of _FASES.slice().reverse()) {
      if (p >= lim) { tip.textContent = txt; break; }
    }
  }, 120);
}

/* Progresso REAL vindo do stream do backend — para a estimativa e mostra o
   percentual/rotulo exatos informados pelo servidor. */
function setGaugeProgress(p, label) {
  clearInterval(_timer);
  if (!_gauge) return;
  const arc = document.getElementById('gauge-arc');
  const circ = 2 * Math.PI * 52;
  arc.style.strokeDashoffset = circ * (1 - Math.min(p, 99) / 100);
  document.getElementById('gauge-pct').textContent = Math.round(p) + '%';
  if (label) document.getElementById('gauge-tip').textContent = label;
  document.getElementById('gauge-label').textContent = 'Analisando…';
}

function completeGauge() {
  clearInterval(_timer);
  if (!_gauge) return;
  const arc = document.getElementById('gauge-arc');
  const pct = document.getElementById('gauge-pct');
  const tip = document.getElementById('gauge-tip');
  const circ = 2 * Math.PI * 52;
  arc.style.transition = 'stroke-dashoffset .5s ease';
  arc.style.strokeDashoffset = 0;
  pct.textContent = '100%';
  tip.textContent = 'Abrindo a análise em nova aba…';
  setTimeout(hideGauge, 900);
}

function failGauge() {
  clearInterval(_timer);
  if (_gauge) {
    document.getElementById('gauge-tip').textContent = 'Falha na análise.';
    setTimeout(hideGauge, 1400);
  }
}

function hideGauge() {
  clearInterval(_timer);
  if (_gauge) _gauge.style.display = 'none';
}

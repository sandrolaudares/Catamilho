/* limiares.js — painel de calibracao fina: sliders dos 7 limiares das regras
   + threshold da vetorizacao + metodo de refinamento de fronteiras. */
const LIM_DEFAULTS = {
  vigor_min: 0.70, outono_min: 0.15, vale_queda_min: 0.15, vale_max: 0.55,
  soja_min: 0.65, senesc_min: 0.25, ampl_min: 0.35,
};
const LIM_UI = {
  vigor_min:      { rotulo: 'Vigor do milho (pico mar–mai)', min: 0.55, max: 0.85, step: 0.01 },
  vale_max:       { rotulo: 'Teto do vale jan–fev',          min: 0.40, max: 0.70, step: 0.01 },
  vale_queda_min: { rotulo: 'Queda mínima p/ o vale',        min: 0.05, max: 0.35, step: 0.01 },
  soja_min:       { rotulo: 'Soja vigorosa (nov–jan)',       min: 0.50, max: 0.80, step: 0.01 },
  senesc_min:     { rotulo: 'Senescência jun–jul (queda)',   min: 0.10, max: 0.45, step: 0.01 },
  outono_min:     { rotulo: 'Pico de outono > pós-pico',     min: 0.05, max: 0.35, step: 0.01 },
  ampl_min:       { rotulo: 'Amplitude anual mínima',        min: 0.20, max: 0.55, step: 0.01 },
};

function initLimiares() {
  const det = document.getElementById('limiares-block');
  const body = document.getElementById('limiares-body');
  Object.entries(LIM_UI).forEach(([k, ui]) => {
    const div = document.createElement('div');
    div.className = 'lim-row';
    div.innerHTML =
      `<label>${ui.rotulo} <span id="lv-${k}" class="lim-val">${LIM_DEFAULTS[k].toFixed(2)}</span></label>` +
      `<input type="range" id="lim-${k}" min="${ui.min}" max="${ui.max}" step="${ui.step}" value="${LIM_DEFAULTS[k]}">`;
    body.appendChild(div);
    body.lastElementChild.querySelector('input').addEventListener('input', (e) => {
      document.getElementById(`lv-${k}`).textContent = (+e.target.value).toFixed(2);
    });
  });
  const btns = document.createElement('div');
  btns.className = 'lim-btns';
  btns.innerHTML =
    `<button id="lim-reset" type="button">Restaurar padrão</button>` +
    `<button id="lim-reclass" type="button">Reclassificar com estes limiares</button>`;
  body.appendChild(btns);
  document.getElementById('lim-reset').addEventListener('click', () => {
    Object.keys(LIM_UI).forEach(k => {
      document.getElementById(`lim-${k}`).value = LIM_DEFAULTS[k];
      document.getElementById(`lv-${k}`).textContent = LIM_DEFAULTS[k].toFixed(2);
    });
  });
  document.getElementById('lim-reclass').addEventListener('click', reclassify);
  det.hidden = false;
}

function getLimiares() {
  const out = {};
  Object.keys(LIM_UI).forEach(k => {
    const el = document.getElementById(`lim-${k}`);
    if (el) out[k] = +el.value;
  });
  return out;
}

async function reclassify() {
  if (!state.series) { setStatus('Rode "Analisar polígono" primeiro.', 'err'); return; }
  try {
    const r = await fetch(API + '/api/classify', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ series: state.series, limiares: getLimiares() }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const cls = await r.json();
    state.cls = cls;
    renderVerdict(cls);
    setStatus(`✔ Reclassificado: ${cls.veredito} (conf. ${Math.round(cls.confianca * 100)}%)`, 'ok');
  } catch (err) {
    setStatus('✖ ' + err.message, 'err');
  }
}

window.addEventListener('DOMContentLoaded', initLimiares);

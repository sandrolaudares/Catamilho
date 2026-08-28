/* classify.js — renderiza veredito, regras e estagios do ciclo */
function renderVerdict(c) {
  document.getElementById('verdict-emoji').textContent = c.emoji || '—';
  document.getElementById('verdict-text').textContent = c.veredito;
  document.getElementById('verdict-conf').textContent =
    Math.round((c.confianca || 0) * 100) + '%';
  document.getElementById('conf-bar').style.width =
    ((c.confianca || 0) * 100) + '%';
  document.getElementById('verdict-resumo').textContent = c.resumo || '';

  const ul = document.getElementById('rules');
  ul.innerHTML = '';
  (c.regras || []).forEach(r => {
    const li = document.createElement('li');
    li.className = r.disponivel ? (r.atendida ? 'ok' : 'no') : 'na';
    li.textContent = `${r.disponivel ? (r.atendida ? '✔' : '✖') : '○'} ${r.descricao}`;
    ul.appendChild(li);
  });

  const tb = document.querySelector('#stages tbody');
  tb.innerHTML = '';
  (c.estagios || []).forEach(s => {
    const tr = document.createElement('tr');
    const mark = s.ok === null || s.ok === undefined ? '—' : (s.ok ? '✔' : '✖');
    tr.innerHTML = `<td>${s.fase}</td><td>${s.janela}</td><td>${s.esperado}</td>` +
      `<td>${s.observado ?? '—'}</td><td>${mark}</td>`;
    tb.appendChild(tr);
  });
}

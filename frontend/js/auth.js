/* auth.js — login obrigatorio + sessao; mostra link de admin p/ papel admin */
(function () {
  const sess = JSON.parse(sessionStorage.getItem('milho_auth') || 'null');
  window.AUTH = sess;

  function showLogin() {
    document.body.insertAdjacentHTML('beforeend', `
    <div id="login-overlay">
      <form id="login-form" class="login-box">
        <h2>🌽 Catamilho</h2>
        <p>Identificação de milho safrinha — Médio Norte MT</p>
        <input id="login-user" placeholder="Usuário" autocomplete="username" required>
        <input id="login-pass" type="password" placeholder="Senha" autocomplete="current-password" required>
        <button type="submit">Entrar</button>
        <p id="login-err" class="login-err"></p>
      </form>
    </div>`);
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = document.getElementById('login-err');
      err.textContent = 'Verificando…';
      try {
        const r = await fetch((window.MILHO_API_URL || '') + '/api/auth/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: document.getElementById('login-user').value,
            password: document.getElementById('login-pass').value,
          }),
        });
        if (!r.ok) throw new Error('usuário ou senha inválidos');
        const data = await r.json();
        sessionStorage.setItem('milho_auth', JSON.stringify(data));
        window.AUTH = data;
        document.getElementById('login-overlay').remove();
        onLoggedIn();
      } catch (ex) { err.textContent = '✖ ' + ex.message; }
    });
  }

  function onLoggedIn() {
    const hdr = document.querySelector('header');
    if (!hdr) return;
    const el = document.createElement('div');
    el.id = 'user-chip';
    el.innerHTML = `<span>👤 ${window.AUTH.username}</span>` +
      (window.AUTH.role === 'admin'
        ? ` <a href="admin.html" target="_blank">⚙️ Admin</a>` : '') +
      ` <a href="#" id="logout-link">sair</a>`;
    hdr.appendChild(el);
    document.getElementById('logout-link').addEventListener('click', (e) => {
      e.preventDefault();
      sessionStorage.removeItem('milho_auth');
      location.reload();
    });
  }

  window.addEventListener('DOMContentLoaded', () => {
    if (!window.AUTH) showLogin();
    else onLoggedIn();
  });
})();

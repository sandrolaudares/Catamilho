/* tracker.js — auditoria de uso: page views, cliques e acoes-chave */
(function () {
  const API = window.MILHO_API_URL || '';
  function send(event, target, meta) {
    const a = window.AUTH;
    if (!a) return;
    try {
      fetch(API + '/api/track', {
        method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json',
                   'Authorization': 'Bearer ' + a.token },
        body: JSON.stringify({
          event, page: location.pathname.split('/').pop() || 'index.html',
          target: (target || '').slice(0, 200),
          meta: meta ? JSON.stringify(meta).slice(0, 400) : null,
        }),
      }).catch(() => {});
    } catch (e) {}
  }
  window.trackEvent = send;

  window.addEventListener('DOMContentLoaded', () => {
    send('page_view');
    // cliques: registra o alvo (id do botao/link ou texto curto)
    document.addEventListener('click', (e) => {
      const el = e.target.closest('button, a, input[type=range], select, .leaflet-interactive');
      if (!el) return;
      const alvo = el.id || el.dataset.mun ||
        (el.textContent || '').trim().slice(0, 60) || el.tagName;
      send('click', alvo);
    }, true);
    // clique no mapa (coordenada)
    if (window.map) {
      map.on('click', (e) =>
        send('click', 'mapa', { lat: +e.latlng.lat.toFixed(4),
                                lng: +e.latlng.lng.toFixed(4) }));
    }
  });
})();

/* sw.js — PWA shell offline; /api e sempre rede (analise depende de download) */
const SHELL = 'milho-shell-v1';
const ASSETS = [
  './', 'index.html', 'styles.css', 'manifest.webmanifest',
  'js/app.js', 'js/map.js', 'js/boundary.js',
  'js/timeseries.js', 'js/classify.js', 'js/export.js',
  'data/mt.geojson',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => { e.waitUntil(clients.claim()); });
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/')) return; // sempre rede
  e.respondWith(
    caches.match(e.request).then(hit => {
      const net = fetch(e.request).then(r => {
        if (r.ok && e.request.method === 'GET') {
          const cp = r.clone();
          caches.open(SHELL).then(c => c.put(e.request, cp));
        }
        return r;
      }).catch(() => hit);
      return hit || net;
    })
  );
});

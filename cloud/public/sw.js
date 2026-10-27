const CACHE = 'taistock-monitor-v16';
const SHELL = ['/', '/styles.css', '/app.js', '/refresh.js', '/manifest.webmanifest', '/icon.svg'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener('fetch', event => {
  const path = new URL(event.request.url).pathname;
  if (event.request.method !== 'GET' || path.startsWith('/api/') || path.startsWith('/download/') || !SHELL.includes(path)) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok && !response.headers.get('cache-control')?.includes('no-store')) {
      const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy));
    }
    return response;
  }).catch(() => caches.match(event.request)));
});

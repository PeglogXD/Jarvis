// J.A.R.V.I.S. — Service Worker
const CACHE = 'jarvis-v1';
const ASSETS = ['./index.html', './manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // Network-first for API calls, cache-first for assets
  if (e.request.url.includes('api.groq.com') || e.request.url.includes('openrouter.ai') || e.request.url.includes('localhost:5050')) {
    e.respondWith(fetch(e.request).catch(() => new Response('Sin conexión', { status: 503 })));
  } else {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});

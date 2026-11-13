// HomeoAI service worker.
// The shell is cached on install; the repertory corpora are cached the first
// time they are used, so a second visit needs no network at all.
const CACHE = 'homeoai-v1';
const SHELL = [
  './',
  './index.html',
  './recommender.html',
  './library.html',
  './styles/app.css',
  './manifest.webmanifest',
  './assets/icon.svg',
  './assets/icon-192.png',
  './assets/icon-512.png',
  './assets/favicon.ico',
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return; // the AI relay stays live-only

  // Corpora never change between releases: serve from cache, refresh quietly.
  if (url.pathname.includes('/data/')) {
    event.respondWith(caches.open(CACHE).then(async cache => {
      const hit = await cache.match(request);
      const network = fetch(request).then(response => {
        if (response && response.ok) cache.put(request, response.clone());
        return response;
      }).catch(() => hit);
      return hit || network;
    }));
    return;
  }

  // Pages and styles: network first so a new deploy is picked up immediately,
  // falling back to the cached copy when offline.
  event.respondWith(
    fetch(request).then(response => {
      if (response && response.ok) caches.open(CACHE).then(cache => cache.put(request, response.clone()));
      return response;
    }).catch(() => caches.match(request).then(hit => hit || caches.match('./index.html')))
  );
});

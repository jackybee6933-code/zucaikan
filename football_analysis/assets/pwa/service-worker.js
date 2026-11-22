const CACHE_NAME = "football-pwa-static-v1";
const STATIC_ASSETS = [
  "/pwa/offline.html",
  "/pwa/icon-192.png",
  "/pwa/icon-512.png",
  "/pwa/app.js"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith("football-pwa-static-") && key !== CACHE_NAME)
      .map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (STATIC_ASSETS.includes(url.pathname) && !url.search) {
    event.respondWith(caches.open(CACHE_NAME).then(cache =>
      cache.match(url.pathname).then(cached => cached || fetch(event.request))
    ));
  } else if (event.request.mode === "navigate") {
    // Private pages are network-only; never retain stale analysis or credentials.
    event.respondWith(fetch(event.request).then(response => {
      if (response.status >= 500) throw new Error("Backend unavailable");
      return response;
    }).catch(() => caches.open(CACHE_NAME).then(cache => cache.match("/pwa/offline.html"))));
  }
});

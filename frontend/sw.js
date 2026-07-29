// Minimal app-shell cache. Only registers over HTTPS or localhost (iOS rule),
// so over plain LAN HTTP the app still works — just without offline caching.
const CACHE = "moviebeli-v1";
const SHELL = ["/", "/index.html", "/styles.css", "/js/app.js", "/js/api.js", "/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const { request } = e;
  // Never cache API calls or TMDB images — always go to network.
  if (request.method !== "GET" || request.url.includes("/api/")) return;
  e.respondWith(
    caches.match(request).then((cached) => cached || fetch(request).catch(() => cached))
  );
});

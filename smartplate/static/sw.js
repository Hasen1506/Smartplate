/* SmartPlate service worker: makes the app installable and opens offline.
   Network-first for the app shell (so a new release is picked up on the next open),
   falling back to the cached copy when offline. API responses are never cached:
   budgets and plans must always be live. */
const CACHE = "smartplate-shell-v1";
const SHELL = ["/", "/static/app.js", "/static/styles.css", "/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/icons/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  const key = e.request.mode === "navigate" ? "/" : e.request;
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok) caches.open(CACHE).then((c) => c.put(key, res.clone()));
    return res;
  }).catch(() => caches.match(key)));
});

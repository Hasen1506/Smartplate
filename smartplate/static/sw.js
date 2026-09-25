/* SmartPlate service worker: makes the app installable, opens offline, and shows
   order-time reminders pushed by the server (smartplate/push.py).
   Network-first for the app shell (so a new release is picked up on the next open),
   falling back to the cached copy when offline. API responses are never cached:
   budgets and plans must always be live. */
const CACHE = "smartplate-shell-v2";
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

/* Push reminders: the server sends {title, body, url, tag}, encrypted end to end. */
self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (_) { data = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(data.title || "SmartPlate", {
    body: data.body || "", tag: data.tag || "smartplate", renotify: true,
    icon: "/static/icons/icon-192.png", badge: "/static/icons/icon-192.png", data: { url: data.url || "/" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    if (url.startsWith("/")) {                       // our own page: reuse an open window
      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const win = wins.find((w) => new URL(w.url).origin === self.location.origin);
      if (win) { await win.focus(); return win.navigate(url); }
    }
    return self.clients.openWindow(url);             // e.g. the Swiggy hand-off link
  })());
});

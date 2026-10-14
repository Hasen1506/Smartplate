/* Ziggy service worker: makes the app installable, opens offline, and shows
   order-time reminders pushed by the server (smartplate/push.py).
   App shell: network-first (a new release is picked up on the next open), falling back
   to the cached copy when offline.
   Plan reads: a short allowlist of read-only GETs (the plan, its orders, the profile list,
   Swiggy status) is also network-first, so budgets and plans are always live when there is
   a network. The last good answer is kept, and served only when the network fails, marked
   with an X-Ziggy-Cached-At header so the app shows "You're offline · plan as loaded at …"
   instead of a blank "needs a connection" screen. Nothing that writes, and no live Swiggy
   cart or menu, is ever cached. Signing out clears the plan cache (app.js clearPlanCache). */
const CACHE = "ziggy-shell-v2";
const API_CACHE = "ziggy-api-v1";
const SHELL = ["/", "/static/app.js", "/static/styles.css", "/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/icons/icon.svg",
  "/static/fonts/figtree-latin.woff2", "/static/fonts/bricolage-latin.woff2"];
// Read-only answers worth keeping for an offline reload. Anything else under /api/ is
// never touched by the service worker.
const API_READS = [/^\/api\/meta$/, /^\/api\/users$/, /^\/api\/user\/\d+\/(plan|swiggy|saved)$/,
  /^\/api\/plan\/\d+(\/orders)?$/];
const cacheableApi = (url) => API_READS.some((re) => re.test(url.pathname)) && !url.search;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== API_CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// The last good copy, re-stamped with when it was fetched (the app shows that time).
async function keepApi(req, res) {
  const body = await res.clone().arrayBuffer();
  const headers = new Headers(res.headers);
  headers.set("X-Ziggy-Cached-At", new Date().toISOString());
  const c = await caches.open(API_CACHE);
  await c.put(req.url, new Response(body, { status: res.status, statusText: res.statusText, headers }));
}

async function apiRead(req) {
  try {
    const res = await fetch(req);
    if (res.ok) keepApi(req, res).catch(() => {});
    return res;
  } catch (err) {
    const hit = await caches.match(req.url, { cacheName: API_CACHE });
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) {
    if (cacheableApi(url)) e.respondWith(apiRead(e.request));
    return;
  }
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
  e.waitUntil(self.registration.showNotification(data.title || "Ziggy", {
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

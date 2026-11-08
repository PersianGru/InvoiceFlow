/* InvoiceFlow service worker: caches the app shell and static assets only.
   API responses are never cached; navigations are network-first with an offline fallback. */
const VERSION = "if-shell-v2";
// The app may live under a base path: the registration scope carries it (e.g. /dashboard/).
const BASE = new URL(self.registration.scope).pathname.replace(/\/$/, "");
const SHELL = ["/my", "/my/capture", "/offline", "/manifest.webmanifest", "/icons/icon-192.png"].map((p) => BASE + p);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => cache.addAll(SHELL).catch(() => undefined))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith(BASE + "/api/")) return; // always live: documents, license state, uploads
  if (url.pathname.startsWith(BASE + "/_next/static/") || url.pathname.startsWith(BASE + "/icons/")) {
    event.respondWith(
      caches.open(VERSION).then((cache) =>
        cache.match(req).then(
          (hit) =>
            hit ||
            fetch(req).then((res) => {
              if (res.ok) cache.put(req, res.clone());
              return res;
            }),
        ),
      ),
    );
    return;
  }
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok && (url.pathname === BASE + "/my" || url.pathname.startsWith(BASE + "/my/"))) {
            caches.open(VERSION).then((cache) => cache.put(req, res.clone()));
          }
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match(BASE + "/offline"))),
    );
  }
});

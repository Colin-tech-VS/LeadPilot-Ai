/* LeadPilot AI — service worker (PWA shell + notifications) */
/* Bump CACHE on every asset change: the activate handler purges every older
   cache, so returning users can never be stuck on a stale bundle. */
const CACHE = "leadpilot-v5";
const ASSETS = [
  "/static/css/main.css",
  "/static/css/logo.css",
  "/static/css/public.css",
  "/static/css/vitrine.css",
  "/static/js/nav.js",
  "/static/js/dashboard.js",
  "/static/js/notifications.js",
  "/static/js/pwa-install.js",
  "/static/admin/admin.css",
  "/static/admin/admin.js",
  "/static/images/logo.svg",
  "/manifest.webmanifest",
  "/public.webmanifest",
];

/* Static assets whose freshness matters more than offline speed. Serving a
   stale main.css here is what makes the UI look broken ("no cards"), so CSS
   and JS go network-first and only fall back to the cache when offline. */
function isFreshnessCritical(pathname) {
  return pathname.endsWith(".css") || pathname.endsWith(".js");
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

/* CSS/JS: network-first so style/script updates always reach the user, with a
   cached fallback for offline. Other static assets (images, fonts): cache-first
   with background refresh for speed. Everything else hits the network. */
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith("/static/")) {
    return;
  }

  if (isFreshnessCritical(url.pathname)) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});

/* Web Push — ready for future server-side push (VAPID). */
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data && event.data.text() };
  }
  const title = data.title || "LeadPilot AI";
  const options = {
    body: data.body || "Nouveau rendez-vous planifié.",
    icon: "/static/images/logo.svg",
    badge: "/static/images/logo.svg",
    tag: data.tag || "leadpilot-appointment",
    data: { url: data.url || "/dashboard" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target =
    (event.notification.data && event.notification.data.url) || "/dashboard";
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        for (const client of clients) {
          if (client.url.includes(target) && "focus" in client) {
            return client.focus();
          }
        }
        if (self.clients.openWindow) return self.clients.openWindow(target);
      })
  );
});

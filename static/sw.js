// A small service worker so Android offers "Install app" (adapted from Where to Eat). Network first: the app always
// shows current data. The last copy of the shell (the page, scripts, styles, fonts, icon) is used only when the phone
// is offline. /api/ answers are never stored: the family's visits and ratings stay off the phone's disk. Writes are
// never touched.
const CACHE = "hometown-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(
  caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()),
));

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then(hit => hit || Response.error())),
  );
});

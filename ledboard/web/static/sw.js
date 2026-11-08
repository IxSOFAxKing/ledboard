// LEDBoard service worker: makes the web UI installable and lets it open instantly.
// The app shell is served network-first (so updates show up right away) with a cached
// fallback; API calls and the live preview always go to the network and are never cached.
const CACHE = "ledboard-shell-v2";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

const OFFLINE = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<body style="background:#0d0f12;color:#e7ebf0;font:16px system-ui;display:grid;place-items:center;height:90vh;text-align:center">
<div><h2>Can't reach LEDBoard</h2><p style="color:#8b95a3">Check that the Pi is on and you're on the same network (or Tailscale).</p>
<button onclick="location.reload()" style="padding:10px 18px;border-radius:8px;border:0;background:#1f6fd1;color:#fff">Try again</button></div>`;

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/spotify/")) return;   // always live
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok && (SHELL.includes(url.pathname) || url.pathname.startsWith("/static/"))) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(async () => (await caches.match(e.request)) ||
        (e.request.mode === "navigate" ? (await caches.match("/")) ||
          new Response(OFFLINE, { headers: { "Content-Type": "text/html" } }) : Response.error()))
  );
});

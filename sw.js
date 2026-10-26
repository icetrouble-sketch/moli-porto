/* Tiene l'app in memoria sul telefono: dopo la prima apertura funziona
   anche senza segnale. Cambia il numero della cache dopo ogni modifica. */
const CACHE = "moli-porto-10";
const FILE = ["./", "./index.html", "./dati.enc.js", "./contesto.js",
              "./manifest.webmanifest", "./icone/icona-180.png", "./icone/icona-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.all(FILE.map(f => c.add(f).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(k => Promise.all(k.filter(n => n !== CACHE).map(n => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  if(e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request)
      .then(r => {
        const copia = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, copia)).catch(() => {});
        return r;
      })
      .catch(() => caches.match(e.request).then(r => r || caches.match("./index.html")))
  );
});

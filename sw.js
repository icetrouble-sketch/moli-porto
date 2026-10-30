/* Tiene l'app in memoria sul telefono: dopo la prima apertura funziona
   anche senza segnale. Cambia il numero della cache dopo ogni modifica.
   I dati delle navi (navi.enc.json) non passano da qui: li tiene l'app, una copia
   sola (prima se ne salvava una nuova a ogni caricamento, perche' l'indirizzo
   cambia ogni volta). Le richieste ad altri siti (foglio delle visite) non si salvano. */
const CACHE = "moli-porto-27";
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
  const req = e.request;
  if(req.method !== "GET") return;
  const url = new URL(req.url);
  if(url.origin !== self.location.origin) return;               // altri siti: direttamente alla rete
  const pagina = req.mode === "navigate";
  if(!pagina && (url.search || url.pathname.endsWith("/navi.enc.json"))) return;   // dati delle navi: li tiene l'app
  e.respondWith(
    fetch(req)
      .then(r => {
        if(r.ok){
          const copia = r.clone();
          caches.open(CACHE).then(c => c.put(pagina ? "./index.html" : req, copia)).catch(() => {});
        }
        return r;
      })
      .catch(() => caches.match(pagina ? "./index.html" : req)
        .then(r => r || (pagina ? caches.match("./") : undefined))
        .then(r => r || Response.error()))
  );
});

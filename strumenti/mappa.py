#!/usr/bin/env python3
"""Modificare i dati della mappa (ditte, colori, moli, banchine, varchi, foglio delle visite).

I dati della mappa stanno solo cifrati in dati.enc.js. Per cambiarli:

  1. python3 strumenti/mappa.py apri     chiede il codice e scrive dati.js (in chiaro, solo sul computer)
  2. si modifica dati.js con un editor di testo (window.TERMINAL, window.BANCHINE, window.VARCHI, window.CONFIG...)
  3. python3 strumenti/mappa.py chiudi   chiede il codice, cifra di nuovo dati.js in dati.enc.js e cancella dati.js
  4. si pubblica dati.enc.js (commit e push) e si aumenta il numero della cache in sw.js

dati.js e' in .gitignore: non finisce mai nel repository pubblico. "chiudi" tiene lo stesso sale e lo stesso numero
di iterazioni: la chiave non cambia, quindi continuano ad aprirsi navi.enc.json e stato.enc.json, il segreto
CODICE_MAPPA su GitHub resta giusto e Face ID sui telefoni continua a funzionare. (Cambiare il codice di accesso e'
un'altra cosa: vanno cifrati di nuovo anche i file delle navi e aggiornato il segreto.)

Il codice si puo' dare anche con la variabile d'ambiente CODICE_MAPPA (per le prove).
Ricreato il 12/11/2026: gli strumenti di prima (decifra.js, cifra.js) non erano mai stati salvati nel repository.
"""
import base64, getpass, json, os, re, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CIFRATO = os.path.join(BASE, "dati.enc.js")
CHIARO = os.path.join(BASE, "dati.js")
INTESTAZIONE = ("/* Dati della mappa, cifrati. Senza il codice di accesso non sono leggibili.\n"
                "   Per modificarli: python3 strumenti/mappa.py apri, poi python3 strumenti/mappa.py chiudi. */\n")
# quello che l'app si aspetta di trovare dopo la decifratura (vedi index.html e aggiorna_navi.leggi_banchine)
NECESSARI = ("window.TERMINAL", "window.CONFIG", "window.BANCHINE")


def leggi_cifrato(percorso=None):
    s = open(percorso or CIFRATO, encoding="utf-8").read()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def chiave(codice, D):
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(D["salt"]), iterations=D["iter"])
    return kdf.derive(codice.encode("utf-8"))


def decifra(codice, D):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    try:
        return AESGCM(chiave(codice, D)).decrypt(base64.b64decode(D["iv"]), base64.b64decode(D["ct"]), None).decode("utf-8")
    except InvalidTag:
        sys.exit("Codice sbagliato: dati.enc.js non si apre.")


def cifra(codice, D, testo):
    """Stesso sale e stesse iterazioni (la chiave resta quella), IV nuovo."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(chiave(codice, D)).encrypt(iv, testo.encode("utf-8"), None)
    nuovo = {k: D[k] for k in ("v", "alg", "iter", "salt") if k in D}
    nuovo.update({"iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()})
    return nuovo


def controlla(testo):
    """Errori evidenti prima di cifrare: un dato.js rotto bloccherebbe l'app per tutti."""
    mancano = [x for x in NECESSARI if x not in testo]
    if mancano:
        sys.exit("dati.js non contiene " + ", ".join(mancano) + ": non cifro niente.")
    m = re.search(r"window\.BANCHINE\s*=\s*(\{.*?\});\s*$", testo, re.S | re.M)
    if not m:
        sys.exit("window.BANCHINE deve stare su una riga sua e finire con ';' (lo legge anche il programma delle navi).")
    try:
        json.loads(m.group(1))
    except ValueError as e:
        sys.exit("window.BANCHINE non e' JSON valido: %s" % e)
    try:
        import shutil, subprocess, tempfile
        if shutil.which("node"):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write("var window = {};\n" + testo + "\nif(!Array.isArray(window.TERMINAL)) throw new Error('TERMINAL non e\\' un elenco');")
            r = subprocess.run(["node", f.name], capture_output=True, text=True)
            os.unlink(f.name)
            if r.returncode:
                sys.exit("dati.js ha un errore di JavaScript, non cifro niente:\n" + r.stderr.strip()[-800:])
    except OSError:
        pass


def codice_da_chiedere():
    return os.environ.get("CODICE_MAPPA") or getpass.getpass("Codice di accesso della mappa: ")


def apri():
    if os.path.exists(CHIARO):
        sys.exit("dati.js esiste gia': modificalo e poi usa 'chiudi' (o cancellalo per ripartire da dati.enc.js).")
    D = leggi_cifrato()
    testo = decifra(codice_da_chiedere(), D)
    with open(CHIARO, "w", encoding="utf-8") as f:
        f.write(testo)
    print("Scritto dati.js (in chiaro, non va pubblicato). Dopo le modifiche: python3 strumenti/mappa.py chiudi")


def chiudi():
    if not os.path.exists(CHIARO):
        sys.exit("Manca dati.js: prima 'python3 strumenti/mappa.py apri'.")
    testo = open(CHIARO, encoding="utf-8").read()
    controlla(testo)
    D = leggi_cifrato()
    codice = codice_da_chiedere()
    decifra(codice, D)                                   # il codice deve essere quello di adesso
    nuovo = cifra(codice, D, testo)
    if decifra(codice, nuovo) != testo:
        sys.exit("Controllo dopo la cifratura non riuscito: dati.enc.js non e' stato cambiato.")
    provvisorio = CIFRATO + ".nuovo"
    with open(provvisorio, "w", encoding="utf-8") as f:
        f.write(INTESTAZIONE + "window.DATI_CIFRATI = " + json.dumps(nuovo) + ";\n")
    os.replace(provvisorio, CIFRATO)
    os.remove(CHIARO)
    print("dati.enc.js aggiornato e dati.js cancellato. Ora: aumenta il numero della cache in sw.js, poi commit e push.")


if __name__ == "__main__":
    comandi = {"apri": apri, "chiudi": chiudi}
    if len(sys.argv) != 2 or sys.argv[1] not in comandi:
        print(__doc__)
        sys.exit(2)
    comandi[sys.argv[1]]()

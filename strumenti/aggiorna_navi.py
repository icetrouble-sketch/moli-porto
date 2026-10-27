#!/usr/bin/env python3
"""Aggiorna le navi del giorno per l'app (gira in GitHub Actions ogni 30 minuti).

1. Apre le navi gia' pubblicate (navi.enc.json) con il codice della mappa.
2. Se sul sito shipsinformationgenova.com c'e' un giornale della Capitaneria
   piu' recente, lo scarica, lo legge (OCR) e tiene arrivi e partenze dei
   terminal della mappa.
3. Ascolta aisstream per qualche minuto e aggiunge la posizione reale delle
   navi: ormeggiata, in rada, in arrivo (con l'ora stimata).
4. Cifra tutto di nuovo in navi.enc.json.

Variabili d'ambiente: CODICE_MAPPA (obbligatoria), AISSTREAM_KEY (facoltativa).
Uso: python3 strumenti/aggiorna_navi.py [--forza-giornale] [--pdf file.pdf]
"""
import os, sys, re, json, math, base64, time, asyncio, tempfile, subprocess, difflib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROMA = ZoneInfo("Europe/Rome")
SITO = "https://www.shipsinformationgenova.com"
TERMINAL_MAPPA = {"imt", "sangiorgio", "gmt", "spinelli", "rinfuse", "bettolo", "sech"}
# imboccatura del bacino di Sampierdarena (per le distanze)
IMBOCCATURA = (44.3985, 8.8960)
# area dei moli: dentro e ferma = in banchina
MOLI_BOX = (44.3960, 44.4125, 8.8720, 8.9200)
# zona in cui ascoltare l'AIS: Mar Ligure
AIS_BOX = [[43.30, 7.50], [44.60, 10.30]]


def log(*a):
    print(datetime.now(ROMA).strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- cifratura
def chiave(codice):
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    s = open(os.path.join(BASE, "dati.enc.js"), encoding="utf-8").read()
    D = json.loads(s[s.index("{"):s.rindex("}") + 1])
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(D["salt"]), iterations=D["iter"])
    k = kdf.derive(codice.encode("utf-8"))
    # il codice deve aprire i dati della mappa, altrimenti l'app non leggerebbe le navi
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    AESGCM(k).decrypt(base64.b64decode(D["iv"]), base64.b64decode(D["ct"]), None)
    return k


def apri(k, percorso):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        o = json.load(open(percorso, encoding="utf-8"))
        return json.loads(AESGCM(k).decrypt(base64.b64decode(o["iv"]), base64.b64decode(o["ct"]), None))
    except FileNotFoundError:
        return None


def chiudi(k, dati, percorso):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(k).encrypt(iv, json.dumps(dati, ensure_ascii=False).encode("utf-8"), None)
    json.dump({"v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()},
              open(percorso, "w", encoding="utf-8"))


# ---------------------------------------------------------------- giornale
def campi_nascosti(html):
    return {m.group(1): m.group(2) for m in
            re.finditer(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html)}


def data_giornale_sul_sito(sess):
    import html as H
    r = sess.get(SITO + "/ITA/Default.aspx", timeout=40)
    r.raise_for_status()
    m = re.search(r"Note della Capitaneria del (\d{2})/(\d{2})/(\d{4})", r.text)
    if not m:
        raise RuntimeError("data del giornale non trovata nella pagina")
    campi = {k: H.unescape(v) for k, v in campi_nascosti(r.text).items()}
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}", campi


def scarica_giornale(sess, campi):
    campi = dict(campi)
    campi["__EVENTTARGET"] = ""; campi["__EVENTARGUMENT"] = ""
    campi["ctl00$ctl00$corpo$btnCap1"] = ""
    r = sess.post(SITO + "/ITA/Default.aspx", data=campi, timeout=60)
    r.raise_for_status()
    p = sess.get(SITO + "/frmPdf.aspx", timeout=60)
    p.raise_for_status()
    if not p.content.startswith(b"%PDF"):
        raise RuntimeError("il sito non ha restituito un PDF")
    return p.content


def leggi_pdf(pdf_bytes):
    import leggi_giornale as L
    righe = []
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "g.pdf")
        open(f, "wb").write(pdf_bytes)
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", f, os.path.join(d, "p")], check=True)
        pagine = sorted(x for x in os.listdir(d) if x.endswith(".png"))
        sezione = None
        for pg in pagine:
            r = L.leggi(os.path.join(d, pg))
            log("pagina", pg, "righe", len(r.get("righe", [])), "angolo", r.get("angolo"), r.get("errore") or "")
            for x in r.get("righe", []):
                if x["sezione"] is None and sezione:
                    x["sezione"] = sezione
                sezione = x["sezione"] or sezione
                righe.append(x)
            # dopo i mezzi diga non serve continuare
            if any((x.get("sezione") or "") == "MEZZI DIGA" for x in r.get("righe", [])):
                break
    return righe


TIPI = ["CONT.", "RO/RO", "BULK", "CHIM.", "TANK", "CARGO", "PASS.", "CRUISE", "YACHT", "HOPPER", "GAS", "REEFER", "GEN.CARGO"]


def pulisci_nome(n):
    n = (n or "").upper().replace("’", "'")
    n = re.sub(r"[^A-Z0-9 '.\-&]", " ", n)
    parole = n.split()
    # via i pezzetti di bordo letti come lettere (es. "7", "A", "OO")
    while parole and (len(parole[0]) == 1 or not re.search(r"[A-Z]{2}", parole[0])):
        parole.pop(0)
    while parole and (len(parole[-1]) == 1 or not re.search(r"[A-Z0-9]{2}", parole[-1])):
        parole.pop()
    return " ".join(parole).strip(" .-")


def pulisci_tipo(t):
    t = re.sub(r"[^A-Z/.]", "", (t or "").upper())
    if not t:
        return ""
    meglio = max(TIPI, key=lambda x: difflib.SequenceMatcher(None, x, t).ratio())
    return meglio if difflib.SequenceMatcher(None, meglio, t).ratio() > 0.6 else t


def pulisci_accosto(a, molo=None):
    a = re.sub(r"[~“”‘’_|©>—]", " ", a or "")
    a = re.sub(r"\s+", " ", a).strip(" .-:;,'\"7")
    if molo:
        # il nome del molo, letto male o attaccato al resto, si riscrive giusto
        m = re.match(r"\s*([A-Z]+)", a.upper())
        if m and difflib.SequenceMatcher(None, m.group(1)[:len(molo)], molo.upper()).ratio() >= 0.75:
            a = molo.upper() + " " + a[m.start(1) + len(molo):].lstrip()
    a = re.sub(r"\b(LEV|PON)[.,]\s*", r"\1. ", a)
    return a.strip()


def misura(v, lo, hi):
    return v if v is not None and lo <= v <= hi else None


def minuti(o):
    m = re.fullmatch(r"(\d{2})\.(\d{2})", o or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def correggi_orari(righe):
    """Nel giornale gli orari di ogni sezione sono in ordine crescente
    (prima ALBA e A.M.). Si tiene la sequenza crescente piu' lunga; un orario
    fuori sequenza e' letto male: si prova la correzione di una cifra che lo
    rimette in ordine, e resta comunque segnato come dubbio."""
    blocchi, cur = [], []
    for i, r in enumerate(righe):
        if cur and righe[cur[-1]]["sezione"] != r["sezione"]:
            blocchi.append(cur); cur = []
        cur.append(i)
    if cur:
        blocchi.append(cur)
    for blocco in blocchi:
        if righe[blocco[0]]["sezione"] not in ("ARRIVI", "PARTENZE"):
            continue
        idx = [i for i in blocco if minuti(righe[i]["ora"]) is not None]
        v = [minuti(righe[i]["ora"]) for i in idx]
        if len(v) < 2:
            continue
        # sottosequenza non decrescente piu' lunga
        L = [1] * len(v); P = [-1] * len(v)
        for a in range(len(v)):
            for b in range(a):
                if v[b] <= v[a] and L[b] + 1 > L[a]:
                    L[a], P[a] = L[b] + 1, b
        k = max(range(len(v)), key=lambda x: L[x]); buoni = set()
        while k >= 0:
            buoni.add(k); k = P[k]
        for j in range(len(v)):
            if j in buoni:
                continue
            prima = max([v[b] for b in buoni if b < j] or [0])
            dopo = min([v[b] for b in buoni if b > j] or [24 * 60 - 1])
            i = idx[j]; ora = righe[i]["ora"]
            candidati = set()
            for pos in (0, 1, 3, 4):
                for c in "0123456789":
                    t = ora[:pos] + c + ora[pos + 1:]
                    w = minuti(t)
                    if w is not None and w < 24 * 60 and prima <= w <= dopo and t[3:] in ("00", "15", "30", "45", "59"):
                        candidati.add(t)
            scelta = sorted(candidati)[0] if len(candidati) == 1 else None
            log("orario fuori ordine:", righe[i]["nave"], ora, "->", scelta or "dubbio")
            righe[i]["ora_letta"] = ora
            righe[i]["ora"] = scelta or ora
            righe[i]["ora_dubbia"] = True


def navi_dal_giornale(righe, data):
    # dopo i MOVIMENTI vengono i mezzi diga (rimorchiatori, draghe): non servono
    visto_mov = False
    for r in righe:
        if r["sezione"] == "MOVIMENTI":
            visto_mov = True
        elif visto_mov and r["sezione"] in ("ARRIVI", "PARTENZE"):
            r["sezione"] = "MEZZI DIGA " + r["sezione"]
    correggi_orari(righe)
    per_nave = {}
    for r in righe:
        if r["sezione"] not in ("ARRIVI", "PARTENZE") or not r.get("dove"):
            continue
        if r["dove"]["terminal"] not in TERMINAL_MAPPA:
            continue
        nome = pulisci_nome(r["nave"])
        if not nome:
            continue
        chiave_n = r["imo"] if r["imo_ok"] else re.sub(r"[^A-Z]", "", nome)
        n = per_nave.setdefault(chiave_n, {"nome": nome, "imo": r["imo"] if r["imo_ok"] else "",
                                           "tipo": pulisci_tipo(r["tipo"]), "loa": r.get("loa"), "larg": r.get("larg"),
                                           "accosto": pulisci_accosto(r["accosto"], r["dove"].get("molo")), "dove": r["dove"],
                                           "arrivo": None, "partenza": None})
        # tra due letture dello stesso nome si tiene la piu' pulita
        if len(re.sub(r"[^A-Z]", "", nome)) > len(re.sub(r"[^A-Z]", "", n["nome"])) and r["imo_ok"]:
            n["nome"] = nome
        n["loa"] = misura(n["loa"], 25, 420) or misura(r.get("loa"), 25, 420)
        n["larg"] = misura(n["larg"], 5, 66) or misura(r.get("larg"), 5, 66)
        n["tipo"] = n["tipo"] or pulisci_tipo(r["tipo"])
        campo = "arrivo" if r["sezione"] == "ARRIVI" else "partenza"
        n[campo] = r["ora"] or None
        if r.get("ora_dubbia"):
            n.setdefault("dubbi", []).append(campo)
        if not r["imo_ok"]:
            n.setdefault("dubbi", []).append("imo")
    navi = list(per_nave.values())
    for n in navi:
        if "dubbi" in n:
            n["dubbi"] = sorted(set(n["dubbi"]))
            if "arrivo" in n["dubbi"] or "partenza" in n["dubbi"]:
                n["nota"] = (n.get("nota", "") + " Orario letto male dal giornale: da verificare.").strip()
    log("navi dei terminal:", len(navi))
    return navi


# ---------------------------------------------------------------- AIS
def distanza_nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h)) / 1.852


def norm_nome(n):
    return re.sub(r"[^A-Z0-9]", "", (n or "").upper())


async def ascolta_ais(chiave_ais, secondi):
    import websockets
    pos, statici = {}, {}
    sub = {"APIKey": chiave_ais, "BoundingBoxes": [AIS_BOX],
           "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}
    fine = time.time() + secondi
    try:
        async with websockets.connect("wss://stream.aisstream.io/v0/stream", open_timeout=30) as ws:
            await ws.send(json.dumps(sub))
            while time.time() < fine:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(1, fine - time.time()))
                except asyncio.TimeoutError:
                    break
                m = json.loads(raw)
                if "error" in m:
                    log("aisstream:", m["error"]); break
                md = m.get("MetaData", {}); mmsi = str(md.get("MMSI", ""))
                if m.get("MessageType") == "PositionReport":
                    p = m["Message"]["PositionReport"]
                    pos[mmsi] = {"lat": p["Latitude"], "lon": p["Longitude"], "sog": p.get("Sog"), "cog": p.get("Cog"),
                                 "stato": p.get("NavigationalStatus"), "nome": (md.get("ShipName") or "").strip(),
                                 "quando": md.get("time_utc")}
                elif m.get("MessageType") == "ShipStaticData":
                    s = m["Message"]["ShipStaticData"]
                    statici[mmsi] = {"imo": str(s.get("ImoNumber") or ""), "nome": (s.get("Name") or "").strip(),
                                     "dest": (s.get("Destination") or "").strip(), "eta": s.get("Eta")}
    except Exception as e:
        log("AIS non raggiungibile:", type(e).__name__, e)
    log("AIS: posizioni", len(pos), "dati nave", len(statici))
    return pos, statici


def aggiungi_ais(dati, pos, statici, adesso):
    cache = dati.setdefault("mmsi", {})                 # imo/nome -> mmsi, resta tra un giro e l'altro
    for mmsi, s in statici.items():
        if s["imo"] and s["imo"] != "0":
            cache[s["imo"]] = mmsi
        if s["nome"]:
            cache["N:" + norm_nome(s["nome"])] = mmsi
    for mmsi, p in pos.items():
        if p["nome"]:
            cache.setdefault("N:" + norm_nome(p["nome"]), mmsi)
    for n in dati["navi"]:
        mmsi = cache.get(n.get("imo") or "-") or cache.get("N:" + norm_nome(n["nome"]))
        if not mmsi:
            continue
        n["mmsi"] = mmsi
        st = statici.get(mmsi)
        if st and st["nome"] and len(st["nome"]) >= 3:
            n["nome"] = st["nome"].upper()                    # nome ufficiale dall'AIS
        p = pos.get(mmsi)
        if not p:
            continue
        d = distanza_nm((p["lat"], p["lon"]), IMBOCCATURA)
        dentro = MOLI_BOX[0] <= p["lat"] <= MOLI_BOX[1] and MOLI_BOX[2] <= p["lon"] <= MOLI_BOX[3]
        sog = p["sog"] or 0
        a = {"lat": round(p["lat"], 5), "lon": round(p["lon"], 5), "vel": round(sog, 1),
             "dist_nm": round(d, 1), "quando": adesso.astimezone(ROMA).strftime("%H.%M")}
        if dentro and sog < 0.7:
            a["ormeggiata"] = True
        elif p["stato"] == 1 or (d < 8 and sog < 0.7):
            a["ancora"] = True                                # ferma in rada
        elif sog >= 2 and d < 300:
            arrivo = adesso + timedelta(hours=d / sog)
            if arrivo.astimezone(ROMA).date() == adesso.astimezone(ROMA).date():
                a["eta"] = arrivo.astimezone(ROMA).strftime("%H.%M")
        n["ais"] = a


# ---------------------------------------------------------------- principale
def main():
    import requests
    codice = os.environ.get("CODICE_MAPPA")
    if not codice:
        sys.exit("manca CODICE_MAPPA")
    k = chiave(codice)
    uscita = os.path.join(BASE, "navi.enc.json")
    dati = apri(k, uscita) or {}
    if dati.get("esempio"):
        dati = {"mmsi": dati.get("mmsi", {})}
    adesso = datetime.now(timezone.utc)
    forza = "--forza-giornale" in sys.argv

    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (moli-porto; aggiornamento previsioni)"
    pdf = None
    if "--pdf" in sys.argv:
        pdf = open(sys.argv[sys.argv.index("--pdf") + 1], "rb").read()
        data = sys.argv[sys.argv.index("--pdf") + 2] if len(sys.argv) > sys.argv.index("--pdf") + 2 else dati.get("data")
    else:
        data, campi = data_giornale_sul_sito(sess)
        log("giornale sul sito:", data, "| pubblicato:", dati.get("data"))
        if forza or data != dati.get("data") or not dati.get("navi"):
            pdf = scarica_giornale(sess, campi)
    if pdf:
        log("leggo il giornale del", data, len(pdf), "byte")
        navi = navi_dal_giornale(leggi_pdf(pdf), data)
        dati.update({"data": data, "navi": navi, "letto": adesso.isoformat(),
                     "fonte": "Capitaneria di Porto di Genova, previsioni del " + data})
    for n in dati.get("navi", []):
        n.pop("ais", None)

    chiave_ais = os.environ.get("AISSTREAM_KEY")
    if chiave_ais and dati.get("navi"):
        pos, statici = asyncio.run(ascolta_ais(chiave_ais, int(os.environ.get("AIS_SECONDI", "150"))))
        aggiungi_ais(dati, pos, statici, adesso)
        dati["ais"] = True
    else:
        dati["ais"] = False
    dati["aggiornato"] = datetime.now(timezone.utc).isoformat()
    dati["esempio"] = False
    chiudi(k, dati, uscita)
    log("scritto", uscita, "navi:", len(dati.get("navi", [])),
        "con AIS:", sum(1 for n in dati.get("navi", []) if n.get("ais")))


if __name__ == "__main__":
    main()

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
# zona in cui ascoltare l'AIS: Mar Ligure e dintorni
AIS_BOX = [[42.00, 6.00], [44.60, 11.00]]   # da Marsiglia all'Elba: le navi in arrivo si vedono ore prima


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
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}", campi, r.text


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


def blocchi_ordinati(righe):
    """Gruppi di righe in cui il giornale e' in ordine di orario.
    Giornale a sezioni: ARRIVI, poi PARTENZE, poi MOVIMENTI, ciascuna in ordine.
    Giornale "misto": tutta la zona in un'unica lista in ordine di orario."""
    zone, cur = [], []
    for i, r in enumerate(righe):
        if cur and righe[cur[-1]].get("zona") != r.get("zona"):
            zone.append(cur); cur = []
        cur.append(i)
    if cur:
        zone.append(cur)
    out = []
    for z in zone:
        cambi = sum(1 for a, b in zip(z, z[1:]) if righe[a]["sezione"] != righe[b]["sezione"])
        if cambi > 3:
            out.append(z)                         # misto
        else:
            parte = [z[0]]
            for a, b in zip(z, z[1:]):
                if righe[a]["sezione"] != righe[b]["sezione"]:
                    out.append(parte); parte = []
                parte.append(b)
            out.append(parte)
    return out


def correggi_orari(righe):
    """Si tiene la sequenza crescente piu' lunga; un orario fuori sequenza e'
    letto male: si prova la correzione di una cifra che lo rimette in ordine
    (resta segnato come dubbio). Un orario non letto si stima tra il
    precedente e il successivo, anche questo come dubbio."""
    for blocco in blocchi_ordinati(righe):
        idx = [i for i in blocco if minuti(righe[i]["ora"]) is not None]
        v = [minuti(righe[i]["ora"]) for i in idx]
        if len(v) >= 2:
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
                        if w is not None and w < 24 * 60 and prima <= w <= dopo and t[3:] in ("00", "15", "30", "31", "45", "59"):
                            candidati.add(t)
                scelta = sorted(candidati)[0] if len(candidati) == 1 else None
                log("orario fuori ordine:", righe[i]["nave"], ora, "->", scelta or "dubbio")
                righe[i]["ora_letta"] = ora
                righe[i]["ora"] = scelta or ora
                righe[i]["ora_dubbia"] = True
        # orari non letti: tra il precedente e il successivo
        for pos, i in enumerate(blocco):
            if righe[i]["ora"] or not righe[i].get("imo_ok"):
                continue
            prec = [righe[k]["ora"] for k in blocco[:pos] if minuti(righe[k]["ora"]) is not None]
            succ = [righe[k]["ora"] for k in blocco[pos + 1:] if minuti(righe[k]["ora"]) is not None]
            if prec and succ:
                righe[i]["ora"] = prec[-1]
                righe[i]["ora_dubbia"] = True
                righe[i]["nota_ora"] = "tra le " + prec[-1].replace(".", ":") + " e le " + succ[0].replace(".", ":")
                log("orario non letto:", righe[i]["nave"], "->", righe[i]["nota_ora"])


# mezzi che non sono navi da visitare: draghe, pontoni, motobarche, rimorchiatori,
# bettoline di rifornimento, gru, yacht
TIPI_ESCLUSI = ("HOPPER", "PONT", "MB", "M.B", "R.RE", "RIM", "CRANE", "BETT", "YACHT", "N.C", "BUNK", "SUPPLY", "TUG")
ZONE_BUONE = ("PRINCIPALE", "VARIAZIONI")


def da_a(accosto):
    """'DA RADA A CANEPA LEV. RAD.' -> ('RADA', 'CANEPA LEV. RAD.')"""
    m = re.match(r"\s*\W*D\s*A\s+(.*?)\s+A\s+(.*)$", (accosto or "").upper())
    return (m.group(1), m.group(2)) if m else (None, None)


def navi_dal_giornale(righe, data):
    import leggi_giornale as L
    correggi_orari(righe)
    ieri = (datetime.fromisoformat(data) - timedelta(days=1)).date().isoformat()
    per_nave = {}
    scartate = []
    for r in righe:
        zona = r.get("zona") or "PRINCIPALE"
        if zona not in ZONE_BUONE:
            continue                                   # Multedo, Voltri, mezzi della diga
        tipo = pulisci_tipo(r["tipo"])
        if any(t in re.sub(r"[^A-Z.]", "", (r["tipo"] or "").upper()) for t in TIPI_ESCLUSI):
            if r.get("dove"):
                scartate.append(pulisci_nome(r["nave"]) + " (" + (r["tipo"] or "").strip() + ")")
            continue
        loa = misura(r.get("loa"), 25, 420)
        if loa and loa < 50:
            continue                                   # mezzi piccoli di servizio
        dove = r.get("dove"); campo = None
        if r["sezione"] == "ARRIVI":
            campo = "arrivo"
        elif r["sezione"] == "PARTENZE":
            campo = "partenza"
        elif r["sezione"] == "MOVIMENTI":
            # spostamento: se va verso una nostra banchina e' un arrivo, se ne esce e' una partenza
            orig, dest = da_a(r["accosto"])
            d_dest = L.riconosci_accosto(dest) if dest else None
            d_orig = L.riconosci_accosto(orig) if orig else None
            if d_dest and d_dest["terminal"] in TERMINAL_MAPPA:
                dove, campo = d_dest, "arrivo"
            elif d_orig and d_orig["terminal"] in TERMINAL_MAPPA:
                dove, campo = d_orig, "partenza"
        if not campo or not dove or dove["terminal"] not in TERMINAL_MAPPA:
            continue
        nome = pulisci_nome(r["nave"])
        if not nome:
            continue
        chiave_n = r["imo"] if r["imo_ok"] else re.sub(r"[^A-Z]", "", nome)
        n = per_nave.setdefault(chiave_n, {"nome": nome, "imo": r["imo"] if r["imo_ok"] else "",
                                           "tipo": tipo, "loa": loa, "larg": r.get("larg"),
                                           "accosto": pulisci_accosto(r["accosto"], dove.get("molo")), "dove": dove,
                                           "arrivo": None, "partenza": None})
        if len(re.sub(r"[^A-Z]", "", nome)) > len(re.sub(r"[^A-Z]", "", n["nome"])) and r["imo_ok"]:
            n["nome"] = nome
        n["loa"] = n["loa"] or loa
        n["larg"] = misura(n["larg"], 5, 66) or misura(r.get("larg"), 5, 66)
        n["tipo"] = n["tipo"] or tipo
        if campo == "arrivo":
            n["dove"] = dove
            n["accosto"] = pulisci_accosto(r["accosto"], dove.get("molo"))
        n[campo] = r["ora"] or None
        if zona == "VARIAZIONI":
            n[campo + "_giorno"] = ieri                # cambio al giornale del giorno prima
        if r["sezione"] == "MOVIMENTI":
            n["spostamento"] = pulisci_accosto(r["accosto"])
        if r.get("ora_dubbia"):
            n.setdefault("dubbi", []).append(campo)
            if r.get("nota_ora"):
                n["nota"] = (n.get("nota", "") + " Orario non leggibile nel giornale: " + r["nota_ora"] + ".").strip()
        if not r["imo_ok"]:
            n.setdefault("dubbi", []).append("imo")
    navi = list(per_nave.values())
    for n in navi:
        n["dove_giornale"] = dict(n["dove"])
        if "dubbi" in n:
            n["dubbi"] = sorted(set(n["dubbi"]))
            if ("arrivo" in n["dubbi"] or "partenza" in n["dubbi"]) and "non leggibile" not in n.get("nota", ""):
                n["nota"] = (n.get("nota", "") + " Orario letto male dal giornale: da verificare.").strip()
    if scartate:
        log("escluse (mezzi di servizio):", ", ".join(scartate))
    log("navi dei terminal:", len(navi))
    return navi


# ---------------------------------------------------------------- AIS
KX = 111320 * math.cos(math.radians(44.405))   # metri per grado di longitudine qui
KY = 111320.0


def distanza_nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h)) / 1.852


def norm_nome(n):
    return re.sub(r"[^A-Z0-9]", "", (n or "").upper())


def ora_utc(s):
    """'2026-10-27 06:10:23.12 +0000 UTC' -> datetime UTC"""
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def tipo_ais(t):
    t = int(t or 0)
    if 60 <= t <= 69: return "PASS."
    if 70 <= t <= 79: return "CARGO"
    if 80 <= t <= 89: return "TANK"
    return ""


async def ascolta_ais(chiave_ais, secondi):
    import websockets
    pos, statici = {}, {}
    sub = {"APIKey": chiave_ais, "BoundingBoxes": [AIS_BOX],
           "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}
    fine = time.time() + secondi
    try:
        async with websockets.connect("wss://stream.aisstream.io/v0/stream", open_timeout=30, max_size=2 ** 22) as ws:
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
                    pos[mmsi] = {"lat": p["Latitude"], "lon": p["Longitude"], "sog": p.get("Sog") or 0,
                                 "cog": p.get("Cog"), "rotta": p.get("TrueHeading"),
                                 "stato": p.get("NavigationalStatus"), "nome": (md.get("ShipName") or "").strip(),
                                 "quando": ora_utc(md.get("time_utc")).isoformat()}
                elif m.get("MessageType") == "ShipStaticData":
                    s = m["Message"]["ShipStaticData"]
                    dim = s.get("Dimension") or {}
                    statici[mmsi] = {"imo": str(s.get("ImoNumber") or ""), "nome": (s.get("Name") or "").strip(),
                                     "dest": (s.get("Destination") or "").strip(), "tipo": s.get("Type"),
                                     "lung": (dim.get("A") or 0) + (dim.get("B") or 0),
                                     "larg": (dim.get("C") or 0) + (dim.get("D") or 0)}
    except Exception as e:
        log("AIS non raggiungibile:", type(e).__name__, e)
    log("AIS: posizioni", len(pos), "dati nave", len(statici))
    return pos, statici


def verso_genova(p):
    """La rotta della nave punta (entro 50 gradi) verso l'imboccatura?"""
    rotta = p.get("cog")
    if rotta is None or rotta >= 360:
        return True
    dy = (IMBOCCATURA[0] - p["lat"]) * KY
    dx = (IMBOCCATURA[1] - p["lon"]) * KX
    rilev = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    diff = abs((rotta - rilev + 180) % 360 - 180)
    return diff <= 50


def leggi_banchine(k):
    """Le banchine dei ponti stanno nei dati cifrati della mappa (window.BANCHINE)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    s = open(os.path.join(BASE, "dati.enc.js"), encoding="utf-8").read()
    D = json.loads(s[s.index("{"):s.rindex("}") + 1])
    testo = AESGCM(k).decrypt(base64.b64decode(D["iv"]), base64.b64decode(D["ct"]), None).decode("utf-8")
    m = re.search(r"window\.BANCHINE\s*=\s*(\{.*?\});\s*$", testo, re.S | re.M)
    return json.loads(m.group(1)) if m else {}


def alla_banchina(lat, lon, banchine):
    """Se il punto e' a ridosso di una banchina dei nostri moli, dice quale:
    (molo, lato, terminal, metri dalla radice lungo la banchina)."""
    meglio = None
    for molo, b in banchine.items():
        for lato in ("ponente", "levante", "testata"):
            l = b.get(lato)
            if not l:
                continue
            ax, ay = l[0][1] * KX, l[0][0] * KY
            bx, by = l[1][1] * KX, l[1][0] * KY
            px, py = lon * KX, lat * KY
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = max(-0.1, min(1.1, ((px - ax) * dx + (py - ay) * dy) / L2))
            qx, qy = ax + t * dx, ay + t * dy
            d = math.hypot(px - qx, py - qy)
            ac = (b.get("acqua") or {}).get(lato) or [0, 0]
            verso = (px - qx) * ac[1] + (py - qy) * ac[0]        # >0: lato acqua
            if d < 75 and verso > -15 and (meglio is None or d < meglio[0]):
                meglio = (d, molo, lato, b["terminal"], round(t * math.sqrt(L2)))
    return None if meglio is None else {"molo": meglio[1], "lato": meglio[2], "terminal": meglio[3], "s_m": meglio[4]}


def chiave_nave(n):
    return n["imo"] if re.fullmatch(r"\d{7}", n.get("imo") or "") else "N:" + norm_nome(n.get("nome"))


def simili(a, b):
    a, b = norm_nome(a), norm_nome(b)
    return bool(a) and bool(b) and (a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.86)


def aggiorna_registro(dati, pos, statici, adesso):
    """Tiene in memoria tra un giro e l'altro le ultime posizioni e i dati delle navi."""
    reg = dati.setdefault("registro", {}); sta = dati.setdefault("statici", {})
    for mmsi, s in statici.items():
        sta[mmsi] = {**sta.get(mmsi, {}), **{k: v for k, v in s.items() if v}}
    for mmsi, p in pos.items():
        reg[mmsi] = p
    limite = adesso - timedelta(days=3)
    for mmsi in [m for m, p in reg.items() if datetime.fromisoformat(p["quando"]) < limite]:
        reg.pop(mmsi, None)


def trova_mmsi(n, dati):
    if n.get("mmsi"):
        return n["mmsi"]
    sta, reg = dati.get("statici", {}), dati.get("registro", {})
    for mmsi, s in sta.items():
        if n.get("imo") and s.get("imo") == n["imo"]:
            return mmsi
    for mmsi, s in sta.items():
        if simili(s.get("nome"), n["nome"]):
            return mmsi
    for mmsi, p in reg.items():
        if simili(p.get("nome"), n["nome"]):
            return mmsi
    return None


def chiave_posto(dg):
    """ponte e lato come scritti nel giornale, es. 'RONCO|testata', 'CANEPA|levante'"""
    if not dg or not dg.get("molo"):
        return None
    return str(dg["molo"]).upper() + "|" + (dg.get("lato") or dg.get("punto") or "")


def impara(dati, dg, dove):
    """Taratura dei nomi delle banchine sui dati reali: ogni volta che una nave
    del giornale e' vista ferma dall'AIS, si annota dove si trova davvero.
    Cosi' si scopre da soli, per esempio, quale lato di Messina e' Ronco e quale
    Canepa, e come si chiama il braccio est delle Rinfuse."""
    k = chiave_posto(dg)
    if not k:
        return
    v = dove["molo"] + "|" + dove["lato"]
    t = dati.setdefault("taratura", {}).setdefault(k, {})
    oggi = datetime.now(ROMA).date().isoformat()
    vist = dati.setdefault("taratura_giorni", {})
    sig = k + ">" + v + ">" + oggi
    if sig in vist:
        return                                   # una sola osservazione al giorno per nave e posto
    vist[sig] = 1
    t[v] = t.get(v, 0) + 1


def posto_imparato(dati, dg):
    """Se la taratura e' chiara (almeno 2 osservazioni, 3 su 4 concordi) dice
    dove sta davvero il posto scritto nel giornale."""
    t = (dati.get("taratura") or {}).get(chiave_posto(dg) or "-")
    if not t:
        return None
    v, c = max(t.items(), key=lambda x: x[1])
    if c >= 2 and c / sum(t.values()) >= 0.75:
        molo, lato = v.split("|")
        return molo, lato
    return None


def applica_taratura(dati):
    for n in dati.get("navi", []):
        if (n.get("ais") or {}).get("ormeggiata"):
            continue                              # gia' disegnata dove si trova
        pi = posto_imparato(dati, n.get("dove_giornale"))
        if pi:
            molo, lato = pi
            n["dove"] = {**n["dove"], "molo": molo,
                         "lato": None if lato == "testata" else lato,
                         "punto": "testata" if lato == "testata" else n["dove"].get("punto")}
            n["posto_tarato"] = True


def riassunto_taratura(dati):
    for k, t in sorted((dati.get("taratura") or {}).items()):
        log("taratura:", k, "->", ", ".join(f"{v} x{c}" for v, c in sorted(t.items(), key=lambda x: -x[1])))


def aggiorna_presenze(dati, banchine, adesso):
    """Mette la posizione AIS sulle navi dell'elenco e aggiunge quelle che l'AIS
    vede ferme alle nostre banchine anche se il giornale non le nomina."""
    reg, sta = dati.get("registro", {}), dati.get("statici", {})
    oggi = adesso.astimezone(ROMA).date().isoformat()
    usati = set()
    for n in dati["navi"]:
        n.pop("ais", None)
        mmsi = trova_mmsi(n, dati)
        if not mmsi:
            continue
        n["mmsi"] = mmsi; usati.add(mmsi)
        s = sta.get(mmsi, {})
        if s.get("nome") and len(s["nome"]) >= 3:
            n["nome"] = s["nome"].upper()                     # nome ufficiale dall'AIS
        if s.get("imo") and re.fullmatch(r"\d{7}", s["imo"]):
            n["imo"] = s["imo"]
        if not n.get("loa") and s.get("lung"):
            n["loa"], n["larg"] = s["lung"], s.get("larg") or None
        p = reg.get(mmsi)
        if not p:
            continue
        quando = datetime.fromisoformat(p["quando"])
        if adesso - quando > timedelta(hours=3):
            continue                                          # posizione vecchia: non si usa
        d = distanza_nm((p["lat"], p["lon"]), IMBOCCATURA)
        a = {"lat": round(p["lat"], 5), "lon": round(p["lon"], 5), "vel": round(p["sog"], 1),
             "dist_nm": round(d, 1), "quando": quando.astimezone(ROMA).strftime("%H.%M")}
        dove = alla_banchina(p["lat"], p["lon"], banchine) if p["sog"] < 0.7 else None
        if dove:
            impara(dati, n.get("dove_giornale"), dove)
            a["ormeggiata"] = True; a["s_m"] = dove["s_m"]
            n["dove"] = {**n.get("dove", {}), "terminal": dove["terminal"], "molo": dove["molo"],
                         "lato": dove["lato"] if dove["lato"] != "testata" else None,
                         "punto": "testata" if dove["lato"] == "testata" else n.get("dove", {}).get("punto"),
                         "in_rada": False}
            n["visto_banchina"] = quando.isoformat()
            n.setdefault("in_porto_dal", oggi)
        elif p["stato"] == 1 or (d < 8 and p["sog"] < 0.7):
            a["ancora"] = True
        elif p["sog"] >= 2:
            if MOLI_BOX[0] <= p["lat"] <= MOLI_BOX[1] and MOLI_BOX[2] <= p["lon"] <= MOLI_BOX[3]:
                a["manovra"] = True
            elif verso_genova(p) and d < 900:
                # in rotta verso Genova: ora stimata d'arrivo (+15% per gli ultimi rallentamenti)
                arrivo = quando + timedelta(hours=d / p["sog"] * 1.15)
                a["eta_iso"] = arrivo.isoformat()
                a["eta"] = arrivo.astimezone(ROMA).strftime("%H.%M")
            elif d > 60:
                a["lontana"] = True
        elif d > 60:
            a["lontana"] = True
        n["ais"] = a
    # navi ferme alle nostre banchine che non sono nell'elenco
    for mmsi, p in reg.items():
        if mmsi in usati or p["sog"] >= 0.7:
            continue
        quando = datetime.fromisoformat(p["quando"])
        if adesso - quando > timedelta(hours=6):
            continue
        dove = alla_banchina(p["lat"], p["lon"], banchine)
        if not dove:
            continue
        s = sta.get(mmsi, {})
        # solo navi vere (passeggeri, carico, cisterne, lunghe almeno 60 m): niente rimorchiatori e barche
        # (le bettoline di rifornimento e i mezzi di servizio sono sotto i 100 m)
        if not s or not tipo_ais(s.get("tipo")) or (s.get("lung") or 0) < 100:
            continue
        nome = (s.get("nome") or p.get("nome") or "").upper()
        if not nome:
            continue
        log("in banchina secondo l'AIS:", nome, dove["molo"], dove["lato"])
        dati["navi"].append({
            "nome": nome, "imo": s.get("imo") if re.fullmatch(r"\d{7}", s.get("imo") or "") else "",
            "mmsi": mmsi, "tipo": tipo_ais(s.get("tipo")), "loa": s.get("lung") or None, "larg": s.get("larg") or None,
            "accosto": dove["molo"] + " " + ("TESTATA" if dove["lato"] == "testata" else dove["lato"][:3].upper() + "."),
            "dove": {"terminal": dove["terminal"], "molo": dove["molo"],
                     "lato": None if dove["lato"] == "testata" else dove["lato"],
                     "punto": "testata" if dove["lato"] == "testata" else None, "in_rada": False},
            "arrivo": None, "partenza": None, "giorno": oggi, "in_porto_dal": oggi,
            "fonti": ["ais"], "visto_banchina": quando.isoformat(),
            "ais": {"lat": round(p["lat"], 5), "lon": round(p["lon"], 5), "vel": round(p["sog"], 1),
                    "dist_nm": round(distanza_nm((p["lat"], p["lon"]), IMBOCCATURA), 1),
                    "quando": quando.astimezone(ROMA).strftime("%H.%M"), "ormeggiata": True, "s_m": dove["s_m"]},
        })


# ---------------------------------------------------------------- memoria tra i giorni
def minuti_ora(o):
    """orario del giornale -> minuti (per le partenze vaghe si prende la fine)"""
    o = (o or "").upper()
    m = re.fullmatch(r"(\d{2})\.(\d{2})", o)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    if "ALBA" in o: return 450
    if re.search(r"A\.?M", o): return 720
    if re.search(r"P\.?M", o): return 1439
    return None


def istante(giorno, o):
    v = minuti_ora(o)
    if v is None or not giorno:
        return None
    return datetime.fromisoformat(giorno).replace(tzinfo=ROMA) + timedelta(minutes=v)


def unisci_giornale(dati, nuove, data):
    """Il giornale nuovo aggiorna l'elenco. Le navi di prima che non hanno
    ancora una partenza passata restano: sono in porto dai giorni scorsi."""
    vecchie = {chiave_nave(n): n for n in dati.get("navi", [])}
    risultato = []
    for n in nuove:
        n["giorno"] = data
        n["fonti"] = ["giornale"]
        v = vecchie.pop(chiave_nave(n), None)
        if v is None:
            v = next((x for k, x in list(vecchie.items()) if simili(x["nome"], n["nome"])), None)
            if v is not None:
                vecchie.pop(chiave_nave(v), None)
        if v is not None:
            for campo in ("mmsi", "visto_banchina") + (() if n.get("arrivo") else ("in_porto_dal",)):
                if v.get(campo):
                    n.setdefault(campo, v[campo])
            if v.get("loa") and not n.get("loa"):
                n["loa"] = v["loa"]
            if not n.get("arrivo") and (v.get("in_porto_dal") or v.get("arrivo")):
                n.setdefault("in_porto_dal", v.get("in_porto_dal") or v.get("giorno"))
        risultato.append(n)
    for v in vecchie.values():
        if v.get("giorno") and v["giorno"] < data:
            part = istante(v.get("partenza_giorno") or v["giorno"], v.get("partenza"))
            if part is not None:
                continue                         # aveva la partenza in un giorno passato: se ne occupa la pulizia
            # arrivata (o gia' in porto) e senza partenza: resta, ora e' "in porto dal ..."
            if v.get("arrivo") or v.get("in_porto_dal"):
                v["in_porto_dal"] = v.get("in_porto_dal") or v["giorno"]
                v["arrivo"] = None
                v.pop("arrivo_giorno", None); v.pop("partenza_giorno", None)
                v["giorno"] = data
                v["fonti"] = sorted(set(v.get("fonti", []) + ["memoria"]))
                risultato.append(v)
        else:
            risultato.append(v)
    dati["navi"] = risultato


def pulizia(dati, adesso):
    """Toglie le navi partite: partenza passata (salvo che l'AIS la veda ancora
    ferma in banchina dopo l'orario), AIS che la vede uscire, o nessuna conferma
    da troppo tempo."""
    tenute = []
    for n in dati.get("navi", []):
        visto = datetime.fromisoformat(n["visto_banchina"]) if n.get("visto_banchina") else None
        part = istante(n.get("partenza_giorno") or n.get("giorno"), n.get("partenza"))
        a = n.get("ais") or {}
        if n.get("conferma", {}).get("tipo") == "partenza" and n["conferma"].get("quando"):
            pc = datetime.fromisoformat(n["conferma"]["quando"])
            part = pc if part is None or pc > part else part
        if part is not None and adesso > part + timedelta(minutes=90):
            if visto and visto > part + timedelta(minutes=30) and a.get("ormeggiata"):
                n["ritardo"] = True                       # doveva partire, ma e' ancora li'
            else:
                log("partita:", n["nome"]); continue
        if visto and not a.get("ormeggiata") and a.get("vel", 0) >= 2 and a.get("dist_nm", 0) > 1 and not n.get("arrivo"):
            log("uscita dal porto (AIS):", n["nome"]); continue
        fonti = n.get("fonti", ["giornale"])
        if "giornale" not in fonti and (n.get("loa") or 0) < 100:
            log("tolta (mezzo di servizio visto solo dall'AIS):", n["nome"]); continue
        if "giornale" not in fonti or "memoria" in fonti or n.get("giorno", "") < adesso.astimezone(ROMA).date().isoformat():
            # tenuta solo dalla memoria o dall'AIS: serve una conferma recente
            if visto and adesso - visto > timedelta(hours=30):
                log("non piu' vista in banchina:", n["nome"]); continue
            dal = n.get("in_porto_dal")
            if not visto and dal and (adesso.astimezone(ROMA).date() - datetime.fromisoformat(dal).date()).days > 3:
                log("in memoria da troppi giorni senza conferme:", n["nome"]); continue
            n["da_confermare"] = not visto
        tenute.append(n)
    dati["navi"] = tenute


# ---------------------------------------------------------------- tabella del sito
def leggi_tabelle_sito(html_pagina):
    """Prossimi arrivi e partenze nella home del sito (con la conferma VHF/CONF)."""
    import html as H
    out = []
    for tipo, ident in (("arrivo", "grdArrivi_ctl00"), ("partenza", "grdPartenze_ctl00")):
        m = re.search(r'<table[^>]*id="[^"]*' + ident + r'".*?</table>', html_pagina, re.S)
        if not m:
            continue
        for riga in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S):
            celle = [H.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", riga, re.S)]
            if len(celle) >= 7 and re.fullmatch(r"\d{2}/\d{2}/\d{4}", celle[4] or ""):
                g, mm, a = celle[4].split("/")
                out.append({"tipo": tipo, "nave": celle[1], "merce": celle[2], "ormeggio": celle[3],
                            "giorno": f"{a}-{mm}-{g}", "ora": celle[5], "conf": celle[6]})
    return out


def applica_conferme(dati, righe):
    for r in righe:
        for n in dati.get("navi", []):
            if simili(n["nome"], r["nave"]):
                q = istante(r["giorno"], r["ora"])
                try:
                    import leggi_giornale as L
                    dg = L.riconosci_accosto(r["ormeggio"])
                    if dg and not n.get("dove_giornale"):
                        n["dove_giornale"] = {"terminal": dg["terminal"], "molo": dg["molo"], "lato": dg["lato"], "punto": dg["punto"]}
                except Exception:
                    pass
                n["conferma"] = {"tipo": r["tipo"], "ora": r["ora"], "giorno": r["giorno"], "stato": r["conf"],
                                 "ormeggio": r["ormeggio"], "quando": q.isoformat() if q else None}
                log("conferma dal sito:", n["nome"], r["tipo"], r["ora"], r["conf"])


# ---------------------------------------------------------------- principale
def main():
    import requests
    codice = os.environ.get("CODICE_MAPPA")
    if not codice:
        sys.exit("manca CODICE_MAPPA")
    k = chiave(codice)
    banchine = leggi_banchine(k)
    uscita = os.path.join(BASE, "navi.enc.json")
    dati = apri(k, uscita) or {}
    if dati.get("esempio"):
        dati = {}
    dati.setdefault("navi", [])
    adesso = datetime.now(timezone.utc)
    forza = "--forza-giornale" in sys.argv

    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (moli-porto; aggiornamento previsioni)"
    pdf, pagina, data = None, "", dati.get("data")
    if "--pdf" in sys.argv:
        i = sys.argv.index("--pdf")
        pdf = open(sys.argv[i + 1], "rb").read()
        data = sys.argv[i + 2] if len(sys.argv) > i + 2 else data
    else:
        try:
            data_sito, campi, pagina = data_giornale_sul_sito(sess)
            log("giornale sul sito:", data_sito, "| gia' letto:", dati.get("data"))
            if forza or data_sito != dati.get("data"):
                pdf = scarica_giornale(sess, campi); data = data_sito
        except Exception as e:
            log("sito non raggiungibile:", type(e).__name__, e)
    if pdf:
        log("leggo il giornale del", data, len(pdf), "byte")
        unisci_giornale(dati, navi_dal_giornale(leggi_pdf(pdf), data), data)
        dati.update({"data": data, "letto": adesso.isoformat(),
                     "fonte": "Capitaneria di Porto di Genova, previsioni del " + data})
    if pagina:
        try:
            applica_conferme(dati, leggi_tabelle_sito(pagina))
        except Exception as e:
            log("tabella del sito non letta:", e)

    chiave_ais = os.environ.get("AISSTREAM_KEY")
    if chiave_ais:
        ora = adesso.astimezone(ROMA)
        # il primo giro del mattino ascolta di piu': le navi ferme trasmettono ogni 3 minuti
        secondi = int(os.environ.get("AIS_SECONDI") or (360 if ora.hour == 7 and ora.minute < 30 else 180))
        pos, statici = asyncio.run(ascolta_ais(chiave_ais, secondi))
        aggiorna_registro(dati, pos, statici, adesso)
        aggiorna_presenze(dati, banchine, adesso)
        dati["ais"] = bool(pos)
    else:
        dati["ais"] = False
    applica_taratura(dati)
    riassunto_taratura(dati)
    # la memoria dei giorni di taratura non serve oltre una settimana
    lim = (adesso.astimezone(ROMA).date() - timedelta(days=7)).isoformat()
    dati["taratura_giorni"] = {k: 1 for k in dati.get("taratura_giorni", {}) if k.rsplit(">", 1)[-1] >= lim}
    pulizia(dati, adesso)
    dati["aggiornato"] = datetime.now(timezone.utc).isoformat()
    dati["esempio"] = False
    chiudi(k, dati, uscita)
    log("scritto", uscita, "navi:", len(dati.get("navi", [])),
        "con AIS:", sum(1 for n in dati.get("navi", []) if n.get("ais")),
        "in banchina:", sum(1 for n in dati.get("navi", []) if (n.get("ais") or {}).get("ormeggiata")))


if __name__ == "__main__":
    main()

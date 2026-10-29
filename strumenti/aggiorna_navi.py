#!/usr/bin/env python3
"""Aggiorna le navi del giorno per l'app (gira in GitHub Actions ogni 15 minuti).

1. Apre la memoria del giro prima (stato.enc.json; la prima volta navi.enc.json) con il codice della mappa.
2. Se sul sito shipsinformationgenova.com c'e' un giornale della Capitaneria
   piu' recente, lo scarica, lo legge (OCR) e tiene arrivi e partenze dei
   terminal della mappa.
3. Ascolta aisstream per qualche minuto e aggiunge la posizione reale delle
   navi: ormeggiata, in rada, in arrivo (con l'ora stimata).
4. Cifra di nuovo: in navi.enc.json solo quello che mostra l'app (e' il file che scarica il telefono),
   in stato.enc.json tutta la memoria del programma (registro AIS, esclusioni, taratura...).

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
VALIDITA_POSIZIONE = timedelta(hours=3)     # quanto vale l'ultima posizione di una nave in movimento
VALIDITA_FERMA = timedelta(hours=6)         # ... e di una nave ferma alla banchina
DISTANZA_ATTRACCO = 400                     # metri: in manovra cosi' vicino a una banchina del suo terminal = sta attraccando
ATTESA_ATTRACCO = timedelta(minutes=20)     # dopo quanto senza segnale si dice "probabilmente in banchina"
# Regola dell'assenza. Una nave ferma trasmette ogni 3 minuti: se l'AIS sente le altre navi dei moli ma non lei
# per due giri di fila (con buona ricezione, vedi qualita_ricezione), non e' piu' li' (o non la sentiamo piu'):
# da confermare, e se e' solo AIS esce dall'elenco.
SEGNALE_RECENTE = timedelta(minutes=40)     # una nave solo-AIS entra nell'elenco solo con un segnale piu' fresco di cosi'
# Giri di fila senza sentirla (con buona ricezione) prima di 'segnale perso': almeno 3 (45 minuti di giorno), fino a 6 per
# le navi che l'AIS di solito sente male (vedi assenze_necessarie). Con 2 (fino al 28/10 sera) il 28/10 tra le 14:33 e le 16:58
# GOYNUK, CHENNAI EXPRESS ed ELISABETH RUSS erano passate a 'segnale perso' pur essendo in banchina (risentite al giro dopo).
ASSENZE_MIN = 3
ASSENZE_TETTO = 6
ERRORE_AMMESSO = 0.02                       # probabilita' accettata di dire 'segnale perso' a una nave che c'e'
ASSENZE_MAX = ASSENZE_MIN                   # (nome di prima)
RICEZIONE_MIN = 5                           # navi ferme nei moli (ultime 2 ore) necessarie per giudicare la ricezione
RICEZIONE_QUOTA = 0.6                       # quota di quelle sentita in questo giro per dire che la ricezione e' buona
FINESTRA_RICEZIONE = timedelta(hours=2)      # le navi ferme nei moli sentite da meno di cosi' fanno da riferimento
FINESTRA_RICEZIONE_MAX = timedelta(hours=8)  # ... e di notte, con i giri distanti, si allarga ma non oltre questo
ASCOLTO_NOTTE = 1200                         # secondi di ascolto AIS nei giri della notte (22-7): sono pochi, si ascolta di piu'
SEGUITE = ("636092930",)                    # MMSI di cui ogni giro scrive la storia (diagnosi: CHENNAI EXPRESS)
# zona in cui ascoltare l'AIS: Mar Ligure e dintorni
AIS_BOX = [[42.00, 6.00], [44.60, 11.00]]   # da Marsiglia all'Elba: le navi in arrivo si vedono ore prima


RIEPILOGO = []


def log(*a):
    riga = " ".join([datetime.now(ROMA).strftime("%H:%M:%S")] + [str(x) for x in a])
    print(riga, flush=True)
    RIEPILOGO.append(riga)


# GitHub taglia ogni annotazione a 4096 caratteri: oltre, si perdeva la fine del riepilogo (elenco delle navi,
# diagnosi). Se il riepilogo e' troppo lungo si tolgono prima le righe meno importanti.
LIMITE_ANNOTAZIONE = 4000
RIGHE_SACRIFICABILI = ("taratura:", "tipo non riconosciuto:", "orario non letto:", "orario fuori ordine:", "bulk nel giornale:",
                       "conferma dal sito:", "pagina p-", "zone del giornale:", "note sul sito:", "giornali gia' letti:",
                       "tentativo ")
# Righe che segnalano un problema: il riepilogo diventa un avviso (warning). GitHub tiene 10 annotazioni per tipo:
# cosi' i giri con problemi non si perdono dietro ai giri normali.
RIGHE_PROBLEMA = ("ERRORE", "ATTENZIONE", "non riuscito", "non raggiungibile", "non scaricato", "non letto, si riprova",
                  "sparita dall'AIS", "abbinamento AIS sbagliato", "diagnosi, ferma nei moli")


def testo_riepilogo(righe, limite=LIMITE_ANNOTAZIONE):
    righe = list(righe)
    def lungo(r):
        return len("\n".join(r))
    tolte = 0
    for chiave in RIGHE_SACRIFICABILI:
        if lungo(righe) <= limite:
            break
        prima = len(righe)
        righe = [r for r in righe if chiave not in r[:40]]
        tolte += prima - len(righe)
    if lungo(righe) > limite:
        righe = [r if len(r) <= 220 else r[:217] + "..." for r in righe]
    nota = "(%d righe meno importanti tolte: sono nel log del lavoro)" % tolte if tolte else ""
    testo = "\n".join(righe + ([nota] if nota else []))
    if len(testo) > limite:
        testo = "...\n" + testo[-(limite - 4):]                 # si tiene la fine: elenco delle navi e conteggi
    return testo


def riepilogo_actions():
    """In GitHub Actions il riepilogo del giro diventa un'annotazione leggibile anche
    senza scaricare i log (utile per controllare da fuori che il giro sia andato bene)."""
    try:
        if os.environ.get("GITHUB_ACTIONS") and RIEPILOGO:
            problema = any(k in r for r in RIEPILOGO for k in RIGHE_PROBLEMA)
            testo = testo_riepilogo(RIEPILOGO)
            testo = testo.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print("::%s title=Riepilogo del giro::%s" % ("warning" if problema else "notice", testo), flush=True)
    except Exception as e:
        print("riepilogo non scritto:", type(e).__name__, e, flush=True)


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
    """Cifra e scrive. Si scrive in un file accanto e poi lo si mette al posto giusto in un colpo solo: se il giro
    si interrompe a meta' scrittura resta il file di prima, mai uno rovinato."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    ct = AESGCM(k).encrypt(iv, json.dumps(dati, ensure_ascii=False).encode("utf-8"), None)
    provvisorio = percorso + ".nuovo"
    with open(provvisorio, "w", encoding="utf-8") as f:
        json.dump({"v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}, f)
    os.replace(provvisorio, percorso)


# ---------------------------------------------------------------- giornale
def campi_nascosti(html):
    return {m.group(1): m.group(2) for m in
            re.finditer(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html)}


BOTTONE_NOTA = "ctl00$ctl00$corpo$btnCap"


def con_tentativi(f, tentativi=3, pausa=10):
    """Il sito ogni tanto non risponde o restituisce una pagina a meta':
    si riprova qualche volta prima di arrendersi."""
    for i in range(tentativi):
        try:
            return f()
        except Exception as e:
            log(f"tentativo {i + 1}/{tentativi} fallito:", type(e).__name__, e)
            if i + 1 == tentativi:
                raise
            time.sleep(pausa * (i + 1))


def note_sul_sito(sess):
    """Tutte le note della Capitaneria che la home elenca con il pulsante di download,
    dalla piu' recente: [(data 'AAAA-MM-GG', nome del pulsante)], e la pagina."""
    r = sess.get(SITO + "/ITA/Default.aspx", timeout=40)
    r.raise_for_status()
    date = [f"{a}-{m}-{g}" for g, m, a in re.findall(r"Note della Capitaneria del (\d{2})/(\d{2})/(\d{4})", r.text)]
    if not date:
        raise RuntimeError("data del giornale non trovata nella pagina")
    pulsanti = sorted({int(n) for n in re.findall(r'name="' + re.escape(BOTTONE_NOTA) + r'(\d+)"', r.text)})
    # la nota piu' recente e' la prima della lista e ha il primo pulsante, la seconda il secondo, ecc.
    if len(pulsanti) >= len(date) and date == sorted(set(date), reverse=True):
        note = [(d, BOTTONE_NOTA + str(pulsanti[i])) for i, d in enumerate(date)]
    else:
        log("attenzione: date", date, "e pulsanti", pulsanti, "non si abbinano: uso solo la prima nota")
        note = [(date[0], BOTTONE_NOTA + "1")]
    log("note sul sito:", ", ".join(f"{d} ({b[-1]})" for d, b in note))
    return note, r.text


def scarica_giornale(sess, bottone):
    """Scarica il PDF della nota associata al pulsante. Ogni scarico riparte dalla
    home, cosi' i campi nascosti del modulo sono sempre freschi."""
    import html as H
    r = sess.get(SITO + "/ITA/Default.aspx", timeout=40)
    r.raise_for_status()
    campi = {k: H.unescape(v) for k, v in campi_nascosti(r.text).items()}
    campi["__EVENTTARGET"] = ""; campi["__EVENTARGUMENT"] = ""
    campi[bottone] = ""
    r = sess.post(SITO + "/ITA/Default.aspx", data=campi, timeout=60)
    r.raise_for_status()
    p = sess.get(SITO + "/frmPdf.aspx", timeout=60)
    p.raise_for_status()
    if not p.content.startswith(b"%PDF"):
        raise RuntimeError("il sito non ha restituito un PDF")
    if len(p.content) < 5000:
        raise RuntimeError(f"PDF troppo piccolo ({len(p.content)} byte): scarico incompleto")
    return p.content


def leggi_pdf(pdf_bytes):
    """Righe del giornale e date lette nei titoli delle pagine (solo per controllo)."""
    import leggi_giornale as L
    righe, date_titoli = [], []
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "g.pdf")
        open(f, "wb").write(pdf_bytes)
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", f, os.path.join(d, "p")], check=True)
        pagine = sorted(x for x in os.listdir(d) if x.endswith(".png"))
        sezione, zona = None, "PRINCIPALE"
        for pg in pagine:
            # la zona (es. MEZZI DIGA) puo' continuare sulla pagina dopo: si porta dietro
            r = L.leggi(os.path.join(d, pg), zona)
            zona = r.get("zona_finale") or zona
            date_titoli += r.get("date_titoli") or []
            log("pagina", pg, "righe", len(r.get("righe", [])), "angolo", r.get("angolo"), "zona a fine pagina:", zona, r.get("errore") or "")
            for x in r.get("righe", []):
                if x["sezione"] is None and sezione:
                    x["sezione"] = sezione
                sezione = x["sezione"] or sezione
                righe.append(x)
            # dopo i mezzi diga non serve continuare
            if zona == "MEZZI DIGA":
                break
    return righe, date_titoli


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
# Le sigle brevi valgono solo all'inizio del tipo: altrimenti "N.C" si trova dentro "GEN.CARGO" e
# "MB" dentro "COMBI", e si scarterebbero navi vere.
TIPI_ESCLUSI_BREVI = ("MB", "M.B", "R.RE", "RIM", "N.C", "TUG")


def e_mezzo_di_servizio(tipo):
    s = re.sub(r"[^A-Z.]", "", (tipo or "").upper())
    return any(t in s for t in TIPI_ESCLUSI if t not in TIPI_ESCLUSI_BREVI) or any(s.startswith(t) for t in TIPI_ESCLUSI_BREVI)
ZONE_BUONE = ("PRINCIPALE", "VARIAZIONI")


def da_a(accosto):
    """'DA RADA A CANEPA LEV. RAD.' -> ('RADA', 'CANEPA LEV. RAD.')"""
    m = re.match(r"\s*\W*D\s*A\s+(.*?)\s+A\s+(.*)$", (accosto or "").upper())
    return (m.group(1), m.group(2)) if m else (None, None)


# ---------------------------------------------------------------- navi che non si visitano
# Oltre ai mezzi di servizio e ai mezzi della diga (esclusi dalla zona del giornale):
# - le navi chemical (chimichiere);
# - le RO/RO che portano passeggeri (ro-pax, traghetti).
# Regola di Mele del 29/10: le navi che portano merci si visitano anche se portano passeggeri (ro-ro con passeggeri,
# ro-pax, traghetti con camion e auto); non si visitano solo quelle che portano soltanto passeggeri (passeggeri, crociere).
NOTA_RORO = "Ro-ro: né il giornale né l'AIS dicono ancora se porta passeggeri, da controllare."     # (non piu' usata: si toglie)
NOTA_PAX_AIS = ("L'AIS la dichiara nave passeggeri: a questi terminal di solito è un traghetto con merci (ro-pax), che si visita. "
                "Se porta solo passeggeri non si visita.")
# Cisterne (TANK) escluse per ora, in attesa di conferma di Mele: per rimetterle in elenco
# basta portare questo interruttore a False.
ESCLUDI_CISTERNE = True
# Lunghezze minime: sotto si tratta di mezzi di servizio. Ai Rinfuse arrivano pero' anche navi piccole
# (rottami, gomma, carbone), che si visitano: li' si scartano solo le misure impossibili per una nave.
LUNGHEZZA_MINIMA = 50
LUNGHEZZA_MINIMA_RINFUSE = 25
# Per le navi viste solo dall'AIS (il giornale non le nomina) serve piu' cautela, per non prendere barche di servizio
LUNGHEZZA_MINIMA_AIS = 100
LUNGHEZZA_MINIMA_AIS_RINFUSE = 50


def lunghezza_minima(terminal):
    return LUNGHEZZA_MINIMA_RINFUSE if terminal == "rinfuse" else LUNGHEZZA_MINIMA


def lunghezza_minima_ais(terminal, tipo):
    """Per l'AIS un cargo ai Rinfuse puo' essere piccolo; tutte le altre devono essere navi vere."""
    return LUNGHEZZA_MINIMA_AIS_RINFUSE if terminal == "rinfuse" and tipo == "CARGO" else LUNGHEZZA_MINIMA_AIS


def lettere(t):
    return re.sub(r"[^A-Z]", "", (t or "").upper())


def e_chimichiera(tipo):
    """Tipo del giornale (letto male o no): CHIM., CHEM., CHEMICAL, OIL/CHEM..."""
    return tipo == "CHIM." or bool(re.search(r"CHEM|CHIM", lettere(tipo)))


def e_cisterna(tipo):
    """TANK, TANKER... (le chemical sono gia' escluse a parte)."""
    return tipo == "TANK" or "TANK" in lettere(tipo)


def e_passeggeri(tipo):
    """Navi passeggeri e da crociera (PASS., CRUISE): portano solo passeggeri, non si visitano.
    Le ro-ro con passeggeri (RO/PAX, RO/RO PASS.) invece si visitano (Mele, 29/10)."""
    t = lettere(tipo)
    return t.startswith("PASS") or "CRUISE" in t or t.startswith("CROCIER")


def ro_ro_pax_dal_testo(tipo):
    """Il giornale scrive a volte RO/PAX o RO/RO PASS.: e' un traghetto."""
    t = lettere(tipo)
    return "PAX" in t or ("RO" in t and "PASS" in t)


def tipo_ais_numero(s):
    try:
        return int((s or {}).get("tipo") or 0)
    except (TypeError, ValueError):
        return 0


# Navi da non mettere mai, indicate da Mele (IMO o MMSI, e il nome per quando mancano). Le draghe e i mezzi di
# lavoro a volte si dichiarano all'AIS come "cargo" e, ferme a una banchina, entravano come navi da visitare.
ESCLUSE_FISSE = [
    {"nome": "PALMIRO Z", "imo": "8334964", "mmsi": "247388500", "motivo": "mezzo di lavoro: draga (Mele, 29/10)"},
]
# motivi delle navi che non sono navi da visitare: non si mostrano neanche in grigio "da non visitare"
MOTIVI_NASCOSTI = ("mezzo ",)


def esclusione_fissa(imo="", nome="", mmsi=""):
    for e in ESCLUSE_FISSE:
        if (imo and imo == e["imo"]) or (mmsi and mmsi == e["mmsi"]) or (nome and stessa_nave(nome, imo, e["nome"], e["imo"])):
            return e["motivo"]
    return None


def esclusa_di(dati, imo, nome, mmsi=None):
    """L'esclusione registrata per questa nave (o None)."""
    for e in dati.get("escluse", []):
        if (mmsi and e.get("mmsi") == mmsi) or (imo and e.get("imo") == imo) \
                or stessa_nave(e.get("nome"), e.get("imo"), nome, imo):
            return e
    return None


def motivo_esclusione(n, dati):
    """Perche' una nave dell'elenco non si visita (None se si visita)."""
    tipo = n.get("tipo") or ""
    fissa = esclusione_fissa(n.get("imo") or "", n.get("nome") or "", n.get("mmsi") or "")
    if fissa:
        return fissa
    if "giornale" not in (n.get("fonti") or ["giornale"]):
        # vista solo dall'AIS: se il giornale la mette tra i mezzi della diga o di servizio, non e' una nave da visitare
        e = esclusa_di(dati, n.get("imo") or "", n.get("nome") or "", n.get("mmsi") or "")
        if e and str(e.get("motivo") or "").startswith(MOTIVI_NASCOSTI):
            return e["motivo"]
    if e_chimichiera(tipo):
        return "nave chemical"
    if e_passeggeri(tipo) and not ro_ro_pax_dal_testo(tipo):
        return "nave passeggeri o da crociera"
    if ESCLUDI_CISTERNE and e_cisterna(tipo):
        return "cisterna (da confermare)"
    t = tipo_ais_numero((dati.get("statici") or {}).get(n.get("mmsi") or ""))
    # il tipo AIS (lo imposta l'equipaggio, a volte male) decide solo se il giornale non si legge: una nave che il
    # giornale scrive CONT., BULK, RO/RO, GAS... resta, anche se l'AIS la dichiara cisterna
    if ESCLUDI_CISTERNE and 80 <= t <= 89 and "giornale" in (n.get("fonti") or ["giornale"]) and not tipo_leggibile(tipo):
        return "cisterna (tipo AIS, il giornale lo scrive " + (tipo or "illeggibile") + ")"
    return None


# Tipi del giornale letti bene e che si visitano: per queste navi il tipo AIS non conta.
# GAS (gasiere): si visitano, decisione di Mele del 28/10 ("per ora inseriscile").
TIPI_VISITABILI = ("CONT.", "RO/RO", "BULK", "CARGO", "GEN.CARGO", "REEFER", "GAS")


def tipo_leggibile(tipo):
    return tipo in TIPI_VISITABILI


def esclusione_superata(e):
    """Esclusioni fatte con la regola di prima (fino al 28/10): una nave che il giornale scriveva con un tipo chiaro
    (CONT., BULK, GAS...) era esclusa solo perche' l'AIS la dichiarava cisterna. Ora quelle navi si visitano."""
    motivo = e.get("motivo") or ""
    if motivo.startswith("ro-ro con passeggeri"):
        return True                                   # fino al 29/10 si escludevano: ora si visitano (Mele)
    m = re.fullmatch(r"cisterna \(tipo AIS, il giornale lo scrive (.*)\)", motivo)
    return bool(m) and tipo_leggibile(m.group(1))


def registra_esclusa(dati, n, motivo, adesso=None):
    """Si ricorda delle navi escluse: l'AIS, che le vede ferme in banchina, non deve
    rimetterle nell'elenco al giro dopo."""
    adesso = adesso or datetime.now(timezone.utc)
    lista = dati.setdefault("escluse", [])
    for e in lista:
        if (n.get("mmsi") and e.get("mmsi") == n["mmsi"]) or stessa_nave(e.get("nome"), e.get("imo"), n.get("nome"), n.get("imo")):
            e.update({k: v for k, v in (("imo", n.get("imo")), ("mmsi", n.get("mmsi")), ("tipo", n.get("tipo"))) if v})
            e["motivo"] = motivo
            e["quando"] = adesso.isoformat()
            return
    lista.append({"nome": n.get("nome"), "imo": n.get("imo") or "", "mmsi": n.get("mmsi") or "",
                  "tipo": n.get("tipo") or "", "motivo": motivo, "quando": adesso.isoformat()})


def e_esclusa(dati, imo, nome, mmsi=None):
    return esclusa_di(dati, imo, nome, mmsi) is not None or bool(esclusione_fissa(imo or "", nome or "", mmsi or ""))


def escludi_non_visitabili(dati, adesso):
    """Toglie dall'elenco chemical e ro-ro con passeggeri (anche quelle gia' presenti
    da giri precedenti) e segna le ro-ro di cui non si sa ancora nulla."""
    superate = [e for e in dati.get("escluse", []) if esclusione_superata(e)]
    if superate:
        log("non piu' escluse (regole nuove: tipo del giornale, ro-ro con passeggeri):", ", ".join(e.get("nome") or "?" for e in superate))
        dati["escluse"] = [e for e in dati["escluse"] if not esclusione_superata(e)]
    tenute = []
    for n in dati.get("navi", []):
        motivo = motivo_esclusione(n, dati)
        if motivo:
            log("esclusa:", n.get("nome"), "-", motivo)
            registra_esclusa(dati, n, motivo, adesso)
            continue
        if NOTA_RORO in (n.get("nota") or ""):         # la nota "da controllare se porta passeggeri" non serve piu'
            n["nota"] = n["nota"].replace(NOTA_RORO, "").strip()
            if not n["nota"]:
                n.pop("nota")
        tenute.append(n)
    dati["navi"] = tenute
    limite = (adesso - timedelta(days=14)).isoformat()
    dati["escluse"] = [e for e in dati.get("escluse", []) if (e.get("quando") or "") >= limite]


def navi_dal_giornale(righe, data):
    """Navi dei terminal della mappa dal giornale del giorno `data`.
    Restituisce (navi, escluse): le escluse sono chemical e ro-ro con passeggeri."""
    import leggi_giornale as L
    correggi_orari(righe)
    ieri = (datetime.fromisoformat(data) - timedelta(days=1)).date().isoformat()
    per_nave = {}
    scartate = []
    escluse = []
    zone_n = {}
    for r in righe:
        zone_n.setdefault(r.get("zona") or "PRINCIPALE", []).append(pulisci_nome(r["nave"]) or "?")
    log("zone del giornale:", " ; ".join(f"{z}: {len(v)} righe (es. {', '.join(v[:3])})" for z, v in zone_n.items()))
    for r in righe:
        zona = r.get("zona") or "PRINCIPALE"
        if "BULK" in lettere(r["tipo"]):
            log("bulk nel giornale:", pulisci_nome(r["nave"]), "| zona", zona, "| accosto", r["accosto"])
        if zona == "MEZZI DIGA":
            # draghe e mezzi del cantiere della diga: non si visitano, ma l'AIS li vede fermi alle banchine
            # (e alcuni si dichiarano "cargo"): si ricordano come esclusi
            nome_m = pulisci_nome(r["nave"])
            if len(lettere(nome_m)) >= 4:
                escluse.append({"nome": nome_m, "imo": r["imo"] if r["imo_ok"] else "", "motivo": "mezzo della diga (dal giornale)",
                                "in_mappa": False})
            continue
        if zona not in ZONE_BUONE:
            continue                                   # Multedo, Voltri
        tipo = pulisci_tipo(r["tipo"])
        if tipo and tipo not in TIPI and "".join(c for c in tipo if c.isalpha()):
            log("tipo non riconosciuto:", pulisci_nome(r["nave"]), repr(r["tipo"]), "| zona", zona)
        if e_mezzo_di_servizio(r["tipo"]):
            if r.get("dove"):
                scartate.append(pulisci_nome(r["nave"]) + " (" + (r["tipo"] or "").strip() + ")")
            nome_m = pulisci_nome(r["nave"])
            if len(lettere(nome_m)) >= 4:
                escluse.append({"nome": nome_m, "imo": r["imo"] if r["imo_ok"] else "", "motivo": "mezzo di servizio (dal giornale)",
                                "in_mappa": False})
            continue
        motivo = ("nave chemical" if e_chimichiera(r["tipo"]) or e_chimichiera(tipo)
                  else "nave passeggeri o da crociera" if (e_passeggeri(r["tipo"]) or e_passeggeri(tipo)) and not ro_ro_pax_dal_testo(r["tipo"])
                  else "cisterna (da confermare)" if ESCLUDI_CISTERNE and (e_cisterna(tipo) or e_cisterna(r["tipo"])) else None)
        if motivo:
            nome_e = pulisci_nome(r["nave"])
            if nome_e:
                escluse.append({"nome": nome_e, "imo": r["imo"] if r["imo_ok"] else "", "motivo": motivo,
                                "in_mappa": bool((r.get("dove") or {}).get("terminal") in TERMINAL_MAPPA)})
            continue
        loa = misura(r.get("loa"), 25, 420)
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
        if loa and loa < lunghezza_minima(dove["terminal"]):
            log("scartata perche' troppo corta:", nome, "|", loa, "m |", dove["terminal"], "|", (r["tipo"] or "").strip())
            continue                                   # mezzi piccoli di servizio
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
        # ogni orario si porta dietro il giorno a cui si riferisce (le variazioni sono
        # cambi al giornale del giorno prima) e il giornale da cui viene
        n[campo + "_giorno"] = ieri if zona == "VARIAZIONI" else data
        n[campo + "_g"] = data
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
    visibili = sorted({e["nome"] + " (" + e["motivo"] + ")" for e in escluse if e["in_mappa"]})
    if visibili:
        log("escluse (non si visitano):", ", ".join(visibili))
    log("navi dei terminal:", len(navi))
    return navi, escluse


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


def misura_banchine(lat, lon, banchine):
    """Distanza del punto da ogni lato di banchina della mappa, dal piu' vicino. Per ciascuno:
    d (metri dal tratto, che si allunga di un decimo oltre le due estremita'), verso (> 0: dalla parte
    dell'acqua), perp (metri dalla retta della banchina), fuori (metri oltre la fine del tratto, 0 se
    sta di fronte), lunga (lunghezza del tratto), s_m (metri dalla radice lungo la banchina),
    rilievo (direzione della banchina, 0-180 gradi)."""
    out = []
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
            L = math.sqrt(L2)
            lungo = ((px - ax) * dx + (py - ay) * dy) / L2        # 0 = radice, 1 = estremita'
            t = max(-0.1, min(1.1, lungo))
            qx, qy = ax + t * dx, ay + t * dy
            d = math.hypot(px - qx, py - qy)
            ac = (b.get("acqua") or {}).get(lato) or [0, 0]
            verso = (px - qx) * ac[1] + (py - qy) * ac[0]        # >0: lato acqua
            perp = abs((px - ax) * dy - (py - ay) * dx) / L
            fuori = max(0.0, -lungo, lungo - 1.0) * L
            rilievo = math.degrees(math.atan2(dx, dy)) % 180          # direzione della banchina (nord = 0)
            out.append({"d": d, "verso": verso, "molo": molo, "lato": lato, "terminal": b["terminal"],
                        "s_m": round(t * L), "perp": perp, "fuori": fuori, "lunga": L, "rilievo": rilievo})
    return sorted(out, key=lambda x: x["d"])


def parallela(rotta, rilievo, tolleranza=30):
    """La prua della nave e' parallela alla banchina (in un verso o nell'altro)? Senza prua (assente o 511) no."""
    if rotta is None or not (0 <= rotta < 360):
        return False
    return abs((rotta - rilievo + 90) % 180 - 90) <= tolleranza


def alla_banchina(lat, lon, banchine, lung=0, rotta=None):
    """Se il punto e' a ridosso di una banchina dei nostri moli, dice quale:
    (molo, lato, terminal, metri dalla radice lungo la banchina).
    Una nave lunga (150 m e piu') puo' avere l'antenna molto oltre la fine del tratto disegnato sulla mappa:
    la banchina vera e' piu' lunga, o la nave sporge (la Chennai Express a Ponte Etiopia: 304 m, antenna a
    27 m dalla linea ma 112 m oltre la fine del tratto). Se non c'e' una banchina entro 75 m, per queste navi si
    accetta il lato di una banchina con l'antenna a non piu' di 50 m dalla sua linea, dalla parte dell'acqua,
    non oltre la meta' della lunghezza della nave dopo la fine del tratto, con la prua parallela alla banchina."""
    misure = misura_banchine(lat, lon, banchine)
    for m in misure:
        if m["d"] < 75 and m["verso"] > -15:
            return {"molo": m["molo"], "lato": m["lato"], "terminal": m["terminal"], "s_m": m["s_m"]}
    if (lung or 0) >= 150:
        for m in sorted(misure, key=lambda x: (x["fuori"], x["perp"])):
            if m["perp"] <= 50 and m["verso"] > -15 and m["fuori"] <= 0.5 * lung and parallela(rotta, m["rilievo"]):
                return {"molo": m["molo"], "lato": m["lato"], "terminal": m["terminal"], "s_m": m["s_m"],
                        "oltre_m": round(m["fuori"])}
    return None


def dove_nave(dati, mmsi, p, banchine, loa=0):
    """alla_banchina per una nave dell'AIS: lunghezza dai dati statici (o dal giornale) e prua dall'ultimo segnale."""
    s = (dati.get("statici") or {}).get(mmsi) or {}
    return alla_banchina(p["lat"], p["lon"], banchine, s.get("lung") or loa or 0, p.get("rotta"))


def nei_moli(p):
    """La posizione e' dentro l'area dei moli?"""
    return MOLI_BOX[0] <= p["lat"] <= MOLI_BOX[1] and MOLI_BOX[2] <= p["lon"] <= MOLI_BOX[3]


def finestra_ricezione(dati, adesso):
    """Quanto indietro si guardano le navi ferme nei moli per giudicare la ricezione. Di giorno i giri sono
    ogni 15 minuti e bastano 2 ore. Di notte i giri sono distanti 3-4 ore: con 2 ore il riferimento sarebbe
    vuoto e la regola dell'assenza non scatterebbe mai. Si guarda quindi indietro fino al giro prima
    (piu' 45 minuti: l'ascolto del giro prima e' cominciato fino a 20 minuti prima della sua registrazione)."""
    try:
        prima = datetime.fromisoformat(dati["aggiornato"])
    except (KeyError, TypeError, ValueError):
        return FINESTRA_RICEZIONE
    return min(max(FINESTRA_RICEZIONE, adesso - prima + timedelta(minutes=45)), FINESTRA_RICEZIONE_MAX)


def qualita_ricezione(dati, sentite, adesso, finestra=FINESTRA_RICEZIONE):
    """(buona, ferme_nei_moli, di_cui_sentite). L'AIS in 8 minuti non sente tutte le navi ferme, anche se
    ciascuna trasmette ogni 3 minuti: aisstream copre a macchia. Il silenzio di una nave vuol dire qualcosa
    solo se in questo giro si e' sentita la maggior parte delle navi che l'ultima volta erano ferme nei moli
    (segnale piu' recente della finestra: 2 ore, di notte fino al giro prima). Se se ne sente poche e' la
    copertura che e' calata: nessuna nave e' 'assente'."""
    riferimento = [m for m, p in (dati.get("registro") or {}).items()
                   if p["sog"] < 0.7 and nei_moli(p) and adesso - datetime.fromisoformat(p["quando"]) <= finestra]
    sentite_ora = sum(1 for m in riferimento if m in sentite)
    buona = len(riferimento) >= RICEZIONE_MIN and sentite_ora >= RICEZIONE_QUOTA * len(riferimento)
    return buona, len(riferimento), sentite_ora


def chiave_nave(n):
    return n["imo"] if re.fullmatch(r"\d{7}", n.get("imo") or "") else "N:" + norm_nome(n.get("nome"))


def imo_valido(s):
    """Numero IMO vero: 7 cifre con la cifra di controllo giusta (l'AIS a volte trasmette 0 o numeri inventati)."""
    s = str(s or "")
    if not re.fullmatch(r"\d{7}", s):
        return False
    return sum(int(s[i]) * (7 - i) for i in range(6)) % 10 == int(s[6])


def numeri_nel_nome(nome):
    """Le parole-numero del nome (cifre e numeri romani): ARGO I e ARGO II, XIN YA ZHOU 1 e 2 sono navi diverse,
    anche se i nomi si somigliano quasi del tutto. Il giornale (scansione) legge a volte I come L o 1: "MSC EDITH IL"
    e' "MSC EDITH II". Nelle parole fatte solo di I, V, X, L e 1 si legge quindi L e 1 come I."""
    out = []
    for t in re.findall(r"[A-Z0-9]+", (nome or "").upper()):
        t = _numero_letto(t)
        if re.fullmatch(r"\d+|[IVX]{1,5}", t):
            out.append(t)
    return sorted(out)


def _numero_letto(t):
    """Una parola fatta solo di I, V, X, L e 1 e' un numero romano letto forse male: L e 1 diventano I."""
    return t.replace("1", "I").replace("L", "I") if re.fullmatch(r"[IVXL1]{1,5}", t) else t


def nome_per_confronto(nome):
    """Il nome senza spazi e segni, con i numeri romani letti male rimessi a posto (ARGO 1 = ARGO I)."""
    return "".join(_numero_letto(t) for t in re.findall(r"[A-Z0-9]+", (nome or "").upper()))


def simili(a, b):
    """Stesso nome, oppure nome quasi uguale (letture sbagliate del giornale), ma con gli stessi numeri."""
    na, nb = nome_per_confronto(a), nome_per_confronto(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return numeri_nel_nome(a) == numeri_nel_nome(b) and difflib.SequenceMatcher(None, na, nb).ratio() >= 0.86


def stessa_nave(nome_a, imo_a, nome_b, imo_b):
    """Con due IMO validi decide l'IMO: due navi gemelle hanno nomi quasi uguali ma IMO diversi. Solo se anche
    il nome e' identico si pensa a un IMO letto male (giornale) o trasmesso male (AIS) e la nave e' la stessa.
    Se uno dei due IMO manca si guarda il nome."""
    if imo_valido(imo_a) and imo_valido(imo_b):
        return str(imo_a) == str(imo_b) or (bool(norm_nome(nome_a)) and norm_nome(nome_a) == norm_nome(nome_b))
    return simili(nome_a, nome_b)


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
    spente = dati.get("spente")
    if spente:
        for mmsi in [m for m in spente if m not in reg]:
            spente.pop(mmsi, None)
    # I dati delle navi (nome, IMO, tipo, misure) di tutto il Mar Ligure non si cancellavano mai: la memoria cresceva
    # a ogni giro. Si tengono solo per le navi sentite negli ultimi 3 giorni (quelle del registro) e per quelle che
    # servono (in elenco, da non visitare, escluse, seguite). Se una nave torna, l'AIS li ritrasmette ogni 6 minuti.
    servono = set(reg) | set(SEGUITE)
    for chiave_l in ("navi", "non_visitare", "escluse"):
        servono |= {x.get("mmsi") for x in dati.get(chiave_l) or [] if x.get("mmsi")}
    for mmsi in [m for m in sta if m not in servono]:
        sta.pop(mmsi, None)


VERSIONE_REGOLA_ASSENZA = 2


def azzera_conteggi_vecchi(dati):
    """Quando la regola dell'assenza cambia, i conteggi fatti con la regola di prima non valgono: si riparte da zero.
    (v1, 28/10 14:41-14:56: la ricezione era giudicata dal numero di navi sentite, troppo largo.)"""
    if dati.get("regola_assenza") == VERSIONE_REGOLA_ASSENZA:
        return
    for n in dati.get("navi", []):
        n.pop("assenze_ais", None); n.pop("segnale_perso", None)
    dati["regola_assenza"] = VERSIONE_REGOLA_ASSENZA


def assenze_necessarie(n):
    """Quanti giri di fila senza sentirla servono per dire 'segnale perso'. Ogni nave ferma in banchina si porta dietro
    quante volte l'AIS l'ha sentita nei giri con buona ricezione (n['ascolto'] = [sentita, giri]): la probabilita' di
    sentirla in un giro e' stimata (sentita + 3) / (giri + 4), cioe' 0,75 finche' non si sa niente. Servono tanti giri
    di assenza quanti bastano perche' sbagliare per caso (non sentirla tutte le volte pur essendo li') sia meno del 2%:
    almeno 3, al massimo 6. Una nave che l'AIS sente quasi sempre: 3 giri; una che sente meta' delle volte: 6."""
    sentita, giri = (list(n.get("ascolto") or [0, 0]) + [0, 0])[:2]
    # la stima si fa sui giri prima delle assenze di adesso: se no ogni assenza abbasserebbe la stima e allungherebbe
    # da sola l'attesa, anche per una nave partita davvero
    giri = max(sentita, giri - (n.get("assenze_ais") or 0))
    p = (sentita + 3) / (giri + 4)
    if p >= 1:
        return ASSENZE_MIN
    k = math.ceil(math.log(ERRORE_AMMESSO) / math.log(1 - p) - 1e-9)
    return max(ASSENZE_MIN, min(ASSENZE_TETTO, k))


def in_silenzio(n):
    return (n.get("assenze_ais") or 0) >= assenze_necessarie(n)


def limite_segnale(ricezione_ok):
    """Quanto puo' essere vecchio l'ultimo segnale di una nave ferma per metterla in elenco.
    Con buona ricezione (si sentono le altre navi dei moli) basta un segnale di meno di 40 minuti;
    senza (o senza saperlo) non si puo' dire che sia sparita: vale il limite lungo di sempre."""
    return SEGNALE_RECENTE if ricezione_ok else VALIDITA_FERMA


def scarto_solo_ais(dati, mmsi, p, dove, adesso, ricezione_ok=None):
    """Perche' una nave che l'AIS ha nel registro NON entra nell'elenco come 'solo AIS'.
    Restituisce il motivo a parole, oppure None se entra. Usata sia per decidere sia per la
    diagnosi nel riepilogo del giro, cosi' le due cose non possono dire cose diverse."""
    s = (dati.get("statici") or {}).get(mmsi) or {}
    if p["sog"] >= 2:
        return "in movimento (%.1f nodi)" % p["sog"]
    if p["sog"] >= 0.7:
        return "velocita' %.1f nodi: tra 0,7 e 2 non conta come ferma" % p["sog"]
    eta = adesso - datetime.fromisoformat(p["quando"])
    if eta > limite_segnale(ricezione_ok):
        return "ultimo segnale di %d minuti fa, troppo vecchio" % (eta.total_seconds() // 60)
    spente = (dati.get("spente") or {}).get(mmsi)
    if spente and p["quando"] <= spente:
        return "tolta per assenza di segnale, nessun segnale nuovo"
    if not dove:
        return "non e' a ridosso di una banchina della mappa"
    if not s:
        return "dati statici (tipo, lunghezza) non ancora ricevuti"
    tipo_a = tipo_ais(s.get("tipo"))
    if not tipo_a:
        return "tipo AIS %s: non e' carico, cisterna o passeggeri" % (s.get("tipo") or "assente")
    minima = lunghezza_minima_ais(dove["terminal"], tipo_a)
    if (s.get("lung") or 0) < minima:
        return "lunga %s m, sotto il minimo di %s m" % (s.get("lung") or 0, minima)
    if ESCLUDI_CISTERNE and tipo_a == "TANK":
        return "cisterna: non si visita"
    nome = (s.get("nome") or p.get("nome") or "").upper()
    if not nome:
        return "senza nome"
    imo = s.get("imo") if imo_valido(s.get("imo")) else ""
    if e_esclusa(dati, imo, nome, mmsi):
        return "nella lista delle escluse (chemical, cisterna, solo passeggeri, mezzi di lavoro)"
    return None


def trova_mmsi(n, dati, gia_usati=()):
    """L'MMSI (identita' AIS) di una nave dell'elenco: prima per IMO, poi per nome. Per nome non si prende mai
    una nave che l'AIS dice avere un altro IMO valido (la gemella), ne' una gia' abbinata a un'altra nave."""
    if n.get("mmsi"):
        return n["mmsi"]
    sta, reg = dati.get("statici", {}), dati.get("registro", {})
    imo = n.get("imo") if imo_valido(n.get("imo")) else ""
    if imo:
        for mmsi, s in sta.items():
            if s.get("imo") == imo and mmsi not in gia_usati:
                return mmsi
    for mmsi, s in sta.items():
        if mmsi not in gia_usati and stessa_nave(s.get("nome"), s.get("imo"), n["nome"], imo):
            return mmsi
    for mmsi, p in reg.items():
        if mmsi not in gia_usati and stessa_nave(p.get("nome"), (sta.get(mmsi) or {}).get("imo"), n["nome"], imo):
            return mmsi
    return None


def abbinamento_sbagliato(n, s):
    """La nave ha un IMO valido (dal giornale) e l'AIS dell'MMSI abbinato e' un'altra nave (altro IMO valido e altro nome)."""
    s = s or {}
    if not (imo_valido(n.get("imo")) and imo_valido(s.get("imo"))):
        return False
    return not stessa_nave(n.get("nome"), n["imo"], s.get("nome"), s["imo"])


def chiave_posto(dg):
    """ponte e lato come scritti nel giornale, es. 'RONCO|testata', 'CANEPA|levante'"""
    if not dg or not dg.get("molo"):
        return None
    return str(dg["molo"]).upper() + "|" + (dg.get("lato") or dg.get("punto") or "")


VERSIONE_TARATURA = 2


def azzera_taratura_vecchia(dati):
    """v1 (fino al 28/10) contava un'osservazione al giorno per posto, di qualunque nave: una sola nave ferma due
    giorni bastava a 'imparare' un posto. I conteggi di prima non dicono quante navi diverse si sono viste: si riparte."""
    if dati.get("versione_taratura") == VERSIONE_TARATURA:
        return
    dati["taratura"], dati["taratura_giorni"] = {}, {}
    dati["versione_taratura"] = VERSIONE_TARATURA


def impara(dati, dg, dove, chi=""):
    """Taratura dei nomi delle banchine sui dati reali: ogni volta che una nave
    del giornale e' vista ferma dall'AIS, si annota dove si trova davvero.
    Cosi' si scopre da soli, per esempio, quale lato di Messina e' Ronco e quale
    Canepa, e come si chiama il braccio est delle Rinfuse.
    chi: la nave (chiave_nave). Ogni nave conta una volta sola per posto (si ricorda per una settimana):
    servono quindi navi diverse per imparare un posto."""
    k = chiave_posto(dg)
    if not k:
        return
    v = dove["molo"] + "|" + dove["lato"]
    t = dati.setdefault("taratura", {}).setdefault(k, {})
    oggi = datetime.now(ROMA).date().isoformat()
    vist = dati.setdefault("taratura_giorni", {})
    base = k + ">" + v + ">" + (chi or "?") + ">"
    if any(s.startswith(base) for s in vist):
        return                                   # questa nave e' gia' contata per questo posto
    vist[base + oggi] = 1
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


def aggiorna_presenze(dati, banchine, adesso, sentite=None, ricezione_ok=None):
    """Mette la posizione AIS sulle navi dell'elenco e aggiunge quelle che l'AIS
    vede ferme alle nostre banchine anche se il giornale non le nomina.
    sentite: gli MMSI sentiti in questo giro; ricezione_ok: in questo giro si sono sentite
    abbastanza navi dei moli. Servono alla regola dell'assenza (senza, la regola non scatta)."""
    reg, sta = dati.get("registro", {}), dati.get("statici", {})
    oggi = adesso.astimezone(ROMA).date().isoformat()
    usati = set()
    # prima le navi gia' abbinate, cosi' un MMSI gia' preso non finisce anche a un'altra nave dal nome simile
    for n in dati["navi"]:
        if n.get("mmsi") and abbinamento_sbagliato(n, sta.get(n["mmsi"])):
            log("abbinamento AIS sbagliato, tolto:", n["nome"], n.get("imo"), "non e'", (sta.get(n["mmsi"]) or {}).get("nome"),
                (sta.get(n["mmsi"]) or {}).get("imo"))
            n.pop("mmsi"); n.pop("ascolto", None); n.pop("assenze_ais", None)
        elif n.get("mmsi"):
            usati.add(n["mmsi"])
    for n in dati["navi"]:
        n.pop("ais", None)
        mmsi = trova_mmsi(n, dati, usati - {n.get("mmsi")})
        if mmsi and not n.get("mmsi") and abbinamento_sbagliato(n, sta.get(mmsi)):
            mmsi = None
        if not mmsi:
            continue
        n["mmsi"] = mmsi; usati.add(mmsi)
        if sentite is not None:
            # giro che conta: buona ricezione e nave che era ferma alla nostra banchina (e non se ne sta andando)
            conta = bool(ricezione_ok and n.get("visto_banchina") and not n.get("uscita_vista"))
            if conta:
                sentita, giri = (list(n.get("ascolto") or [0, 0]) + [0, 0])[:2]
                sentita, giri = sentita + (1 if mmsi in sentite else 0), giri + 1
                if giri > 40:                                  # si ricordano gli ultimi 40 giri, piu' o meno
                    sentita, giri = sentita * 40 / giri, 40
                n["ascolto"] = [round(sentita, 2), round(giri, 2)]
            if mmsi in sentite:
                n["assenze_ais"] = 0
            elif conta:
                # si sentono le altre navi e lei no: un giro di assenza
                n["assenze_ais"] = (n.get("assenze_ais") or 0) + 1
                log("assente all'AIS (con buona ricezione):", n["nome"], "giri di fila:", n["assenze_ais"],
                    "su", assenze_necessarie(n), "| sentita in", n["ascolto"][0], "giri su", n["ascolto"][1])
        s = sta.get(mmsi, {})
        if s.get("nome") and len(s["nome"]) >= 3:
            n["nome"] = s["nome"].upper()                     # nome ufficiale dall'AIS
        if imo_valido(s.get("imo")):
            n["imo"] = s["imo"]
        if not n.get("loa") and s.get("lung"):
            n["loa"], n["larg"] = s["lung"], s.get("larg") or None
        p = reg.get(mmsi)
        if not p:
            continue
        quando = datetime.fromisoformat(p["quando"])
        dove = dove_nave(dati, mmsi, p, banchine, n.get("loa")) if p["sog"] < 0.7 else None
        # in manovra dentro il porto a poche centinaia di metri da una banchina del suo terminal: se poi il segnale
        # manca, quasi certamente ha attraccato li' (in alcune banchine aisstream non riceve le navi ormeggiate)
        vicino = None
        if not dove and 0.7 <= p["sog"] < 3 and nei_moli(p) and "giornale" in (n.get("fonti") or ["giornale"]):
            mie = [x for x in misura_banchine(p["lat"], p["lon"], banchine) if x["terminal"] == (n.get("dove") or {}).get("terminal")]
            if mie and mie[0]["d"] <= DISTANZA_ATTRACCO:
                vicino = mie[0]
        # una nave in movimento cambia posto in fretta: la sua posizione vale 3 ore; una ferma in banchina
        # resta li' e il suo segnale puo' mancare per qualche giro: la sua posizione vale di piu'
        if adesso - quando > (VALIDITA_FERMA if (dove or vicino) else VALIDITA_POSIZIONE):
            continue                                          # posizione vecchia: non si usa
        d = distanza_nm((p["lat"], p["lon"]), IMBOCCATURA)
        a = {"lat": round(p["lat"], 5), "lon": round(p["lon"], 5), "vel": round(p["sog"], 1),
             "dist_nm": round(d, 1), "quando": quando.astimezone(ROMA).strftime("%H.%M")}
        if dove:
            impara(dati, n.get("dove_giornale"), dove, chiave_nave(n))
            a["ormeggiata"] = True; a["s_m"] = dove["s_m"]
            if in_silenzio(n):
                a["silenzio"] = True                              # l'app non la dice piu' 'in tempo'
            n["dove"] = {**n.get("dove", {}), "terminal": dove["terminal"], "molo": dove["molo"],
                         "lato": dove["lato"] if dove["lato"] != "testata" else None,
                         "punto": "testata" if dove["lato"] == "testata" else n.get("dove", {}).get("punto"),
                         "in_rada": False}
            n["visto_banchina"] = quando.isoformat()
            n.setdefault("in_porto_dal", oggi)
            n.pop("uscita_vista", None)
        elif p["stato"] == 1 or (d < 8 and p["sog"] < 0.7):
            a["ancora"] = True
        elif p["sog"] >= 2 or nei_moli(p):
            # sopra i 2 nodi, oppure piano (0,7-2 nodi) ma dentro il porto: in manovra (prima a 0,7-2 nodi non si diceva niente)
            dentro = MOLI_BOX[0] <= p["lat"] <= MOLI_BOX[1] and MOLI_BOX[2] <= p["lon"] <= MOLI_BOX[3]
            era_ferma = False
            if n.get("visto_banchina"):
                era_ferma = adesso - datetime.fromisoformat(n["visto_banchina"]) <= timedelta(hours=12)
            if era_ferma and (dentro or not verso_genova(p)):
                # era ferma alla nostra banchina e adesso si muove: se ne sta andando
                a["in_uscita"] = True
                n["uscita_vista"] = quando.isoformat()
            elif dentro:
                a["manovra"] = True
                if vicino and adesso - quando >= ATTESA_ATTRACCO:
                    # entrata in manovra vicino alla sua banchina e poi nessun segnale: con ogni probabilita' ha attraccato
                    a["attracco_probabile"] = True
                    a["banchina_probabile"] = vicino["molo"] + " " + vicino["lato"]
                    a["m_banchina"] = int(round(vicino["d"]))
                    log("attracco probabile (poi nessun segnale):", n["nome"], vicino["molo"], vicino["lato"],
                        "a %d m, ultimo segnale delle" % vicino["d"], a["quando"])
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
        if adesso - quando > limite_segnale(ricezione_ok):
            continue
        dove = dove_nave(dati, mmsi, p, banchine)
        if not dove:
            continue
        s = sta.get(mmsi, {})
        # solo navi vere (carico; passeggeri e cisterne no): niente rimorchiatori e barche
        # (le bettoline di rifornimento e i mezzi di servizio sono sotto i 100 m).
        # I motivi di scarto sono tutti in scarto_solo_ais, la stessa funzione della diagnosi.
        if scarto_solo_ais(dati, mmsi, p, dove, adesso, ricezione_ok):
            continue
        nome = (s.get("nome") or p.get("nome") or "").upper()
        log("in banchina secondo l'AIS:", nome, dove["molo"], dove["lato"],
            "(nave lunga, oltre la fine del tratto di %d m)" % dove["oltre_m"] if dove.get("oltre_m") else "")
        dati["navi"].append({
            "nome": nome, "imo": s.get("imo") if imo_valido(s.get("imo")) else "",
            "mmsi": mmsi, "tipo": "RO/RO" if tipo_ais(s.get("tipo")) == "PASS." else tipo_ais(s.get("tipo")),
            **({"nota": NOTA_PAX_AIS} if tipo_ais(s.get("tipo")) == "PASS." else {}),
            "loa": s.get("lung") or None, "larg": s.get("larg") or None,
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


def costruisci_non_visitare(dati, banchine, adesso, ricezione_ok=None):
    """Le navi che non si visitano (chemical, solo passeggeri, cisterne) ma che l'AIS vede
    davvero ferme a una banchina della mappa: l'app le disegna in grigio con 'da non visitare',
    cosi' chi guarda sa che sono lì e che si lasciano stare. Se non sono ferme alla banchina
    non si mettono, anche se il giornale le prevede."""
    reg, sta = dati.get("registro", {}), dati.get("statici", {})
    in_elenco = {n.get("mmsi") for n in dati.get("navi", []) if n.get("mmsi")}
    trovate = {}                                              # mmsi -> (nome, imo, tipo, motivo)
    for e in dati.get("escluse", []):
        mmsi = e.get("mmsi") or trova_mmsi({"imo": e.get("imo") or "", "nome": e.get("nome") or ""}, dati)
        if mmsi:
            e["mmsi"] = mmsi
            if str(e.get("motivo") or "").startswith(MOTIVI_NASCOSTI):
                trovate[mmsi] = None                          # draghe e mezzi di lavoro: non si mostrano proprio
            else:
                trovate[mmsi] = (e.get("nome"), e.get("imo"), e.get("tipo"), e.get("motivo"))
    for e in ESCLUSE_FISSE:
        if e["mmsi"]:
            trovate[e["mmsi"]] = None
    for mmsi, s in sta.items():                               # cisterne e passeggeri che il giornale non nomina
        t = tipo_ais(s.get("tipo"))
        if mmsi in trovate or (s.get("lung") or 0) < LUNGHEZZA_MINIMA_AIS:
            continue
        if t == "TANK" and ESCLUDI_CISTERNE:
            trovate[mmsi] = (s.get("nome"), s.get("imo"), "TANK", "cisterna (da confermare)")
    risultato = []
    for mmsi, voce in trovate.items():
        if voce is None:
            continue
        nome, imo, tipo, motivo = voce
        p = reg.get(mmsi)
        if not p or mmsi in in_elenco or p["sog"] >= 0.7:
            continue
        quando = datetime.fromisoformat(p["quando"])
        if adesso - quando > limite_segnale(ricezione_ok):
            continue
        dove = dove_nave(dati, mmsi, p, banchine)
        if not dove:
            continue
        s = sta.get(mmsi, {})
        nome = (s.get("nome") or nome or p.get("nome") or "").upper()
        if not nome:
            continue
        imo = s.get("imo") if imo_valido(s.get("imo")) else (imo or "")
        log("da non visitare, ferma in banchina:", nome, "-", motivo, "-", dove["molo"], dove["lato"])
        risultato.append({
            "nome": nome, "imo": imo, "mmsi": mmsi, "tipo": tipo or tipo_ais(s.get("tipo")) or "",
            "loa": s.get("lung") or None, "larg": s.get("larg") or None, "motivo": motivo,
            "accosto": dove["molo"] + " " + ("TESTATA" if dove["lato"] == "testata" else dove["lato"][:3].upper() + "."),
            "dove": {"terminal": dove["terminal"], "molo": dove["molo"],
                     "lato": None if dove["lato"] == "testata" else dove["lato"],
                     "punto": "testata" if dove["lato"] == "testata" else None, "in_rada": False},
            "ais": {"lat": round(p["lat"], 5), "lon": round(p["lon"], 5), "vel": round(p["sog"], 1),
                    "dist_nm": round(distanza_nm((p["lat"], p["lon"]), IMBOCCATURA), 1),
                    "quando": quando.astimezone(ROMA).strftime("%H.%M"), "ormeggiata": True, "s_m": dove["s_m"]},
        })
    dati["non_visitare"] = sorted(risultato, key=lambda x: x["nome"])


def descrivi_nave_ais(dati, banchine, mmsi, p, adesso, ricezione_ok, sentite):
    """Una riga che dice tutto di una nave vista dall'AIS: velocita', eta' del segnale, banchina piu'
    vicina (con distanza e lato), dati statici e perche' non e' in elenco."""
    s = (dati.get("statici") or {}).get(mmsi) or {}
    eta = int((adesso - datetime.fromisoformat(p["quando"])).total_seconds() // 60)
    vicine = misura_banchine(p["lat"], p["lon"], banchine)
    if vicine:
        m = vicine[0]
        prua = p.get("rotta")
        banchina = ("banchina piu' vicina %s %s a %d m (verso acqua %d; di lato %d m dalla linea, %d m oltre la fine del tratto, "
                    "tratto lungo %d m; prua %s, banchina %d gradi)") % (
            m["molo"], m["lato"], m["d"], m["verso"], m["perp"], m["fuori"], m["lunga"],
            "%d" % prua if prua is not None and 0 <= prua < 360 else "non nota", m["rilievo"])
    else:
        banchina = "nessuna banchina in mappa"
    dove = dove_nave(dati, mmsi, p, banchine)
    motivo = scarto_solo_ais(dati, mmsi, p, dove, adesso, ricezione_ok)
    in_elenco = any(n.get("mmsi") == mmsi for n in dati.get("navi", []))
    return "%s (%s): %.1f nodi, segnale di %d min fa%s, %s, tipo %s, %s m | %s" % (
        (s.get("nome") or p.get("nome") or "?").upper(), mmsi, p["sog"], eta,
        "" if sentite is None else (", sentita in questo giro" if mmsi in sentite else ", NON sentita in questo giro"),
        banchina, s.get("tipo", "assente"), s.get("lung", "?"),
        "IN ELENCO" if in_elenco else ("fuori elenco: " + (motivo or "dovrebbe entrare")))


def diagnostica_moli(dati, banchine, adesso, sentite=None, ricezione_ok=None):
    """Scrive nel riepilogo del giro (leggibile da fuori) le navi che l'AIS vede ferme o quasi nei moli
    e non sono in elenco, col motivo preciso; poi la storia delle navi 'seguite'. Serve a capire
    perche' una nave che si vede in banchina non compare (es. la Chennai Express a Spinelli)."""
    reg, sta = dati.get("registro", {}), dati.get("statici", {})
    in_elenco = {n.get("mmsi") for n in dati.get("navi", []) if n.get("mmsi")}
    in_elenco |= {n.get("mmsi") for n in dati.get("non_visitare", []) if n.get("mmsi")}
    righe = []
    for mmsi, p in reg.items():
        if mmsi in in_elenco or mmsi in SEGUITE or p["sog"] >= 2 or not nei_moli(p):
            continue
        if adesso - datetime.fromisoformat(p["quando"]) > VALIDITA_FERMA:
            continue
        s = sta.get(mmsi) or {}
        vicine = misura_banchine(p["lat"], p["lon"], banchine)
        rinfuse_piccola = bool(vicine) and vicine[0]["terminal"] == "rinfuse" and tipo_ais(s.get("tipo")) == "CARGO" \
            and (s.get("lung") or 0) >= LUNGHEZZA_MINIMA_AIS_RINFUSE
        if s and (s.get("lung") or 0) < 100 and not rinfuse_piccola:
            continue                                  # rimorchiatori e barche: non interessano
        vicina = vicine[0]["d"] if vicine else 9999
        if vicina > 400 or (not s and vicina > 150):
            continue                                  # lontana da ogni banchina (all'ancora, in transito): non e' un caso da spiegare
        righe.append(descrivi_nave_ais(dati, banchine, mmsi, p, adesso, ricezione_ok, sentite))
    for r in sorted(righe)[:30]:
        log("diagnosi, ferma nei moli:", r)
    if len(righe) > 30:
        log("diagnosi: altre", len(righe) - 30, "navi non elencate")
    for mmsi in SEGUITE:
        p = reg.get(mmsi)
        if p:
            log("seguita:", descrivi_nave_ais(dati, banchine, mmsi, p, adesso, ricezione_ok, sentite))
        else:
            log("seguita:", mmsi, "- non c'e' nel registro: l'AIS non ne ha ricevuto nessuna posizione negli ultimi 3 giorni;",
                "dati statici:", dati.get("statici", {}).get(mmsi, "nessuno"))


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


EVENTI = ("arrivo", "partenza")


def normalizza_eventi(n):
    """Ogni orario (arrivo, partenza) si porta dietro il giorno a cui si riferisce
    (campo_giorno) e la data del giornale da cui viene (campo_g). Le navi salvate
    prima di questa regola prendono il giorno del loro giornale."""
    for campo in EVENTI:
        if n.get(campo):
            n.setdefault(campo + "_giorno", n.get("giorno"))
            n.setdefault(campo + "_g", n.get("giorno"))
        else:
            for suf in ("_giorno", "_g"):
                n.pop(campo + suf, None)


def passa_ad_in_porto(n, oggi):
    """Se l'arrivo e' di un giorno passato la nave e' arrivata: da ora e' 'in porto dal ...'.
    (Un arrivo di oggi resta com'e', anche se l'ora e' passata: l'app lo sa leggere,
    e uno previsto per stasera non va cancellato.)"""
    arr = n.get("arrivo_giorno") or n.get("giorno")
    if n.get("arrivo") and arr and arr < oggi:
        n["in_porto_dal"] = n.get("in_porto_dal") or arr
        n["arrivo"] = None
        n.pop("arrivo_giorno", None); n.pop("arrivo_g", None)
    elif not n.get("arrivo") and not n.get("in_porto_dal") and n.get("giorno") and n["giorno"] < oggi:
        n["in_porto_dal"] = n["giorno"]


def fondi_navi(v, n, adesso=None):
    """v: la nave gia' in elenco; n: la stessa nave letta ora da un giornale.
    - Orari: per ogni evento vale quello del giorno piu' avanti; a parita' di giorno
      vale il giornale piu' recente (le variazioni correggono il giornale prima).
    - Dati fissi (posto, misure, tipo): vale il giornale piu' recente, l'altro riempie i vuoti.
    - Sosta: un arrivo nuovo la fa ripartire (la nave torna in porto), salvo che l'AIS la veda gia' ferma in banchina
      da poco (meno di 12 ore prima di adesso): allora l'"arrivo" e' uno spostamento a un'altra banchina e la sosta
      continua, cosi' una visita fatta oggi non si perde quando il giornale di domani la sposta.
    Funziona anche se si legge un giornale piu' vecchio di uno gia' letto."""
    n_recente = (n.get("giorno") or "") >= (v.get("giorno") or "")
    base, altra = (n, v) if n_recente else (v, n)
    for campo in EVENTI:
        con = [x for x in (v, n) if x.get(campo)]
        scelto = {}
        if con:
            m = max(con, key=lambda x: (x.get(campo + "_giorno") or "", x.get(campo + "_g") or "", x is n))
            scelto = {suf: m.get(campo + suf) for suf in ("", "_giorno", "_g")}
        for suf in ("", "_giorno", "_g"):          # prima si svuota (m puo' essere proprio base), poi si riempie
            base.pop(campo + suf, None)
        for suf, valore in scelto.items():
            if valore is not None:
                base[campo + suf] = valore
    for campo in ("mmsi", "visto_banchina", "loa", "larg", "tipo", "imo"):
        if altra.get(campo) and not base.get(campo):
            base[campo] = altra[campo]
    gia_in_banchina = False
    if adesso is not None and (v.get("visto_banchina") or base.get("visto_banchina")):
        try:
            visto = datetime.fromisoformat(v.get("visto_banchina") or base["visto_banchina"])
            gia_in_banchina = adesso - visto <= timedelta(hours=12)
        except (TypeError, ValueError):
            pass
    if base.get("arrivo") and not gia_in_banchina:
        base.pop("in_porto_dal", None)           # sta arrivando: la sosta riparte dall'arrivo
    else:
        ip = base.get("in_porto_dal") or altra.get("in_porto_dal")
        if ip:
            base["in_porto_dal"] = ip
    base["fonti"] = sorted(set(base.get("fonti", []) + ["giornale"]))
    return base


def unisci_giornale(dati, nuove, data, adesso=None):
    """Il giornale (di qualunque giorno) aggiorna l'elenco. Le navi di prima che non
    sono nel giornale nuovo restano finche' non risultano partite: se stanno in porto
    piu' giorni sono 'in porto dal ...'. Gli orari di prima non si perdono."""
    adesso = adesso or datetime.now(timezone.utc)
    oggi = adesso.astimezone(ROMA).date().isoformat()
    vecchie = {chiave_nave(n): n for n in dati.get("navi", [])}
    for v in vecchie.values():
        normalizza_eventi(v)
    risultato = []
    for n in nuove:
        n["giorno"] = data
        n["fonti"] = ["giornale"]
        normalizza_eventi(n)
        v = vecchie.pop(chiave_nave(n), None)
        if v is None:
            # stessa nave scritta un po' diversa (lettura del giornale), ma mai due navi con IMO validi diversi
            k_v = next((k for k, x in vecchie.items() if stessa_nave(x["nome"], x.get("imo"), n["nome"], n.get("imo"))), None)
            if k_v is not None:
                v = vecchie.pop(k_v)
        if v is not None:
            n = fondi_navi(v, n, adesso)
            n.setdefault("giorno", data)
        passa_ad_in_porto(n, oggi)
        risultato.append(n)
    for v in vecchie.values():
        if v.get("giorno") and v["giorno"] < data:
            part = istante(v.get("partenza_giorno") or v["giorno"], v.get("partenza")) if v.get("partenza") else None
            if part is not None and adesso > part + timedelta(hours=3):
                continue                         # ripartita da un pezzo
            if not (v.get("arrivo") or v.get("in_porto_dal") or part is not None):
                continue                         # niente da cui capire che e' ancora in porto
            vecchio_giorno = v["giorno"] < oggi
            passa_ad_in_porto(v, oggi)
            if vecchio_giorno:
                v["fonti"] = sorted(set(v.get("fonti", []) + ["memoria"]))
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
        # regola dell'assenza: per due giri di fila l'AIS ha sentito le altre navi dei moli ma non lei
        silenzio = in_silenzio(n)
        if n.get("conferma", {}).get("tipo") == "partenza" and n["conferma"].get("quando"):
            pc = datetime.fromisoformat(n["conferma"]["quando"])
            part = pc if part is None or pc > part else part
        if part is not None and adesso > part + timedelta(minutes=90):
            if visto and visto > part + timedelta(minutes=30) and a.get("ormeggiata") and not silenzio:
                n["ritardo"] = True                       # doveva partire, ma e' ancora li'
            else:
                log("partita:", n["nome"]); continue
        if silenzio and "giornale" not in n.get("fonti", ["giornale"]):
            # solo AIS e adesso non la sente piu': se ne e' andata (o e' fuori copertura). Si ricorda
            # l'ultimo segnale, cosi' non rientra dal registro finche' non ne arriva uno nuovo.
            log("sparita dall'AIS (assente da", n.get("assenze_ais"), "giri con buona ricezione):", n["nome"])
            ultimo = (dati.get("registro") or {}).get(n.get("mmsi") or "", {}).get("quando")
            if ultimo and n.get("mmsi"):
                dati.setdefault("spente", {})[n["mmsi"]] = ultimo
            continue
        uscita = datetime.fromisoformat(n["uscita_vista"]) if n.get("uscita_vista") else None
        if uscita and not a.get("ormeggiata"):
            # l'AIS l'ha vista andarsene: per un'ora e mezza resta 'si allontana', poi si toglie
            if adesso - uscita > timedelta(minutes=90):
                log("uscita dal porto (AIS):", n["nome"]); continue
        elif visto and not a.get("ormeggiata") and not a.get("in_uscita") \
                and a.get("vel", 0) >= 2 and a.get("dist_nm", 0) > 1 and not n.get("arrivo"):
            log("uscita dal porto (AIS):", n["nome"]); continue
        fonti = n.get("fonti", ["giornale"])
        if "giornale" not in fonti and (n.get("loa") or 0) < lunghezza_minima_ais((n.get("dove") or {}).get("terminal"), n.get("tipo")):
            log("tolta (mezzo di servizio visto solo dall'AIS):", n["nome"]); continue
        if "giornale" not in fonti or "memoria" in fonti or n.get("giorno", "") < adesso.astimezone(ROMA).date().isoformat():
            # tenuta solo dalla memoria o dall'AIS: serve una conferma recente
            if visto and adesso - visto > timedelta(hours=30):
                log("non piu' vista in banchina:", n["nome"]); continue
            dal = n.get("in_porto_dal")
            if not visto and dal and (adesso.astimezone(ROMA).date() - datetime.fromisoformat(dal).date()).days > 3:
                log("in memoria da troppi giorni senza conferme:", n["nome"]); continue
            n["da_confermare"] = not visto
        if silenzio:
            n["segnale_perso"] = True                     # sul giornale, ma l'AIS non la sente piu': da confermare
        else:
            n.pop("segnale_perso", None)
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


def nave_della_riga(navi, nome):
    """La nave dell'elenco a cui si riferisce una riga della tabella del sito (che ha solo il nome):
    quella con lo stesso nome, se c'e'. Altrimenti una sola nave molto somigliante (0,92: una lettera diversa in un
    nome lungo, letto male dal giornale) e con gli stessi numeri nel nome. Nei nomi corti una lettera diversa e' un'altra
    nave (NORDIC ANNE e NORDIC ANNA): la conferma di partenza di una gemella non deve togliere la nostra nave."""
    k = norm_nome(nome)
    if not k:
        return None
    for n in navi:
        if norm_nome(n.get("nome")) == k:
            return n
    candidati = [n for n in navi if simili(n.get("nome"), nome)
                 and difflib.SequenceMatcher(None, norm_nome(n.get("nome")), k).ratio() >= 0.92]
    return candidati[0] if len(candidati) == 1 else None


def applica_conferme(dati, righe):
    for r in righe:
        n = nave_della_riga(dati.get("navi", []), r["nave"])
        if n is None:
            continue
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
def secondi_ascolto(ora):
    """Quanto si ascolta l'AIS in questo giro. ora: l'orario di Genova.
    Una nave ferma trasmette la posizione ogni 3 minuti, ma nome e IMO solo ogni 6, e aisstream ne
    sente solo una parte per volta (28/10: dal 40% all'80% delle navi ferme in un giro da 8 minuti).
    Di giorno si ascolta 10 minuti (12 al primo giro del mattino): ogni giro dura meno di 12 minuti, il ciclo e' di 15.
    Di notte i giri sono solo tre e distanti ore: se ne perdi uno hai perso 4 ore, quindi si ascolta 20 minuti.
    AIS_SECONDI nell'ambiente vince su tutto (prove)."""
    if os.environ.get("AIS_SECONDI"):
        return int(os.environ["AIS_SECONDI"])
    if ora.hour >= 22 or ora.hour < 7:                # l'ultimo giro del giorno puo' partire fino alle 21:04
        return ASCOLTO_NOTTE
    return 720 if ora.hour == 7 and ora.minute < 30 else 600


# Il telefono scarica solo navi.enc.json: dentro ci sono soltanto le cose che l'app mostra. La memoria del
# programma (posizioni AIS di tutto il Mar Ligure degli ultimi 3 giorni, dati delle navi, esclusioni, taratura...)
# sta in stato.enc.json, cifrato allo stesso modo, che l'app non scarica: cosi' il file del telefono resta piccolo
# (prima cresceva di giorno in giorno: 230 KB il 28/10, scaricati a ogni apertura).
CAMPI_APP = ("navi", "non_visitare", "data", "giornali", "fonte", "letto", "ais", "aggiornato", "esempio")


def dati_per_app(dati):
    return {c: dati[c] for c in CAMPI_APP if c in dati}


def apri_stato(k, stato, uscita):
    """La memoria del programma: stato.enc.json; se manca (prima volta) o non si apre, i dati dell'app di prima
    (fino al 28/10 tutto stava in navi.enc.json), cosi' non si riparte mai da zero per un file rovinato."""
    try:
        d = apri(k, stato)
        if d is not None:
            return d
    except Exception as e:
        log("ATTENZIONE: stato.enc.json non si apre (", type(e).__name__, e, "): riparto da navi.enc.json")
    return apri(k, uscita) or {}


def main():
    import requests
    codice = os.environ.get("CODICE_MAPPA")
    if not codice:
        sys.exit("manca CODICE_MAPPA")
    k = chiave(codice)
    banchine = leggi_banchine(k)
    uscita = os.path.join(BASE, "navi.enc.json")
    stato = os.path.join(BASE, "stato.enc.json")
    dati = apri_stato(k, stato, uscita)
    if dati.get("esempio"):
        dati = {}
    dati.setdefault("navi", [])
    azzera_conteggi_vecchi(dati)
    azzera_taratura_vecchia(dati)
    adesso = datetime.now(timezone.utc)
    forza = "--forza-giornale" in sys.argv

    sess = requests.Session()
    sess.headers["User-Agent"] = "Mozilla/5.0 (moli-porto; aggiornamento previsioni)"
    oggi_r = adesso.astimezone(ROMA).date()
    # giornali gia' letti (i dati salvati prima di questa regola ne avevano uno solo: 'data')
    letti = set(dati.get("giornali") or ([dati["data"]] if dati.get("data") else []))
    pagina, da_leggere = "", []          # da_leggere: [(data, pdf)]
    if "--pdf" in sys.argv:
        i = sys.argv.index("--pdf")
        da_leggere = [(sys.argv[i + 2] if len(sys.argv) > i + 2 else dati.get("data"), open(sys.argv[i + 1], "rb").read())]
    else:
        try:
            note, pagina = con_tentativi(lambda: note_sul_sito(sess))
            # servono oggi e domani, ciascuno col suo giorno. Ieri solo se non c'e' ancora memoria di nessun
            # giornale (le navi che restano piu' giorni si ricordano dai giri precedenti); piu' vecchi mai.
            dal = oggi_r if letti else oggi_r - timedelta(days=1)
            utili = [(d, b) for d, b in note if dal.isoformat() <= d <= (oggi_r + timedelta(days=1)).isoformat()]
            log("giornali gia' letti:", sorted(letti) or "nessuno", "| utili sul sito:", [d for d, _ in utili])
            for d, b in sorted(x for x in utili if forza or x[0] not in letti):
                try:
                    da_leggere.append((d, con_tentativi(lambda: scarica_giornale(sess, b))))
                except Exception as e:
                    log(f"giornale del {d} non scaricato:", type(e).__name__, e)
            if oggi_r.isoformat() not in letti and oggi_r.isoformat() not in [d for d, _ in da_leggere]:
                log("ATTENZIONE: il giornale di oggi", oggi_r.isoformat(), "non e' ne' letto ne' scaricabile")
        except Exception as e:
            log("sito non raggiungibile:", type(e).__name__, e)
    for data, pdf in da_leggere:
        try:
            log("leggo il giornale del", data, len(pdf), "byte")
            righe, date_titoli = leggi_pdf(pdf)
            if len(righe) < 3:
                raise RuntimeError(f"lettura fallita: solo {len(righe)} righe trovate")
            if date_titoli and data not in date_titoli:
                log("ATTENZIONE: nel PDF si legge", sorted(set(date_titoli)), "ma il sito lo dava come", data)
            navi, escluse = navi_dal_giornale(righe, data)
            for e in escluse:
                registra_esclusa(dati, e, e["motivo"], adesso)
            unisci_giornale(dati, navi, data, adesso)
            letti.add(data)
            dati.update({"letto": adesso.isoformat()})
        except Exception as e:
            log(f"giornale del {data} non letto, si riprova al prossimo giro:", type(e).__name__, e)
    if letti:
        recenti = sorted(d for d in letti if d >= (oggi_r - timedelta(days=7)).isoformat())
        passati = [d for d in recenti if d <= oggi_r.isoformat()]
        dati["giornali"] = recenti
        dati["data"] = oggi_r.isoformat() if oggi_r.isoformat() in recenti else (passati[-1] if passati else recenti[0])
        dati["fonte"] = "Capitaneria di Porto di Genova, previsioni del " + dati["data"]
    if pagina:
        try:
            applica_conferme(dati, leggi_tabelle_sito(pagina))
        except Exception as e:
            log("tabella del sito non letta:", e)

    chiave_ais = os.environ.get("AISSTREAM_KEY")
    if chiave_ais:
        ora = adesso.astimezone(ROMA)
        # quanto si ascolta: vedi secondi_ascolto
        secondi = secondi_ascolto(ora)
        pos, statici = asyncio.run(ascolta_ais(chiave_ais, secondi))
        # ricezione buona = in questo giro si sono sentite abbastanza navi dentro i moli: solo allora il
        # silenzio di una nave vuol dire qualcosa (un calo di copertura toglierebbe tutte le navi insieme).
        # Si giudica PRIMA di mettere nel registro le posizioni di questo giro: il riferimento sono le navi che i giri
        # precedenti avevano sentito ferme nei moli. Dopo, le navi sentite adesso ci sarebbero sempre e la ricezione
        # sembrerebbe perfetta a ogni ripartenza (mattino, o dopo una pausa lunga): falsi "segnale perso".
        sentite = set(pos)
        finestra = finestra_ricezione(dati, adesso)       # dati["aggiornato"] e' ancora quello del giro prima
        ricezione_ok, n_rif, n_sentite = qualita_ricezione(dati, sentite, adesso, finestra)
        aggiorna_registro(dati, pos, statici, adesso)
        log("AIS: ascolto di", secondi, "secondi - navi ferme nei moli nei giri prima (ultime %.1f ore)" % (finestra.total_seconds() / 3600),
            n_rif, "- risentite in questo giro", n_sentite, "- ricezione",
            "buona" if ricezione_ok else "scarsa o non giudicabile (la regola dell'assenza non scatta)")
        aggiorna_presenze(dati, banchine, adesso, sentite, ricezione_ok)
        dati["ais"] = bool(pos)
    else:
        dati["ais"] = False
        sentite, ricezione_ok = None, None
    escludi_non_visitabili(dati, adesso)      # chemical, solo passeggeri, cisterne (dopo l'AIS: serve il suo tipo)
    costruisci_non_visitare(dati, banchine, adesso, ricezione_ok)   # quelle escluse che sono davvero ferme alle nostre banchine
    applica_taratura(dati)
    riassunto_taratura(dati)
    # la memoria dei giorni di taratura non serve oltre una settimana
    lim = (adesso.astimezone(ROMA).date() - timedelta(days=7)).isoformat()
    dati["taratura_giorni"] = {k: 1 for k in dati.get("taratura_giorni", {}) if k.rsplit(">", 1)[-1] >= lim}
    pulizia(dati, adesso)
    if chiave_ais:
        diagnostica_moli(dati, banchine, adesso, sentite, ricezione_ok)
    for n in sorted(dati.get("navi", []), key=lambda x: x.get("nome") or ""):
        ultimo = (dati.get("registro") or {}).get(n.get("mmsi") or "", {}).get("quando")
        eta_seg = "%d min" % ((adesso - datetime.fromisoformat(ultimo)).total_seconds() // 60) if ultimo else "mai"
        log("in elenco:", n.get("nome"), "|", n.get("tipo") or "?", "| AIS tipo", (dati.get("statici") or {}).get(n.get("mmsi") or "", {}).get("tipo", "-"),
            "| ultimo segnale", eta_seg, "| assenze", n.get("assenze_ais") or 0,
            "|", (n.get("dove") or {}).get("terminal"),
            "|", "segnale perso" if n.get("segnale_perso") else
            ("ormeggiata" if (n.get("ais") or {}).get("ormeggiata") else ("solo AIS" if n.get("fonti") == ["ais"] else "giornale")))
    dati["aggiornato"] = datetime.now(timezone.utc).isoformat()
    dati["esempio"] = False
    chiudi(k, dati, stato)                    # prima la memoria: se il giro si interrompe dopo, non si perde
    # Il workflow nuovo (STATO_SEPARATO=si) pubblica anche stato.enc.json: al telefono va solo il necessario.
    # Con il workflow di prima, o a mano, stato.enc.json non verrebbe pubblicato: navi.enc.json resta completo,
    # cosi' passando da uno all'altro la memoria non si perde mai.
    chiudi(k, dati_per_app(dati) if os.environ.get("STATO_SEPARATO") == "si" else dati, uscita)
    log("scritto", uscita, "(%d KB) e la memoria in stato.enc.json (%d KB)" % (os.path.getsize(uscita) // 1024, os.path.getsize(stato) // 1024),
        "navi:", len(dati.get("navi", [])),
        "con AIS:", sum(1 for n in dati.get("navi", []) if n.get("ais")),
        "in banchina:", sum(1 for n in dati.get("navi", []) if (n.get("ais") or {}).get("ormeggiata")))


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:
        log("ERRORE:", type(e).__name__, e)
        raise
    finally:
        riepilogo_actions()

#!/usr/bin/env python3
"""Legge il giornale "Previsioni" della Capitaneria (immagine o pagina di PDF
scansionato) e restituisce le righe: ora, operazione, nave, IMO, accosto.

Metodo: trova la griglia della tabella (linee orizzontali e verticali), poi
legge ogni cella da sola con Tesseract. L'IMO viene controllato con la sua
cifra di controllo; le righe dubbie vengono segnalate, mai inventate.

Uso: python3 -I leggi_giornale.py immagine.jpg [uscita.json]
"""
import sys, json, re
import cv2, numpy as np, pytesseract

def imo_valido(s):
    if not re.fullmatch(r"\d{7}", s or ""):
        return False
    d = [int(c) for c in s]
    return sum(d[i] * (7 - i) for i in range(6)) % 10 == d[6]

import difflib

# ponte o calata scritto nel giornale -> terminal della mappa
MOLI = {
    "RONCO": "imt", "CANEPA": "imt",
    "LIBIA": "sangiorgio", "SOMALIA": "sangiorgio",
    "ERITREA": "gmt", "MOGADISCIO": "gmt",
    "ETIOPIA": "spinelli", "IDROSCALO": "spinelli", "MASSAUA": "spinelli", "INGLESE": "spinelli",
    "RUBATTINO": "rinfuse", "GIACCONE": "rinfuse",
    "BETTOLO": "bettolo", "SANITA": "sech", "SECH": "sech",
}

def riconosci_accosto(testo):
    """Da un accosto letto (anche con errori) ricava terminal, molo, lato, punto."""
    t = re.sub(r"[^A-Z0-9 ]", " ", (testo or "").upper().replace("À", "A"))
    parole = [w for w in t.split() if len(w) >= 4]
    molo = None
    for w in parole:
        for k in MOLI:
            if difflib.SequenceMatcher(None, w[:len(k)], k).ratio() >= 0.78:
                molo = k; break
        if molo:
            break
    if not molo:
        return None
    rada = bool(re.search(r"\bRADA\b", t)) and molo in ("SECH", "GMT")
    lato = "levante" if re.search(r"LEV", t) else ("ponente" if re.search(r"PON(?!TE)", t) else None)
    punto = "radice" if re.search(r"(?<!\bRADA )\bRAD\b|RADICE|(LEV|PON) ?RAD\b", t) else (
            "testata" if "TESTATA" in t else ("prolungamento" if "PROL" in t else None))
    return {"terminal": MOLI[molo], "molo": molo.title(), "lato": lato, "punto": punto, "in_rada": rada}

def raggruppa(pos, tolleranza):
    """Unisce posizioni vicine (spessore delle linee) e ne tiene il centro."""
    gruppi, cur = [], []
    for p in pos:
        if cur and p - cur[-1] > tolleranza:
            gruppi.append(int(np.mean(cur))); cur = []
        cur.append(p)
    if cur:
        gruppi.append(int(np.mean(cur)))
    return gruppi

def griglia(bin_inv):
    h, w = bin_inv.shape
    oriz = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (60, 1)))
    vert = cv2.morphologyEx(bin_inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40)))
    dens_r = np.convolve(oriz.sum(axis=1) / 255 / w, np.ones(5) / 5, mode="same")
    righe = raggruppa(np.where(dens_r > 0.10)[0], 8)
    # le linee verticali di una scansione sono spezzate e un po' inclinate:
    # si somma lungo la colonna e si smussa di qualche pixel
    dens = np.convolve(vert.sum(axis=0) / 255 / h, np.ones(9) / 9, mode="same")
    colonne = raggruppa(np.where(dens > 0.04)[0], 12)
    return righe, colonne, oriz, vert

# larghezze relative delle 12 colonne del giornale (misurate sul PDF del 26/10/2026)
LARGHEZZE = [109, 212, 491, 182, 208, 208, 203, 183, 185, 183, 636, 482]

def scegli_colonne(colonne):
    """Tra le linee verticali trovate (bordi pagina e riquadri compresi) sceglie le
    13 consecutive che somigliano di piu' alla griglia del giornale."""
    lin = []
    for x in colonne:                      # linee doppie (bordo spesso) -> una sola
        if lin and x - lin[-1] < 40:
            continue
        lin.append(x)
    rif = np.array(LARGHEZZE, float); rif /= rif.sum()
    meglio, err_min = lin[-13:], 1e9
    for i in range(0, len(lin) - 12):
        w = np.diff(lin[i:i + 13]).astype(float); w /= w.sum()
        err = float(np.abs(w - rif).sum())
        if err < err_min:
            meglio, err_min = lin[i:i + 13], err
    return meglio

def leggi_ora(img):
    img = cv2.copyMakeBorder(img, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    cifre = ocr(img, "--psm 7 -c tessedit_char_whitelist=0123456789.:")
    m = re.search(r"(\d{1,2})[.:,]?(\d{2})", cifre)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return f"{int(m.group(1)):02d}.{m.group(2)}"
    parole = ocr(img, "--psm 7").upper().replace(" ", "")
    if "ALB" in parole:
        return "ALBA"
    if re.search(r"A\.?M", parole):
        return "A.M."
    if re.search(r"P\.?M", parole):
        return "P.M."
    return ""

def numero(t):
    m = re.search(r"(\d{1,3})[,.](\d{1,2})", t or "")
    if m:
        return float(m.group(1) + "." + m.group(2))
    m = re.search(r"\d{2,3}", t or "")
    return float(m.group(0)) if m else None

def ocr(img, config):
    return pytesseract.image_to_string(img, lang="eng", config=config).strip()

def raddrizza(grigio):
    """La scansione e' un po' storta: si cerca l'angolo (entro 2 gradi) che rende
    piu' nette le righe della tabella e si ruota la pagina."""
    picc = cv2.resize(grigio, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    inv = cv2.adaptiveThreshold(picc, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    oriz = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1)))
    h, w = oriz.shape
    def nitidezza(ang):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1)
        r = cv2.warpAffine(oriz, M, (w, h), flags=cv2.INTER_NEAREST)
        prof = r.sum(axis=1).astype(float)
        return float((prof ** 2).sum())
    migliore = max(np.arange(-2, 2.01, 0.1), key=nitidezza)
    fine = max(np.arange(migliore - 0.1, migliore + 0.101, 0.02), key=nitidezza)
    H, W = grigio.shape
    M = cv2.getRotationMatrix2D((W / 2, H / 2), fine, 1)
    return cv2.warpAffine(grigio, M, (W, H), flags=cv2.INTER_CUBIC, borderValue=255), fine

def leggi(percorso, zona_iniziale="PRINCIPALE"):
    """zona_iniziale: la zona in cui si trovava l'ultima riga della pagina prima.
    Una zona (per esempio MEZZI DIGA) puo' continuare sulla pagina dopo senza
    ripetere il titolo: senza questo, le sue righe sembrerebbero della zona principale."""
    src = cv2.imread(percorso)
    grigio = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    grigio, angolo = raddrizza(grigio)
    f = 3500.0 / grigio.shape[1]   # stessa scala per screenshot e pagine a 300 dpi
    grigio = cv2.resize(grigio, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    bin_inv = cv2.adaptiveThreshold(grigio, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    righe, colonne, oriz, vert = griglia(bin_inv)
    pulita = cv2.bitwise_not(cv2.subtract(bin_inv, cv2.bitwise_or(oriz, vert)))  # testo nero su bianco, senza bordi

    # colonne della tabella principale: H, OP, NAVE, GT, IMO, PR, PP, LOA, LARG, TYPE, ACCOSTO, AGENZIA
    # (servono 13 linee verticali); si prende la sequenza piu' ampia
    risultati = []
    if len(colonne) < 13:
        return {"errore": f"griglia non trovata ({len(colonne)} colonne)", "righe": [],
                "zona_finale": zona_iniziale, "date_titoli": []}
    c = scegli_colonne(colonne)
    nomi = ["H", "OP", "NAVE", "GT", "IMO", "PR", "PP", "LOA", "LARG", "TYPE", "ACCOSTO", "AGENZIA"]
    sezione = None
    # zona del giornale: PRINCIPALE (bacino di Sampierdarena e porto storico),
    # VARIAZIONI (cambi al giornale del giorno prima), AREA MULTEDO, AREA VOLTRI,
    # MEZZI DIGA (draghe, pontoni e motobarche del cantiere della nuova diga)
    zona = zona_iniziale
    date_titoli = []      # date lette nei titoli ("PREVISIONI DEL 27/10/2026"), solo per controllo
    def colonne_locali(y0, y1):
        striscia = bin_inv[y0:y1]
        loc = []
        for g in c:
            a, b = max(0, g - 30), min(striscia.shape[1], g + 31)
            somma = striscia[:, a:b].sum(axis=0)
            loc.append(a + int(np.argmax(somma)) if somma.max() > 255 * (y1 - y0) * 0.5 else g)
        return loc

    def riga_titolo(y0, y1):
        """Le righe dei titoli (PREVISIONI DEL…, AREA MULTEDO, MEZZI DIGA…) sono una
        cella unica: dentro non ci sono le linee verticali delle colonne."""
        presenti = 0
        for g in c[2:11]:
            fascia = vert[y0 + 4:max(y0 + 5, y1 - 4), max(0, g - 10):g + 11]
            if fascia.size and fascia.any(axis=1).mean() > 0.5:
                presenti += 1
        return presenti <= 2

    for y0, y1 in zip(righe, righe[1:]):
        if y1 - y0 < 25:
            continue
        cl = colonne_locali(y0, y1)   # usati solo per ora e operazione
        M = 18  # margine (in pixel ingranditi) per non prendere la colonna accanto
        cella = lambda i: pulita[y0 + 3:y1 - 2, c[i] + M:c[i + 1] - 6]
        intera = pulita[y0 + 3:y1 - 2, c[0] + 4:c[-1] - 3]
        if riga_titolo(y0, y1):
            testo_titolo = ocr(intera, "--psm 7")
            dt = re.search(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(20\d{2})", testo_titolo)
            if dt:
                date_titoli.append(f"{dt.group(3)}-{int(dt.group(2)):02d}-{int(dt.group(1)):02d}")
            titolo = re.sub(r"[^A-Z]", "", testo_titolo.upper())
            if not titolo:
                continue
            simile = lambda parola: difflib.SequenceMatcher(None, parola, titolo).find_longest_match(0, len(parola), 0, len(titolo)).size >= len(parola) - 1
            if simile("DIGA") or simile("MEZZI"):
                zona = "MEZZI DIGA"
            elif simile("MULTEDO"):
                zona = "AREA MULTEDO"
            elif simile("VOLTRI"):
                zona = "AREA VOLTRI"
            elif simile("VARIAZIONI"):
                zona = "VARIAZIONI"
            elif simile("PREVISIONI"):
                zona = "PRINCIPALE"
            for nome in ("ARRIVI", "PARTENZE", "MOVIMENTI"):
                if titolo.startswith(nome[:5]) or (len(titolo) <= len(nome) + 3 and simile(nome)):
                    sezione = nome
            continue
        nave = ocr(cella(2), "--psm 7")
        imo = re.sub(r"\D", "", ocr(cella(4), "--psm 7 -c tessedit_char_whitelist=0123456789"))
        if not nave or (len(imo) < 6 and not re.search(r"[A-Z]{3}", nave.upper())):
            continue
        riga = {
            "zona": zona,
            "sezione": sezione,
            "ora": leggi_ora(pulita[y0 + 3:y1 - 2, cl[0] + 8:cl[1] - 6]),
            "op": ocr(pulita[y0 + 3:y1 - 2, cl[1] + 8:cl[2] - 6], "--psm 7"),
            "nave": re.sub(r"\s+", " ", nave).strip(" |_-—"),
            "imo": imo,
            "imo_ok": imo_valido(imo),
            "accosto": re.sub(r"\s+", " ", ocr(cella(10), "--psm 6")).strip(" |_—"),
            "tipo": ocr(cella(9), "--psm 7"),
            "loa": numero(ocr(cella(7), "--psm 7 -c tessedit_char_whitelist=0123456789,.")),
            "larg": numero(ocr(cella(8), "--psm 7 -c tessedit_char_whitelist=0123456789,.")),
        }
        # l'operazione scritta nella riga vale piu' del titolo (nel giornale "misto" sono alternate)
        op = re.sub(r"[^A-Z]", "", riga["op"].upper())
        if re.search(r"AR+[RI]|RRIV|ARRI", op):
            sezione = "ARRIVI"
        elif re.search(r"PAR|ART|PNT|FANT|PANT", op):
            sezione = "PARTENZE"
        elif re.search(r"MOV|OV", op):
            sezione = "MOVIMENTI"
        riga["sezione"] = sezione
        riga["dove"] = riconosci_accosto(riga["accosto"])
        risultati.append(riga)
    return {"righe": risultati, "colonne": len(colonne), "linee": len(righe), "angolo": round(float(angolo), 2),
            "zona_finale": zona, "date_titoli": date_titoli}

if __name__ == "__main__":
    r = leggi(sys.argv[1])
    testo = json.dumps(r, ensure_ascii=False, indent=1)
    if len(sys.argv) > 2:
        open(sys.argv[2], "w", encoding="utf-8").write(testo)
    else:
        print(testo)

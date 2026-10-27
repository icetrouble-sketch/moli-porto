#!/usr/bin/env python3
"""Prove delle regole di aggiorna_navi.py (nessuna rete, nessun PDF vero).

Uso: python3 -I strumenti/prova_regole.py

Verifica: navi che non si visitano (chemical, ro-ro con passeggeri), zona del
giornale che continua da una pagina all'altra, abbinamento note-data-pulsante del
sito, e la memoria tra i giorni (navi che restano piu' giorni, orari di stasera
che non vanno persi quando arriva il giornale di domani).
"""
import os, sys, tempfile, unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aggiorna_navi as A
import leggi_giornale as L

ROMA = ZoneInfo("Europe/Rome")


def quando(giorno, ora):
    """istante UTC di un orario di Genova"""
    g = datetime.fromisoformat(giorno)
    h, m = map(int, ora.split(":"))
    return g.replace(hour=h, minute=m, tzinfo=ROMA).astimezone(timezone.utc)


def riga(nave, imo, tipo, sezione, ora, accosto="CANEPA LEV. RAD.", zona="PRINCIPALE", loa=200.0):
    return {"zona": zona, "sezione": sezione, "ora": ora, "op": sezione, "nave": nave, "imo": imo, "imo_ok": True,
            "accosto": accosto, "tipo": tipo, "loa": loa, "larg": 30.0, "dove": L.riconosci_accosto(accosto)}


class Esclusioni(unittest.TestCase):
    def navi(self, *righe):
        navi, escluse = A.navi_dal_giornale(list(righe), "2026-10-27")
        return {n["nome"]: n for n in navi}, escluse

    def test_chemical_tutte_le_scritture(self):
        for tipo in ("CHIM.", "CHEM.", "CHEMICAL", "OIL/CHEM", "CHEM TANKER"):
            navi, escluse = self.navi(riga("ALFA", "9074729", tipo, "ARRIVI", "10.00"))
            self.assertEqual(navi, {}, tipo)
            self.assertEqual(escluse[0]["motivo"], "nave chemical", tipo)

    def test_cisterna_normale_resta(self):
        navi, _ = self.navi(riga("BETA", "9074729", "TANK", "ARRIVI", "10.00"))
        self.assertIn("BETA", navi)

    def test_ro_ro_con_passeggeri_dal_testo(self):
        for tipo in ("RO/PAX", "RO-PAX", "RO/RO PAX", "RO/RO PASS."):
            navi, escluse = self.navi(riga("GAMMA", "9074729", tipo, "ARRIVI", "10.00"))
            self.assertEqual(navi, {}, tipo)

    def test_ro_ro_cargo_resta_con_nota_poi_si_decide_con_l_ais(self):
        navi, _ = self.navi(riga("DELTA", "9074729", "RO/RO", "ARRIVI", "10.00"))
        d = {"navi": [navi["DELTA"]], "statici": {}}
        A.escludi_non_visitabili(d, quando("2026-10-27", "09:00"))
        self.assertEqual(len(d["navi"]), 1)
        self.assertIn(A.NOTA_RORO, d["navi"][0]["nota"])          # AIS non ancora: resta, da controllare
        d["navi"][0]["mmsi"] = "247000001"
        d["statici"]["247000001"] = {"tipo": 70}                  # AIS: nave da carico
        A.escludi_non_visitabili(d, quando("2026-10-27", "09:30"))
        self.assertEqual(len(d["navi"]), 1)
        self.assertNotIn("nota", d["navi"][0])                    # confermata cargo: la nota sparisce

    def test_ro_ro_passeggeri_per_l_ais(self):
        for t in (60, 61, 69):
            navi, _ = self.navi(riga("EPSILON", "9074729", "RO/RO", "ARRIVI", "10.00"))
            d = {"navi": [navi["EPSILON"]], "statici": {"247000002": {"tipo": t}}}
            d["navi"][0]["mmsi"] = "247000002"
            A.escludi_non_visitabili(d, quando("2026-10-27", "09:00"))
            self.assertEqual(d["navi"], [], t)
            self.assertTrue(A.e_esclusa(d, "9074729", "EPSILON", "247000002"))

    def test_chimichiera_gia_in_elenco_viene_tolta(self):
        d = {"navi": [{"nome": "ZETA", "imo": "", "tipo": "CHIM."}, {"nome": "ETA", "imo": "", "tipo": "CONT."}], "statici": {}}
        A.escludi_non_visitabili(d, quando("2026-10-27", "09:00"))
        self.assertEqual([n["nome"] for n in d["navi"]], ["ETA"])

    def test_l_ais_non_rimette_una_nave_esclusa(self):
        # banchina finta: lato ponente di un molo, acqua a ovest
        banchine = {"MOLO": {"terminal": "rinfuse", "ponente": [[44.4000, 8.9000], [44.4010, 8.9000]],
                             "acqua": {"ponente": [0, -1]}}}
        adesso = quando("2026-10-27", "10:00")
        d = {"navi": [], "escluse": [], "statici": {"247000003": {"imo": "9074729", "nome": "THETA", "tipo": 80, "lung": 150}},
             "registro": {"247000003": {"lat": 44.4005, "lon": 8.8999, "sog": 0, "nome": "THETA", "stato": 5,
                                        "quando": adesso.isoformat()}}}
        A.aggiorna_presenze(d, banchine, adesso)
        self.assertEqual(len(d["navi"]), 1)                        # senza esclusione l'AIS la aggiunge
        d["navi"] = []
        A.registra_esclusa(d, {"nome": "THETA", "imo": "9074729"}, "nave chemical", adesso)
        A.aggiorna_presenze(d, banchine, adesso)
        self.assertEqual(d["navi"], [])                            # esclusa: non torna


class ZonaTraPagine(unittest.TestCase):
    def _pdf_due_pagine(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as t:
            pdf = os.path.join(t, "g.pdf")
            Image.new("L", (600, 800), 255).save(pdf, save_all=True, append_images=[Image.new("L", (600, 800), 255)])
            return open(pdf, "rb").read()

    def _leggi_con(self, finta):
        originale = L.leggi
        L.leggi = finta
        try:
            return A.leggi_pdf(self._pdf_due_pagine())
        finally:
            L.leggi = originale

    def test_titolo_mezzi_diga_in_fondo_alla_pagina_si_smette_di_leggere(self):
        """Il titolo MEZZI DIGA chiude la pagina 1 senza righe sotto: i bulk della pagina 2 non devono entrare."""
        chiamate = []

        def finta(percorso, zona="PRINCIPALE"):
            chiamate.append(zona)
            return {"righe": [riga("NORMALE", "9074729", "CONT.", "ARRIVI", "08.00")], "zona_finale": "MEZZI DIGA",
                    "date_titoli": ["2026-10-27"]}
        righe, date_titoli = self._leggi_con(finta)
        self.assertEqual(chiamate, ["PRINCIPALE"])                       # la pagina 2 non si legge
        self.assertEqual([r["nave"] for r in righe], ["NORMALE"])
        self.assertEqual(date_titoli, ["2026-10-27"])

    def test_la_zona_passa_alla_pagina_dopo(self):
        """Una zona (qui VARIAZIONI) che continua sulla pagina 2 senza ripetere il titolo."""
        chiamate = []

        def finta(percorso, zona="PRINCIPALE"):
            chiamate.append(zona)
            if len(chiamate) == 1:
                return {"righe": [riga("PRIMA", "9074729", "CONT.", "ARRIVI", "08.00")], "zona_finale": "VARIAZIONI", "date_titoli": []}
            return {"righe": [riga("SECONDA", "9000019", "CONT.", "ARRIVI", "09.00", zona=zona)], "zona_finale": zona, "date_titoli": []}
        righe, _ = self._leggi_con(finta)
        self.assertEqual(chiamate, ["PRINCIPALE", "VARIAZIONI"])
        self.assertEqual(righe[1]["zona"], "VARIAZIONI")

    def test_zona_portata_alla_riga(self):
        righe = [riga("NORMALE", "9074729", "CONT.", "ARRIVI", "08.00"),
                 riga("BULK DIGA", "9000019", "BULK", "ARRIVI", "09.00", zona="MEZZI DIGA")]
        navi, _ = A.navi_dal_giornale(righe, "2026-10-27")
        self.assertEqual([n["nome"] for n in navi], ["NORMALE"])


class FintaSessione:
    def __init__(self, testo):
        self.testo = testo

    def get(self, url, timeout=0):
        testo = self.testo

        class R:
            text = testo

            def raise_for_status(self):
                pass
        return R()


def home(date, pulsanti):
    h = "".join(f'<span>Note della Capitaneria del {d}</span>' for d in date)
    h += "".join(f'<input type="image" name="ctl00$ctl00$corpo$btnCap{p}" />' for p in pulsanti)
    return h


class NoteDelSito(unittest.TestCase):
    def test_abbina_ogni_data_al_suo_pulsante(self):
        note, _ = A.note_sul_sito(FintaSessione(home(["28/10/2026", "27/10/2026", "26/10/2026"], [1, 2, 3])))
        self.assertEqual(note, [("2026-10-28", A.BOTTONE_NOTA + "1"), ("2026-10-27", A.BOTTONE_NOTA + "2"),
                                ("2026-10-26", A.BOTTONE_NOTA + "3")])

    def test_se_non_si_abbina_usa_solo_la_prima(self):
        note, _ = A.note_sul_sito(FintaSessione(home(["28/10/2026", "27/10/2026", "26/10/2026"], [1])))
        self.assertEqual(note, [("2026-10-28", A.BOTTONE_NOTA + "1")])
        note, _ = A.note_sul_sito(FintaSessione(home(["26/10/2026", "28/10/2026"], [1, 2])))   # non in ordine
        self.assertEqual(note, [("2026-10-26", A.BOTTONE_NOTA + "1")])

    def test_nessuna_data_e_un_errore(self):
        with self.assertRaises(RuntimeError):
            A.note_sul_sito(FintaSessione("<html></html>"))

    def test_tentativi(self):
        chiamate = []

        def instabile():
            chiamate.append(1)
            if len(chiamate) < 3:
                raise RuntimeError("giu'")
            return "ok"
        self.assertEqual(A.con_tentativi(instabile, 3, 0), "ok")
        self.assertEqual(len(chiamate), 3)
        with self.assertRaises(RuntimeError):
            A.con_tentativi(lambda: (_ for _ in ()).throw(RuntimeError("sempre giu'")), 2, 0)


class MemoriaTraGiorni(unittest.TestCase):
    def giornale(self, data, *righe):
        navi, _ = A.navi_dal_giornale(list(righe), data)
        return navi

    def setUp(self):
        self.g27 = lambda: self.giornale(
            "2026-10-27",
            riga("ALDEBARAN", "9074729", "CONT.", "ARRIVI", "10.00"),
            riga("BERNARDO", "9000019", "CONT.", "PARTENZE", "23.00"),
            riga("CAPRICORN", "9000020", "BULK", "ARRIVI", "23.30", accosto="RUBATTINO LEV. RAD."),
            riga("DOLOMITI", "9000032", "CONT.", "PARTENZE", "12.00"))
        self.g28 = lambda: self.giornale(
            "2026-10-28",
            riga("ALDEBARAN", "9074729", "CONT.", "PARTENZE", "06.00"),
            riga("ESPERIA", "9000044", "CONT.", "ARRIVI", "08.00"))
        self.sera = quando("2026-10-27", "20:50")

    def assertEventi(self, d, nome, **attesi):
        n = next(x for x in d["navi"] if x["nome"] == nome)
        for k, v in attesi.items():
            self.assertEqual(n.get(k), v, f"{nome}.{k}")
        return n

    def prova_stasera(self, d):
        A.pulizia(d, self.sera)       # toglie le partite (qui DOLOMITI, partita a mezzogiorno)
        nomi = sorted(n["nome"] for n in d["navi"])
        self.assertEqual(nomi, ["ALDEBARAN", "BERNARDO", "CAPRICORN", "ESPERIA"])      # D e' partita da ore
        # A: arrivata oggi alle 10 e parte domani alle 6, tutti e due col loro giorno
        self.assertEventi(d, "ALDEBARAN", arrivo="10.00", arrivo_giorno="2026-10-27", partenza="06.00", partenza_giorno="2026-10-28")
        # B parte stasera alle 23: non si perde quando arriva il giornale di domani
        self.assertEventi(d, "BERNARDO", partenza="23.00", partenza_giorno="2026-10-27")
        # C arriva stasera alle 23.30: non diventa "gia' in porto"
        self.assertEventi(d, "CAPRICORN", arrivo="23.30", arrivo_giorno="2026-10-27")
        self.assertEventi(d, "ESPERIA", arrivo="08.00", arrivo_giorno="2026-10-28")

    def test_prima_oggi_poi_domani(self):
        d = {"navi": []}
        A.unisci_giornale(d, self.g27(), "2026-10-27", quando("2026-10-27", "08:00"))
        A.unisci_giornale(d, self.g28(), "2026-10-28", self.sera)
        self.prova_stasera(d)

    def test_prima_domani_poi_oggi_a_ritroso(self):
        """E' la situazione di stasera: il giornale di domani e' gia' letto, quello di oggi arriva dopo."""
        d = {"navi": []}
        A.unisci_giornale(d, self.g28(), "2026-10-28", self.sera)
        A.unisci_giornale(d, self.g27(), "2026-10-27", self.sera)
        self.prova_stasera(d)

    def test_dati_salvati_prima_della_regola(self):
        """Navi salvate senza i giorni degli orari: prendono quello del loro giornale."""
        d = {"navi": self.g27()}
        for n in d["navi"]:
            for k in ("arrivo_giorno", "arrivo_g", "partenza_giorno", "partenza_g"):
                n.pop(k, None)
            n["giorno"] = "2026-10-27"
        A.unisci_giornale(d, self.g28(), "2026-10-28", self.sera)
        self.prova_stasera(d)

    def test_nave_che_resta_piu_giorni(self):
        """Arrivata il 27 e non piu' nominata nel giornale del 28: da domani e' 'in porto dal 27'."""
        d = {"navi": []}
        A.unisci_giornale(d, self.g27(), "2026-10-27", quando("2026-10-27", "08:00"))
        mattina = quando("2026-10-28", "07:00")
        A.unisci_giornale(d, [], "2026-10-28", mattina)
        n = self.assertEventi(d, "ALDEBARAN", in_porto_dal="2026-10-27")
        self.assertIsNone(n.get("arrivo"))
        self.assertEqual(n["partenza"], "06.00") if n.get("partenza") else None
        capricorn = self.assertEventi(d, "CAPRICORN", in_porto_dal="2026-10-27")   # arrivata ieri sera
        self.assertIsNone(capricorn.get("arrivo"))
        self.assertNotIn("DOLOMITI", [x["nome"] for x in d["navi"]])            # D e' partita ieri

    def test_variazione_corregge_il_giornale_di_ieri(self):
        d = {"navi": []}
        A.unisci_giornale(d, self.g27(), "2026-10-27", quando("2026-10-27", "08:00"))
        variazione = riga("BERNARDO", "9000019", "CONT.", "PARTENZE", "21.30", zona="VARIAZIONI")
        navi, _ = A.navi_dal_giornale([variazione], "2026-10-28")
        A.unisci_giornale(d, navi, "2026-10-28", self.sera)
        self.assertEventi(d, "BERNARDO", partenza="21.30", partenza_giorno="2026-10-27")

    def test_nave_che_riarriva_riparte_la_sosta(self):
        d = {"navi": [{"nome": "ALDEBARAN", "imo": "9074729", "tipo": "CONT.", "giorno": "2026-10-20",
                       "in_porto_dal": "2026-10-20", "arrivo": None, "partenza": None, "dove": {}, "fonti": ["memoria"]}]}
        A.unisci_giornale(d, self.giornale("2026-10-28", riga("ALDEBARAN", "9074729", "CONT.", "ARRIVI", "08.00")),
                          "2026-10-28", self.sera)
        n = self.assertEventi(d, "ALDEBARAN", arrivo="08.00", arrivo_giorno="2026-10-28")
        self.assertNotIn("in_porto_dal", n)

    def test_pulizia_toglie_solo_le_partite(self):
        d = {"navi": []}
        A.unisci_giornale(d, self.g27(), "2026-10-27", quando("2026-10-27", "08:00"))
        A.pulizia(d, quando("2026-10-27", "20:50"))
        nomi = sorted(n["nome"] for n in d["navi"])
        self.assertEqual(nomi, ["ALDEBARAN", "BERNARDO", "CAPRICORN"])      # D e' partita alle 12


if __name__ == "__main__":
    unittest.main(verbosity=2)

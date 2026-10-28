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

    def test_cisterne_escluse_per_ora(self):
        for tipo in ("TANK", "TANKER"):
            navi, escluse = self.navi(riga("BETA", "9074729", tipo, "ARRIVI", "10.00"))
            self.assertEqual(navi, {}, tipo)
            self.assertEqual(escluse[0]["motivo"], "cisterna (da confermare)")
        d = {"navi": [{"nome": "OMEGA", "imo": "", "tipo": "TANK"}], "statici": {}}
        A.escludi_non_visitabili(d, quando("2026-10-27", "09:00"))
        self.assertEqual(d["navi"], [])                              # gia' in elenco: tolta
        A.ESCLUDI_CISTERNE = False                                   # con l'interruttore spento tornano
        try:
            navi, _ = self.navi(riga("BETA", "9074729", "TANK", "ARRIVI", "10.00"))
            self.assertIn("BETA", navi)
        finally:
            A.ESCLUDI_CISTERNE = True

    def test_cisterna_col_tipo_illeggibile_si_riconosce_dall_ais(self):
        d = {"navi": [{"nome": "GOYNUK", "imo": "", "tipo": "MM.", "mmsi": "271000001", "fonti": ["giornale"]},
                      {"nome": "CONTAINER", "imo": "", "tipo": "CONT.", "mmsi": "271000002", "fonti": ["giornale"]}],
             "statici": {"271000001": {"tipo": 82}, "271000002": {"tipo": 70}}}
        A.escludi_non_visitabili(d, quando("2026-10-27", "09:00"))
        self.assertEqual([n["nome"] for n in d["navi"]], ["CONTAINER"])

    def test_cisterna_vista_solo_dall_ais_non_entra(self):
        banchine = {"MOLO": {"terminal": "rinfuse", "ponente": [[44.4000, 8.9000], [44.4010, 8.9000]],
                             "acqua": {"ponente": [0, -1]}}}
        adesso = quando("2026-10-27", "10:00")
        d = {"navi": [], "escluse": [], "statici": {"247000009": {"imo": "9074729", "nome": "SIGMA", "tipo": 82, "lung": 150}},
             "registro": {"247000009": {"lat": 44.4005, "lon": 8.8999, "sog": 0, "nome": "SIGMA", "stato": 5,
                                        "quando": adesso.isoformat()}}}
        A.aggiorna_presenze(d, banchine, adesso)
        self.assertEqual(d["navi"], [])

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
        d = {"navi": [], "escluse": [], "statici": {"247000003": {"imo": "9074729", "nome": "THETA", "tipo": 70, "lung": 150}},
             "registro": {"247000003": {"lat": 44.4005, "lon": 8.8999, "sog": 0, "nome": "THETA", "stato": 5,
                                        "quando": adesso.isoformat()}}}
        A.aggiorna_presenze(d, banchine, adesso)
        self.assertEqual(len(d["navi"]), 1)                        # senza esclusione l'AIS la aggiunge
        d["navi"] = []
        A.registra_esclusa(d, {"nome": "THETA", "imo": "9074729"}, "nave chemical", adesso)
        A.aggiorna_presenze(d, banchine, adesso)
        self.assertEqual(d["navi"], [])                            # esclusa: non torna


BANCHINE_PROVA = {"MOLO": {"terminal": "rinfuse", "ponente": [[44.4000, 8.9000], [44.4010, 8.9000]],
                           "acqua": {"ponente": [0, -1]}}}


def posizione(adesso, minuti_fa=0, lat=44.4005, lon=8.8999, sog=0.0, cog=None):
    from datetime import timedelta
    return {"lat": lat, "lon": lon, "sog": sog, "cog": cog, "stato": 5, "nome": "X",
            "quando": (adesso - timedelta(minutes=minuti_fa)).isoformat()}


class PresenzaInBanchina(unittest.TestCase):
    """Sulla mappa solo le navi che l'AIS vede ferme in banchina; le altre hanno uno stato a parte."""
    adesso = quando("2026-10-27", "10:00")

    def nave(self, mmsi="247000009", **extra):
        n = {"nome": "SIGMA", "imo": "", "mmsi": mmsi, "tipo": "CONT.", "fonti": ["giornale"], "giorno": "2026-10-27",
             "dove": {"terminal": "rinfuse", "molo": "MOLO", "lato": "ponente", "in_rada": False}}
        n.update(extra)
        return n

    # --- navi da non visitare ferme alla banchina
    def test_esclusa_ferma_in_banchina_compare_come_da_non_visitare(self):
        d = {"navi": [], "statici": {"247000003": {"imo": "9074729", "nome": "THETA", "tipo": 80, "lung": 150}},
             "registro": {"247000003": posizione(self.adesso)}}
        A.registra_esclusa(d, {"nome": "THETA", "imo": "9074729", "tipo": "CHIM."}, "nave chemical", self.adesso)
        A.costruisci_non_visitare(d, BANCHINE_PROVA, self.adesso)
        self.assertEqual([(n["nome"], n["motivo"], n["dove"]["terminal"], n["ais"]["ormeggiata"]) for n in d["non_visitare"]],
                         [("THETA", "nave chemical", "rinfuse", True)])

    def test_esclusa_non_ferma_alla_banchina_non_si_mette(self):
        casi = {"in movimento": posizione(self.adesso, sog=6.0),
                "lontana dalla banchina": posizione(self.adesso, lat=44.30),
                "posizione di 7 ore fa": posizione(self.adesso, minuti_fa=7 * 60),
                "mai vista": None}
        for nome, pos in casi.items():
            d = {"navi": [], "statici": {"247000003": {"imo": "9074729", "nome": "THETA", "tipo": 80, "lung": 150}},
                 "registro": {"247000003": pos} if pos else {}}
            A.registra_esclusa(d, {"nome": "THETA", "imo": "9074729"}, "nave chemical", self.adesso)
            A.costruisci_non_visitare(d, BANCHINE_PROVA, self.adesso)
            self.assertEqual(d["non_visitare"], [], nome)

    def test_cisterna_solo_ais_ferma_in_banchina_e_da_non_visitare(self):
        d = {"navi": [], "escluse": [], "statici": {"247000009": {"imo": "9074729", "nome": "SIGMA", "tipo": 82, "lung": 150}},
             "registro": {"247000009": posizione(self.adesso)}}
        A.costruisci_non_visitare(d, BANCHINE_PROVA, self.adesso)
        self.assertEqual([(n["nome"], n["motivo"]) for n in d["non_visitare"]], [("SIGMA", "cisterna (da confermare)")])
        A.ESCLUDI_CISTERNE = False
        try:
            A.costruisci_non_visitare(d, BANCHINE_PROVA, self.adesso)
            self.assertEqual(d["non_visitare"], [])               # interruttore spento: e' una nave da visitare
        finally:
            A.ESCLUDI_CISTERNE = True

    def test_una_nave_da_visitare_non_finisce_anche_tra_le_non_visitare(self):
        d = {"navi": [self.nave()], "escluse": [], "statici": {"247000009": {"nome": "SIGMA", "tipo": 82, "lung": 150}},
             "registro": {"247000009": posizione(self.adesso)}}
        A.costruisci_non_visitare(d, BANCHINE_PROVA, self.adesso)
        self.assertEqual(d["non_visitare"], [])

    # --- quanto vale una posizione
    def test_posizione_di_nave_ferma_vale_sei_ore_quella_in_movimento_tre(self):
        for minuti, sog, atteso in ((5 * 60, 0.0, True), (7 * 60, 0.0, False), (2 * 60, 8.0, True), (4 * 60, 8.0, False)):
            d = {"navi": [self.nave()], "statici": {}, "registro": {"247000009": posizione(self.adesso, minuti, sog=sog, cog=0)}}
            A.aggiorna_presenze(d, BANCHINE_PROVA, self.adesso)
            self.assertEqual("ais" in d["navi"][0], atteso, (minuti, sog))

    # --- si allontana / in avvicinamento
    def test_nave_che_lascia_la_banchina_e_in_uscita_per_un_ora_e_mezza(self):
        from datetime import timedelta
        visto = (self.adesso - timedelta(hours=1)).isoformat()
        d = {"navi": [self.nave(visto_banchina=visto)], "statici": {},
             "registro": {"247000009": posizione(self.adesso, 5, lat=44.30, sog=6.0, cog=180)}}   # verso sud, via da Genova
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.adesso)
        n = d["navi"][0]
        self.assertTrue(n["ais"].get("in_uscita"))
        self.assertNotIn("eta_iso", n["ais"])
        A.pulizia(d, self.adesso + timedelta(minutes=30))
        self.assertEqual(len(d["navi"]), 1)                          # mezz'ora dopo: ancora 'si allontana'
        A.pulizia(d, self.adesso + timedelta(hours=2))
        self.assertEqual(d["navi"], [])                              # due ore dopo: uscita, si toglie

    def test_nave_in_arrivo_non_e_in_uscita(self):
        d = {"navi": [self.nave()], "statici": {},
             "registro": {"247000009": posizione(self.adesso, 5, lat=44.0, sog=12.0, cog=0)}}      # verso nord, verso Genova
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.adesso)
        a = d["navi"][0]["ais"]
        self.assertNotIn("in_uscita", a)
        self.assertIn("eta_iso", a)
        self.assertNotIn("uscita_vista", d["navi"][0])

    def test_visto_in_banchina_tanto_tempo_fa_non_conta_come_uscita(self):
        from datetime import timedelta
        d = {"navi": [self.nave(visto_banchina=(self.adesso - timedelta(hours=30)).isoformat())], "statici": {},
             "registro": {"247000009": posizione(self.adesso, 5, lat=44.0, sog=12.0, cog=0)}}
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.adesso)
        self.assertNotIn("in_uscita", d["navi"][0]["ais"])

    def test_tornata_ferma_in_banchina_si_cancella_l_uscita(self):
        from datetime import timedelta
        d = {"navi": [self.nave(uscita_vista=(self.adesso - timedelta(minutes=20)).isoformat())], "statici": {},
             "registro": {"247000009": posizione(self.adesso)}}
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.adesso)
        self.assertTrue(d["navi"][0]["ais"]["ormeggiata"])
        self.assertNotIn("uscita_vista", d["navi"][0])


class NaviPiccoleEPasseggeri(unittest.TestCase):
    def navi(self, *righe):
        navi, escluse = A.navi_dal_giornale(list(righe), "2026-10-27")
        return {n["nome"]: n for n in navi}, escluse

    def test_general_cargo_non_e_piu_scambiata_per_un_mezzo_di_servizio(self):
        # "N.C" (sigla di servizio) e' dentro "GEN.CARGO": prima la nave veniva scartata
        for tipo in ("GEN.CARGO", "GEN. CARGO", "CARGO"):
            navi, _ = self.navi(riga("EOS", "9074729", tipo, "ARRIVI", "10.00", accosto="RUBATTINO LEV.", loa=80.0))
            self.assertIn("EOS", navi, tipo)

    def test_mezzi_di_servizio_restano_esclusi(self):
        for tipo in ("HOPPER", "PONT.", "MB", "M.B.", "N.C.", "RIM.", "TUG", "BUNKER", "YACHT", "SUPPLY", "BETT."):
            navi, _ = self.navi(riga("EOS", "9074729", tipo, "ARRIVI", "10.00", accosto="RUBATTINO LEV.", loa=80.0))
            self.assertEqual(navi, {}, tipo)
        navi, _ = self.navi(riga("EOS", "9074729", "COMBI", "ARRIVI", "10.00", accosto="RUBATTINO LEV.", loa=80.0))
        self.assertIn("EOS", navi)                                   # "MB" dentro "COMBI" non conta

    def test_ai_rinfuse_entrano_anche_le_navi_piccole(self):
        navi, _ = self.navi(riga("PICCOLA", "9074729", "GEN.CARGO", "ARRIVI", "10.00", accosto="RUBATTINO LEV.", loa=42.0))
        self.assertIn("PICCOLA", navi)                               # 42 m ai Rinfuse: si visita
        navi, _ = self.navi(riga("PICCOLA", "9074729", "GEN.CARGO", "ARRIVI", "10.00", accosto="CANEPA LEV.", loa=42.0))
        self.assertEqual(navi, {})                                   # altrove sotto i 50 m sono mezzi di servizio

    def test_passeggeri_e_crociera_escluse(self):
        for tipo in ("PASS.", "CRUISE", "PASSEGGERI"):
            navi, escluse = self.navi(riga("ALFA", "9074729", tipo, "ARRIVI", "10.00"))
            self.assertEqual(navi, {}, tipo)
            self.assertEqual(escluse[0]["motivo"], "nave passeggeri o da crociera", tipo)
        self.assertEqual(A.motivo_esclusione({"nome": "X", "tipo": "PASS."}, {}), "nave passeggeri o da crociera")

    def test_le_bulk_dei_rinfuse_restano(self):
        navi, _ = self.navi(riga("CARBONIERA", "9074729", "BULK", "ARRIVI", "10.00", accosto="GIACCONE TESTATA", loa=90.0))
        self.assertIn("CARBONIERA", navi)

    def test_solo_ais_cargo_piccolo_ai_rinfuse_si_ma_non_altrove(self):
        adesso = quando("2026-10-27", "10:00")
        def prova(terminal, lung, tipo_ais):
            banchine = {"MOLO": {"terminal": terminal, "ponente": [[44.4000, 8.9000], [44.4010, 8.9000]],
                                 "acqua": {"ponente": [0, -1]}}}
            d = {"navi": [], "escluse": [], "statici": {"247000010": {"imo": "9074729", "nome": "COSTIERA", "tipo": tipo_ais, "lung": lung}},
                 "registro": {"247000010": posizione(adesso)}}
            A.aggiorna_presenze(d, banchine, adesso)
            return d
        self.assertEqual([n["nome"] for n in prova("rinfuse", 70, 70)["navi"]], ["COSTIERA"])
        self.assertEqual(prova("rinfuse", 40, 70)["navi"], [])      # troppo piccola anche per i Rinfuse
        self.assertEqual(prova("imt", 70, 70)["navi"], [])           # altrove servono 100 m
        self.assertEqual(prova("rinfuse", 200, 65)["navi"], [])      # passeggeri: non entra
        d = prova("rinfuse", 70, 70)
        A.pulizia(d, adesso)
        self.assertEqual(len(d["navi"]), 1)                          # e la pulizia non la toglie

    def test_passeggeri_visti_solo_dall_ais_sono_da_non_visitare(self):
        adesso = quando("2026-10-27", "10:00")
        d = {"navi": [], "escluse": [], "statici": {"247000011": {"imo": "9074729", "nome": "TRAGHETTO", "tipo": 60, "lung": 180}},
             "registro": {"247000011": posizione(adesso)}}
        A.costruisci_non_visitare(d, BANCHINE_PROVA, adesso)
        self.assertEqual([(n["nome"], n["motivo"]) for n in d["non_visitare"]], [("TRAGHETTO", "nave passeggeri o da crociera")])


class RegolaDellAssenza(unittest.TestCase):
    """Una nave ferma trasmette ogni 3 minuti. Se per due giri di fila l'AIS sente le altre navi dei moli
    ma non lei: segnale perso, e se e' solo AIS esce dall'elenco. Se non si sente nessuno (calo di copertura)
    la regola non scatta."""
    MMSI = "247000009"
    t0 = quando("2026-10-27", "10:00")

    def nave(self, **extra):
        n = {"nome": "SIGMA", "imo": "", "mmsi": self.MMSI, "tipo": "CONT.", "fonti": ["giornale"], "giorno": "2026-10-27",
             "visto_banchina": self.t0.isoformat(),
             "dove": {"terminal": "rinfuse", "molo": "MOLO", "lato": "ponente", "in_rada": False}}
        n.update(extra)
        return n

    def dati(self, navi, minuti_fa_segnale=0):
        return {"navi": navi, "escluse": [],
                "statici": {self.MMSI: {"imo": "9074729", "nome": "SIGMA", "tipo": 70, "lung": 200}},
                "registro": {self.MMSI: posizione(self.t0, minuti_fa_segnale)}}

    def giro(self, d, minuti, sentite=("999",), ricezione_ok=True):
        """un giro 'minuti' dopo le 10:00: l'AIS ha sentito solo gli MMSI in 'sentite'"""
        from datetime import timedelta
        adesso = self.t0 + timedelta(minutes=minuti)
        A.aggiorna_presenze(d, BANCHINE_PROVA, adesso, set(sentite), ricezione_ok)
        A.pulizia(d, adesso)
        return adesso

    def test_nave_del_giornale_dopo_due_assenze_diventa_segnale_perso_ma_resta(self):
        d = self.dati([self.nave()])
        self.giro(d, 15)
        n = d["navi"][0]
        self.assertEqual(n["assenze_ais"], 1)
        self.assertTrue(n["ais"]["ormeggiata"])
        self.assertNotIn("silenzio", n["ais"])                          # un giro solo: ancora 'in tempo'
        self.assertNotIn("segnale_perso", n)
        self.giro(d, 30)
        n = d["navi"][0]
        self.assertEqual(n["assenze_ais"], 2)
        self.assertTrue(n["ais"]["silenzio"])
        self.assertTrue(n["segnale_perso"])                             # dal giornale: resta, da confermare

    def test_se_la_nave_si_risente_il_conto_riparte(self):
        d = self.dati([self.nave()])
        self.giro(d, 15); self.giro(d, 30)
        self.assertTrue(d["navi"][0]["segnale_perso"])
        d["registro"][self.MMSI] = posizione(self.t0, minuti_fa=-45)    # segnale nuovo
        self.giro(d, 45, sentite=(self.MMSI,))
        n = d["navi"][0]
        self.assertEqual(n["assenze_ais"], 0)
        self.assertNotIn("silenzio", n["ais"])
        self.assertNotIn("segnale_perso", n)

    def test_senza_buona_ricezione_il_silenzio_non_conta(self):
        d = self.dati([self.nave()])
        for minuti in (15, 30, 45, 60, 75):
            self.giro(d, minuti, sentite=(), ricezione_ok=False)
        n = d["navi"][0]
        self.assertEqual(n.get("assenze_ais", 0), 0)
        self.assertNotIn("segnale_perso", n)

    def test_senza_sapere_chi_e_stato_sentito_la_regola_non_scatta(self):
        d = self.dati([self.nave()])
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.t0)                  # chiamata 'vecchia maniera'
        self.assertNotIn("assenze_ais", d["navi"][0])

    def test_nave_non_ancora_arrivata_non_accumula_assenze(self):
        n = self.nave()
        n.pop("visto_banchina")                                          # mai vista ferma in banchina
        d = self.dati([n])
        d["registro"][self.MMSI] = posizione(self.t0, lat=44.30, sog=12.0, cog=0)   # ultima posizione: in mare
        self.giro(d, 15); self.giro(d, 30); self.giro(d, 45)
        self.assertEqual(d["navi"][0].get("assenze_ais", 0), 0)
        self.assertNotIn("segnale_perso", d["navi"][0])

    def test_nave_che_se_ne_sta_andando_non_accumula_assenze(self):
        d = self.dati([self.nave(uscita_vista=self.t0.isoformat())])
        self.giro(d, 15)
        self.assertEqual(d["navi"][0].get("assenze_ais", 0), 0)

    def test_nave_solo_ais_dopo_due_assenze_esce_e_non_rientra_dal_registro(self):
        d = self.dati([self.nave(fonti=["ais"])])
        self.giro(d, 15)
        self.assertEqual(len(d["navi"]), 1)
        self.giro(d, 30)
        self.assertEqual(d["navi"], [])                                  # sparita
        self.assertIn(self.MMSI, d["spente"])
        self.giro(d, 45)                                                 # il registro ha ancora la vecchia posizione
        self.assertEqual(d["navi"], [])
        d["registro"][self.MMSI] = posizione(self.t0, minuti_fa=-60)     # ma se ricompare un segnale nuovo...
        self.giro(d, 60, sentite=(self.MMSI,))
        self.assertEqual([n["nome"] for n in d["navi"]], ["SIGMA"])      # ...rientra

    def test_nave_del_giornale_con_partenza_passata_e_silenzio_si_toglie_invece_di_ritardo(self):
        from datetime import timedelta
        adesso = self.t0 + timedelta(hours=1)
        for assenze, atteso in ((0, ["SIGMA"]), (2, [])):
            n = self.nave(partenza="09.00", assenze_ais=assenze, visto_banchina=(adesso - timedelta(minutes=10)).isoformat(),
                          ais={"ormeggiata": True})
            d = {"navi": [n]}
            A.pulizia(d, adesso)
            self.assertEqual([x["nome"] for x in d["navi"]], atteso, assenze)
        d = {"navi": [self.nave(partenza="09.00", visto_banchina=(adesso - timedelta(minutes=10)).isoformat(), ais={"ormeggiata": True})]}
        A.pulizia(d, adesso)
        self.assertTrue(d["navi"][0]["ritardo"])                         # senza silenzio vale la regola di sempre

    # --- nuove navi solo AIS: serve un segnale fresco, se la ricezione e' buona
    def test_solo_ais_nuova_nave_serve_segnale_di_meno_di_40_minuti(self):
        for minuti, ok, atteso in ((30, True, 1), (50, True, 0), (50, False, 1), (50, None, 1), (7 * 60, False, 0)):
            d = self.dati([], minuti_fa_segnale=minuti)
            A.aggiorna_presenze(d, BANCHINE_PROVA, self.t0, {"999"} if ok is not None else None, ok)
            self.assertEqual(len(d["navi"]), atteso, (minuti, ok))

    def test_non_visitare_con_buona_ricezione_vale_il_segnale_fresco(self):
        d = {"navi": [], "escluse": [], "statici": {self.MMSI: {"imo": "9074729", "nome": "THETA", "tipo": 80, "lung": 150}},
             "registro": {self.MMSI: posizione(self.t0, 90)}}
        A.registra_esclusa(d, {"nome": "THETA", "imo": "9074729", "tipo": "CHIM."}, "nave chemical", self.t0)
        A.costruisci_non_visitare(d, BANCHINE_PROVA, self.t0, True)
        self.assertEqual(d["non_visitare"], [])                          # ferma da 90 minuti: partita
        A.costruisci_non_visitare(d, BANCHINE_PROVA, self.t0, False)
        self.assertEqual(len(d["non_visitare"]), 1)                      # senza buona ricezione non si sa: resta


class MisuraBanchine(unittest.TestCase):
    def test_misura_scostamento_laterale_e_sporgenza_oltre_la_fine(self):
        # banchina lunga ~111 m (da 44.4000 a 44.4010 a lon 8.9000), acqua a ovest
        lat = 44.4010 + 90 / A.KY                                 # 90 m oltre la fine
        lon = 8.9000 - 30 / A.KX                                  # 30 m dal lato acqua
        m = A.misura_banchine(lat, lon, BANCHINE_PROVA)[0]
        self.assertAlmostEqual(m["perp"], 30, delta=0.5)
        self.assertAlmostEqual(m["fuori"], 90, delta=0.5)
        self.assertAlmostEqual(m["lunga"], 111.3, delta=0.5)
        self.assertGreater(m["verso"], 0)                         # dalla parte dell'acqua
        self.assertAlmostEqual(m["d"], (30 ** 2 + (90 - 0.1 * 111.3) ** 2) ** 0.5, delta=0.5)   # il tratto si allunga di un decimo
        self.assertIsNone(A.alla_banchina(lat, lon, BANCHINE_PROVA))   # a 90 m oltre la fine (84 m dal tratto) non e' 'alla banchina'
        di_fronte = A.misura_banchine(44.4005, lon, BANCHINE_PROVA)[0]
        self.assertAlmostEqual(di_fronte["fuori"], 0, delta=0.5)
        self.assertEqual(A.alla_banchina(44.4005, lon, BANCHINE_PROVA)["molo"], "MOLO")


class NaveLungaOltreIlTratto(unittest.TestCase):
    """La Chennai Express (304 m) a Ponte Etiopia: antenna a 27 m dalla linea di banchina ma 112 m oltre la fine
    del tratto disegnato (il tratto e' lungo 202 m). La banchina vera e' piu' lunga della mappa."""
    t0 = quando("2026-10-27", "10:00")
    FINE = 44.4010                                               # fine del tratto di prova (banchina nord-sud, acqua a ovest)

    def punto(self, oltre=112, lato=27):
        return self.FINE + oltre / A.KY, 8.9000 - lato / A.KX

    def test_nave_lunga_parallela_alla_banchina_oltre_la_fine_del_tratto_e_alla_banchina(self):
        lat, lon = self.punto()
        d = A.alla_banchina(lat, lon, BANCHINE_PROVA, 304, 190)       # prua 190: parallela alla banchina (nord-sud)
        self.assertEqual((d["molo"], d["lato"], d["oltre_m"]), ("MOLO", "ponente", 112))

    def test_la_regola_larga_vale_solo_con_le_condizioni_giuste(self):
        lat, lon = self.punto()
        casi = {"nave corta (100 m)": (lat, lon, 100, 190), "prua di traverso": (lat, lon, 304, 90),
                "prua sconosciuta (511)": (lat, lon, 304, 511), "senza prua": (lat, lon, 304, None),
                "troppo oltre la fine (200 m)": (*self.punto(oltre=200), 304, 190),
                "troppo lontana di lato (60 m)": (*self.punto(lato=60), 304, 190),
                "dalla parte della terra": (self.FINE + 112 / A.KY, 8.9000 + 27 / A.KX, 304, 190)}
        for nome, (la, lo, lung, rotta) in casi.items():
            self.assertIsNone(A.alla_banchina(la, lo, BANCHINE_PROVA, lung, rotta), nome)

    def test_chi_e_gia_entro_75_m_resta_come_prima_senza_regola_larga(self):
        d = A.alla_banchina(44.4005, 8.9000 - 20 / A.KX, BANCHINE_PROVA, 304, None)
        self.assertEqual(d["molo"], "MOLO")
        self.assertNotIn("oltre_m", d)

    def test_prua_parallela_nei_due_versi_e_con_giro_di_360(self):
        self.assertTrue(A.parallela(190, 0)); self.assertTrue(A.parallela(10, 0)); self.assertTrue(A.parallela(355, 0))
        self.assertTrue(A.parallela(234, 54)); self.assertFalse(A.parallela(90, 0)); self.assertFalse(A.parallela(None, 0))

    def test_l_ais_mette_in_elenco_la_nave_lunga_vista_oltre_il_tratto(self):
        mmsi = "636092930"
        lat, lon = self.punto()
        p = posizione(self.t0, lat=lat, lon=lon); p["rotta"] = 190
        d = {"navi": [], "escluse": [], "statici": {mmsi: {"imo": "9306158", "nome": "CHENNAI EXPRESS", "tipo": 71, "lung": 304, "larg": 40}},
             "registro": {mmsi: p}}
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.t0)
        self.assertEqual([n["nome"] for n in d["navi"]], ["CHENNAI EXPRESS"])
        self.assertTrue(d["navi"][0]["ais"]["ormeggiata"])


class ConteggiVecchi(unittest.TestCase):
    def test_i_conteggi_di_una_regola_vecchia_si_azzerano_una_volta_sola(self):
        d = {"navi": [{"nome": "X", "assenze_ais": 2, "segnale_perso": True}]}
        A.azzera_conteggi_vecchi(d)
        self.assertNotIn("assenze_ais", d["navi"][0]); self.assertNotIn("segnale_perso", d["navi"][0])
        self.assertEqual(d["regola_assenza"], A.VERSIONE_REGOLA_ASSENZA)
        d["navi"][0]["assenze_ais"] = 1
        A.azzera_conteggi_vecchi(d)
        self.assertEqual(d["navi"][0]["assenze_ais"], 1)                    # stessa versione: non si tocca


class QualitaDellaRicezione(unittest.TestCase):
    t0 = quando("2026-10-27", "10:00")

    def registro(self, n, minuti_fa=10):
        return {str(300 + i): posizione(self.t0, minuti_fa) for i in range(n)}

    def test_buona_se_si_sente_la_maggior_parte_delle_navi_ferme(self):
        d = {"registro": self.registro(10)}
        sentite = {str(300 + i) for i in range(7)}
        self.assertEqual(A.qualita_ricezione(d, sentite, self.t0), (True, 10, 7))
        self.assertEqual(A.qualita_ricezione(d, {str(300 + i) for i in range(5)}, self.t0), (False, 10, 5))   # 5 su 10: scarsa

    def test_non_si_giudica_con_poche_navi_di_riferimento_o_vecchie(self):
        self.assertFalse(A.qualita_ricezione({"registro": self.registro(4)}, set(self.registro(4)), self.t0)[0])
        self.assertEqual(A.qualita_ricezione({"registro": self.registro(10, 150)}, set(), self.t0)[1], 0)   # segnali di 2,5 ore fa
        in_moto = {"registro": {"1": posizione(self.t0, sog=8.0), "2": posizione(self.t0, lat=44.30)}}
        self.assertEqual(A.qualita_ricezione(in_moto, {"1", "2"}, self.t0)[1], 0)                          # in moto o fuori dai moli


class DiagnosiDelGiro(unittest.TestCase):
    t0 = quando("2026-10-27", "10:00")

    def righe(self, d, sentite=None, ricezione_ok=True):
        del A.RIEPILOGO[:]
        A.diagnostica_moli(d, BANCHINE_PROVA, self.t0, sentite, ricezione_ok)
        return "\n".join(A.RIEPILOGO)

    def test_nave_grande_ferma_nei_moli_ma_fuori_elenco_dice_perche(self):
        casi = {"velocita' 1.2 nodi": ("247000020", posizione(self.t0, sog=1.2), {"tipo": 70, "lung": 300}),
                "tipo AIS 99": ("247000021", posizione(self.t0), {"tipo": 99, "lung": 300}),
                "non e' a ridosso": ("247000022", posizione(self.t0, lat=44.4005, lon=8.9015), {"tipo": 70, "lung": 300}),   # a 120 m
                "dati statici": ("247000023", posizione(self.t0), None)}
        for atteso, (mmsi, p, s) in casi.items():
            d = {"navi": [], "escluse": [], "statici": {mmsi: {"nome": "GRANDE", **s}} if s else {}, "registro": {mmsi: p}}
            self.assertIn(atteso, self.righe(d), atteso)

    def test_navi_lontane_da_ogni_banchina_non_si_elencano(self):
        d = {"navi": [], "escluse": [], "statici": {"247000024": {"nome": "ALL'ANCORA", "tipo": 70, "lung": 300}},
             "registro": {"247000024": posizione(self.t0, lat=44.4050, lon=8.9100)}}      # a ~800 m dalla banchina
        self.assertNotIn("diagnosi, ferma nei moli", self.righe(d))

    def test_rimorchiatori_e_navi_in_elenco_non_si_elencano(self):
        d = {"navi": [{"nome": "IN ELENCO", "mmsi": "247000030"}], "escluse": [],
             "statici": {"247000030": {"tipo": 70, "lung": 300}, "247000031": {"tipo": 52, "lung": 30}},
             "registro": {"247000030": posizione(self.t0), "247000031": posizione(self.t0)}}
        self.assertNotIn("diagnosi, ferma nei moli", self.righe(d))

    def test_nave_seguita_assente_dal_registro_lo_dice(self):
        d = {"navi": [], "escluse": [], "statici": {}, "registro": {}}
        self.assertIn("non c'e' nel registro", self.righe(d, set()))
        seguita = A.SEGUITE[0]
        d = {"navi": [], "escluse": [], "statici": {seguita: {"nome": "CHENNAI EXPRESS", "tipo": 71, "lung": 304}},
             "registro": {seguita: posizione(self.t0)}}
        testo = self.righe(d, set())
        self.assertIn("CHENNAI EXPRESS", testo)
        self.assertIn("NON sentita in questo giro", testo)
        self.assertIn("banchina piu' vicina MOLO ponente", testo)


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

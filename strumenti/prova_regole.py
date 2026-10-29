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
    """Una nave ferma trasmette ogni 3 minuti. Se per tre giri di fila (o di piu', per le navi che l'AIS sente male)
    l'AIS sente le altre navi dei moli ma non lei: segnale perso, e se e' solo AIS esce dall'elenco. Se non si sente
    nessuno (calo di copertura) la regola non scatta."""
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

    def test_nave_del_giornale_dopo_tre_assenze_diventa_segnale_perso_ma_resta(self):
        d = self.dati([self.nave()])
        for minuti, assenze in ((15, 1), (30, 2)):
            self.giro(d, minuti)
            n = d["navi"][0]
            self.assertEqual(n["assenze_ais"], assenze)
            self.assertTrue(n["ais"]["ormeggiata"])
            self.assertNotIn("silenzio", n["ais"])                      # uno o due giri: ancora 'in tempo'
            self.assertNotIn("segnale_perso", n)
        self.giro(d, 45)
        n = d["navi"][0]
        self.assertEqual(n["assenze_ais"], 3)
        self.assertTrue(n["ais"]["silenzio"])
        self.assertTrue(n["segnale_perso"])                             # dal giornale: resta, da confermare

    def test_due_giri_mancati_non_bastano_piu(self):
        """28/10 14:33-16:58: GOYNUK, CHENNAI EXPRESS ed ELISABETH RUSS, in banchina, non sentite per due giri di fila
        con 'buona' ricezione, e risentite al terzo. Con la regola dei due giri passavano a 'segnale perso'."""
        d = self.dati([self.nave()])
        self.giro(d, 15); self.giro(d, 30)
        self.assertNotIn("segnale_perso", d["navi"][0])
        d["registro"][self.MMSI] = posizione(self.t0, minuti_fa=-45)
        self.giro(d, 45, sentite=(self.MMSI,))
        self.assertEqual(d["navi"][0]["assenze_ais"], 0)

    def test_le_navi_sentite_male_aspettano_di_piu(self):
        self.assertEqual(A.assenze_necessarie({}), 3)                           # niente storia: 3 giri
        self.assertEqual(A.assenze_necessarie({"ascolto": [20, 20]}), 3)          # sentita sempre
        self.assertEqual(A.assenze_necessarie({"ascolto": [6, 10]}), 4)           # 6 volte su 10
        self.assertEqual(A.assenze_necessarie({"ascolto": [5, 10]}), 5)           # una volta su due
        self.assertEqual(A.assenze_necessarie({"ascolto": [2, 20]}), 6)           # quasi mai: al massimo 6
        d = self.dati([self.nave(ascolto=[5, 10])])
        for minuti in (15, 30, 45, 60):
            self.giro(d, minuti)
        self.assertNotIn("segnale_perso", d["navi"][0])                       # 4 giri: per lei non bastano
        self.giro(d, 75)
        self.assertTrue(d["navi"][0]["segnale_perso"])

    def test_se_la_nave_si_risente_il_conto_riparte(self):
        d = self.dati([self.nave()])
        self.giro(d, 15); self.giro(d, 30); self.giro(d, 45)
        self.assertTrue(d["navi"][0]["segnale_perso"])
        d["registro"][self.MMSI] = posizione(self.t0, minuti_fa=-60)    # segnale nuovo
        self.giro(d, 60, sentite=(self.MMSI,))
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

    def test_nave_solo_ais_dopo_tre_assenze_esce_e_non_rientra_dal_registro(self):
        d = self.dati([self.nave(fonti=["ais"])])
        self.giro(d, 15); self.giro(d, 30)
        self.assertEqual(len(d["navi"]), 1)
        self.giro(d, 45)
        self.assertEqual(d["navi"], [])                                  # sparita
        self.assertIn(self.MMSI, d["spente"])
        self.giro(d, 60)                                                 # il registro ha ancora la vecchia posizione
        self.assertEqual(d["navi"], [])
        d["registro"][self.MMSI] = posizione(self.t0, minuti_fa=-75)     # ma se ricompare un segnale nuovo...
        self.giro(d, 75, sentite=(self.MMSI,))
        self.assertEqual([n["nome"] for n in d["navi"]], ["SIGMA"])      # ...rientra

    def test_nave_del_giornale_con_partenza_passata_e_silenzio_si_toglie_invece_di_ritardo(self):
        from datetime import timedelta
        adesso = self.t0 + timedelta(hours=1)
        for assenze, atteso in ((0, ["SIGMA"]), (3, [])):
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


class GiriDellaNotte(unittest.TestCase):
    """Di notte i giri sono tre (23, 3, 6) e distanti ore. La ricezione si giudica guardando indietro fino
    al giro prima, e si ascolta di piu'. Con la finestra di 2 ore la ricezione sarebbe sembrata sempre
    perfetta (le navi non sentite, piu' vecchie di 2 ore, non contavano) e ogni nave non sentita sarebbe
    passata a 'segnale perso' al primo calo di copertura."""
    MMSI = "247000009"
    t0 = quando("2026-10-28", "03:05")

    def test_la_finestra_di_giorno_resta_di_due_ore(self):
        from datetime import timedelta
        self.assertEqual(A.finestra_ricezione({}, self.t0), timedelta(hours=2))                       # primo giro in assoluto
        self.assertEqual(A.finestra_ricezione({"aggiornato": "non una data"}, self.t0), timedelta(hours=2))
        for minuti in (15, 30, 70):
            d = {"aggiornato": (self.t0 - timedelta(minutes=minuti)).isoformat()}
            self.assertEqual(A.finestra_ricezione(d, self.t0), timedelta(hours=2), minuti)

    def test_la_finestra_di_notte_arriva_al_giro_prima_e_non_oltre_otto_ore(self):
        from datetime import timedelta
        d = {"aggiornato": (self.t0 - timedelta(hours=4)).isoformat()}
        self.assertEqual(A.finestra_ricezione(d, self.t0), timedelta(hours=4, minutes=45))
        d = {"aggiornato": (self.t0 - timedelta(hours=20)).isoformat()}
        self.assertEqual(A.finestra_ricezione(d, self.t0), timedelta(hours=8))

    def test_di_notte_le_navi_non_sentite_pesano_sulla_ricezione(self):
        from datetime import timedelta
        # 5 navi sentite adesso e 5 sentite 4 ore fa (giro delle 23) e questa volta no
        reg = {str(300 + i): posizione(self.t0, minuti_fa=0 if i < 5 else 240) for i in range(10)}
        d = {"registro": reg, "aggiornato": (self.t0 - timedelta(hours=3, minutes=40)).isoformat()}
        sentite = {str(300 + i) for i in range(5)}
        self.assertEqual(A.qualita_ricezione(d, sentite, self.t0), (True, 5, 5))        # finestra di 2 ore: sembrava perfetta (sbagliato)
        f = A.finestra_ricezione(d, self.t0)
        self.assertEqual(A.qualita_ricezione(d, sentite, self.t0, f), (False, 10, 5))   # con la finestra giusta: 5 su 10, scarsa
        sentite = {str(300 + i) for i in range(8)}
        for i in range(5, 8):
            reg[str(300 + i)] = posizione(self.t0, 0)                                  # tre delle altre cinque si sentono adesso
        self.assertEqual(A.qualita_ricezione(d, sentite, self.t0, f), (True, 10, 8))

    def test_tre_giri_di_notte_di_assenza_fanno_segnale_perso(self):
        from datetime import timedelta
        n = {"nome": "SIGMA", "imo": "", "mmsi": self.MMSI, "tipo": "CONT.", "fonti": ["giornale"], "giorno": "2026-10-27",
             "visto_banchina": (self.t0 - timedelta(hours=4)).isoformat(),
             "dove": {"terminal": "rinfuse", "molo": "MOLO", "lato": "ponente", "in_rada": False}}
        d = {"navi": [n], "escluse": [], "statici": {self.MMSI: {"imo": "9074729", "nome": "SIGMA", "tipo": 70, "lung": 200}},
             "registro": {self.MMSI: posizione(self.t0, minuti_fa=240)}}              # ultimo segnale alle 23:05
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.t0, {"999"}, True)                  # giro delle 3: sentite le altre, lei no
        A.pulizia(d, self.t0)
        self.assertEqual(d["navi"][0]["assenze_ais"], 1)
        self.assertTrue(d["navi"][0]["ais"]["ormeggiata"])                              # segnale di 4 ore fa: ancora valido
        t6 = self.t0 + timedelta(hours=3)
        A.aggiorna_presenze(d, BANCHINE_PROVA, t6, {"999"}, True)                       # giro delle 6
        A.pulizia(d, t6)
        self.assertEqual(d["navi"][0]["assenze_ais"], 2)
        self.assertNotIn("segnale_perso", d["navi"][0])
        t7 = self.t0 + timedelta(hours=4)
        A.aggiorna_presenze(d, BANCHINE_PROVA, t7, {"999"}, True)                       # giro delle 7
        A.pulizia(d, t7)
        self.assertEqual(d["navi"][0]["assenze_ais"], 3)
        self.assertTrue(d["navi"][0]["segnale_perso"])

    def test_ascolto_piu_lungo_di_notte(self):
        from datetime import datetime
        salvato = os.environ.pop("AIS_SECONDI", None)
        try:
            def sec(h, m=0):
                return A.secondi_ascolto(datetime(2026, 10, 28, h, m, tzinfo=ROMA))
            for h, m in ((23, 5), (3, 5), (6, 5), (6, 59), (22, 30), (0, 0)):
                self.assertEqual(sec(h, m), A.ASCOLTO_NOTTE, (h, m))
            self.assertEqual(sec(7, 5), 720)                                            # primo giro del mattino
            for h, m in ((7, 30), (7, 45), (12, 0), (20, 59), (21, 0), (21, 4)):
                self.assertEqual(sec(h, m), 600, (h, m))
            os.environ["AIS_SECONDI"] = "30"
            self.assertEqual(sec(3, 5), 30)                                             # la variabile d'ambiente vince
        finally:
            os.environ.pop("AIS_SECONDI", None)
            if salvato is not None:
                os.environ["AIS_SECONDI"] = salvato


class OrarioDeiGiri(unittest.TestCase):
    """strumenti/orario_giro.py: di giorno un giro ogni 15 minuti (7:00-20:45), di notte uno alle 23, alle 3 e alle 6.
    Un giro saltato o in ritardo si fa appena possibile, uno gia' fatto non si rifa'. L'esecuzione (staffetta) aspetta
    l'orario del giro: non dipende piu' dalle partenze programmate di GitHub."""

    @staticmethod
    def attesa(giorno, ora, ultimo=None):
        """minuti da aspettare. ultimo: 'AAAA-MM-GG HH:MM' di fine dell'ultimo giro (ora di Genova) o None"""
        import orario_giro as O
        u = quando(*ultimo.split()) if ultimo else None
        return O.attesa(quando(giorno, ora), u) / 60

    def test_di_giorno_un_giro_ogni_quindici_minuti(self):
        self.assertEqual(self.attesa("2026-10-28", "12:31", "2026-10-28 12:11"), 0)          # quello delle 12:30 e' dovuto
        self.assertAlmostEqual(self.attesa("2026-10-28", "12:41", "2026-10-28 12:41"), 4, delta=0.1)   # fatto: alle 12:45
        self.assertEqual(self.attesa("2026-10-28", "07:00", "2026-10-28 06:21"), 0)          # il primo del mattino
        self.assertAlmostEqual(self.attesa("2026-10-28", "07:13", "2026-10-28 07:13"), 2, delta=0.1)

    def test_la_sera_dopo_le_20_45_si_aspetta_le_23(self):
        self.assertAlmostEqual(self.attesa("2026-10-28", "20:56", "2026-10-28 20:56"), 124, delta=0.1)
        self.assertAlmostEqual(self.attesa("2026-10-28", "21:30", "2026-10-28 20:56"), 90, delta=0.1)

    def test_un_giro_alle_23_alle_3_alle_6(self):
        self.assertEqual(self.attesa("2026-10-28", "23:00", "2026-10-28 20:56"), 0)
        self.assertAlmostEqual(self.attesa("2026-10-28", "23:21", "2026-10-28 23:21"), 219, delta=0.1)   # alle 3
        self.assertEqual(self.attesa("2026-10-29", "03:00", "2026-10-28 23:21"), 0)
        self.assertAlmostEqual(self.attesa("2026-10-29", "03:21", "2026-10-29 03:21"), 159, delta=0.1)   # alle 6
        self.assertEqual(self.attesa("2026-10-29", "06:00", "2026-10-29 03:21"), 0)
        self.assertAlmostEqual(self.attesa("2026-10-29", "06:21", "2026-10-29 06:21"), 39, delta=0.1)    # alle 7

    def test_una_volta_fatto_il_giro_non_si_rifa(self):
        for g, o, ultimo in (("2026-10-28", "23:35", "2026-10-28 23:21"), ("2026-10-29", "00:05", "2026-10-28 23:21"),
                             ("2026-10-29", "02:05", "2026-10-28 23:21"), ("2026-10-29", "04:05", "2026-10-29 03:21"),
                             ("2026-10-29", "05:05", "2026-10-29 03:21"), ("2026-10-29", "06:35", "2026-10-29 06:21")):
            self.assertGreater(self.attesa(g, o, ultimo), 0, (g, o))

    def test_un_giro_saltato_o_in_ritardo_si_fa_subito(self):
        self.assertEqual(self.attesa("2026-10-29", "04:05", "2026-10-28 23:21"), 0)    # il giro delle 3 e' saltato
        self.assertEqual(self.attesa("2026-10-29", "00:05", "2026-10-28 20:56"), 0)    # saltato quello delle 23
        self.assertEqual(self.attesa("2026-10-29", "06:50", "2026-10-29 03:21"), 0)    # arrivato tardi
        self.assertEqual(self.attesa("2026-10-28", "14:52", "2026-10-28 14:20"), 0)    # di giorno: quello delle 14:45

    def test_senza_storia_o_avvio_a_mano_si_fa_subito(self):
        self.assertEqual(self.attesa("2026-10-29", "01:05", None), 0)
        self.assertEqual(self.attesa("2026-10-28", "14:00", None), 0)

    def test_la_staffetta_di_notte_aspetta_l_orario(self):
        """L'esecuzione che parte all'1 (passaggio di testimone) non rifa' il giro delle 23: aspetta le 3."""
        self.assertAlmostEqual(self.attesa("2026-10-29", "01:00", "2026-10-28 23:21"), 120, delta=0.1)

    def test_il_cambio_dell_ora_legale_non_confonde(self):
        # notte del 25/10/2026: le 2-3 vengono due volte. Il giro delle 3 si fa, quello delle 6 pure.
        self.assertEqual(self.attesa("2026-10-25", "04:05", "2026-10-24 23:21"), 0)
        self.assertEqual(self.attesa("2026-10-25", "06:05", "2026-10-25 03:40"), 0)
        self.assertGreater(self.attesa("2026-10-25", "05:05", "2026-10-25 03:40"), 0)
        self.assertEqual(self.attesa("2026-03-29", "03:05", "2026-03-28 23:21"), 0)    # notte dell'ora legale
        self.assertAlmostEqual(self.attesa("2026-03-29", "03:21", "2026-03-29 03:21"), 159, delta=0.1)

    def test_da_riga_di_comando(self):
        import subprocess
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orario_giro.py")
        adesso, ultimo = int(quando("2026-10-28", "12:41").timestamp()), int(quando("2026-10-28", "12:41").timestamp())
        r = subprocess.run([sys.executable, "-I", script, "attesa", str(ultimo), str(adesso)], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stdout.strip(), "240")
        r = subprocess.run([sys.executable, "-I", script, "attesa", "0", str(adesso)], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stdout.strip(), "0")
        r = subprocess.run([sys.executable, "-I", script, "descrivi", str(ultimo), str(adesso)], capture_output=True, text=True, timeout=20)
        self.assertIn("12:45", r.stdout)


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


class NaviGemelle(unittest.TestCase):
    """Navi con nomi quasi uguali (ARGO I e ARGO II, NORDIC ANNE e NORDIC ANNA) non sono la stessa nave.
    Controllo del 28/10: la nave del giornale prendeva IMO e posizione della gemella, due navi si fondevano in una
    e la conferma di partenza della gemella dal sito si attaccava alla nostra."""
    T = quando("2026-10-28", "10:05")

    def test_i_numeri_nel_nome_distinguono(self):
        for a, b in (("ARGO I", "ARGO II"), ("XIN YA ZHOU 1", "XIN YA ZHOU 2"), ("STAR ATLANTIC", "STAR ATLANTIS 2")):
            self.assertFalse(A.simili(a, b), (a, b))
        for a, b in (("JOLLY GIADA", "JOLLY GlADA"), ("MSC RITA V", "MSC RITA V"), ("SEASPAN BRIGHTNESS", "SEASPAN BRIGHTNES"),
                     ("MSC EDITH II", "MSC EDITH IL"), ("ARGO I", "ARGO 1")):
            self.assertTrue(A.simili(a, b), (a, b))                           # letture sbagliate del giornale: stessa nave

    def test_con_due_imo_validi_decide_l_imo(self):
        self.assertFalse(A.stessa_nave("NORDIC ANNE", "9074729", "NORDIC ANNA", "9395044"))
        self.assertTrue(A.stessa_nave("NORDIC ANNE", "9074729", "NORDIC ANNE", "9395044"))   # stesso nome: IMO letto male
        self.assertTrue(A.stessa_nave("NORDIC ANNE", "9074729", "NORDIC ANNA", ""))         # un IMO manca: decide il nome
        self.assertTrue(A.stessa_nave("NORDIC ANNE", "1234568", "NORDIC ANNA", "9395044"))  # IMO non valido: decide il nome

    def test_la_nave_del_giornale_non_prende_la_gemella_dell_ais(self):
        d = {"navi": [{"nome": "NORDIC ANNE", "imo": "9074729", "tipo": "BULK", "fonti": ["giornale"],
                       "dove": {"terminal": "rinfuse", "molo": "MOLO", "lato": "ponente", "in_rada": False}}],
             "statici": {"111": {"imo": "9395044", "nome": "NORDIC ANNA", "lung": 190, "tipo": 70}},
             "registro": {"111": posizione(self.T)}}
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.T)
        n = d["navi"][0]
        self.assertEqual((n["nome"], n["imo"]), ("NORDIC ANNE", "9074729"))
        self.assertNotIn("mmsi", n)
        self.assertNotIn("ais", n)
        # la gemella vera, ferma in banchina, entra per conto suo (solo AIS)
        self.assertIn("NORDIC ANNA", [x["nome"] for x in d["navi"]])

    def test_un_abbinamento_sbagliato_di_prima_si_toglie(self):
        d = {"navi": [{"nome": "NORDIC ANNE", "imo": "9074729", "mmsi": "111", "tipo": "BULK", "fonti": ["giornale"],
                       "dove": {"terminal": "rinfuse", "molo": "MOLO", "lato": "ponente", "in_rada": False}}],
             "statici": {"111": {"imo": "9395044", "nome": "NORDIC ANNA", "lung": 190, "tipo": 70},
                         "222": {"imo": "9074729", "nome": "NORDIC ANNE", "lung": 190, "tipo": 70}},
             "registro": {"111": posizione(self.T), "222": posizione(self.T, lat=44.4002)}}
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.T)
        n = next(x for x in d["navi"] if x["imo"] == "9074729")
        self.assertEqual(n["mmsi"], "222")                                    # ora ha la sua identita' AIS
        self.assertTrue(n["ais"]["ormeggiata"])

    def test_due_navi_gemelle_del_giornale_non_si_fondono(self):
        d = {"navi": [{"nome": "ARGO I", "imo": "9074729", "tipo": "BULK", "giorno": "2026-10-27", "in_porto_dal": "2026-10-27",
                       "dove": {"terminal": "rinfuse"}, "fonti": ["giornale"], "partenza": "18.00", "partenza_giorno": "2026-10-28"}]}
        nuove = [{"nome": "ARGO II", "imo": "9395044", "tipo": "BULK", "dove": {"terminal": "rinfuse"}, "arrivo": "20.00",
                  "arrivo_giorno": "2026-10-28", "arrivo_g": "2026-10-28"}]
        A.unisci_giornale(d, nuove, "2026-10-28", quando("2026-10-28", "09:00"))
        self.assertEqual(sorted(n["nome"] for n in d["navi"]), ["ARGO I", "ARGO II"])

    def test_la_conferma_del_sito_va_solo_alla_nave_giusta(self):
        d = {"navi": [{"nome": "ARGO I", "imo": "9074729"}, {"nome": "NORDIC ANNE", "imo": "9395044"}]}
        A.applica_conferme(d, [{"tipo": "partenza", "nave": "ARGO II", "ormeggio": "RUBATTINO", "giorno": "2026-10-28",
                                "ora": "10.00", "conf": "CONF"},
                               {"tipo": "partenza", "nave": "NORDIC ANNA", "ormeggio": "RUBATTINO", "giorno": "2026-10-28",
                                "ora": "11.00", "conf": "CONF"}])
        self.assertNotIn("conferma", d["navi"][0])
        self.assertNotIn("conferma", d["navi"][1])                            # 0,9 non basta: ANNE non e' ANNA
        A.applica_conferme(d, [{"tipo": "arrivo", "nave": "ARGO I", "ormeggio": "RUBATTINO", "giorno": "2026-10-28",
                                "ora": "12.00", "conf": "CONF"}])
        self.assertEqual(d["navi"][0]["conferma"]["ora"], "12.00")

    def test_una_esclusa_non_si_porta_dietro_la_gemella(self):
        d = {"escluse": [{"nome": "BOMAR RIGEL", "imo": "9074729", "mmsi": "", "motivo": "nave chemical"}]}
        self.assertFalse(A.e_esclusa(d, "9395044", "BOMAR RIGEL 2"))
        self.assertFalse(A.e_esclusa(d, "9395044", "BOMAR RIGEI"))           # altro IMO valido, altro nome
        self.assertTrue(A.e_esclusa(d, "", "BOMAR RIGEL"))


class TipoAisEGiornale(unittest.TestCase):
    """Il tipo AIS 80-89 (cisterna) lo imposta l'equipaggio, a volte male: esclude solo se il giornale non si legge.
    Le gasiere (GAS) si visitano (Mele, 28/10)."""

    def test_il_tipo_del_giornale_vince(self):
        for tipo in ("CONT.", "BULK", "RO/RO", "GAS", "CARGO", "GEN.CARGO", "REEFER"):
            n = {"nome": "BETA", "imo": "9074729", "mmsi": "222", "tipo": tipo, "fonti": ["giornale"]}
            self.assertIsNone(A.motivo_esclusione(n, {"statici": {"222": {"tipo": 80}}}), tipo)

    def test_col_tipo_illeggibile_decide_l_ais(self):
        for tipo in ("", "MM.", "X7"):
            n = {"nome": "BETA", "imo": "9074729", "mmsi": "222", "tipo": tipo, "fonti": ["giornale"]}
            self.assertTrue(A.motivo_esclusione(n, {"statici": {"222": {"tipo": 84}}}).startswith("cisterna (tipo AIS"), tipo)

    def test_le_gasiere_del_giornale_entrano(self):
        navi, escluse = A.navi_dal_giornale([riga("GAMMA GAS", "9074729", "GAS", "ARRIVI", "10.00")], "2026-10-28")
        self.assertEqual([n["nome"] for n in navi], ["GAMMA GAS"])
        self.assertEqual(escluse, [])

    def test_le_esclusioni_fatte_con_la_regola_vecchia_si_tolgono(self):
        adesso = quando("2026-10-28", "17:00")
        d = {"navi": [], "statici": {}, "escluse": [
            {"nome": "DELTA", "imo": "9074729", "motivo": "cisterna (tipo AIS, il giornale lo scrive CONT.)", "quando": adesso.isoformat()},
            {"nome": "EPSILON", "imo": "9395044", "motivo": "cisterna (tipo AIS, il giornale lo scrive illeggibile)", "quando": adesso.isoformat()},
            {"nome": "ZETA", "imo": "9000019", "motivo": "nave chemical", "quando": adesso.isoformat()}]}
        A.escludi_non_visitabili(d, adesso)
        self.assertEqual(sorted(e["nome"] for e in d["escluse"]), ["EPSILON", "ZETA"])


class SostaESpostamenti(unittest.TestCase):
    """Una nave gia' ferma in banchina che il giornale di domani sposta (MOVIMENTI) non 'riparte': la visita di oggi
    deve continuare a valere. Una nave che torna davvero in porto, invece, ricomincia la sosta."""

    def test_spostamento_di_una_nave_in_banchina_tiene_la_sosta(self):
        v = {"nome": "ALFA", "imo": "9074729", "tipo": "CONT.", "giorno": "2026-10-28", "in_porto_dal": "2026-10-27",
             "dove": {"terminal": "sangiorgio", "molo": "Libia"}, "fonti": ["giornale"],
             "visto_banchina": quando("2026-10-28", "15:00").isoformat()}
        d = {"navi": [v]}
        mov = dict(riga("ALFA", "9074729", "CONT.", "MOVIMENTI", "08.00", accosto="DA LIBIA PON. A SOMALIA LEV."))
        navi, _ = A.navi_dal_giornale([mov], "2026-10-29")
        A.unisci_giornale(d, navi, "2026-10-29", quando("2026-10-28", "15:05"))
        n = d["navi"][0]
        self.assertEqual(n["in_porto_dal"], "2026-10-27")
        self.assertEqual((n["arrivo"], n["arrivo_giorno"]), ("08.00", "2026-10-29"))

    def test_nave_vista_in_banchina_molto_tempo_fa_riparte_la_sosta(self):
        v = {"nome": "ALFA", "imo": "9074729", "tipo": "CONT.", "giorno": "2026-10-20", "in_porto_dal": "2026-10-20",
             "dove": {}, "fonti": ["memoria"], "visto_banchina": quando("2026-10-21", "10:00").isoformat()}
        d = {"navi": [v]}
        navi, _ = A.navi_dal_giornale([riga("ALFA", "9074729", "CONT.", "ARRIVI", "08.00")], "2026-10-29")
        A.unisci_giornale(d, navi, "2026-10-29", quando("2026-10-28", "15:05"))
        self.assertNotIn("in_porto_dal", d["navi"][0])


class RicezionePrimaDelRegistro(unittest.TestCase):
    """La ricezione si giudica sulle navi che i giri precedenti avevano sentito ferme nei moli, prima di mettere nel
    registro quelle di questo giro (come fa main). Dopo una pausa lunga il riferimento e' vuoto: non si giudica."""
    sera, mattina = quando("2026-10-28", "20:50"), quando("2026-10-29", "07:00")

    def registro(self, quando_, n=8):
        return {str(100 + i): posizione(quando_, lat=44.4005, lon=8.8999) for i in range(n)}

    def test_dopo_una_pausa_lunga_la_ricezione_non_si_giudica(self):
        d = {"registro": self.registro(self.sera), "aggiornato": self.sera.isoformat()}
        sentite = {str(100 + i) for i in range(6)}
        f = A.finestra_ricezione(d, self.mattina)
        self.assertEqual(A.qualita_ricezione(d, sentite, self.mattina, f), (False, 0, 0))

    def test_di_giorno_conta_chi_si_risente(self):
        t = quando("2026-10-29", "10:00")
        d = {"registro": self.registro(t - __import__("datetime").timedelta(minutes=15)),
             "aggiornato": (t - __import__("datetime").timedelta(minutes=3)).isoformat()}
        f = A.finestra_ricezione(d, t)
        self.assertEqual(A.qualita_ricezione(d, {str(100 + i) for i in range(6)}, t, f), (True, 8, 6))
        self.assertEqual(A.qualita_ricezione(d, {str(100 + i) for i in range(3)}, t, f), (False, 8, 3))


class TitoloMezziDiga(unittest.TestCase):
    """Le parole corte (DIGA, MEZZI) si riconoscono solo intere: NAVIGAZIONE e OBBLIGATORIO contengono 'IGA'
    e prima facevano smettere di leggere il giornale."""

    def test_titoli(self):
        attese = {"MEZZI DIGA": "MEZZI DIGA", "MEZZIDIGA": "MEZZI DIGA", "MEZZl DlGA": "MEZZI DIGA",
                  "MEZZI IMPIEGATI NUOVA DIGA": "MEZZI DIGA", "PREVISIONI DEL 29/10/2026": "PRINCIPALE",
                  "VARIAZIONI": "VARIAZIONI", "AREA MULTEDO": "AREA MULTEDO", "AREA VOLTRI": "AREA VOLTRI",
                  "NAVIGAZIONE": None, "PILOTAGGIO OBBLIGATORIO": None, "ARRIVI": None, "": None}
        for testo, zona in attese.items():
            self.assertEqual(L.zona_del_titolo(testo), zona, testo)


class TaraturaPerNave(unittest.TestCase):
    """Servono navi diverse per imparare un posto: una sola nave ferma due giorni non basta."""

    def test_una_nave_non_basta(self):
        d = {}
        A.azzera_taratura_vecchia(d)
        for _ in range(3):
            A.impara(d, {"molo": "CANEPA", "punto": "testata"}, {"molo": "RONCO", "lato": "testata"}, "9074729")
        self.assertIsNone(A.posto_imparato(d, {"molo": "CANEPA", "punto": "testata"}))
        A.impara(d, {"molo": "CANEPA", "punto": "testata"}, {"molo": "RONCO", "lato": "testata"}, "9395044")
        self.assertEqual(A.posto_imparato(d, {"molo": "CANEPA", "punto": "testata"}), ("RONCO", "testata"))

    def test_la_taratura_vecchia_si_azzera_una_volta(self):
        d = {"taratura": {"CANEPA|testata": {"RONCO|testata": 2}}, "taratura_giorni": {"x>y>2026-10-28": 1}}
        A.azzera_taratura_vecchia(d)
        self.assertEqual((d["taratura"], d["taratura_giorni"]), ({}, {}))
        d["taratura"]["A|b"] = {"C|d": 1}
        A.azzera_taratura_vecchia(d)
        self.assertEqual(d["taratura"], {"A|b": {"C|d": 1}})


class DatiPerIlTelefono(unittest.TestCase):
    """Il telefono scarica solo quello che l'app mostra; la memoria del programma sta in stato.enc.json."""

    def test_solo_i_campi_dell_app(self):
        d = {"navi": [{"nome": "A"}], "non_visitare": [], "data": "2026-10-28", "giornali": ["2026-10-28"], "ais": True,
             "aggiornato": "x", "esempio": False, "registro": {"1": {}}, "statici": {"1": {}}, "escluse": [], "taratura": {}}
        self.assertEqual(sorted(A.dati_per_app(d)), ["aggiornato", "ais", "data", "esempio", "giornali", "navi", "non_visitare"])

    def test_la_memoria_si_apre_dallo_stato_o_dai_dati_di_prima(self):
        k = os.urandom(32)
        with tempfile.TemporaryDirectory() as t:
            stato, uscita = os.path.join(t, "stato.enc.json"), os.path.join(t, "navi.enc.json")
            A.chiudi(k, {"navi": [], "registro": {"1": "vecchio"}}, uscita)
            self.assertEqual(A.apri_stato(k, stato, uscita)["registro"], {"1": "vecchio"})   # prima volta: dai dati di prima
            A.chiudi(k, {"navi": [], "registro": {"1": "nuovo"}}, stato)
            self.assertEqual(A.apri_stato(k, stato, uscita)["registro"], {"1": "nuovo"})
            open(stato, "w").write("rovinato")
            self.assertEqual(A.apri_stato(k, stato, uscita)["registro"], {"1": "vecchio"})   # rovinato: non si riparte da zero
            self.assertFalse(os.path.exists(stato + ".nuovo"))


class MemoriaERiepilogo(unittest.TestCase):
    """29/10: i dati delle navi dell'AIS non si cancellavano mai; il riepilogo oltre i 4096 caratteri perdeva la fine."""

    def test_i_dati_delle_navi_non_sentite_da_3_giorni_si_tolgono(self):
        from datetime import timedelta
        t = quando("2026-10-29", "07:00")
        d = {"registro": {"1": posizione(t, 60), "2": posizione(t, 4 * 24 * 60)},
             "statici": {"1": {"nome": "A"}, "2": {"nome": "B"}, "3": {"nome": "C"}, "4": {"nome": "D"}, "5": {"nome": "E"}},
             "navi": [{"nome": "D", "mmsi": "4"}], "escluse": [{"nome": "E", "mmsi": "5"}]}
        A.aggiorna_registro(d, {}, {"6": {"nome": "F"}}, t)
        self.assertEqual(sorted(d["registro"]), ["1"])
        self.assertEqual(sorted(d["statici"]), ["1", "4", "5"])          # 2 e 3 non si sentono da 3 giorni, 6 senza posizione

    def test_il_riepilogo_sta_nei_4096_caratteri_e_tiene_la_fine(self):
        righe = ["07:00:00 note sul sito: x"] + ["07:00:01 taratura: POSTO%d -> ALTRO x1" % i for i in range(60)] + \
                ["07:00:02 diagnosi, ferma nei moli: " + "N" * 300] + ["07:10:00 in elenco: NAVE %d | CONT." % i for i in range(40)] + \
                ["07:10:01 scritto navi.enc.json navi: 40"]
        t = A.testo_riepilogo(righe)
        self.assertLessEqual(len(t), 4096)
        self.assertIn("scritto navi.enc.json navi: 40", t)
        self.assertIn("in elenco: NAVE 0 |", t)
        self.assertIn("diagnosi, ferma nei moli", t)
        self.assertNotIn("taratura:", t)
        self.assertIn("righe meno importanti tolte", t)
        corto = ["07:00:00 taratura: A -> B x1", "07:10:00 in elenco: NAVE | CONT."]
        self.assertEqual(A.testo_riepilogo(corto), "\n".join(corto))    # se c'e' spazio non si toglie niente


class DragheEAttracchi(unittest.TestCase):
    """29/10: PALMIRO Z (draga, si dichiara cargo da 68 m) entrava come nave da visitare ai Rinfuse; ULYSSE, entrata in
    manovra a 1,9 nodi alle 9:01 e poi mai piu' sentita da aisstream (ormeggiata secondo VesselFinder), restava
    'Non ancora in banchina'."""
    T = quando("2026-10-29", "10:00")
    MMSI = "247000010"

    def test_la_draga_di_mele_non_entra_e_se_c_e_si_toglie(self):
        d = {"navi": [], "escluse": [], "statici": {"247388500": {"imo": "8334964", "nome": "PALMIRO Z", "tipo": 70, "lung": 68}},
             "registro": {"247388500": posizione(self.T, 5)}}
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.T, {"247388500"}, True)
        self.assertEqual(d["navi"], [])
        d["navi"] = [{"nome": "PALMIRO Z", "imo": "", "mmsi": "247388500", "tipo": "CARGO", "fonti": ["ais"], "dove": {"terminal": "rinfuse"}}]
        A.escludi_non_visitabili(d, self.T)
        self.assertEqual(d["navi"], [])
        A.costruisci_non_visitare(d, BANCHINE_PROVA, self.T, True)
        self.assertEqual(d["non_visitare"], [])                          # non si mostra neanche in grigio

    def test_i_mezzi_della_diga_del_giornale_non_entrano_dall_ais(self):
        righe = [riga("ANNAMARIA Z", "8666939", "DRAGA", "ARRIVI", "08.00", zona="MEZZI DIGA"),
                 riga("NORMALE", "9074729", "CONT.", "ARRIVI", "08.00")]
        navi, escluse = A.navi_dal_giornale(righe, "2026-10-29")
        self.assertEqual([n["nome"] for n in navi], ["NORMALE"])
        d = {"navi": [], "escluse": [], "statici": {self.MMSI: {"imo": "8666939", "nome": "ANNAMARIA Z", "tipo": 70, "lung": 120}},
             "registro": {self.MMSI: posizione(self.T, 5)}}
        for e in escluse:
            A.registra_esclusa(d, e, e["motivo"], self.T)
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.T, {self.MMSI}, True)
        self.assertEqual(d["navi"], [])
        A.costruisci_non_visitare(d, BANCHINE_PROVA, self.T, True)
        self.assertEqual(d["non_visitare"], [])

    def nave(self):
        return {"nome": "ULYSSE", "imo": "9142459", "mmsi": self.MMSI, "tipo": "RO/RO", "fonti": ["giornale"], "giorno": "2026-10-29",
                "arrivo": "08.00", "arrivo_giorno": "2026-10-29", "dove": {"terminal": "rinfuse", "molo": "MOLO", "lato": "ponente"}}

    def dati(self, minuti_fa, lon=8.8985, sog=1.9):
        return {"navi": [self.nave()], "escluse": [],
                "statici": {self.MMSI: {"imo": "9142459", "nome": "ULYSSE", "tipo": 70, "lung": 162}},
                "registro": {self.MMSI: posizione(self.T, minuti_fa, lon=lon, sog=sog, cog=0)}}

    def test_lenta_dentro_il_porto_e_in_manovra(self):
        d = self.dati(2)
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.T)
        a = d["navi"][0]["ais"]
        self.assertTrue(a["manovra"])
        self.assertNotIn("attracco_probabile", a)                        # segnale fresco: sta ancora manovrando

    def test_in_manovra_vicino_alla_sua_banchina_poi_silenzio_e_attracco_probabile(self):
        d = self.dati(60)
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.T)
        a = d["navi"][0]["ais"]
        self.assertTrue(a["attracco_probabile"])
        self.assertEqual(a["banchina_probabile"], "MOLO ponente")
        d = self.dati(4 * 60)                                            # vale come una nave ferma: 6 ore
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.T)
        self.assertTrue(d["navi"][0]["ais"]["attracco_probabile"])

    def test_lontana_dalla_sua_banchina_niente_attracco_probabile(self):
        d = self.dati(60, lon=8.9150)                                    # circa 1,2 km a est
        A.aggiorna_presenze(d, BANCHINE_PROVA, self.T)
        self.assertNotIn("attracco_probabile", d["navi"][0]["ais"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

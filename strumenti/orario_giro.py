#!/usr/bin/env python3
"""Quando fare il prossimo giro di "Navi del giorno" (ora di Genova).

Gli orari previsti sono:
- di giorno, dalle 7:00 alle 20:45, uno ogni 15 minuti (7:00, 7:15, 7:30, ...);
- di notte uno solo alle 23, alle 3 e alle 6 (scelta di Mele del 28/10: pochi giri, con ascolto AIS piu' lungo).

Un giro e' "dovuto" se l'ultimo giro fatto e' finito prima dell'ultimo orario previsto: cosi' un giro saltato o in
ritardo (GitHub lenta, esecuzione che riparte) si fa appena possibile, e un giro gia' fatto non si rifa'.
L'esecuzione resta accesa, aspetta l'orario e poi passa il testimone a quella dopo (vedi navi.yml):
le partenze programmate di GitHub non servono piu' a tenere vivo l'aggiornamento.

Uso (dal workflow):
  python3 strumenti/orario_giro.py attesa ULTIMO [ADESSO]    secondi da aspettare prima del prossimo giro (0 = subito)
  python3 strumenti/orario_giro.py descrivi ULTIMO [ADESSO]  la stessa cosa a parole, per il log
ULTIMO e ADESSO sono secondi Unix (ULTIMO 0 = nessun giro fatto).
"""
import sys
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")
GIORNO_DA, GIORNO_A = 7, 21          # di giorno: dalle 7:00 all'ultimo giro delle 20:45
OGNI_MINUTI = 15
NOTTE = (23, 3, 6)


def orari_di(giorno):
    """Gli orari previsti di un giorno di calendario (ora di Genova), in ordine."""
    out = [datetime(giorno.year, giorno.month, giorno.day, h, 0, tzinfo=ROMA) for h in NOTTE]
    m = GIORNO_DA * 60
    while m < GIORNO_A * 60:
        out.append(datetime(giorno.year, giorno.month, giorno.day, m // 60, m % 60, tzinfo=ROMA))
        m += OGNI_MINUTI
    return sorted(out, key=lambda d: d.astimezone(timezone.utc))


def _vicini(adesso):
    g = adesso.astimezone(ROMA).date()
    tutti = []
    for d in (g - timedelta(days=1), g, g + timedelta(days=1)):
        tutti += orari_di(d)
    return sorted(tutti, key=lambda d: d.astimezone(timezone.utc))


def ultimo_orario(adesso):
    """L'ultimo orario previsto gia' arrivato (<= adesso)."""
    return max(o for o in _vicini(adesso) if o <= adesso)


def prossimo_orario(adesso):
    """Il primo orario previsto dopo adesso."""
    return min(o for o in _vicini(adesso) if o > adesso)


def attesa(adesso, ultimo_giro):
    """Secondi da aspettare prima del prossimo giro: 0 se l'ultimo orario previsto non e' ancora stato coperto
    (giro saltato, in ritardo, o nessun giro fatto)."""
    if ultimo_giro is None or ultimo_giro < ultimo_orario(adesso):
        return 0
    return max(1, int((prossimo_orario(adesso) - adesso).total_seconds() + 0.999))


def _da_epoca(s):
    s = int(float(s or 0))
    return datetime.fromtimestamp(s, timezone.utc) if s > 0 else None


def main(argv):
    if len(argv) < 2 or argv[0] not in ("attesa", "descrivi"):
        print(__doc__, file=sys.stderr)
        return 2
    ultimo = _da_epoca(argv[1])
    adesso = _da_epoca(argv[2]) if len(argv) > 2 else datetime.now(timezone.utc)
    a = attesa(adesso, ultimo)
    if argv[0] == "attesa":
        print(a)
    else:
        u = ultimo.astimezone(ROMA).strftime("%d/%m %H:%M") if ultimo else "mai"
        if a == 0:
            print("giro adesso (ultimo giro finito: %s; orario previsto delle %s)" % (u, ultimo_orario(adesso).astimezone(ROMA).strftime("%H:%M")))
        else:
            print("prossimo giro alle %s, tra %d min (ultimo giro finito: %s)" % (
                prossimo_orario(adesso).astimezone(ROMA).strftime("%H:%M"), a // 60, u))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

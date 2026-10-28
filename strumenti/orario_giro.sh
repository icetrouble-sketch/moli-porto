#!/usr/bin/env bash
# Decide che cosa deve fare questa esecuzione di "Navi del giorno". Scrive due righe adatte a $GITHUB_OUTPUT:
#   vai=si|no       fare giri oppure no
#   un_giro=si|no   un solo giro (notte) oppure un ciclo ogni 15 minuti (giorno)
#
# Dalle 7 alle 21 (ora di Genova) l'esecuzione resta accesa e fa un giro ogni 15 minuti.
# Di notte si fa un solo giro alle 23, alle 3 e alle 6. GitHub fa partire le esecuzioni programmate in
# ritardo e a volte le salta: per questo non si guarda l'ora esatta ma si chiede "il giro dell'ultimo
# orario previsto e' gia' stato fatto?". Se l'ultimo aggiornamento dei dati e' piu' vecchio dell'ultimo orario
# previsto (23, 3 o 6) il giro si fa adesso, anche con un'ora di ritardo; se no si salta.
# L'ora dell'ultimo aggiornamento e' quella dell'ultimo commit "Navi aggiornate ..." (serve la storia, non solo
# l'ultimo commit: il checkout prende gli ultimi 30).
#
# Variabili: EVENTO (nome dell'evento GitHub; workflow_dispatch = avvio a mano), ADESSO_PROVA e ULTIMO_PROVA
# (secondi Unix, solo per le prove).
set -u
ADESSO=${ADESSO_PROVA:-$(date +%s)}
ORA=$(TZ=Europe/Rome date -d "@$ADESSO" +%-H)
GIORNO=no
if [ "$ORA" -ge 7 ] && [ "$ORA" -le 20 ]; then GIORNO=si; fi

if [ "$GIORNO" = si ]; then
  echo "vai=si"; echo "un_giro=no"; exit 0
fi
if [ "${EVENTO:-}" = "workflow_dispatch" ]; then
  echo "Avvio a mano di notte (ore $ORA): un solo giro" >&2
  echo "vai=si"; echo "un_giro=si"; exit 0
fi

if [ -n "${ULTIMO_PROVA+x}" ]; then
  ULTIMO=$ULTIMO_PROVA
else
  ULTIMO=$(git log -1 --format=%ct --grep='^Navi aggiornate' 2>/dev/null || true)
fi
ULTIMO=${ULTIMO:-0}

OGGI=$(TZ=Europe/Rome date -d "@$ADESSO" +%F)
IERI=$(TZ=Europe/Rome date -d "@$((ADESSO - 86400))" +%F)
BERSAGLIO=0                                   # l'ultimo orario previsto (23, 3 o 6) che e' gia' passato
for H in 23 3 6; do
  T=$(TZ=Europe/Rome date -d "$OGGI $H:00" +%s)
  if [ "$T" -gt "$ADESSO" ]; then T=$(TZ=Europe/Rome date -d "$IERI $H:00" +%s); fi
  if [ "$T" -gt "$BERSAGLIO" ]; then BERSAGLIO=$T; fi
done

if [ "$ULTIMO" -lt "$BERSAGLIO" ]; then
  echo "Giro notturno (ore $ORA): ultimo aggiornamento prima delle $(TZ=Europe/Rome date -d "@$BERSAGLIO" +%H:%M)" >&2
  echo "vai=si"; echo "un_giro=si"
else
  echo "Fuori orario (ore $ORA): il giro previsto e' gia' stato fatto" >&2
  echo "vai=no"; echo "un_giro=no"
fi

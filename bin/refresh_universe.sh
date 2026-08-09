#!/usr/bin/env bash
# Holt die IWM-Holdings frisch und schiebt sie auf den Screener-Server.
#
# Muss lokal laufen. iShares beantwortet Anfragen von Rechenzentrums-IPs mit
# 403 -- gemessen am 2026-08-09, mit und ohne Browser-Kennung, waehrend
# dieselbe Anfrage von einem Wohnanschluss 200 liefert. Der Server kann die
# Datei also nie selbst laden und faellt taeglich auf seinen Cache zurueck.
# Dieses Skript ist der Weg, wie dieser Cache wieder jung wird.
#
# Aufruf:  bin/refresh_universe.sh
#          bin/refresh_universe.sh --local-only     # nur hier erneuern
#
# Ziel ueberschreibbar:
#   STOCKPICKER_SSH_HOST   (Vorgabe heimdal@152.53.12.174)
#   STOCKPICKER_SSH_PORT   (Vorgabe 51245)
#   STOCKPICKER_REMOTE     (Vorgabe stock-picker)
#
# Der Russell 2000 wird einmal jaehrlich im Juni neu zusammengesetzt,
# dazwischen aendern sich einzelne Titel im Monat. Woechentlich reicht.

set -euo pipefail

LOCAL_ONLY=false
case "${1:-}" in
  "")           ;;
  --local-only) LOCAL_ONLY=true ;;
  *) echo "Unbekanntes Argument '$1'. Erlaubt ist nur --local-only." >&2; exit 2 ;;
esac

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

SSH_HOST="${STOCKPICKER_SSH_HOST:-heimdal@152.53.12.174}"
SSH_PORT="${STOCKPICKER_SSH_PORT:-51245}"
REMOTE="${STOCKPICKER_REMOTE:-stock-picker}"
CACHE="data/cache/iwm_holdings.csv"

# Mindestgroesse des Universums. Der Russell 2000 haelt rund 2000 Titel;
# alles unter 1500 ist eine abgeschnittene Antwort und keine Holdings-Datei.
# parse_holdings_csv wirft zwar bei HTML statt CSV, aber nicht bei einer
# Datei, die nach der Haelfte abbricht.
MIN_ROWS=1500

previous=""
[[ -f "$CACHE" ]] && previous="$(date -r "$CACHE" +%F 2>/dev/null || true)"

echo "→ Lade IWM-Holdings von iShares"

# max_age_days=0 ueberspringt den Frische-Zweig und erzwingt den Download.
# load_universe faellt bei einem Fehler still auf den Cache zurueck und gibt
# das nur ueber die Warnung zurueck -- ohne diese Pruefung wuerde das Skript
# eine unveraenderte Datei als frisch weiterreichen.
ROWS="$(uv run python - <<'PY'
import sys
from stockpicker import universe

rows, warning = universe.load_universe(max_age_days=0)
if warning:
    print(f"Download fehlgeschlagen: {warning}", file=sys.stderr)
    raise SystemExit(1)
print(len(rows))
PY
)" || { echo "✗ Abbruch: der Cache wurde nicht erneuert." >&2; exit 1; }

(( ROWS >= MIN_ROWS )) || {
  echo "✗ Abbruch: nur $ROWS Titel gelesen, erwartet mindestens $MIN_ROWS." >&2
  echo "  Die heruntergeladene Datei sieht abgeschnitten aus." >&2
  exit 1
}

echo "   $ROWS Titel, $(wc -c < "$CACHE" | tr -d ' ') Bytes${previous:+, vorher vom $previous}"

if $LOCAL_ONLY; then
  echo "   --local-only: nichts uebertragen."
  exit 0
fi

echo "→ Uebertrage nach $SSH_HOST:$REMOTE/data/cache/"
scp -P "$SSH_PORT" -q "$CACHE" "$SSH_HOST:$REMOTE/data/cache/iwm_holdings.csv"

# Gegenprobe auf der anderen Seite: uebertragen heisst nicht lesbar. Ein
# halb angekommener CSV faellt hier auf und nicht erst im naechsten Lauf
# um 06:40, wenn niemand hinsieht.
ssh -p "$SSH_PORT" "$SSH_HOST" "cd '$REMOTE' && \$HOME/.local/bin/uv run python - <<'PY'
from pathlib import Path
from stockpicker import universe
rows = universe.parse_holdings_csv(Path('data/cache/iwm_holdings.csv').read_text(encoding='utf-8'))
print(f'   angekommen: {len(rows)} Titel')
raise SystemExit(0 if len(rows) >= $MIN_ROWS else 1)
PY" || { echo "✗ Die Datei auf dem Server ist nicht brauchbar." >&2; exit 1; }

echo "✓ Universum erneuert."

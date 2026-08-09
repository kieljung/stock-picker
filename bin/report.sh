#!/usr/bin/env bash
# Stufe 3 und 4 von Hand: Report pruefen, Ledger fortschreiben, ans Portal geben.
#
# Aufruf:  bin/report.sh out/report-2026-08-09.json
#          bin/report.sh out/report-2026-08-09.json --dry-run
#          bin/report.sh out/report-2026-08-09.json --replace
#
# Laeuft auf dem Server, weil der Ledger dort lebt und weil validate.py den
# Report gegen candidates.json prueft -- und die entsteht seit dem Timer hier.
# Ein Report gegen die Kandidatenliste eines anderen Rechners zu pruefen
# hiesse, die Pruefung gegen die falsche Referenz laufen zu lassen.
#
# --replace geht nur an validate.py: das Portal ersetzt ein bereits
# gepushtes Datum von sich aus, publish.py kennt die Option nicht.

set -euo pipefail

REPORT="${1:-}"
[[ -n "$REPORT" ]] || { echo "Aufruf: $(basename "$0") out/report-YYYY-MM-DD.json [--dry-run] [--replace]" >&2; exit 2; }
shift

DRY=""
REPLACE=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY="--dry-run" ;;
    --replace) REPLACE="--replace" ;;
    *) echo "Unbekanntes Argument '$arg'." >&2; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
export PATH="$HOME/.local/bin:$PATH"

[[ -f "$REPORT" ]] || { echo "✗ $REPORT gibt es nicht." >&2; exit 1; }

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%F).log"

# Dasselbe Lock wie die Timer: publish.py liest market.db, und ein
# gleichzeitiger Screener-Lauf haette denselben Cache offen. Hier wird
# gewartet statt uebersprungen -- der Aufruf kommt von Hand, und ein
# kommentarlos uebersprungener Report waere die schlechtere Antwort.
exec 9>"$REPO/.market-db.lock"
if ! flock -w 900 9; then
  echo "✗ Cache seit 15 Minuten von einem anderen Lauf belegt. Spaeter erneut." >&2
  exit 1
fi

{
  echo "════════════════════════════════════════════════════════════"
  echo "[$(date -Is)] Report: $REPORT ${DRY:-} ${REPLACE:-}"
  echo "════════════════════════════════════════════════════════════"
} >>"$LOG_FILE"

# Stufe 3 zuerst und ohne Netz. Scheitert sie, ist der Report falsch und
# darf das Portal gar nicht erst sehen -- set -e bricht hier ab.
echo "→ Stufe 3: Levels pruefen, Ledger fortschreiben"
uv run python validate.py "$REPORT" $DRY $REPLACE 2>&1 | tee -a "$LOG_FILE"

echo "→ Stufe 4: Report, Kurshistorie und Devisenkurse ans Portal"
uv run python publish.py "$REPORT" $DRY 2>&1 | tee -a "$LOG_FILE"

echo "[$(date -Is)] Report fertig: $REPORT" >>"$LOG_FILE"
echo "✓ Fertig."

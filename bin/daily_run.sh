#!/usr/bin/env bash
# Täglicher, unbeaufsichtigter Lauf der stock-picker-Pipeline.
# Wird von den systemd-User-Timern `stockpicker-screen.timer` und
# `stockpicker-sync.timer` gestartet.
#
# Aufruf:         bin/daily_run.sh screen | sync
# Manueller Test: bin/daily_run.sh sync
# Logs:           logs/YYYY-MM-DD.log  (30 Tage Aufbewahrung)
#
# Automatisiert sind bewusst nur die beiden maschinellen Stufen. Stufe 2 ist
# eine Claude-Session mit Websuche und läuft nicht unbeaufsichtigt; damit
# bleiben auch validate.py und der Report-Push Handarbeit.

set -euo pipefail

MODE="${1:-}"
case "$MODE" in
  screen|sync) ;;
  *) echo "Aufruf: $(basename "$0") screen|sync" >&2; exit 2 ;;
esac

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
LOG_FILE="$LOG_DIR/$(date +%F).log"

# Ein gemeinsames Lock für beide Modi, nicht je Modus eines: beide schreiben
# in denselben SQLite-Cache. Sieben Stunden Abstand machen eine Überlappung
# unwahrscheinlich, ein hängender Lauf aber nicht unmöglich -- und zwei
# Schreiber auf market.db enden in "database is locked" statt in Daten.
LOCK_FILE="$REPO/.market-db.lock"

mkdir -p "$LOG_DIR"

# flock gibt sofort auf, statt zu warten: ein hängender Lauf soll den
# nächsten Tag nicht blockieren, sondern übersprungen werden.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -Is)] $MODE: Cache ist von einem anderen Lauf belegt — übersprungen." >>"$LOG_FILE"
  exit 0
fi

cd "$REPO"

# systemd-User-Services erben keine Shell-Profile. uv liegt in ~/.local/bin,
# das die Unit über Environment=PATH mitgibt; hier steht es noch einmal,
# damit das Skript auch von Hand aufgerufen funktioniert.
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || {
  echo "[$(date -Is)] ABBRUCH: 'uv' nicht im PATH. PATH=$PATH" >>"$LOG_FILE"
  exit 1
}

{
  echo "════════════════════════════════════════════════════════════"
  echo "[$(date -Is)] Start: $MODE"
  echo "════════════════════════════════════════════════════════════"
} >>"$LOG_FILE"

set +e
case "$MODE" in
  screen)
    # Stufe 1. Schreibt candidates.json für die Claude-Session.
    uv run python screen.py >>"$LOG_FILE" 2>&1
    ;;
  sync)
    # Kurse, Devisenkurse und Trade-Ausgänge aller offenen Picks ans Portal.
    uv run python publish.py --sync >>"$LOG_FILE" 2>&1
    ;;
esac
STATUS=$?
set -e

echo "[$(date -Is)] Ende: $MODE (exit=$STATUS)" >>"$LOG_FILE"

find "$LOG_DIR" -name '*.log' -type f -mtime +30 -delete 2>/dev/null || true

exit "$STATUS"

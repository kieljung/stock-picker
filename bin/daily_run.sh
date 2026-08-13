#!/usr/bin/env bash
# Täglicher, unbeaufsichtigter Lauf der stock-picker-Pipeline.
# Beide Modi ruft n8n per SSH auf: `screen` als ersten Schritt der Kette,
# `sync` als letzten.
#
# Aufruf:         bin/daily_run.sh screen | sync
# Manueller Test: bin/daily_run.sh sync
# Logs:           logs/YYYY-MM-DD.log  (30 Tage Aufbewahrung)
#
# Dieses Skript deckt nur die beiden Stufen ab, die den Kurs-Cache anfassen:
# Stufe 1 und den Sync. Stufe 2 bis 4 — die Claude-Session mit Websuche,
# validate.py und der Report-Push — laufen in `bin/analyze.sh`.
#
# Der Exit-Code ist die Rückmeldung an n8n und wird sauber durchgereicht:
# `screen` endet mit 0 nur dann, wenn candidates.json frisch geschrieben
# wurde. Nur in dem Fall darf analyze.sh anlaufen. `sync` gehört dagegen auf
# beide Pfade -- er pflegt die offenen Picks früherer Tage und hängt an der
# heutigen Analyse nur der Reihenfolge wegen.
#
# Ausgaben gehen in zwei Richtungen. Vollständig und immer ins Logfile; nach
# stderr nur, was schiefging -- der Abbruchgrund und die Ausgabe des
# gescheiterten Laufs. Ein erfolgreicher Lauf bleibt auf stdout und stderr
# still, damit ein Alarm in n8n den Traceback direkt enthält, statt ihn per
# zweitem SSH-Aufruf nachladen zu müssen.

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

# Nur ins Logfile.
log()  { echo "[$(date -Is)] $*" >>"$LOG_FILE"; }
# Ins Logfile und nach stderr -- für alles, was der Aufrufer sehen muss.
warn() { echo "[$(date -Is)] $*" | tee -a "$LOG_FILE" >&2; }

# Stand des Logfiles vor dem Lauf. Scheitert er, wird ab hier nach stderr
# nachgereicht -- genau die Zeilen dieses Laufs, nicht das ganze Tageslog.
LOG_MARK=$(if [[ -f "$LOG_FILE" ]]; then wc -l <"$LOG_FILE"; else echo 0; fi)

# flock gibt sofort auf, statt zu warten: ein hängender Lauf soll den
# nächsten Tag nicht blockieren, sondern übersprungen werden.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  # Kein Fehler, deshalb Exit 0 -- aber nach stderr, weil sonst niemand
  # merkt, dass dieser Lauf nichts getan hat.
  warn "$MODE: Cache ist von einem anderen Lauf belegt — übersprungen."
  exit 0
fi

cd "$REPO"

# uv liegt in ~/.local/bin. Ein nicht-interaktives `ssh host befehl`, wie n8n
# es absetzt, liest kein Login-Profil -- der PATH ist dann der nackte Default
# aus /etc/environment. Diese Zeile ist deshalb die einzige Stelle, die uv
# auffindbar macht; früher gab die systemd-Unit ihn zusätzlich per
# Environment= mit, die gibt es nicht mehr.
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || {
  warn "ABBRUCH: 'uv' nicht im PATH. PATH=$PATH"
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

log "Ende: $MODE (exit=$STATUS)"

# Der Traceback steht schon im Logfile -- hier geht er zusätzlich nach
# stderr, damit er ohne Umweg im n8n-Alarm landet.
if [[ $STATUS -ne 0 ]]; then
  {
    echo "[$(date -Is)] ABBRUCH: $MODE gescheitert (exit=$STATUS). Ausgabe des Laufs:"
    tail -n +"$((LOG_MARK + 1))" "$LOG_FILE"
  } >&2
fi

find "$LOG_DIR" -name '*.log' -type f -mtime +30 -delete 2>/dev/null || true

exit "$STATUS"

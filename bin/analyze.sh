#!/usr/bin/env bash
# Stufe 2 bis 4 unbeaufsichtigt: Claude-Session mit Websuche ueber
# candidates.json, danach validate.py und publish.py.
#
# Wird von n8n per SSH gestartet, und zwar erst, wenn `daily_run.sh screen`
# mit 0 geendet hat -- also wenn Stufe 1 sauber durchlief und candidates.json
# wirklich neu ist. Eine eigene Uhrzeit waere eine Wette auf die Laufzeit des
# Screeners (Minuten bei warmem Cache, eine knappe Stunde bei leerem); die
# Verkettung ueber den Exit-Code ist keine.
#
# Danach fehlt noch `daily_run.sh sync`, den n8n eigens nachziehen muss --
# auf diesem Pfad wie auf dem Fehlerpfad, damit er je Tag genau einmal laeuft.
# Dieses Skript ruft ihn nicht selbst auf: es endet je nach Ausgang mit einem
# anderen Exit-Code, und der Sync soll von beidem unabhaengig sein.
#
# Die Pruefungen weiter unten sind der zweite Boden dafuer: laeuft analyze.sh
# doch einmal ohne frische Kandidatenliste an, bricht es ab, statt den Tag von
# gestern ein zweites Mal zu analysieren.
#
# Manueller Test:  bin/analyze.sh
#                  bin/analyze.sh --dry-run   (Stufe 3 und 4 nur simulieren)
# Logs:            logs/YYYY-MM-DD.log

set -euo pipefail

DRY=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY="--dry-run" ;;
    *) echo "Aufruf: $(basename "$0") [--dry-run]" >&2; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO/logs"
LOG_FILE="$LOG_DIR/$(date +%F).log"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"

mkdir -p "$LOG_DIR"
cd "$REPO"

# Vollstaendig ins Logfile, im Fehlerfall zusaetzlich nach stderr -- damit der
# Grund ohne zweiten SSH-Aufruf im n8n-Alarm steht. Ein sauberer Lauf schweigt
# bis auf den Abschlussbericht der Claude-Session.
log()  { echo "[$(date -Is)] $*" >>"$LOG_FILE"; }
warn() { echo "[$(date -Is)] $*" | tee -a "$LOG_FILE" >&2; }

# Stand des Logfiles vor dem Lauf, fuer das Nachreichen der Ausgabe nach stderr.
LOG_MARK=$(if [[ -f "$LOG_FILE" ]]; then wc -l <"$LOG_FILE"; else echo 0; fi)
dump_log() { tail -n +"$((LOG_MARK + 1))" "$LOG_FILE" >&2; }

# Ein eigenes Lock, nicht das der Datenbank: die Analyse laeuft eine gute
# halbe Stunde und beruehrt market.db nicht. Wuerde sie .market-db.lock
# halten, liefe sie am Ende gegen ihren eigenen Aufruf von report.sh, der
# dasselbe Lock erwartet -- flock zaehlt pro Datei-Handle, nicht pro
# Prozessbaum, und ein Elternprozess sperrt sein Kind genauso aus wie einen
# Fremden.
exec 8>"$REPO/.analyze.lock"
if ! flock -n 8; then
  # Kein Fehler, deshalb Exit 0 -- aber nach stderr, damit ein Lauf, der
  # nichts getan hat, nicht als erledigt durchgeht.
  warn "Analyse: Vorheriger Lauf laeuft noch — uebersprungen."
  exit 0
fi

export PATH="$HOME/.local/bin:$PATH"

{
  echo "════════════════════════════════════════════════════════════"
  echo "[$(date -Is)] Start: Analyse (Stufe 2-4, unbeaufsichtigt) ${DRY:-}"
  echo "════════════════════════════════════════════════════════════"
} >>"$LOG_FILE"

[[ -x "$CLAUDE_BIN" ]] || {
  warn "ABBRUCH: claude nicht ausfuehrbar unter $CLAUDE_BIN"
  exit 1
}
[[ -f "$REPO/candidates.json" ]] || {
  warn "ABBRUCH: candidates.json fehlt — lief Stufe 1?"
  exit 1
}

# Das Report-Datum kommt aus generated_at, nicht von `date`: PROMPT.md Block 6
# schreibt das Datum des Screener-Laufs vor, und validate.py prueft den Report
# gegen genau diese Kandidatenliste. Ein Nachholtag laege sonst um eins daneben.
RUN_DATE="$(uv run python -c '
import json, sys
with open("candidates.json") as fh:
    print(json.load(fh)["generated_at"][:10])
' 2>>"$LOG_FILE")" || {
  warn "ABBRUCH: candidates.json ist nicht lesbar."
  dump_log
  exit 1
}

# Eine Kandidatenliste von gestern heisst: Stufe 1 wurde uebersprungen (Lock
# belegt) oder ist gescheitert. Dann waere die Analyse eine teure Wiederholung
# eines Tages, der laengst im Ledger steht.
TODAY="$(date +%F)"
if [[ "$RUN_DATE" != "$TODAY" ]]; then
  warn "ABBRUCH: candidates.json ist vom $RUN_DATE, heute ist $TODAY — lief Stufe 1 heute sauber durch?"
  exit 1
fi

REPORT="out/report-$RUN_DATE.json"
if [[ -f "$REPORT" ]]; then
  warn "$REPORT gibt es schon — Analyse uebersprungen."
  exit 0
fi

mkdir -p "$REPO/out"

# Der Prompt geht ueber stdin, NICHT als Positional-Argument: `--add-dir` ist
# variadisch und wuerde einen nachfolgenden Prompt-String als weiteres
# Verzeichnis verschlucken.
#
# Der Report wird von Claude selbst geschrieben, nicht aus stdout
# abgefangen. PROMPT.md Block 6 verlangt reines JSON ohne umgebenden Text --
# das gilt fuer die Antwort in einer Chat-Session; ein Lauf mit Websuche
# schreibt auf dem Weg dorthin Zwischenstaende nach stdout, und ein
# `> report.json` faenge sie mit ein. Die Datei ist die Ausgabe, nicht die
# Konsole.
PROMPT=$(cat <<'EOF'
Fuehre die taegliche Small-Cap-Swing-Analyse aus (Stufe 2 der Pipeline).

Lies dazu zuerst PROMPT.md und arbeite den dort beschriebenen Ablauf
vollstaendig ab — Marktkontext, technische Klassifikation, Katalysator-
Recherche per Websuche, Scoring, Auswahl. Input ist candidates.json im
Projektverzeichnis.

Abweichend von Block 6 gibst du das JSON-Dokument NICHT auf der Konsole aus,
sondern schreibst es mit dem Write-Tool nach:

    __REPORT_PATH__

Der Dateiinhalt ist ausschliesslich das JSON-Dokument nach Block 6 — kein
umgebender Text, kein Codefence, keine Kommentare. Alle Regeln aus Block 2,
5, 6 und die Selbstpruefung aus Block 7 gelten unveraendert; die Checkliste
gehoert als `checklist`-Array in das Dokument.

Dies ist ein unbeaufsichtigter Lauf: stelle KEINE Rueckfragen und warte auf
KEINE Nutzereingabe. Triff alle Entscheidungen nach den Regeln in PROMPT.md.
Fuehre selbst weder validate.py noch publish.py aus — das uebernimmt das
aufrufende Skript. Bei einem harten Fehler (candidates.json fehlt oder ist
leer, Websuche nicht verfuegbar) brich ab und gib eine klare Fehlermeldung
aus, statt einen Report zu raten.

Gib am Ende einen kurzen Abschlussbericht auf der Konsole aus: Anzahl
Kandidaten, Anzahl Picks, hoechster Composite, was auffiel.
EOF
)
PROMPT="${PROMPT//__REPORT_PATH__/$REPORT}"

set +e
printf '%s' "$PROMPT" | "$CLAUDE_BIN" \
  --print \
  --permission-mode bypassPermissions \
  --add-dir "$REPO" \
  >>"$LOG_FILE" 2>&1
STATUS=$?
set -e

log "Stufe 2 fertig (exit=$STATUS)"

if [[ $STATUS -ne 0 ]]; then
  warn "ABBRUCH: Claude-Lauf gescheitert (exit=$STATUS). Ausgabe des Laufs:"
  dump_log
  exit "$STATUS"
fi

# Erst pruefen, dann weiterreichen: ein abgebrochener Lauf hinterlaesst eine
# halbe Datei, und validate.py meldete dann einen Schemafehler statt der
# eigentlichen Ursache.
if [[ ! -s "$REPORT" ]]; then
  warn "ABBRUCH: $REPORT wurde nicht geschrieben. Ausgabe des Laufs:"
  dump_log
  exit 1
fi
if ! uv run python -c 'import json,sys; json.load(open(sys.argv[1]))' "$REPORT" 2>>"$LOG_FILE"; then
  warn "ABBRUCH: $REPORT ist kein valides JSON."
  exit 1
fi

# Stufe 3 und 4. report.sh nimmt sich das Datenbank-Lock selbst und bricht bei
# einem Fehler in validate.py ab, bevor das Portal den Report sieht. Sein
# stdout bleibt unumgeleitet: report.sh schreibt selbst per tee ins Logfile,
# eine zweite Umleitung haette jede Zeile doppelt im Log.
log "Weiter mit Stufe 3 und 4: $REPORT"
set +e
"$REPO/bin/report.sh" "$REPORT" $DRY 2>>"$LOG_FILE"
STATUS=$?
set -e

log "Ende: Analyse (exit=$STATUS)"

if [[ $STATUS -ne 0 ]]; then
  warn "ABBRUCH: Stufe 3/4 gescheitert (exit=$STATUS). Ausgabe des Laufs:"
  dump_log
fi

find "$LOG_DIR" -name '*.log' -type f -mtime +30 -delete 2>/dev/null || true

exit "$STATUS"

#!/usr/bin/env bash
# Baut die Assets, laedt das Portal hoch, erzeugt beim ersten Mal ein
# Passwort und prueft danach, dass der Schutz tatsaechlich greift.
#
# Aufruf:  ./deploy.sh [--rotate-password]
#
# Das Skript ist idempotent: ein zweiter Lauf ohne Aenderungen laedt
# dieselben Dateien hoch, erzeugt kein neues Passwort und laesst die
# Datenbank auf dem Server unberuehrt.
set -euo pipefail

cd "$(dirname "$0")"

ROTATE=false
(( $# <= 1 )) || { echo "Zu viele Argumente. Aufruf: ./deploy.sh [--rotate-password]"; exit 1; }
case "${1:-}" in
  "")                 ;;
  --rotate-password)  ROTATE=true ;;
  *) echo "Unbekanntes Argument '$1'. Erlaubt ist nur --rotate-password."; exit 1 ;;
esac

ENV_FILE="../.env"          # Zugangsdaten fuer publish.py
HTPASSWD_NAME=".htpasswd"

# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

die() { echo "✗ $*" >&2; exit 1; }

# Liest einen Wert aus ../.env. Anfuehrungszeichen werden abgestreift, damit
# die Datei gleich gelesen wird wie von python-dotenv in publish.py.
read_env_value() {
  local key="$1" line
  [[ -f "$ENV_FILE" ]] || return 1
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" | tail -1)" || return 1
  [[ -n "$line" ]] || return 1
  line="${line#*=}"
  line="${line%\"}"; line="${line#\"}"
  line="${line%\'}"; line="${line#\'}"
  [[ -n "$line" ]] || return 1
  printf '%s' "$line"
}

# HTTP-Status einer URL. Zugangsdaten gehen ueber --config von der
# Standardeingabe, nie ueber die Kommandozeile: argv ist auf einem
# Mehrbenutzersystem in der Prozessliste lesbar.
http_status() {
  local url="$1" user="${2:-}" pass="${3:-}"
  if [[ -n "$user" ]]; then
    curl -sS -o /dev/null -w '%{http_code}' --max-time 20 --config - <<CURLCFG
url = "$url"
user = "$user:$pass"
CURLCFG
  else
    curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$url"
  fi
}

FAILED=0
check() {
  local url="$1" expected="$2" label="$3" user="${4:-}" pass="${5:-}"
  local code
  code="$(http_status "$url" "$user" "$pass")" || code="(kein Ergebnis)"
  if [[ "$code" != "$expected" ]]; then
    echo "   ✗ $label: erwartet $expected, war $code  ($url)"
    FAILED=1
    return 0
  fi
  echo "   ✓ $label"
}

# --------------------------------------------------------------------------
# 1/7  Vorpruefung
# --------------------------------------------------------------------------
echo "→ 1/7  Vorpruefung"

[[ -f deploy.env ]] || die "deploy.env fehlt — kopiere deploy.env.example und trage deine Werte ein."
# shellcheck disable=SC1091
source ./deploy.env

for var in SSH_HOST REMOTE_PATH BASE_URL PORTAL_USER; do
  [[ -n "${!var:-}" ]] || die "deploy.env: $var ist leer."
done

# REMOTE_PATH wird in Remote-Kommandos eingesetzt und muss deshalb ein
# absoluter Pfad ohne Sonderzeichen sein. Das schliesst nebenbei die
# Zeichen aus, an denen eine sed-Ersetzung oder eine Shell-Zitierung
# scheitern wuerde.
[[ "$REMOTE_PATH" =~ ^/[A-Za-z0-9._/-]*$ ]] \
  || die "REMOTE_PATH muss ein absoluter Pfad aus [A-Za-z0-9._/-] sein: '$REMOTE_PATH'"
REMOTE_PATH="${REMOTE_PATH%/}"
[[ -n "$REMOTE_PATH" ]] || die "REMOTE_PATH darf nicht / sein."
[[ "$SSH_HOST" =~ ^[A-Za-z0-9._@%:-]+$ ]] || die "SSH_HOST enthaelt unerlaubte Zeichen: '$SSH_HOST'"
[[ "$BASE_URL" =~ ^https?://[^[:space:]]+$ ]] || die "BASE_URL muss mit http:// oder https:// beginnen."
BASE_URL="${BASE_URL%/}"

for tool in ssh rsync curl npm openssl; do
  command -v "$tool" >/dev/null || die "$tool nicht gefunden."
done
if ! command -v htpasswd >/dev/null && ! command -v php >/dev/null; then
  die "Weder htpasswd noch php gefunden. Installiere apache2-utils oder php-cli."
fi

# Erreichbarkeit zuerst pruefen: ein npm-Build von einer Minute, der danach
# an einem toten Host scheitert, ist verschwendete Zeit — und ein nackter
# ssh-Fehler mitten im Ablauf sagt weniger als diese Meldung.
if ! ssh -o BatchMode=yes -o ConnectTimeout=15 "$SSH_HOST" true 2>/dev/null; then
  die "Keine SSH-Verbindung zu '$SSH_HOST'. Pruefe SSH_HOST in deploy.env und dass
  ein Schluessel ohne Passphrase-Abfrage hinterlegt ist (BatchMode)."
fi
HTPASSWD_REMOTE="$REMOTE_PATH/$HTPASSWD_NAME"
echo "   $SSH_HOST:$REMOTE_PATH — erreichbar"

# --------------------------------------------------------------------------
# 2/7  Assets bauen
# --------------------------------------------------------------------------
# Muss vor dem Upload laufen: Tailwind 4 nimmt nur die Klassen ins CSS, die
# im gescannten Markup (htdocs/**/*.php) vorkommen. Ein CSS aus einem Lauf
# vor der letzten View-Aenderung waere lueckenhaft.
echo "→ 2/7  Assets bauen"
npm install --silent
npm run build --silent

for asset in htdocs/assets/app.css htdocs/assets/flowbite.min.js \
             htdocs/assets/lightweight-charts.standalone.production.js; do
  [[ -s "$asset" ]] || die "$asset fehlt oder ist leer — der Build ist nicht durchgelaufen."
done
CSS_BYTES="$(wc -c < htdocs/assets/app.css | tr -d ' ')"
(( CSS_BYTES > 2000 )) || die "htdocs/assets/app.css ist nur $CSS_BYTES Bytes gross.
  Das sieht nach einem Build ohne erfasstes Markup aus (@source in src/app.css)."
echo "   app.css: $CSS_BYTES Bytes"

# --------------------------------------------------------------------------
# 3/7  Passwort
# --------------------------------------------------------------------------
echo "→ 3/7  Passwort"
PASSWORD=""
HASH_LINE=""
NEW_PASSWORD=false

# HTPASSWD_REMOTE soll hier auf der Client-Seite expandieren -- der Pfad ist
# oben auf [A-Za-z0-9._/-] geprueft und damit fuer die Remote-Shell harmlos.
# shellcheck disable=SC2029
if $ROTATE || ! ssh "$SSH_HOST" "test -f '$HTPASSWD_REMOTE'"; then
  # Nur Buchstaben und Ziffern: das Passwort wandert durch .env, curl und
  # htpasswd, und jedes Sonderzeichen ist dort eine Zitierungsfrage mehr.
  # 32 Zeichen aus 62 Moeglichkeiten sind rund 190 Bit.
  PASSWORD="$(openssl rand -base64 64 | LC_ALL=C tr -dc 'A-Za-z0-9' | cut -c1-32)"
  [[ ${#PASSWORD} -eq 32 ]] || die "Passworterzeugung fehlgeschlagen."
  if command -v htpasswd >/dev/null; then
    # -i liest das Passwort von stdin. htpasswd -nb haette es in argv
    # gestellt und damit in die Prozessliste.
    HASH_LINE="$(printf '%s' "$PASSWORD" | htpasswd -niB "$PORTAL_USER")"
  else
    HASH_LINE="$PORTAL_USER:$(printf '%s' "$PASSWORD" \
      | php -r 'echo password_hash(stream_get_contents(STDIN), PASSWORD_BCRYPT);')"
  fi
  # '$2' ist hier das bcrypt-Praefix im Hash, keine Variable.
  # shellcheck disable=SC2016
  [[ "$HASH_LINE" == "$PORTAL_USER:"*'$2'* ]] || die "Kein bcrypt-Hash erzeugt: '$HASH_LINE'"
  NEW_PASSWORD=true
  echo "   Neues Passwort erzeugt."
else
  echo "   .htpasswd existiert bereits — Passwort bleibt. (--rotate-password erzwingt ein neues)"
  # Fuer die Pruefung in Schritt 6 wird das bestehende Passwort gebraucht.
  # Es steht in ../.env, weil ein frueherer Lauf es dort abgelegt hat.
  # Ohne diesen Griff waere jedes Update nur halb geprueft: dass der Schutz
  # abweist, saehe man, dass er die richtigen Leute durchlaesst, nicht.
  if PASSWORD="$(read_env_value PORTAL_PASSWORD)"; then
    ENV_USER="$(read_env_value PORTAL_USER || true)"
    if [[ -n "${ENV_USER:-}" && "$ENV_USER" != "$PORTAL_USER" ]]; then
      echo "   ! $ENV_FILE nennt PORTAL_USER='$ENV_USER', deploy.env '$PORTAL_USER'."
      echo "     Geprueft wird mit '$PORTAL_USER' aus deploy.env."
    fi
  else
    PASSWORD=""
    echo "   ! Kein PORTAL_PASSWORD in $ENV_FILE — der Zugangs-Test wird uebersprungen."
  fi
fi

# --------------------------------------------------------------------------
# 4/7  Upload
# --------------------------------------------------------------------------
echo "→ 4/7  Upload"

# Die beiden Ausschluesse sind der gefaehrlichste Teil dieses Skripts.
# --delete raeumt auf dem Server alles weg, was lokal fehlt:
#   data/      enthaelt die SQLite-Datenbank mit dem gesamten Ledger und
#              existiert lokal gar nicht (gitignored).
#   .htpasswd  liegt im Document-Root, wird aber nur beim ersten Lauf oder
#              bei --rotate-password neu geschrieben. Ohne Ausschluss
#              loescht jedes Update die Zugangsdaten und sperrt das Portal
#              entweder auf oder komplett zu.
RSYNC_EXCLUDES=(--exclude "data/" --exclude "/$HTPASSWD_NAME" --exclude ".DS_Store" --exclude ".gitkeep")

# .htaccess bekommt den absoluten Pfad zur .htpasswd auf dem Server.
# Ersetzt wird per Bash-Expansion statt per sed: sed braucht ein
# Trennzeichen, das im Pfad nicht vorkommen darf, und interpretiert im
# Ersatztext ausserdem & und \. Die Expansion kennt beides nicht.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp -R htdocs/. "$TMP/"
HTACCESS="$(cat "$TMP/.htaccess")"
[[ "$HTACCESS" == *__AUTH_USER_FILE__* ]] || die "htdocs/.htaccess enthaelt keinen Platzhalter __AUTH_USER_FILE__."
printf '%s\n' "${HTACCESS//__AUTH_USER_FILE__/$HTPASSWD_REMOTE}" > "$TMP/.htaccess"
grep -q "^AuthUserFile $HTPASSWD_REMOTE\$" "$TMP/.htaccess" \
  || die "Ersetzung von __AUTH_USER_FILE__ ist fehlgeschlagen."

# Probelauf: die Ausschluesse werden nicht geglaubt, sondern nachgesehen.
# Eine falsch geschriebene --exclude-Regel faellt hier auf, bevor sie
# Daten kostet. Nebenbei sieht man, was der echte Lauf loeschen wird.
DRY_RUN="$(rsync -a --delete --dry-run --out-format='%o %n' "${RSYNC_EXCLUDES[@]}" \
             "$TMP/" "$SSH_HOST:$REMOTE_PATH/")"
DELETIONS="$(printf '%s\n' "$DRY_RUN" | grep -E '^(del\.|\*?deleting)' || true)"
if [[ -n "$DELETIONS" ]]; then
  echo "   Der Upload wuerde diese Dateien auf dem Server entfernen:"
  printf '%s\n' "$DELETIONS" | sed 's/^/     /'
fi
DANGER="$(printf '%s\n' "$DELETIONS" | awk 'NF {print $NF}' \
            | grep -E "^(data(/|\$)|${HTPASSWD_NAME}\$)" || true)"
if [[ -n "$DANGER" ]]; then
  die "Abbruch: der Probelauf wuerde data/ oder $HTPASSWD_NAME loeschen.
  Die --exclude-Regeln greifen nicht. Es wurde nichts hochgeladen."
fi

rsync -az --delete "${RSYNC_EXCLUDES[@]}" "$TMP/" "$SSH_HOST:$REMOTE_PATH/"

if [[ "$NEW_PASSWORD" == true ]]; then
  # Der Hash geht ueber stdin, nicht als Argument: sonst stuende er in der
  # Prozessliste des Servers. umask 077 sorgt dafuer, dass die Datei nie
  # -- auch nicht fuer den Bruchteil zwischen cat und chmod -- weiter
  # lesbar ist als noetig.
  # shellcheck disable=SC2029
  printf '%s\n' "$HASH_LINE" \
    | ssh "$SSH_HOST" "umask 077 && cat > '$HTPASSWD_REMOTE' && chmod 640 '$HTPASSWD_REMOTE'"
  echo "   .htpasswd geschrieben."
fi

# --------------------------------------------------------------------------
# 5/7  Serverseite einrichten
# --------------------------------------------------------------------------
echo "→ 5/7  Serverseite einrichten"
REMOTE_OUT="$(ssh "$SSH_HOST" bash -s -- "$REMOTE_PATH" <<'REMOTE'
set -eu
cd "$1"
mkdir -p data

# Der Dateiname der Datenbank ist gewuerfelt und darf sich nie aendern --
# ein neuer Name waere eine leere Datenbank.
db="$(ls data/portal-*.sqlite 2>/dev/null | head -1 || true)"
if [ -z "$db" ]; then
  rand="$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')"
  db="data/portal-$rand.sqlite"
  echo "   Datenbank angelegt: $db"
fi
[ -f "$db" ] || : > "$db"
chmod 660 "$db" 2>/dev/null || true

# Bei jedem Lauf, nicht nur beim ersten: rsync hat lib/config.php gerade
# durch die Repo-Fassung ersetzt, die wieder auf data/portal.sqlite zeigt.
# Ohne sed -i: der Suffix-Umgang von -i ist zwischen GNU, BSD und busybox
# uneinheitlich, ein Schreiben ueber eine Zwischendatei ist es nicht.
sed "s#/../data/portal.sqlite#/../$db#" lib/config.php > lib/config.php.new
mv lib/config.php.new lib/config.php
grep -q "'db'.*/../$db'" lib/config.php || {
  echo "config.php zeigt nach der Ersetzung nicht auf $db" >&2
  exit 1
}

# Ebenfalls bei jedem Lauf: die Datei kommt nicht aus dem rsync (data/ ist
# ausgeschlossen) und ist die zweite Sicherung hinter der FilesMatch-Regel
# der Wurzel-.htaccess.
printf 'Require all denied\n' > data/.htaccess
chmod 750 data lib

echo "DBFILE=$(basename "$db")"
REMOTE
)" || die "Die Einrichtung auf dem Server ist gescheitert. Das Portal ist hochgeladen,
  aber lib/config.php zeigt moeglicherweise auf die falsche Datenbank."

printf '%s\n' "$REMOTE_OUT" | grep -v '^DBFILE=' || true
DBFILE="$(printf '%s\n' "$REMOTE_OUT" | sed -n 's/^DBFILE=//p' | tail -1)"
[[ -n "$DBFILE" ]] || die "Der Server hat keinen Datenbanknamen zurueckgemeldet."

# --------------------------------------------------------------------------
# 6/7  Schutz pruefen
# --------------------------------------------------------------------------
echo "→ 6/7  Schutz pruefen"
check "$BASE_URL/api/health" 401 "API ohne Zugangsdaten abgewiesen"
if [[ -n "$PASSWORD" ]]; then
  check "$BASE_URL/api/health" 200 "API mit Zugangsdaten erreichbar" "$PORTAL_USER" "$PASSWORD"
else
  echo "   – Zugangs-Test uebersprungen: das Passwort ist hier nicht bekannt."
  echo "     Mit --rotate-password erzeugt der naechste Lauf eines und prueft vollstaendig."
fi
check "$BASE_URL/$HTPASSWD_NAME" 403 ".htpasswd gesperrt"
check "$BASE_URL/data/$DBFILE" 403 "Datenbank gesperrt"

if [[ "$FAILED" != 0 ]]; then
  echo
  echo "Deployment gescheitert: der Schutz greift nicht wie erwartet."
  echo "Das Portal ist hochgeladen, aber moeglicherweise offen. Pruefe die"
  echo ".htaccess-Unterstuetzung des Hosters (AllowOverride) und wiederhole."
  if [[ "$NEW_PASSWORD" != true && -n "$PASSWORD" ]]; then
    echo "Schlug nur der Zugangs-Test fehl, passt das Passwort in $ENV_FILE nicht"
    echo "mehr zur .htpasswd auf dem Server: ./deploy.sh --rotate-password."
  fi
  exit 1
fi

# --------------------------------------------------------------------------
# 7/7  Zugangsdaten ablegen
# --------------------------------------------------------------------------
echo "→ 7/7  Fertig"
if [[ "$NEW_PASSWORD" == true ]]; then
  # .env neu schreiben: alte PORTAL_*-Zeilen raus, neue rein. Erst in eine
  # Datei mit 600, dann an ihren Platz -- ein Zwischenstand mit 644 waere
  # ein lesbares Passwort auf einem Mehrbenutzersystem.
  ENV_TMP="$TMP/env"
  ( umask 077
    grep -vE '^[[:space:]]*(export[[:space:]]+)?PORTAL_(URL|USER|PASSWORD)=' "$ENV_FILE" 2>/dev/null > "$ENV_TMP" || true
    {
      echo "PORTAL_URL=$BASE_URL"
      echo "PORTAL_USER=$PORTAL_USER"
      echo "PORTAL_PASSWORD=$PASSWORD"
    } >> "$ENV_TMP" )
  [[ -f "$ENV_FILE" ]] || ( umask 077; : > "$ENV_FILE" )
  chmod 600 "$ENV_FILE"
  cat "$ENV_TMP" > "$ENV_FILE"

  cat <<BANNER

  ┌────────────────────────────────────────────────────────┐
  │  Zugangsdaten — sie werden nur dieses eine Mal gezeigt  │
  ├────────────────────────────────────────────────────────┤
     URL:      $BASE_URL
     Benutzer: $PORTAL_USER
     Passwort: $PASSWORD

     In $ENV_FILE geschrieben, publish.py findet sie dort.
  └────────────────────────────────────────────────────────┘

BANNER
else
  echo "   $BASE_URL ist aktuell. Zugangsdaten unveraendert."
fi

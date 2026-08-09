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

# Schreibt die Zugangsdaten nach ../.env. Wird aufgerufen, sobald die
# .htpasswd auf dem Server liegt -- nicht erst am Ende: scheitert die
# Pruefung in Schritt 6, gilt das neue Passwort auf dem Server trotzdem
# schon, und ein Passwort, das nur der Server kennt, sperrt aus.
write_env_credentials() {
  # Alte PORTAL_*-Zeilen raus, neue rein. Erst in eine Datei mit 600, dann
  # an ihren Platz -- ein Zwischenstand mit 644 waere ein lesbares Passwort
  # auf einem Mehrbenutzersystem.
  local env_tmp="$TMP/env"
  ( umask 077
    grep -vE '^[[:space:]]*(export[[:space:]]+)?PORTAL_(URL|USER|PASSWORD)=' "$ENV_FILE" 2>/dev/null > "$env_tmp" || true
    {
      echo "PORTAL_URL=$BASE_URL"
      echo "PORTAL_USER=$PORTAL_USER"
      echo "PORTAL_PASSWORD=$PASSWORD"
    } >> "$env_tmp" )
  [[ -f "$ENV_FILE" ]] || ( umask 077; : > "$ENV_FILE" )
  chmod 600 "$ENV_FILE"
  cat "$env_tmp" > "$ENV_FILE"
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

# WEB_PATH ist derselbe Ordner, nur aus der Sicht des Webservers. Auf
# Hostern mit chroot-SSH (Plesk) sind die beiden Pfade verschieden: die
# SSH-Sitzung sieht /domain.de/httpdocs, Apache /var/www/vhosts/...
# /domain.de/httpdocs. Der Unterschied faellt nur an einer Stelle auf, dort
# aber hart: AuthUserFile in der .htaccess liest Apache, nicht die
# SSH-Shell. Steht dort der chroot-Pfad, findet Apache die Datei nicht --
# ohne Zugangsdaten kommt weiter ein sauberes 401, mit Zugangsdaten aber
# ein 500, und das fuer jede Datei der Seite.
# Ohne chroot sind beide Pfade gleich; dann darf WEB_PATH leer bleiben.
WEB_PATH="${WEB_PATH:-$REMOTE_PATH}"

# REMOTE_PATH wird in Remote-Kommandos eingesetzt und muss deshalb ein
# absoluter Pfad ohne Sonderzeichen sein. Das schliesst nebenbei die
# Zeichen aus, an denen eine sed-Ersetzung oder eine Shell-Zitierung
# scheitern wuerde.
[[ "$REMOTE_PATH" =~ ^/[A-Za-z0-9._/-]*$ ]] \
  || die "REMOTE_PATH muss ein absoluter Pfad aus [A-Za-z0-9._/-] sein: '$REMOTE_PATH'"
REMOTE_PATH="${REMOTE_PATH%/}"
[[ -n "$REMOTE_PATH" ]] || die "REMOTE_PATH darf nicht / sein."
[[ "$WEB_PATH" =~ ^/[A-Za-z0-9._/-]*$ ]] \
  || die "WEB_PATH muss ein absoluter Pfad aus [A-Za-z0-9._/-] sein: '$WEB_PATH'"
WEB_PATH="${WEB_PATH%/}"
[[ -n "$WEB_PATH" ]] || die "WEB_PATH darf nicht / sein."
[[ "$SSH_HOST" =~ ^[A-Za-z0-9._@%:-]+$ ]] || die "SSH_HOST enthaelt unerlaubte Zeichen: '$SSH_HOST'"
[[ "$BASE_URL" =~ ^https?://[^[:space:]]+$ ]] || die "BASE_URL muss mit http:// oder https:// beginnen."
BASE_URL="${BASE_URL%/}"

for tool in ssh tar curl npm openssl; do
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

# Der Hoster gibt nur eine chroot-Shell mit einem kleinen Werkzeugsatz.
# Fehlt dort etwas, faellt es hier auf und nicht mitten im Upload.
MISSING="$(ssh "$SSH_HOST" 'for t in tar find sed grep php chmod mkdir rm rmdir; do
  command -v "$t" >/dev/null || echo "$t"
done')" || die "Werkzeug-Pruefung auf dem Server fehlgeschlagen."
[[ -z "$MISSING" ]] || die "Auf dem Server fehlen: $(echo $MISSING). Ohne sie laeuft das Deployment nicht."

HTPASSWD_REMOTE="$REMOTE_PATH/$HTPASSWD_NAME"   # zum Schreiben ueber ssh
HTPASSWD_WEB="$WEB_PATH/$HTPASSWD_NAME"         # fuer AuthUserFile in der .htaccess
echo "   $SSH_HOST:$REMOTE_PATH — erreichbar"
[[ "$WEB_PATH" == "$REMOTE_PATH" ]] || echo "   Webserver-Sicht: $WEB_PATH"

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

# Der Hoster hat kein rsync -- die chroot-Shell kennt nur tar, find und sed.
# Deshalb zwei Schritte: tar ueber ssh schiebt die Dateien hin, ein
# Listenvergleich raeumt hinterher das weg, was rsync --delete entfernt
# haette. Beides zusammen ist wie vorher idempotent.
#
# Die beiden Ausnahmen sind der gefaehrlichste Teil dieses Skripts.
# Das Aufraeumen entfernt auf dem Server alles, was lokal fehlt:
#   data/      enthaelt die SQLite-Datenbank mit dem gesamten Ledger und
#              existiert lokal gar nicht (gitignored).
#   .htpasswd  liegt im Document-Root, wird aber nur beim ersten Lauf oder
#              bei --rotate-password neu geschrieben. Ohne Ausnahme
#              loescht jedes Update die Zugangsdaten und sperrt das Portal
#              entweder auf oder komplett zu.
# Beide sind vom Vergleich ausgenommen und werden zusaetzlich unten noch
# einmal gegengeprueft, bevor irgendetwas geloescht wird.
KEEP_RE='^(data(/|$)|'"$HTPASSWD_NAME"'$)'

# .htaccess bekommt den absoluten Pfad zur .htpasswd auf dem Server.
# Ersetzt wird per Bash-Expansion statt per sed: sed braucht ein
# Trennzeichen, das im Pfad nicht vorkommen darf, und interpretiert im
# Ersatztext ausserdem & und \. Die Expansion kennt beides nicht.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp -R htdocs/. "$TMP/"

# Statt tar --exclude: der Sendekorb ist eine Wegwerfkopie, aus der das
# Unerwuenschte einfach verschwindet. Das spart die Frage, gegen welche
# Form des Pfades ein tar-Muster eigentlich matcht -- BSD- und GNU-tar
# antworten darauf verschieden.
find "$TMP" \( -name '.DS_Store' -o -name '.gitkeep' -o -name '._*' \) -delete
rm -rf "$TMP/data" "${TMP:?}/$HTPASSWD_NAME"

HTACCESS="$(cat "$TMP/.htaccess")"
[[ "$HTACCESS" == *__AUTH_USER_FILE__* ]] || die "htdocs/.htaccess enthaelt keinen Platzhalter __AUTH_USER_FILE__."
printf '%s\n' "${HTACCESS//__AUTH_USER_FILE__/$HTPASSWD_WEB}" > "$TMP/.htaccess"
grep -q "^AuthUserFile $HTPASSWD_WEB\$" "$TMP/.htaccess" \
  || die "Ersetzung von __AUTH_USER_FILE__ ist fehlgeschlagen."

# Was liegt hier, was liegt dort? Verglichen werden Pfade relativ zum
# Document-Root, beide Listen sortiert, damit comm sie gegeneinander
# halten kann.
LOCAL_FILES="$( cd "$TMP" && find . \( -type f -o -type l \) | sed 's#^\./##' | LC_ALL=C sort )"
LOCAL_DIRS="$(  cd "$TMP" && find . -mindepth 1 -type d      | sed 's#^\./##' | LC_ALL=C sort )"

REMOTE_LISTING="$(ssh "$SSH_HOST" bash -s -- "$REMOTE_PATH" <<'REMOTE'
set -eu
cd "$1"
find . \( -type f -o -type l \) | sed 's#^\./##' | sed 's#^#F #'
find . -mindepth 1 -type d      | sed 's#^\./##' | sed 's#^#D #'
REMOTE
)" || die "Das Auflisten der Dateien auf dem Server ist gescheitert."

REMOTE_FILES="$(printf '%s\n' "$REMOTE_LISTING" | sed -n 's/^F //p' | grep -Ev "$KEEP_RE" | LC_ALL=C sort || true)"
REMOTE_DIRS="$( printf '%s\n' "$REMOTE_LISTING" | sed -n 's/^D //p' | grep -Ev "$KEEP_RE" | LC_ALL=C sort || true)"

STALE_FILES="$(comm -13 <(printf '%s\n' "$LOCAL_FILES") <(printf '%s\n' "$REMOTE_FILES"))"
# Verzeichnisse absteigend, damit das tiefste zuerst faellt: ein rmdir auf
# ein noch gefuelltes Verzeichnis scheitert sonst.
STALE_DIRS="$(comm -13 <(printf '%s\n' "$LOCAL_DIRS") <(printf '%s\n' "$REMOTE_DIRS") | LC_ALL=C sort -r)"

if [[ -n "$STALE_FILES" || -n "$STALE_DIRS" ]]; then
  echo "   Das Aufraeumen entfernt auf dem Server:"
  printf '%s\n' "$STALE_FILES" "$STALE_DIRS" | grep -v '^$' | sed 's/^/     /'
fi

# Die Ausnahmen werden nicht geglaubt, sondern nachgesehen: eine falsch
# geschriebene Regel faellt hier auf, bevor sie Daten kostet.
DANGER="$(printf '%s\n' "$STALE_FILES" "$STALE_DIRS" | grep -E "$KEEP_RE" || true)"
if [[ -n "$DANGER" ]]; then
  die "Abbruch: das Aufraeumen wuerde data/ oder $HTPASSWD_NAME loeschen.
  Die Ausnahmeregel greift nicht. Es wurde nichts hochgeladen."
fi

# COPYFILE_DISABLE und --no-xattrs halten die AppleDouble-Dateien (._foo)
# und die macOS-Metadaten aus dem Archiv, die das tar von macOS sonst fuer
# jede Datei mitschickt -- das GNU-tar der Gegenseite warnt sonst bei jeder
# einzelnen Datei ueber unbekannte Header.
#
# --no-overwrite-dir ist der wichtige Teil: das Archiv enthaelt als ersten
# Eintrag sein eigenes Wurzelverzeichnis, und dessen Rechte sind die des
# mktemp-Verzeichnisses (700). Ohne die Option setzt das Auspacken sie dem
# Document-Root auf, der Webserver kommt nicht mehr hinein und die ganze
# Seite antwortet mit 403. Mit ihr behalten bestehende Verzeichnisse die
# Rechte, die der Hoster ihnen gegeben hat.
COPYFILE_DISABLE=1 tar -C "$TMP" --no-xattrs -czf - . \
  | ssh "$SSH_HOST" "cd '$REMOTE_PATH' && tar -xzf - --no-overwrite-dir" \
  || die "Der Upload ist gescheitert."

# Die Namen wandern ueber die Standardeingabe und werden von der
# Remote-Shell gelesen, nie ausgefuehrt: ein Dateiname mit Sonderzeichen
# ist damit ein Wert und kein Kommando.
if [[ -n "$STALE_FILES" ]]; then
  printf '%s\n' "$STALE_FILES" \
    | ssh "$SSH_HOST" "cd '$REMOTE_PATH' && while IFS= read -r f; do
         [ -n \"\$f\" ] && rm -f -- \"./\$f\"
       done" || die "Das Entfernen alter Dateien ist gescheitert."
fi
if [[ -n "$STALE_DIRS" ]]; then
  # rmdir statt rm -rf: was wider Erwarten noch gefuellt ist, bleibt
  # stehen, statt mitsamt Inhalt zu verschwinden.
  printf '%s\n' "$STALE_DIRS" \
    | ssh "$SSH_HOST" "cd '$REMOTE_PATH' && while IFS= read -r d; do
         [ -n \"\$d\" ] && rmdir -- \"./\$d\" 2>/dev/null || true
       done" || true
fi

if [[ "$NEW_PASSWORD" == true ]]; then
  # Der Hash geht ueber stdin, nicht als Argument: sonst stuende er in der
  # Prozessliste des Servers. umask 077 sorgt dafuer, dass die Datei zwischen
  # cat und chmod nicht weiter lesbar ist als am Ende.
  #
  # 644 und nicht enger: Apache liest die .htpasswd als eigener Benutzer,
  # der weder Eigentuemer noch in dessen Gruppe ist. Mit 640 kommt er nicht
  # an die Datei und antwortet auf jede Anfrage mit Zugangsdaten -- auch auf
  # eine nach einer CSS-Datei -- mit 500. Vor dem Netz schuetzt die Datei
  # nicht die Rechte, sondern die FilesMatch-Regel der .htaccess; dass sie
  # greift, prueft Schritt 6.
  # shellcheck disable=SC2029
  printf '%s\n' "$HASH_LINE" \
    | ssh "$SSH_HOST" "umask 077 && cat > '$HTPASSWD_REMOTE' && chmod 644 '$HTPASSWD_REMOTE'"
  echo "   .htpasswd geschrieben."
  write_env_credentials
  echo "   Zugangsdaten in $ENV_FILE hinterlegt."
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
  # od gibt es in der chroot-Shell des Hosters nicht, php schon.
  rand="$(php -r 'echo bin2hex(random_bytes(8));')"
  [ ${#rand} -eq 16 ] || { echo "Zufallsname fehlgeschlagen" >&2; exit 1; }
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
  echo
  echo "Kam bei 'mit Zugangsdaten' ein 500 -- und zwar fuer jede Datei, auch"
  echo "fuer ein CSS --, dann findet Apache die .htpasswd nicht: WEB_PATH in"
  echo "deploy.env zeigt nicht dorthin, wo der Webserver das Verzeichnis"
  echo "sieht. Den Pfad nennt ein <?php echo __DIR__; im Document-Root."
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

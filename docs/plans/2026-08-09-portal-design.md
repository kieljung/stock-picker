# Design: Report-Portal mit API

**Datum:** 2026-08-09
**Status:** Genehmigt
**Ziel:** Ein passwortgeschütztes PHP-Portal auf Shared Hosting, das die täglichen Analyse-Reports lesbar darstellt — je Papier ein Chart mit Kursverlauf und eingezeichneten Levels neben der Begründung — und die Trefferquote des Systems über die Zeit sichtbar macht. Die Analyse-Pipeline liefert ihre Ergebnisse über eine API an.

## Einordnung

Das Portal ist reiner Leser. Es schreibt nichts zurück in die Pipeline, berechnet keine Kurse und trifft keine Entscheidungen. Alles, was es zeigt, hat die Pipeline vorher erzeugt und geprüft. Diese Einbahnstraße ist Absicht: Sobald beide Seiten schreiben dürfen, stellt sich die Frage, welche die Wahrheit hält, und der Ledger verliert seinen Wert als Protokoll.

Zwei Änderungen am bestehenden System sind Voraussetzung. Stufe 2 gibt künftig **nur noch JSON** aus statt eines Markdown-Reports mit angehängtem JSON-Block; die Prosa steckt als Markdown-Strings in den Feldern und wird erst im Portal gerendert. Und hinter `validate.py` tritt ein neues `publish.py`, das den Push übernimmt.

## Architektur

Gewählt wurde server-gerendertes PHP ohne Framework, mit client-seitig hydrierten Charts.

```
screen.py → candidates.json
              │  (Claude-Session, PROMPT.md)
              ▼
     out/report-2026-08-09.json          ← nur noch JSON
              │  validate.py             ← liest die Datei direkt
              ▼
       data/ledger.jsonl
              │  publish.py
              ▼
   POST /api/reports   Report + Picks + 6 Monate Bars je Ticker

täglich:  publish.py --sync
   POST /api/bars      Delta-Bars aller offenen Picks
   POST /api/outcomes  Status je Pick aus outcomes.evaluate
```

Kein Composer, keine Runtime-Abhängigkeit auf dem Hoster außer `pdo_sqlite` und `mod_rewrite`. Rund 800–1200 Zeilen PHP. Der Datenumfang ist klein — fünf Picks am Tag —, die Seiten sind weitgehend statisch, und die einzige echte Interaktivität steckt im Chart, der ohnehin JavaScript braucht.

Die Auswertung bleibt in Python. `outcomes.evaluate` ist getestet und kennt die Regel, dass eine Bar, die Stop *und* Target berührt, konservativ als Stop zählt. Diese Logik ein zweites Mal in PHP zu schreiben hieße, sie ein zweites Mal falsch machen zu können. Das Portal bekommt fertige Status und aggregiert nur noch in SQL.

### Verworfene Alternativen

**PHP nur als JSON-API, Frontend als statische SPA** — sauberere Trennung, dasselbe API bedient Pipeline und Browser. Verworfen: Routing, State und Rendering wandern nach JavaScript, für vier Seiten entsteht eine zweite Anwendung. Der Umfang verdoppelt sich, ohne dass ein Nutzer davon etwas merkt.

**Pipeline generiert statisches HTML, PHP nur für den Upload** — am wenigsten Code auf dem Server, kaum Angriffsfläche. Verworfen: Die Performance-Sicht müsste bei jedem Push vollständig neu gebaut werden, Sortieren und Filtern fällt weg, und das Templating läge in Python statt in PHP.

**Portal holt Kurse selbst live** — PHP fragt yfinance oder Stooq direkt ab, immer aktuell ohne zweiten Job. Verworfen: auf Shared Hosting fragil durch Timeouts und gedrosselte ausgehende Verbindungen, braucht eigene Caching-Logik und verlagert Datenlogik ins Portal, das sonst nichts weiß.

**Bars nur einmal beim Report-Push** — ein einziger API-Call, kein täglicher Job. Verworfen: Der Chart friert am Analysetag ein. Der Verlauf *nach* dem Pick ist der Teil, wegen dem das Portal existiert.

**Markdown-Parser in der Pipeline** — der bestehende Report bliebe unverändert, ein Python-Modul zerlegt ihn an den festen Überschriften. Verworfen zugunsten der einfacheren Lösung: Das Modell erzeugt gar kein Markdown-Dokument mehr, sondern nur noch JSON. Kein Parser, keine Formatabweichung, die Prosa steht genau einmal im System.

## Datenmodell

### Report-JSON — der neue Output von Stufe 2

```json
{
  "date": "2026-08-09",
  "generated_at": "2026-08-09T12:35:34",
  "market_context": "Der Russell 2000 notiert über seiner SMA 50 …",
  "note": "Auffällig an diesem Lauf: neun der zwanzig Kandidaten …",
  "picks": [{
    "ticker": "CRSR", "name": "Corsair Gaming", "sector": "Information Technology",
    "setup": "PEAD", "rank": 1,
    "score_technical": 9, "score_catalyst": 8, "score_risk": 9, "composite": 87,
    "score_rationale": {
      "technical": "PEAD-Kriterien deutlich übererfüllt: Gap 19,53 % gegen geforderte 5 % …",
      "catalyst":  "Harte Zahlen mit Prognoseanhebung, abgewertet wegen Zolleffekt …",
      "risk":      "R:R 2,06, Stop 1,83 ATR, 28,3 Mio USD Tagesvolumen"
    },
    "entry": 14.60, "stop": 12.95, "target1": 18.00, "target2": 20.00,
    "risk_reward": 2.06, "position_size": 60,
    "catalyst": "Beat-and-Raise, Rekordmarge 2026-08-06",
    "thesis": "…", "technicals": "…", "catalyst_detail": "…",
    "plan": { "entry": "…", "stop": "…", "target1": "…", "target2": "…" },
    "bear_case": "…", "invalidation": "…",
    "metrics": { "close": 14.35, "rs_percentile": 99.0, "rsi14": 75.8, "atr14": 0.9,
                 "rvol": 6.12, "gap_pct": 19.53, "…": "aus candidates.json übernommen" },
    "sources": [{ "url": "https://…", "title": "StockTitan", "date": "2026-08-06" }]
  }],
  "checklist": ["Jede Zahl trägt Screener oder Quelle", "…"]
}
```

`metrics` ist der Screener-Datensatz des Tickers, unverändert durchgereicht. Das Modell erfindet dort nichts, und das Portal kann die Kennzahlen visualisieren, ohne sie neu abzuleiten.

`sources` wird von Strings auf Objekte umgestellt, damit die Quellenliste lesbare Namen statt nackter URLs zeigt. `validate.py` akzeptiert beide Formen, sonst brechen die bestehenden Ledger-Zeilen.

### Portal-SQLite

| Tabelle | Inhalt | Schlüssel |
|---|---|---|
| `reports` | Datum, Marktkontext, Notiz, Checkliste, `pushed_at` | `date` |
| `picks` | alle Pick-Felder, plus `status`, `r_multiple`, `triggered_on`, `closed_on` | `(report_date, ticker)` |
| `bars` | OHLCV, tickerweit geteilt über alle Reports | `(ticker, date)` |
| `pushes` | Audit: Endpunkt, Zeitpunkt, Bytes, Ergebnis | `id` |
| `schema_version` | Migrationsstand | — |

`bars` hängt am Ticker, nicht am Report — taucht ein Titel zweimal auf, existiert die Reihe einmal.

Ein Push mit bekanntem Datum ersetzt Report und Picks in einer Transaktion; `bars` und der bereits ermittelte Status bleiben erhalten. Sonst würde eine Reportkorrektur die Historie löschen. Dass ein Report korrigiert werden darf, ist eine bewusste Abkehr von `append_entries`, das heute bei einem vorhandenen Datum abbricht.

## API

Vier Endpunkte, alle JSON, alle hinter derselben Basic Auth.

| Methode | Pfad | Body | Antwort |
|---|---|---|---|
| `POST` | `/api/reports` | Report-JSON + `bars` je Ticker | `{"date":"…","picks":5,"bars_written":627,"replaced":false}` |
| `POST` | `/api/bars` | `{"CRSR":[["2026-08-10",14.4,15.1,14.2,15.0,2103400], …]}` | `{"written":42}` |
| `POST` | `/api/outcomes` | `[{"date":"…","ticker":"…","status":"TARGET1","r_multiple":2.06}]` | `{"updated":5}` |
| `GET` | `/api/health` | — | `{"ok":true,"reports":12,"picks":58,"last_push":"…"}` |

Bars als Array-of-Arrays statt Objekte: bei 125 Tagen mal fünf Titeln spart das rund zwei Drittel Payload, und die Spaltenreihenfolge ist fix — Datum, Open, High, Low, Close, Volume.

Antworten sind immer `{"ok":…}` oder `{"error":"…","details":[…]}` mit passendem Statuscode. `publish.py` bricht bei allem außer 2xx ab und gibt `details` unverändert aus.

Das API validiert eingehende Reports gegen dieselben harten Regeln wie `validate.py`: Stop unter Entry, Target 1 über Entry, Target 2 über Target 1, R:R mindestens 2, Composite mindestens 60, höchstens fünf Picks, höchstens zwei je Sektor, Quellen vorhanden. Doppelt geprüft, absichtlich — die Datenbank ist die einzige Instanz, die kein Mensch vorher gesehen hat, und ein 422 mit klarer Fehlerliste ist billiger als ein stiller Fehleintrag.

## Authentifizierung und Dateilayout

Die Auth läuft auf Apache-Ebene, nicht in PHP: eine `.htpasswd` mit bcrypt, **ein** gemeinsamer Nutzer für Portal und API. Das schützt auch alles, was PHP nie zu sehen bekommt — Assets, versehentlich abgelegte Dateien, den Verzeichnisindex. PHP liest `$_SERVER['REMOTE_USER']` nur zur Anzeige.

```
htdocs/
  .htaccess                  Auth, Rewrite, Deny-Regeln
  .htpasswd                  bcrypt, vom Deploy erzeugt
  index.php                  Front-Controller UI
  api.php                    Front-Controller API
  lib/
    .htaccess                Require all denied
    db.php  repo.php  api.php  validate.php  markdown.php  Parsedown.php
    config.php  migrate.php
    views/  layout.php  reports.php  report.php  pick.php  performance.php
  assets/
    app.css  flowbite.min.js  lightweight-charts.standalone.production.js
  data/
    .htaccess                Require all denied
    portal-<8 Zufallszeichen>.sqlite
    portal.log
```

Die Datenbank bekannt zu benennen wäre die eine Nachlässigkeit, die ein einzelner Konfigurationsfehler beim Hoster in einen vollständigen Datenabfluss verwandelt. Der Zufallsname kostet nichts und macht das Erraten des Pfads unmöglich; er steht in `lib/config.php`.

```apache
AuthType Basic
AuthName "stock-picker"
AuthUserFile /absoluter/pfad/htdocs/.htpasswd
Require valid-user

<FilesMatch "^\.(htaccess|htpasswd|env)$|\.(sqlite|sqlite-wal|sqlite-shm|db|log)$">
  Require all denied
</FilesMatch>

Options -Indexes
RewriteEngine On
RewriteRule ^api/(.*)$      api.php   [QSA,L]
RewriteCond %{REQUEST_FILENAME} !-f
RewriteRule ^(.*)$          index.php [QSA,L]
```

Ein `Require all denied` innerhalb eines Blocks mit `Require valid-user` gewinnt nicht automatisch. Die Deny-Regeln stehen deshalb zusätzlich in eigenen `.htaccess`-Dateien in `lib/` und `data/`, wo kein konkurrierendes `Require` steht.

## Oberfläche

Vier Seiten, dunkles Flowbite-4-Theme als Standard mit Umschalter. Assets werden lokal gebaut, nicht vom CDN geladen: Flowbite 4 ist ein Tailwind-v4-Plugin, ein `npx @tailwindcss/cli`-Lauf erzeugt eine `app.css`, die als statische Datei mitgeht. Kein Node auf dem Server, kein externer Request zur Laufzeit.

**`/` Übersicht.** Kopfzeile mit vier Kennzahlen — offene Picks, Trefferquote, Erwartungswert in R, letzter Push. Darunter ein Report je Zeile: Datum, die fünf Ticker als Chips mit Composite-Zahl und Status-Badge, die ersten zwei Zeilen Marktkontext.

**`/r/2026-08-09` Report.** Marktkontext und Notiz als Fließtext, darunter die Übersichtstabelle — Ticker, Setup, Composite, Entry, Stop, Target 1, R:R, Katalysator —, sortierbar, jede Zeile verlinkt aufs Detail. Darunter die fünf Karten mit Miniatur-Chart.

**`/r/2026-08-09/CRSR` Pick-Detail.** Zweispaltig ab Tablet-Breite: links Chart und Zahlen, mitscrollend fixiert; rechts die Begründungen.

Im Chart:

- Kerzen plus Volumenhistogramm in der unteren Achsenzone
- vier beschriftete Preislinien — Stop rot, Entry blau, Target 1 und 2 in zwei Grünstufen
- Marker am Report-Tag (`Pick · 87`), am Auslösetag (`Entry`), am Schlusstag (`Stop` oder `T1`), sobald `outcomes` sie geliefert hat
- ein Marker am letzten Earnings-Termin, wenn er ins Fenster fällt — bei neun PEAD-Kandidaten in einem Lauf ist das die Bezugslinie, ohne die der Verlauf nicht lesbar ist

Daneben eine schmale vertikale **R-Leiter**: Stop, Entry, Target 1 und Target 2 maßstäblich untereinander, mit dem aktuellen Kurs als Zeiger. Sie beantwortet in einem Blick, was der Chart nur mit Mühe zeigt — wie weit der Trade in Vielfachen des Risikos gelaufen ist. Als schattierte Fläche im Chart bräuchte das ein eigenes Series-Primitive von lightweight-charts; die Leiter ist zwanzig Zeilen HTML und liest sich besser.

Die Begründungen werden auf zwei Wegen visualisiert. Erstens die **Score-Zerlegung**: drei Balken für Technik, Katalysator und Risiko, jeder mit dem zugehörigen Satz aus `score_rationale` direkt darunter — Zahl und Grund stehen nebeneinander statt in getrennten Abschnitten. Der Composite steht als Ring daneben. Zweitens der **Kennzahlenstreifen** aus `metrics`: RS-Perzentil, RSI(14), RVOL, ATR %, Gap %, Abstand zum Pivot, je als kleiner Balken mit eingezeichneter Schwelle des jeweiligen Setup-Templates. Damit ist sichtbar, ob ein Kriterium knapp erfüllt oder deutlich übererfüllt ist — genau die Unterscheidung, die die Prosa heute in Worten trifft.

Rechts die Textabschnitte in fester Reihenfolge: These, Technische Lage, Katalysator, Handelsplan, Bear-Case, Invalidierung, Quellen. Keine Tabs, kein Akkordeon — Bear-Case und Invalidierung sind die Abschnitte, die man am ehesten überspringt, und ein zugeklappter Bear-Case ist ein ungelesener Bear-Case. Beide sind farblich abgesetzt: Bear-Case als bernsteinfarbene Karte, Invalidierung als rot umrandeter Callout mit der Schwelle als Überschrift.

**`/performance`.** Vier KPI-Kacheln, darunter eine Linie der kumulierten R über die Zeit — dieselbe Chart-Bibliothek, andere Serie. Dann zwei Tabellen nach Setup und nach Katalysator-Bucket mit Trades, Trefferquote und Erwartungswert, wie `review.py` sie heute in die Konsole schreibt. Ein Datumsfilter oben; die Aggregation ist reines SQL über `picks` und braucht keinen Push.

Auf dem Telefon fällt die zweite Spalte unter die erste, der Chart behält volle Breite und feste Höhe. Tabellen scrollen horizontal in einem eigenen Container, statt die Seite zu verbreitern.

## Deployment

`deploy.sh` liest Host, Zielpfad und Basis-URL aus `portal/deploy.env` und läuft in sechs Schritten:

1. **Assets bauen** — `npx @tailwindcss/cli` gegen `src/app.css`, Flowbite und lightweight-charts aus `node_modules` nach `assets/`.
2. **Passwort erzeugen** — `openssl rand -base64 18`, sofern keine `.htpasswd` auf dem Server liegt oder `--rotate-password` gesetzt ist. Hash mit `htpasswd -bB`; fehlt das Binary, springt `php -r 'password_hash(…, PASSWORD_BCRYPT)'` ein. Kein stiller Fallback auf `openssl passwd -apr1` — MD5-apr1 ist für ein Passwort, das ein Jahr steht, zu wenig.
3. **`rsync -a --delete --exclude data/`** nach `htdocs/`. Das Ausschließen von `data/` ist die eine Zeile, deren Fehlen den Ledger löscht; sie steht in einer Variablen, die das Skript vorher auf Nichtleere prüft.
4. **Erstinstallation erkennen** — fehlt `data/`, wird sie angelegt, der Zufallsname der Datenbank gewürfelt und in `lib/config.php` geschrieben. Bei einem Update bleibt beides unangetastet.
5. **Vier HTTP-Prüfungen** gegen die Live-URL: `/api/health` ohne Credentials 401, mit Credentials 200, Datenbankdatei 403, `.htpasswd` 403. Fällt eine durch, Exit-Code 1 mit Angabe welcher.
6. **Passwort ausgeben** — einmalig in einem umrahmten Block, zugleich `PORTAL_URL`, `PORTAL_USER`, `PORTAL_PASSWORD` in die lokale `.env`, damit `publish.py` es ohne Copy-Paste findet. Danach steht es nirgends mehr im Klartext.

Das Schema trägt eine Versionsnummer; `lib/migrate.php` läuft beim ersten Request nach dem Deploy und wendet ausstehende Schritte an. Bei vier Tabellen ist das heute Overhead — sobald eine Spalte dazukommt und Monate an Ledger auf dem Server liegen, ist es der Unterschied zwischen einer Migration und einem Datenverlust.

## Fehlerbehandlung

`publish.py` bricht bei allem außer 2xx ab und gibt `details` unverändert aus; bei 5xx und Netzwerkfehlern dreimal mit wachsender Wartezeit. `--dry-run` validiert lokal und meldet die Payload-Größe, ohne zu senden.

Serverseitig laufen alle Schreibvorgänge in einer Transaktion, SQLite im WAL-Modus mit `busy_timeout`. PHP läuft mit `display_errors=Off`; Ausnahmen gehen nach `data/portal.log` im gesperrten Verzeichnis, der Browser bekommt eine schlichte Fehlerseite.

Die Report-Prosa stammt von einem Sprachmodell und ist damit nicht vertrauenswürdiger Input. Parsedown läuft im Safe Mode, Links bekommen `rel="noopener noreferrer"`, Quell-URLs werden gegen `https?://` geprüft, bevor sie im Markup landen. Fehlen für einen Ticker Bars, zeigt der Chartbereich einen Hinweis statt einer leeren Fläche.

## Tests

Die Python-Seite kommt in die bestehende pytest-Suite: Schema-Validierung des Report-JSON gegen gültige und absichtlich kaputte Fixtures, der Payload-Bau in `publish.py`, die Delta-Auswahl der Bars, das Mapping von `outcomes.evaluate` auf den Outcomes-Push.

Für PHP startet ein Test `php -S` auf einem freien Port, spielt ein Fixture-Report ein und prüft Statuscodes, Idempotenz beim zweiten Push, die Ablehnung eines Reports mit R:R unter 2 und dass die drei Seiten die erwarteten Kernzahlen enthalten. Fehlt das PHP-CLI, überspringt der Test sich selbst.

Die Auth wird nicht im Test geprüft, sondern in Schritt 5 des Deployments gegen den echten Apache. Der eingebaute PHP-Server kennt `.htaccess` nicht; ein Test dagegen wäre eine Zusicherung, die nichts zusichert.

## Bewusst nicht enthalten

Keine Benutzerverwaltung, keine Bearbeitung von Picks im Portal, keine Live-Kurse, keine Benachrichtigungen, kein Export, keine Kandidaten-Tabelle der nicht ausgewählten Titel. Das Portal liest, was die Pipeline schreibt.

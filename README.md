# stock-picker

Täglicher Screener, Analyse-Prompt und Report-Portal für Small-Cap-Swing-Setups
im Russell 2000.

**Kein Anlageberatungssystem.** Das Werkzeug strukturiert Recherche und macht
Entscheidungen im Nachhinein überprüfbar. Es prognostiziert keine Kurse und
garantiert keine Trefferquote.

## Tagesablauf

```bash
# 1 — Screener (ca. 5 Min, dominiert vom Metadaten-Abruf)
uv run python screen.py

# 2 — candidates.json zusammen mit PROMPT.md in eine Claude-Session mit
#     Websuche geben. Das Modell antwortet mit einem einzigen JSON-Dokument;
#     speichere es unverändert als out/report-YYYY-MM-DD.json
#
# 3 — Levels prüfen und Ledger fortschreiben
uv run python validate.py out/report-2026-08-09.json

# 4 — Report, Kurshistorie und Begründungen ans Portal übergeben
uv run python publish.py out/report-2026-08-09.json

# täglich — Kurse und Trade-Ausgänge aller offenen Picks nachliefern
uv run python publish.py --sync

# Wöchentlich — auswerten, was funktioniert hat
uv run python review.py --days 10
```

Vor dem ersten scharfen Lauf lohnt `--dry-run`. Beide Schritte kennen ihn, und
`publish.py --dry-run` liest dabei ausschließlich den Kurs-Cache: ein Probelauf,
der erst das Netz braucht, prüft nicht mehr, er handelt schon.

```bash
uv run python validate.py out/report-2026-08-09.json --dry-run
uv run python publish.py out/report-2026-08-09.json --dry-run
```

Ein korrigierter Report für ein Datum, das schon im Ledger steht, braucht
`validate.py … --replace`; das Portal ersetzt ein bereits gepushtes Datum von
sich aus.

## Aufbau

| Datei | Rolle |
|---|---|
| `screen.py` | Stufe 1 — Kennzahlen und Hard-Filter, schreibt `candidates.json` |
| `PROMPT.md` | Stufe 2 — Rubrik, Arbeitsablauf und JSON-Format für das Modell |
| `validate.py` | Stufe 3 — prüft Levels gegen Kursdaten, schreibt `data/ledger.jsonl` |
| `publish.py` | Stufe 4 — übergibt Report, Kurshistorie und Ausgänge ans Portal |
| `review.py` | Auswertung — Trefferquote und Erwartungswert je Setup-Typ |
| `stockpicker/report.py` | Schema des Report-Dokuments, Prüfung, Projektion auf den Ledger |
| `stockpicker/publish.py` | Payloads und HTTP-Client für die Übergabe ans Portal |
| `stockpicker/dotenv.py` | Liest `.env` — API-Keys und Portal-Zugang, ohne Zusatzabhängigkeit |

Die Arbeitsteilung folgt der Fehleranfälligkeit: Zahlen berechnet Code, Kontext
bewertet das Modell, und die halluzinationsanfälligste Stelle — konkrete
Kursniveaus — wird maschinell gegengeprüft. `validate.py` bricht ab, wenn ein
Stop über dem Entry liegt, das Chance-Risiko-Verhältnis unter 2:1 fällt oder ein
Entry mehr als 15 % vom letzten Schlusskurs abweicht. Dieselben Regeln laufen
im Portal ein zweites Mal, bevor ein Report in die Datenbank geht — die API ist
nicht darauf angewiesen, dass der Absender schon geprüft hat.

Details in `docs/plans/2026-08-09-swing-trade-prompt-design.md`.

## Setup-Templates

Vier anerkannte Ansätze, mechanisch vorgefiltert und vom Modell qualitativ
bewertet:

| Template | Grundlage |
|---|---|
| `VCP_BREAKOUT` | Minervini Volatility Contraction Pattern, O'Neil Base-Breakout |
| `TREND_PULLBACK` | Rücksetzer auf SMA 20/50 im intakten Aufwärtstrend |
| `MEAN_REVERSION` | Connors RSI-2 oberhalb der SMA 200 |
| `PEAD` | Post-Earnings-Announcement-Drift |

## Earnings-Kalender (optional, aber empfohlen)

yfinance kennt für rund ein Fünftel der gescreenten Titel keinen kommenden
Earnings-Termin und liegt gelegentlich grob daneben — in einem gemessenen Fall
um drei Monate. Ein übersehener Termin ist teuer: ein Earnings-Gap überspringt
jeden Stop, und `validate.py` kann das nicht abfangen, weil es kein Level-Fehler
ist. Der Screener zieht deshalb zwei zusätzliche Kalender heran.

```bash
cat >> .env <<'KEYS'
FINNHUB_API_KEY=dein-key
ALPHAVANTAGE_API_KEY=dein-key
KEYS
```

`.env` ist gitignored. Beide Free-Tiers reichen aus, weil ein einziger Request
pro Tag und Quelle den gesamten Markt abdeckt:

| Quelle | Free-Tier | Abdeckung |
|---|---|---|
| Finnhub | 60 Requests/Minute | ein Monat voraus, US-Markt |
| Alpha Vantage | 25 Requests/Tag | drei Monate voraus, ~735 der Russell-2000-Titel |

Es gewinnt immer der **früheste** bekannte Termin. Die Kosten sind asymmetrisch:
ein überflüssiger Blackout kostet eine Gelegenheit, ein übersehener kostet einen
Gap durch den Stop hindurch. `earnings_source` nennt je Kandidat alle Quellen,
die genau diesen Termin liefern (`yfinance+finnhub`), oder `null`, wenn keine
etwas weiß — dann muss die Analyse in Stufe 2 selbst recherchieren.

Ohne Keys läuft alles weiter, nur mit den yfinance-Terminen allein. `screen.py`
meldet das als Warnung und druckt bei jedem Lauf die Abdeckung je Quelle.

Die Kalender kennen nur *kommende* Termine; `days_since_earnings` für das
PEAD-Setup bleibt yfinance.

## Cache

Kursdaten und Metadaten liegen in einer SQLite-Datei unter
`data/cache/market.db` (gitignored). Der erste Lauf befüllt sie, danach lädt
der Screener nur noch die Tage nach, die fehlen.

| Lauf | Dauer für 150 Titel |
|---|---|
| Cache leer | 28 s |
| Cache warm, neuer Handelstag | 4,5 s |
| Cache warm, kein neuer Handelstag | 2,1 s, ohne Netzwerk |

Der wichtigere Effekt ist Reproduzierbarkeit. yfinance' `info`-Endpunkt
antwortet unter Last unvollständig — zwischen zwei Läufen im Abstand von
zwanzig Minuten schwankte die Zahl der Titel mit bekanntem Earnings-Termin um
62. Der Cache überschreibt einen gespeicherten Wert nie mit `None`, ein Titel
behält also seine Marktkapitalisierung von vorgestern, statt aus dem Screening
zu fallen. `screen.py` zählt in jedem Lauf, wie oft das passiert ist.

```bash
uv run python screen.py --no-cache            # alles frisch laden
uv run python screen.py --meta-max-age 1      # Fundamentaldaten täglich erneuern
rm data/cache/market.db                       # Cache verwerfen
```

Fundamentaldaten gelten fünf Tage als frisch — Marktkapitalisierung und Short
Float bewegen sich nicht so schnell, dass es für Grenzen bei 300 Mio und 3 Mrd
USD eine Rolle spielt. Earnings-Termine kommen aus den Kalendern und haben ihren
eigenen Tages-Cache.

## Portal

Ein passwortgeschütztes PHP-Portal auf Shared Hosting, das die Reports lesbar
macht: Chart, Handelsplan und Begründung je Papier, dazu die Auswertung über
alle Läufe. Es liegt unter `portal/`, deployt wird ausschließlich
`portal/htdocs/`. Kein Framework, kein Build auf dem Server — ein
Front-Controller für die Seiten, einer für die API, SQLite als Datenbank,
serverseitiges Rendering. Die Charts hydriert `assets/chart.js` client-seitig.

Vier Seiten:

| Adresse | Inhalt |
|---|---|
| `/` | Übersicht aller Reports, Gesamtperformance, Zeitpunkt des letzten Pushes |
| `/r/<datum>` | Der Report des Tages: Marktkontext, alle Picks mit Chart, verworfene Kandidaten und Checkliste eingeklappt |
| `/r/<datum>/<ticker>` | Pick-Detail: Chart mit Entry, Stop und Zielen, Kennzahlen, Handelsplan, These, Bear-Case, Invalidierung, Quellen |
| `/performance` | Trefferquote und Erwartungswert je Setup und Katalysator-Typ, kumulierte R-Kurve, Zeitraumfilter |

### Deployment

```bash
cp portal/deploy.env.example portal/deploy.env
$EDITOR portal/deploy.env      # SSH_HOST, REMOTE_PATH, BASE_URL, PORTAL_USER
./portal/deploy.sh
```

`deploy.sh` baut die Assets, lädt `htdocs/` per `rsync --delete` hoch, richtet
die Serverseite ein und prüft danach, ob der Schutz greift. Es ist idempotent:
ein zweiter Lauf ohne Änderungen lässt Datenbank und Passwort in Ruhe. Beides
ist vom Upload ausgenommen, und ein `--dry-run` von rsync bricht ab, falls die
Ausschlüsse doch einmal nicht greifen sollten.

Das Passwort erzeugt der erste Lauf selbst — 32 Zeichen aus Buchstaben und
Ziffern. Der bcrypt-Hash landet als `.htpasswd` im Document-Root, das Passwort
im Klartext ausschließlich in `.env` (Modus 600), wo `publish.py` es findet.
Im Terminal steht es **ein einziges Mal**, am Ende des Laufs. Wer es verliert,
rotiert es:

```bash
./portal/deploy.sh --rotate-password
```

Das schreibt eine neue `.htpasswd`, ersetzt die `PORTAL_*`-Zeilen in `.env` und
zeigt das neue Passwort wieder einmalig an.

### Die vier Schutzprüfungen

Nach dem Upload fragt `deploy.sh` den laufenden Server über HTTP ab, statt der
`.htaccess` zu glauben:

| Prüfung | Erwartung |
|---|---|
| `GET /api/health` ohne Zugangsdaten | 401 — Basic Auth greift überhaupt |
| `GET /api/health` mit Zugangsdaten | 200 — sie lässt auch die richtigen Leute durch |
| `GET /.htpasswd` | 403 — die Hashes sind nicht abrufbar |
| `GET /data/<datenbank>.sqlite` | 403 — der Ledger ist nicht abrufbar |

Schlägt eine fehl, endet das Skript mit Fehler und dem Hinweis, dass das Portal
zwar hochgeladen, aber möglicherweise offen ist. Häufigste Ursache ist ein
Hoster, der `.htaccess` nicht auswertet. Die zweite Prüfung entfällt, wenn kein
Passwort bekannt ist — dann prüft erst der nächste Lauf mit
`--rotate-password` vollständig.

Der Dateiname der SQLite-Datei wird beim ersten Deployment gewürfelt und danach
nie geändert; ein neuer Name wäre eine leere Datenbank.

### Was der Hoster mitbringen muss

- **PHP 8.1 oder neuer** mit `pdo_sqlite`. Die Untergrenze ist keine
  Vorsichtsmarge: `api.php` nutzt den Rückgabetyp `never`, den es erst ab 8.1
  gibt.
- **`mod_rewrite`** für die sprechenden Adressen. Ohne das Modul bleiben nur
  `index.php?path=…` und `api.php?path=…` — die Front-Controller lesen beides.
- **`AllowOverride All`** für das Verzeichnis, sonst ist die `.htaccess` wirkungslos
  und mit ihr Basic Auth, die Sperre für `.htpasswd` und Datenbank sowie das
  Rewriting. Reicht der Hoster nur einzelne Direktiven durch, braucht es
  `AuthConfig`, `FileInfo`, `Limit` und `Indexes`.

Lokal braucht `deploy.sh` `ssh`, `rsync`, `curl`, `npm`, `openssl` und
entweder `htpasswd` oder `php`. Der SSH-Zugang muss ohne Passwortabfrage
funktionieren, sonst hängt das Skript.

### Assets und Tests

```bash
cd portal && npm install && npm run build   # deploy.sh macht das selbst
bash portal/tests/run.sh
```

Der Build minifiziert das Tailwind-CSS und kopiert Flowbite und
lightweight-charts aus `node_modules` nach `htdocs/assets/`. Diese Dateien sind
gitignored; `htdocs/assets/chart.js` ist dagegen Quelltext und versioniert.

`run.sh` läuft alle `test_*.php` durch und überspringt sich mit Exit 0, wenn auf
der Maschine kein `php` liegt. Ein Test-Framework gibt es bewusst nicht: bei vier
Tabellen und vier Endpunkten wäre PHPUnit mehr Konfiguration als Testcode. Der
API-Test startet sich seinen eigenen `php -S` auf einem freien Port und räumt
ihn wieder ab.

## Entwicklung

```bash
uv venv --python 3.12 && uv sync --all-groups
uv run pytest
bash portal/tests/run.sh
```

Kein Test darf Netzwerk verwenden. Marktdaten kommen in Tests immer als
Argument oder über `monkeypatch`.

## Kalibrierung

Nach etwa 30 Läufen zeigt `review.py`, welche der vier Templates tatsächlich
tragen. Die Gewichtung wird dann in `PROMPT.md` angepasst — Block 5, nicht im
Code. `data/ledger.jsonl` ist deshalb versioniert: er ist das Gedächtnis des
Systems.

## Bewusst nicht enthalten

Kein Backtest-Framework, kein Broker-Anschluss, keine Intraday-Daten, keine
Automatisierung der Modell-Stufe. Das Portal zeigt Ergebnisse an und nimmt sie
entgegen — es löst nichts aus. Der Ledger liefert die Evidenz; alles Weitere
wartet, bis diese Evidenz zeigt, dass sich der Aufwand lohnt.

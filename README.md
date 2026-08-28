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

## Automatisierung

Der Tagesablauf ist eine Kette aus drei Schritten, angestoßen von **n8n** per
SSH auf den Host. Die Skripte wissen nichts von n8n — sie sind gewöhnliche
Kommandos, die im Repo-Verzeichnis laufen und einen Exit-Code zurückgeben.
Genau der ist der Vertrag zwischen Pipeline und Scheduler.

| Schritt | Aufruf | Was | Wann |
|---|---|---|---|
| 1 | `bin/daily_run.sh screen` | Stufe 1 — Screener, schreibt `candidates.json` | Mo–Fr 06:40 |
| 2 | `bin/analyze.sh` | Stufe 2 bis 4 — Analyse, Validierung, Report-Push | nur wenn Schritt 1 mit `0` endet |
| 3 | `bin/daily_run.sh sync` | Kurse, Devisen und Ausgänge der offenen Picks | **immer**, wie auch immer 1 und 2 ausgehen |

Verkettet wird über Exit-Codes, nicht über gestaffelte Uhrzeiten: der Screener
braucht bei warmem Cache Minuten und bei leerem eine knappe Stunde, jede feste
Uhrzeit wäre eine Wette auf seine Laufzeit. Scheitert Stufe 1, gibt es keine
frische Kandidatenliste — dann darf Schritt 2 nicht anlaufen.

Schritt 3 ist der leicht zu übersehende: er hängt an keinem der beiden anderen,
sondern muss in n8n eigens verdrahtet werden, auf dem Erfolgs- **und** auf dem
Fehlerpfad. Er pflegt die offenen Picks früherer Tage und hat mit der heutigen
Analyse nichts zu tun außer der Reihenfolge. Fällt er still aus, stehen die
offenen Positionen ohne frische Kurse und ohne Ausgangsprüfung da, ohne dass
ein fehlender Ledger-Eintrag darauf hinweisen würde.

> **Stand 13.08.2026:** In n8n sind bisher nur Schritt 1 und 2 verdrahtet, ohne
> Verzweigung auf den Exit-Code. Schritt 3 fehlt — vom 11. bis 13.08. lief kein
> Sync. Diese Notiz kann weg, sobald die Kette vollständig ist.

Ein nicht ausgewerteter Exit-Code ist der eigentliche Fallstrick: ein
Scheduler, der nur startet und nie hinsieht, macht aus jedem Fehler einen
stillen Ausfall.

Damit der Alarm ohne zweiten SSH-Aufruf brauchbar ist, teilen beide Skripte
ihre Ausgabe auf:

| Kanal | Inhalt |
|---|---|
| Logfile | alles, immer — der vollständige Lauf |
| stderr | nur der Abbruchgrund, und bei einem harten Fehler zusätzlich die Ausgabe genau dieses Laufs samt Traceback |
| stdout | im Fehlerfall leer |

Ein übersprungener Lauf — belegtes Lock, Report zum Datum liegt schon vor —
endet weiterhin mit `0`, schreibt seinen Grund aber ebenfalls nach stderr.
Sonst wäre er von einem erfolgreichen Lauf nicht zu unterscheiden: `exit 0`
heißt „nichts zu beanstanden", nicht „hat gearbeitet". Für die Verzweigung ist
das richtig so, für den Wächter unten ist es der Grund, warum es ihn braucht.

`bin/analyze.sh` gibt `PROMPT.md` und `candidates.json` an eine unbeaufsichtigte
Claude-Session mit Websuche, lässt den Report nach `out/report-<datum>.json`
schreiben und reicht ihn an `bin/report.sh` weiter — Stufe 3 bricht ab, bevor
ein fehlerhafter Report das Portal sieht.

Dass der Sync morgens statt abends läuft, kostet einen Tag Sichtbarkeit — er
sieht die Schlusskurse des Vortags, ein Ausgang steht also am Folgemorgen im
Portal. Ein zweiter Lauf am Abend nach US-Schluss wäre möglich, ist aber
bewusst nicht eingerichtet.

Drei Abbruchbedingungen vor dem Modellaufruf, alle aus demselben Grund: ein
Lauf auf der falschen Kandidatenliste kostet mehr als ein ausgefallener Tag.
Fehlt `candidates.json`, trägt sie ein anderes Datum als heute, oder gibt es
den Report zum Datum schon, endet `analyze.sh` ohne Claude zu starten.

```bash
tail -f logs/$(date +%F).log            # der Lauf des Tages
grep -E 'Ende: |ABBRUCH' logs/*.log     # Exit-Codes und Abbrüche aller Tage
bin/daily_run.sh screen                 # Stufe 1 von Hand
bin/analyze.sh --dry-run                # Stufe 2 scharf, Stufe 3 und 4 simuliert
uv run python publish.py --sync --dry-run   # Schritt 3 ohne Portal-Push
```

Die zweite Zeile ist die schnellste Kontrolle: jeder Schritt schreibt beim
Verlassen `Ende: … (exit=N)`, ein fehlendes Ende heißt abgestürzt oder noch
laufend.

Als Wächter gegen den stillen Ausfall gehört ein vierter n8n-Knoten hinter die
Kette, werktags gegen 08:30:

```bash
test -f /home/heimdal/stock-picker/out/report-$(date +%F).json
```

Exit 1 heißt: kein Report für heute, gleich aus welchem Grund — Absturz,
belegtes Lock, oder ein Knoten, der nie gefeuert hat. Der Report ist der
bessere Anker als der Ledger, weil er auch an einem Tag ohne Picks entsteht.
An US-Feiertagen schlägt der Wächter fälschlich an; das ist der Preis dafür,
dass er sonst nie stillhält.

Alles landet zusätzlich in `logs/YYYY-MM-DD.log`, 30 Tage lang.

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
| `stockpicker/fx.py` | EUR/USD-Tageskurse — die Anzeigeschicht für den Euro-Broker |
| `stockpicker/dotenv.py` | Liest `.env` — API-Keys und Portal-Zugang, ohne Zusatzabhängigkeit |

Die Arbeitsteilung folgt der Fehleranfälligkeit: Zahlen berechnet Code, Kontext
bewertet das Modell, und die halluzinationsanfälligste Stelle — konkrete
Kursniveaus — wird maschinell gegengeprüft. `validate.py` bricht ab, wenn ein
Stop über dem Entry liegt, das Chance-Risiko-Verhältnis unter 2:1 fällt, ein
Entry mehr als 15 % vom letzten Schlusskurs abweicht oder ein Titel ein zweites
Mal gepickt wird, während seine Position noch läuft. Dieselben Regeln laufen
im Portal ein zweites Mal, bevor ein Report in die Datenbank geht — die API ist
nicht darauf angewiesen, dass der Absender schon geprüft hat.

### Ein Titel, eine Position

Solange das Beobachtungsfenster eines Picks läuft, ist derselbe Titel keine
neue Gelegenheit, sondern eine zweite Position auf dieselbe These. `screen.py`
nimmt laufende Positionen deshalb aus der Kandidatenliste, bevor das Modell sie
sieht; `validate.py` prüft dieselbe Regel noch einmal, für den Fall, dass die
Kandidatenliste nicht vom heutigen Lauf stammt.

Prävention statt Abbruch: ein Titel, den das Modell nie sieht, kostet keinen
Lauf. Der Backstop in Stufe 3 nennt den Grund beim Namen, statt ihn als „nicht
in der Kandidatenliste" zu tarnen.

Details in `docs/plans/2026-08-09-swing-trade-prompt-design.md`.

## Währung: gerechnet in USD, geordert in Euro

Die Titel notieren in USD, das Depot steht in Euro. Beides gleichzeitig
richtig zu haben, geht nur mit einer klaren Trennung:

**Gerechnet wird durchgängig in USD.** Kurse, Indikatoren, Entry, Stop, die
Prüfungen in `validate.py` und die R-Multiples im Ledger — alles in der
Währung, in der der Titel gehandelt wird. Das ist keine Bequemlichkeit: ein
in Euro gerechneter Stop löst aus, weil der Euro gestiegen ist, und diese
Bewegung hat mit dem Titel nichts zu tun. Bei einem Stop 3 % unter dem Entry
kostet ein Tag mit 1 % Euro-Stärke ein Drittel des Puffers.

**Angezeigt wird zusätzlich in Euro.** Das Portal führt eine Tabelle mit dem
EUR/USD-Tageskurs (`EURUSD=X`, aus demselben Cache wie die Kursdaten) und
schreibt unter jedes USD-Niveau den Euro-Betrag: die Zahl, die ins
Orderfenster gehört. Die Stückzahl rechnet gegen ein **Euro-Konto** —
`account` in `portal/htdocs/lib/config.php` steht in EUR, das Risiko je Stück
wird vorher umgerechnet.

Gewählt wird immer der Kurs des Reporttages, nicht der von heute. Ein Report
ist das Dokument seines Tages; würde er mit dem Tageskurs gerechnet, änderten
sich die Euro-Niveaus eines längst geschlossenen Trades noch Monate später.
Für den frischen Report von heute ist beides dasselbe.

Kennt das Portal für einen Tag keinen Kurs, bleibt die Euro-Zeile weg und die
Stückzahl leer — eine stillschweigend mit 1,00 gerechnete Position wäre bei
EUR/USD 1,16 um 16 % zu groß. Der nächste `publish.py --sync` liefert den
Kurs nach.

Was die Euro-Beträge **nicht** sind: eine Abrechnung. Ein Broker stellt zum
Kurs der Ausführung ab und nimmt eine Fremdwährungsmarge; bei Trade Republic
kommt hinzu, dass US-Titel dort über einen europäischen Handelsplatz laufen,
dessen Euro-Notierung nicht bei jedem Tick zum US-Kurs passt. Die Zahlen im
Portal sind Orientierung für die Ordergröße, nicht die erwartete Abrechnung.

Ob ein Titel bei deinem Broker überhaupt handelbar ist, prüft das Werkzeug
nicht — der Screener kennt nur den Russell 2000, nicht das Angebot einer
App. Das bleibt ein Blick von Hand vor der Order.

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

### Splits — die Bruchstelle des Delta-Ladens

Genau das Nachladen, das den Cache schnell macht, macht ihn bei einem Split
falsch. yfinance rechnet seine Historie rückwirkend um; der Cache hält die alte
Stückelung und bekommt die neue angehängt. Danach steht beides in derselben
Reihe, ohne dass man es der Tabelle ansieht:

```
BRCC   2026-08-21   0,88 USD      ← vor dem 1:10-Reverse-Split
BRCC   2026-08-25   8,55 USD      ← danach
```

Ein Sprung um den Split-Faktor ist von einem Kurssprung nicht zu unterscheiden.
Jeder gleitende Durchschnitt darüber ist Fiktion, und der Preisfilter dreht sich
um: vor dem Split lag BRCC unter `min_price`, danach darüber — mit einem
Zehnfach-„Ausbruch" in der Historie. Am 28.08.2026 traf das vier Titel im
Universum: BRCC, REAX, SFBS und WLFC.

`screen.py` und `publish.py --sync` fragen deshalb vor jedem Laden Alpacas
Corporate-Actions-Endpunkt nach Ex-Daten und **verwerfen** die Historie
betroffener Titel, statt sie zu ergänzen. Der Titel gilt danach als unbekannt
und wird voll neu geladen — durchgehend in einer Stückelung. Verarbeitete
Splits stehen in der Tabelle `splits`; ohne dieses Gedächtnis würde derselbe
Split jeden Tag aufs Neue zwei Jahre Historie ziehen.

```bash
cat >> .env <<'KEYS'
ALPACA_API_KEY=dein-key
ALPACA_SECRET_KEY=dein-secret
KEYS
```

Ein Paper-Account genügt — abgefragt wird nur die Market-Data-API. Alpaca ist
hier Datenquelle, kein Broker-Anschluss: es wird nichts geordert (siehe
„Bewusst nicht enthalten").

Ohne Keys läuft alles weiter, aber `screen.py` meldet in jedem Lauf, dass die
Prüfung ausfällt — und `candidates.json` trägt die Warnung mit. Ein stiller
Ausfall wäre hier besonders teuer, weil ein unbemerkter Split nicht wie ein
Fehler aussieht, sondern wie ein Treffer.

Zwei Grenzen bleiben:

- Der Endpunkt kennt Splits, keine **ADR-Ratio-Änderungen**. ACH hat sich am
  10.08.2026 sauber halbiert, ohne dass eine Corporate Action das meldet — im
  Cache sieht das aus wie ein Split und wird nicht bereinigt.
- Der Cache lässt sich neu laden, der **Ledger nicht**. Entry, Stop und Target
  eines laufenden Picks stehen in der alten Stückelung und passen nach einem
  Split zu keinem Kurs mehr. Beide Skripte warnen, wenn ein bereinigter Titel
  eine offene Position hat; korrigieren muss man den Eintrag von Hand.

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
$EDITOR portal/deploy.env      # SSH_HOST, REMOTE_PATH, WEB_PATH, BASE_URL, PORTAL_USER
./portal/deploy.sh
```

`deploy.sh` baut die Assets, lädt `htdocs/` per `tar` über SSH hoch, entfernt
danach auf dem Server, was lokal fehlt, richtet die Serverseite ein und prüft
zum Schluss, ob der Schutz greift. Es ist idempotent: ein zweiter Lauf ohne
Änderungen lässt Datenbank und Passwort in Ruhe. Beides ist vom Aufräumen
ausgenommen, und das Skript bricht ab, falls die Ausnahmen doch einmal nicht
greifen sollten.

`rsync` setzt das Skript nicht voraus: Hoster mit eingeschränkter Shell haben
es oft nicht. Aus demselben Grund gibt es `WEB_PATH`. Ist die SSH-Sitzung
chrooted — bei Plesk der Normalfall —, sieht die Shell den Document-Root unter
einem anderen Pfad als der Webserver. Für `AuthUserFile` in der `.htaccess`
zählt die Sicht des Webservers; steht dort der Pfad der Shell, antwortet die
Seite auf jede Anfrage *mit* Zugangsdaten mit 500. Sind beide Pfade gleich,
bleibt `WEB_PATH` leer.

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

Lokal braucht `deploy.sh` `ssh`, `tar`, `curl`, `npm`, `openssl` und
entweder `htpasswd` oder `php`; auf dem Server `tar`, `find`, `sed`, `grep`
und `php`. Beide Listen prüft der erste Schritt. Der SSH-Zugang muss ohne Passwortabfrage
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
der Maschine kein `php` liegt. Ein Test-Framework gibt es bewusst nicht: bei fünf
Tabellen und fünf Endpunkten wäre PHPUnit mehr Konfiguration als Testcode. Der
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

Damit die Auswertung überhaupt etwas zu vergleichen hat, braucht jedes Template
Einträge im Ledger. `select_candidates` lässt die Templates deshalb reihum
antreten, statt allein nach RS-Rang zu deckeln — sonst besetzt das Template,
das im aktuellen Markt am häufigsten trifft, die ganze Liste, und was den
Deckel nie erreicht, kann sich auch nie als tragfähig erweisen.

Aus demselben Grund gibt es zum ATR-Boden von 3 % einen zweiten, tieferen von
1,5 % — ausschließlich für Titel, die eine VCP-Basis bilden. Der Boden soll
tote Titel aussortieren, traf aber auch das VCP, das sich gerade über die
schrumpfende Spanne definiert: an einem gemessenen Tag scheiterten drei der
acht VCP-Treffer im Universum genau daran. Geprüft wird mit
`templates.is_vcp_breakout` selbst, nur ohne das RS-Perzentil — das entsteht
erst aus der Menge derer, die die Hard-Filter überlebt haben.

### Was als Trade zählt

Ein Pick, der weder Stop noch Target 1 berührt, wird nach `hold_days_max` zum
Schlusskurs abgerechnet (`TIME_STOP`) und zählt wie jeder andere Trade. Das ist
keine Kosmetik: der Stop liegt im Median rund 1,5 ATR vom Entry entfernt,
Target 1 rund 3 ATR. Im Fenster von zehn Tagen löst der Stop deshalb deutlich
häufiger auf als das Ziel. Zählte nur, was einen der beiden Punkte berührt hat,
blieben die zähen Gewinner als „offen" liegen und die Bilanz zeigte
systematisch zu viel Verlust — bei den ersten 51 Picks war der Unterschied
-0,57R gegenüber -0,13R.

Gewinner ist entsprechend, wer im Plus abgerechnet wurde, nicht nur, wer
Target 1 erreicht hat. `outcomes.TRADE_STATUS` und `CLOSED_STATUS` in
`portal/htdocs/lib/repo.php` müssen dabei dasselbe sagen, sonst widerspricht
das Portal dem Terminal.

## Bewusst nicht enthalten

Kein Backtest-Framework, kein Broker-Anschluss, keine Intraday-Daten. Das
Portal zeigt Ergebnisse an und nimmt sie entgegen — es löst nichts aus. Der Ledger liefert die Evidenz; alles Weitere
wartet, bis diese Evidenz zeigt, dass sich der Aufwand lohnt.

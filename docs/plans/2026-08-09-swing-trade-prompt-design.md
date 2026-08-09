# Design: Täglicher Small-Cap-Swing-Trading-Prompt

**Datum:** 2026-08-09
**Status:** Genehmigt
**Ziel:** Ein strukturiertes System, das arbeitstäglich bis zu fünf US-Small-Caps mit kurzfristiger Swing-Chance identifiziert, jede Empfehlung nach anerkannten Methoden begründet und die Ergebnisse für eine spätere Auswertung protokolliert.

## Einordnung

Das System screent und begründet Kandidaten, es garantiert keine Trefferquote. Small-Cap-Swing-Trading hat hohe Varianz, und öffentlich verfügbare Daten sind verzögert oder bereits eingepreist. Der Output ist deshalb ein Analyse-Dossier mit expliziten Levels und Invalidierungskriterien — keine Kaufempfehlung. Long-only, Haltedauer 2–10 Handelstage.

## Architektur

Gewählt wurde ein Zwei-Stufen-Gate mit getrennten Zuständigkeiten: Zahlen berechnet Code, Kontext bewertet das Modell, und die halluzinationsanfälligste Stelle — konkrete Kursniveaus — wird maschinell gegengeprüft.

```
russell2000.csv ──┐
                  ▼
        [Stufe 1: screen.py]  yfinance → Kennzahlen → Hard-Filter
                  │  candidates.json (~20 Titel, reine Fakten)
                  ▼
        [Stufe 2: PROMPT.md]  Claude + Websuche → Katalysator, Scoring, Ranking, Dossiers
                  │  report.md + picks.json
                  ▼
        [Stufe 3: validate.py]  Levels gegen Kursdaten prüfen → ledger.jsonl
                  ▼
        [review.py]  nach N Tagen: Stop/Target getroffen? Trefferquote je Setup-Typ
```

Vier kleine Python-Dateien, kein Framework. Der eigentliche Deliverable ist `PROMPT.md`; der Code existiert, damit der Prompt saubere Zahlen bekommt und keine erfundenen Levels ausgibt.

### Verworfene Alternativen

**Dünner Screener, dickes Modell** — Python liefert nur OHLCV, das Modell rechnet Indikatoren selbst. Verworfen: stille Rechenfehler bei RSI/ATR/Relative Stärke, und das Ranking ist zwischen zwei Tagen nicht reproduzierbar. Damit wäre der Ledger wertlos.

**Dicker Screener, dünnes Modell** — Python berechnet den Composite-Score und wählt die fünf Titel, das Modell schreibt nur Text. Verworfen: Der Katalysator, bei Small Caps der eigentliche Kursbeweger, flösse nicht in die Auswahl ein, sondern würde nachträglich draufgeschrieben. Die Websuche wäre Deko statt Entscheidungsgrundlage.

## Stufe 1 — Screener

Universum: Russell-2000-Konstituenten aus der öffentlichen iShares-IWM-Holdings-Datei, täglich gecacht. Fallback bei Abrufproblemen: zuletzt gecachte Liste, mit Warnung im Report.

### Ausschlussfilter

| Filter | Schwelle | Begründung |
|---|---|---|
| Kurs | 3–100 USD | Unter 3 USD dominieren Spread und Manipulation |
| Ø-Volumen (20T) | ≥ 500.000 Stück | Ausstieg muss ohne Slippage möglich sein |
| Ø-Dollarvolumen | ≥ 5 Mio USD/Tag | Verhindert, dass billige Titel über Stückzahl durchrutschen |
| Marktkapitalisierung | 300 Mio – 3 Mrd USD | Small-Cap-Definition mit Toleranzrand |
| ATR(14)/Kurs | 3 % – 12 % | Unter 3 % lohnt der Swing nicht, über 12 % ist es Lotterie |
| Kurshistorie | ≥ 250 Handelstage | RS-Rank und 52-Wochen-Levels brauchen Basis |

### Berechnete Fakten

Der Screener bewertet nicht, er misst. Pro Titel:

- **Trend** — Kurs vs. SMA 10/20/50/200, SMA-Stapelung, Abstand zu 52-Wochen-Hoch und -Tief
- **Relative Stärke** — Performance 1M/3M/6M gegen IWM, daraus RS-Perzentil im Universum (O'Neil-Logik)
- **Momentum** — RSI(14), RSI(2) für Connors, MACD-Histogramm, ADX(14)
- **Kontraktion** — Bollinger-Bandbreite, %B, 5-Tage-Range gegen 20-Tage-Range als VCP-Proxy
- **Volumen** — RVOL heute, Up/Down-Volume-Ratio über 50 Tage (Akkumulation), OBV-Steigung
- **Struktur** — Abstand zum 20-Tage-Pivot, Basis-Länge und Basis-Tiefe nach Minervini, heutiges Gap in %
- **Kontext** — nächster Earnings-Termin, Short Float sofern verfügbar, Sektor

### Deckelung auf ~20 Kandidaten

Ein Titel kommt durch, wenn er mindestens ein Setup-Template mechanisch erfüllt — Breakout-nah, Pullback im Aufwärtstrend, Mean-Reversion-Kandidat oder Katalysator-Gap. Sortierung nach RS-Perzentil. Das ist eine Kappung, keine Vorab-Bewertung.

### Earnings-Regel

Termin innerhalb der nächsten fünf Handelstage → Ausschluss aus den Kern-Picks. Earnings in den letzten drei Tagen → kein Ausschluss, sondern `PEAD_CANDIDATE`-Flag, weil der Post-Earnings-Drift ein gewolltes Setup ist.

## Stufe 2 — Prompt

### Scoring-Rubrik

Drei Achsen, je 0–10, gewichtet zu einem Composite von 0–100. Die Rubrik steht im Prompt, nicht im Code, damit sie nach den ersten Ledger-Auswertungen ohne Codeänderung justierbar ist.

**Technisches Setup — 40 %**
Genau ein Template pro Kandidat, bewertet wird dessen Qualität, nicht dessen Existenz:

- *Minervini VCP / O'Neil Base-Breakout* — Basis ≥ 3 Wochen und ≤ 35 % tief, mindestens zwei sich verengende Kontraktionen, Volumen trocknet in der Basis aus, Kurs < 5 % unter Pivot, RS-Perzentil ≥ 80
- *Pullback im Trend* — SMA-Stapel intakt, Rücksetzer auf SMA 20 oder 50 bei fallendem Volumen, erste Aufwärtskerze
- *Connors Mean Reversion* — Kurs > SMA 200, RSI(2) < 10, %B < 0,1
- *PEAD* — Earnings-Gap > 5 % mit RVOL > 3, Kurs hält über dem Gap-Tief

Volle Punktzahl nur bei vollständiger Erfüllung aller Template-Kriterien. Teilerfüllung deckelt bei 6.

**Katalysator & Narrativ — 35 %**
Bewertet werden Härte und Frische des Auslösers, nicht die Trefferzahl: harte Unternehmensmeldung (Guidance, Vertrag, Zulassung, Übernahme) > Analysten-Upgrade > Sektor-Rotation > Retail-Buzz. Älter als fünf Handelstage halbiert den Score. Kein Katalysator gefunden → Score ≤ 3, explizit so im Dossier vermerkt. Der Prompt darf keinen erfinden.

**Risiko, Liquidität & Chance-Risiko-Verhältnis — 25 %**
R:R zum ersten Target, Stopweite in ATR (unter 1,5 ATR wird der Stop von Rauschen abgeholt), Dollarvolumen, Short Float, Lückenhaftigkeit der Kurshistorie.

### Harte Regeln

- Nur Titel mit Composite ≥ 60 werden empfohlen. Reißen an einem Tag nur drei die Schwelle, gibt der Report drei aus und begründet die Lücke. Kein Auffüllen auf fünf.
- Maximal zwei Titel aus demselben Sektor.
- Jede Zahl trägt entweder das Etikett „Screener" oder eine Quellenangabe.
- Keine Kursprognose ohne zugehöriges Invalidierungskriterium.

### Websuche-Protokoll

Mindestens zwei unabhängige Quellen pro Kandidat, mit Datum, jeweils als Link im Dossier.

Zugelassen: Yahoo Finance, Reuters, Bloomberg, CNBC, Barron's, MarketWatch, Seeking Alpha (nur Fakten, nicht die Meinung), SEC-Filings über EDGAR, Unternehmens-IR.
Ausgeschlossen: Promo-Newsletter, Stocktwits und Reddit als Faktenquelle, Aggregatoren ohne Primärquelle.

### Arbeitsablauf im Prompt

Die Reihenfolge ist erzwungen: erst alle ~20 Kandidaten technisch klassifizieren, dann recherchieren, dann scoren. Das verhindert, dass eine spannende Schlagzeile die technische Bewertung rückwirkend einfärbt.

### Prompt-Aufbau

`PROMPT.md` in sieben Blöcken:

1. Rolle und Mandat
2. Harte Verbote
3. Input-Schema `candidates.json`
4. Sechsschrittiger Arbeitsablauf
5. Scoring-Rubrik
6. Output-Schema
7. Selbstprüfungs-Checkliste, die das Modell am Ende explizit abarbeitet

## Output-Format

Kopf: Top-5-Tabelle mit Ticker, Setup-Typ, Composite, Entry, Stop, Target 1, R:R, Katalysator in fünf Wörtern.

Pro Titel 600–900 Wörter in fester Reihenfolge:

1. **These in zwei Sätzen** — warum genau jetzt
2. **Score-Aufschlüsselung** — 40/35/25 mit je einer Zeile Begründung
3. **Technische Lage** — Template, relevante Screener-Zahlen im Fließtext
4. **Katalysator** — was, wann, Quelle mit Link, wie stark bereits eingepreist
5. **Handelsplan** — Entry-Trigger als Bedingung (nicht nur Preis), Stop mit ATR-Begründung, Target 1 und 2 mit Herleitung, R:R, Positionsgröße bei 1 % Kontorisiko
6. **Bear-Case** — das stärkste Gegenargument, ernsthaft formuliert
7. **Invalidierung** — welches konkrete Ereignis die These killt, unabhängig vom Stop

Abschluss: Marktkontext (IWM-Trend, VIX, Risk-on/off), verworfene Kandidaten mit je einem Satz Begründung, Risikohinweis.

## Stufe 3 — Validierung und Ledger

`validate.py` prüft jedes vom Modell genannte Level gegen die realen Kursdaten. Liegt ein Entry unter dem gestrigen Tief oder ein Stop über dem Entry, bricht der Lauf mit Fehler ab, statt still Unsinn zu speichern. Danach eine Zeile je Pick nach `ledger.jsonl`: Datum, Ticker, Setup-Typ, Scores, Levels, Katalysator-Kurzform, Quellen.

`review.py --days 10` holt die Kursverläufe seit Eintrag und beantwortet: Wurde Target 1 vor dem Stop erreicht? Trefferquote und Erwartungswert in R, aufgeschlüsselt nach Setup-Typ und nach Katalysator-Score. Nach etwa 30 Läufen wird sichtbar, welche der vier Templates tatsächlich tragen — die Gewichtung lässt sich dann in `PROMPT.md` anpassen, ohne Code anzufassen.

## Konfigurationsannahmen

Als Konfigblock oben im Prompt, jeweils eine Zeile Änderung:

- Kontorisiko: 1 % pro Trade
- Mindest-R:R: 2:1
- Haltedauer: 2–10 Handelstage
- Richtung: Long-only

## Fehlerbehandlung

- yfinance-Ausfall bei Einzeltiteln → Titel überspringen, im Report zählen
- IWM-Holdings nicht abrufbar → gecachte Liste mit Warnung
- Weniger als fünf Kandidaten über Schwelle 60 → weniger ausgeben, Lücke begründen
- Level-Validierung schlägt fehl → Abbruch mit Fehlermeldung, kein Ledger-Eintrag

## Offene Punkte für die Umsetzung

- Rate-Limiting bei yfinance über ~2000 Ticker: Batch-Download und lokaler Cache nötig
- Short-Float-Daten sind bei yfinance lückenhaft; fehlender Wert darf den Risiko-Score nicht verzerren

## Nachtrag 2026-08-09 — Basis-Metrik korrigiert

Die ursprüngliche Kennzahl „Länge der Konsolidierung" zählte rückwärts, solange
die kumulierte Kursspanne unter 10 % blieb. Das war mit dem ATR-Filter dieses
Screeners (3–12 %) unvereinbar: ein Titel mit 5 % ATR reißt eine 10-%-Schranke
nach zwei Tagen. Die für `VCP_BREAKOUT` geforderten 15 Basis-Tage waren damit
strukturell unerreichbar — das Template konnte im Volllauf über 1950 Titel
keinen einzigen Treffer erzeugen.

Ersetzt durch zwei Kennzahlen nach Minervinis Basis-Definition: `base_length`
(Bars seit dem höchsten Hoch der letzten 120 Tage) und `base_depth_pct`
(Rückgang von diesem Hoch zum tiefsten Tief danach). Beide sind robust gegen
einzelne weite Bars. `VCP_BREAKOUT` verlangt jetzt zusätzlich eine Basis-Tiefe
von höchstens 35 % — tiefere Basen sind keine Konsolidierung, sondern ein
Abwärtstrend.

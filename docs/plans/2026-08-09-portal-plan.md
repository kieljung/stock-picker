# Report-Portal Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ein passwortgeschütztes PHP-Portal auf Shared Hosting, das die täglichen Analyse-Reports mit Chart und Begründung je Papier darstellt, plus eine API, an die die Python-Pipeline Report, Kursdaten und Trade-Ausgänge pusht.

**Architecture:** Stufe 2 der Pipeline gibt künftig nur noch JSON aus. `validate.py` prüft es, `publish.py` schickt es an vier JSON-Endpunkte eines framework-losen PHP-Front-Controllers, der in SQLite schreibt. Das Portal rendert server-seitig mit Flowbite 4 und hydriert die Charts client-seitig mit lightweight-charts. Basic Auth läuft auf Apache-Ebene.

**Tech Stack:** Python 3.11+ (pytest, requests, pandas), PHP 8.1+ mit `pdo_sqlite`, SQLite, Apache mit `mod_rewrite`, Tailwind CSS 4 + Flowbite 4 (lokal gebaut), lightweight-charts 5 (vendored), Parsedown (vendored).

**Design:** `docs/plans/2026-08-09-portal-design.md`

---

## Orientierung für den ausführenden Entwickler

Das Projekt ist ein dreistufiger Aktien-Screener. `screen.py` berechnet Kennzahlen und schreibt `candidates.json`. Ein Sprachmodell erzeugt daraus einen Analyse-Report. `validate.py` prüft die Kursniveaus im Report gegen die Screener-Zahlen und schreibt sie nach `data/ledger.jsonl`. `review.py` wertet später aus, ob Stop oder Target zuerst getroffen wurde.

Die Regeln, die dabei gelten, stehen an genau einer Stelle: `stockpicker/config.py`. Hartkodiere keine Schwellen, sondern lies sie von dort.

Alles in diesem Projekt läuft über `uv`:

```bash
uv run pytest                    # gesamte Suite
uv run pytest tests/test_x.py -v # eine Datei
uv run python screen.py --help
```

**Kein Test in diesem Projekt darf Netzwerk verwenden.** `tests/conftest.py` hat eine autouse-Fixture, die lokale API-Keys aus der Umgebung entfernt, damit die Suite nicht davon abhängt, was gerade auf der Maschine liegt. Halte das ein: HTTP wird gemockt, nie wirklich gesendet.

Commit-Nachrichten sind englisch im Conventional-Commits-Stil (`feat:`, `fix:`, `docs:`), Code-Kommentare und Nutzer-Ausgaben sind deutsch ohne Umlaute in Docstrings (bestehende Konvention, siehe `stockpicker/store.py`). Prosa in Markdown-Dateien hat Umlaute.

---

## Teil A — Pipeline

### Task 1: Report-JSON-Dokument definieren und validieren

Das Modell liefert künftig ein Dokument statt eines Markdown-Reports. Diese Task baut das Schema und seine Prüfung. Die Prüfung der Handelsniveaus existiert bereits in `stockpicker/ledger.py` und wird wiederverwendet, nicht neu geschrieben.

**Files:**
- Create: `stockpicker/report.py`
- Create: `tests/test_report.py`

**Step 1: Write the failing test**

`tests/test_report.py`:

```python
import json

import pytest

from stockpicker import report


def good_pick(**overrides):
    pick = {
        "ticker": "XYZ", "name": "Example Corp", "sector": "Health Care",
        "setup": "VCP_BREAKOUT", "rank": 1,
        "score_technical": 8, "score_catalyst": 7, "score_risk": 7, "composite": 76,
        "score_rationale": {"technical": "a", "catalyst": "b", "risk": "c"},
        "entry": 42.80, "stop": 40.10, "target1": 48.20, "target2": 52.00,
        "risk_reward": 2.0, "position_size": 37,
        "catalyst": "FDA-Zulassung 2026-08-06",
        "thesis": "t", "technicals": "tl", "catalyst_detail": "kd",
        "plan": {"entry": "e", "stop": "s", "target1": "t1", "target2": "t2"},
        "bear_case": "bc", "invalidation": "inv",
        "metrics": {"close": 42.15, "atr14": 1.85},
        "sources": [{"url": "https://example.com/a", "title": "Example", "date": "2026-08-06"}],
    }
    pick.update(overrides)
    return pick


def good_document(**overrides):
    doc = {
        "date": "2026-08-09",
        "generated_at": "2026-08-09T12:35:34",
        "market_context": "mk",
        "note": "n",
        "picks": [good_pick()],
        "rejected": [{"ticker": "ABC", "reason": "Earnings in drei Tagen"}],
        "checklist": ["Jede Zahl traegt Screener oder Quelle"],
        "disclaimer": "Keine Anlageberatung.",
    }
    doc.update(overrides)
    return doc


CANDIDATES = {"candidates": [{"ticker": "XYZ", "close": 42.15, "atr14": 1.85,
                              "sector": "Health Care"}]}


def test_valid_document_passes():
    assert report.validate_document(good_document(), CANDIDATES["candidates"]) == []


def test_missing_top_level_field_is_reported():
    doc = good_document()
    del doc["market_context"]
    errors = report.validate_document(doc, CANDIDATES["candidates"])
    assert any("market_context" in e for e in errors)


def test_missing_prose_field_is_reported():
    pick = good_pick()
    del pick["bear_case"]
    errors = report.validate_document(good_document(picks=[pick]), CANDIDATES["candidates"])
    assert any("bear_case" in e for e in errors)


def test_empty_prose_field_is_reported():
    errors = report.validate_document(
        good_document(picks=[good_pick(invalidation="   ")]), CANDIDATES["candidates"])
    assert any("invalidation" in e for e in errors)


def test_level_errors_from_ledger_are_included():
    # Stop ueber Entry -- die Regel lebt in ledger.validate_pick, nicht hier.
    errors = report.validate_document(
        good_document(picks=[good_pick(stop=43.50)]), CANDIDATES["candidates"])
    assert any("Stop" in e for e in errors)


def test_source_without_url_is_reported():
    errors = report.validate_document(
        good_document(picks=[good_pick(sources=[{"title": "ohne URL"}])]),
        CANDIDATES["candidates"])
    assert any("sources" in e for e in errors)


def test_source_with_non_http_url_is_reported():
    errors = report.validate_document(
        good_document(picks=[good_pick(sources=[{"url": "javascript:alert(1)"}])]),
        CANDIDATES["candidates"])
    assert any("sources" in e for e in errors)


def test_plain_string_sources_are_accepted_for_compatibility():
    errors = report.validate_document(
        good_document(picks=[good_pick(sources=["https://example.com/a"])]),
        CANDIDATES["candidates"])
    assert errors == []


def test_load_document_reads_json_file(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(good_document()), encoding="utf-8")
    assert report.load_document(path)["date"] == "2026-08-09"


def test_load_document_rejects_markdown(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# Report\n\nkein JSON", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        report.load_document(path)


def test_ledger_entry_strips_prose():
    entry = report.ledger_entry(good_pick())
    assert entry["entry"] == 42.80
    assert "thesis" not in entry
    assert "metrics" not in entry
    assert entry["sources"] == ["https://example.com/a"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockpicker.report'`

**Step 3: Write minimal implementation**

`stockpicker/report.py`:

```python
"""Das Report-Dokument von Stufe 2 -- Schema, Pruefung, Ledger-Projektion.

Stufe 2 liefert seit dem Portal ein reines JSON-Dokument statt eines
Markdown-Reports mit angehaengtem JSON-Block. Die Prosa steckt als
Markdown-Strings in den Feldern und wird erst im Portal gerendert.

Die Pruefung der Handelsniveaus lebt weiterhin in ledger.py. Dieses Modul
prueft nur, was durch das Dokumentformat dazukommt: dass die Textfelder da
und nicht leer sind und dass Quellen brauchbare URLs tragen.
"""

from __future__ import annotations

import json
from pathlib import Path

from stockpicker import ledger
from stockpicker.config import TRADE, TradeConfig

DOCUMENT_FIELDS = ["date", "generated_at", "market_context", "picks"]

PROSE_FIELDS = ["thesis", "technicals", "catalyst_detail", "bear_case", "invalidation"]

PICK_FIELDS = [
    "ticker", "name", "sector", "setup", "rank",
    "score_technical", "score_catalyst", "score_risk", "composite",
    "score_rationale", "entry", "stop", "target1", "target2",
    "catalyst", "plan", "metrics", "sources", *PROSE_FIELDS,
]

RATIONALE_KEYS = ["technical", "catalyst", "risk"]
PLAN_KEYS = ["entry", "stop", "target1", "target2"]

# Was in den Ledger wandert. Bewusst ohne Prosa: der Ledger ist ein
# Handelsprotokoll, kein Textarchiv. Mit Prosa waechst er je Lauf um
# mehrere Kilobyte Fliesstext, den nie jemand dort liest.
LEDGER_FIELDS = [
    "ticker", "setup", "sector",
    "score_technical", "score_catalyst", "score_risk", "composite",
    "entry", "stop", "target1", "target2", "catalyst",
]


def load_document(path: Path | str) -> dict:
    """Liest das Report-Dokument. Nur JSON, kein Markdown mehr."""
    text = Path(path).read_text(encoding="utf-8").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path} ist kein valides JSON: {exc}. Stufe 2 liefert seit dem "
            f"Portal ein reines JSON-Dokument, keinen Markdown-Report."
        ) from exc
    if not isinstance(data, dict) or "picks" not in data:
        raise ValueError(f"{path}: JSON ohne 'picks'-Key")
    return data


def validate_document(doc: dict, candidates: list[dict],
                      cfg: TradeConfig = TRADE) -> list[str]:
    """Alle harten Fehler. Nicht leer heisst: Lauf abbrechen."""
    errors: list[str] = []

    for field in DOCUMENT_FIELDS:
        if not doc.get(field):
            errors.append(f"Dokument: Feld '{field}' fehlt oder ist leer")
    if errors:
        return errors

    for index, pick in enumerate(doc["picks"], start=1):
        errors.extend(_validate_pick_shape(pick, index))

    # Levels, Sektorgrenzen, Composite-Schwelle -- die bestehenden Regeln.
    errors.extend(ledger.validate_report(doc["picks"], candidates, cfg))
    return errors


def _validate_pick_shape(pick: dict, index: int) -> list[str]:
    label = pick.get("ticker") or f"Pick {index}"
    errors: list[str] = []

    for field in PICK_FIELDS:
        if field not in pick:
            errors.append(f"{label}: Feld '{field}' fehlt")

    for field in PROSE_FIELDS:
        value = pick.get(field)
        if field in pick and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{label}: Feld '{field}' ist leer")

    rationale = pick.get("score_rationale")
    if isinstance(rationale, dict):
        for key in RATIONALE_KEYS:
            if not str(rationale.get(key, "")).strip():
                errors.append(f"{label}: score_rationale.{key} fehlt")
    elif "score_rationale" in pick:
        errors.append(f"{label}: score_rationale ist kein Objekt")

    plan = pick.get("plan")
    if isinstance(plan, dict):
        for key in PLAN_KEYS:
            if not str(plan.get(key, "")).strip():
                errors.append(f"{label}: plan.{key} fehlt")
    elif "plan" in pick:
        errors.append(f"{label}: plan ist kein Objekt")

    for url in source_urls(pick.get("sources") or []):
        if not url.startswith(("http://", "https://")):
            errors.append(f"{label}: sources enthaelt keine brauchbare URL ({url!r})")

    if pick.get("sources") and not source_urls(pick["sources"]):
        errors.append(f"{label}: sources ohne 'url'-Feld")

    return errors


def source_urls(sources: list) -> list[str]:
    """URLs aus Objekten oder aus alten String-Listen.

    Der Ledger enthaelt Zeilen aus der Zeit vor dem Portal, in denen
    sources eine Liste nackter URLs war. Beide Formen bleiben lesbar.
    """
    urls: list[str] = []
    for source in sources:
        if isinstance(source, str):
            urls.append(source)
        elif isinstance(source, dict) and source.get("url"):
            urls.append(str(source["url"]))
    return urls


def ledger_entry(pick: dict) -> dict:
    """Projiziert einen Pick auf die Felder, die in den Ledger gehoeren."""
    entry = {field: pick[field] for field in LEDGER_FIELDS if field in pick}
    entry["sources"] = source_urls(pick.get("sources") or [])
    return entry
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_report.py -v`
Expected: PASS, 11 Tests

**Step 5: Commit**

```bash
git add stockpicker/report.py tests/test_report.py
git commit -m "feat: report document schema and validation"
```

---

### Task 2: Ledger auf schlanke Einträge und Korrekturen umstellen

`append_entries` schreibt heute den kompletten Pick und verweigert ein zweites Mal dasselbe Datum. Beides passt nicht mehr: Die Picks tragen jetzt Prosa, und ein korrigierter Report muss erneut geschrieben werden dürfen.

**Files:**
- Modify: `stockpicker/ledger.py:append_entries`
- Modify: `tests/test_ledger.py`

**Step 1: Write the failing test**

An `tests/test_ledger.py` anhängen:

```python
def test_append_entries_writes_only_ledger_fields(tmp_path):
    path = tmp_path / "ledger.jsonl"
    pick = good_pick(thesis="lange Prosa", metrics={"close": 42.15})
    ledger.append_entries(path, "2026-08-09", [pick])
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert written["entry"] == 42.80
    assert "thesis" not in written
    assert "metrics" not in written


def test_append_entries_rejects_duplicate_date_by_default(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_entries(path, "2026-08-09", [good_pick()])
    with pytest.raises(ValueError, match="bereits"):
        ledger.append_entries(path, "2026-08-09", [good_pick()])


def test_append_entries_replaces_date_when_asked(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_entries(path, "2026-08-09", [good_pick(), good_pick(ticker="ABC")])
    ledger.append_entries(path, "2026-08-09", [good_pick(entry=44.0)], replace=True)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["entry"] == 44.0


def test_replace_keeps_entries_of_other_dates(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_entries(path, "2026-08-08", [good_pick(ticker="OLD")])
    ledger.append_entries(path, "2026-08-09", [good_pick()])
    ledger.append_entries(path, "2026-08-09", [good_pick(entry=44.0)], replace=True)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["date"] for line in lines] == ["2026-08-08", "2026-08-09"]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ledger.py -v -k "append_entries or replace"`
Expected: FAIL — `thesis` steht in der Zeile, `replace` ist kein Parameter

**Step 3: Write minimal implementation**

`stockpicker/ledger.py` — `append_entries` ersetzen:

```python
def append_entries(path: Path, date: str, picks: list[dict], *,
                   replace: bool = False) -> int:
    """Schreibt je Pick eine Zeile.

    Ein Datum darf nur einmal vorkommen. ``replace=True`` schreibt die
    Zeilen des Datums neu, statt abzubrechen -- fuer den Fall, dass ein
    Report korrigiert und erneut eingespielt wird.
    """
    from stockpicker.report import ledger_entry  # zyklische Importe vermeiden

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if json.loads(line).get("date") == date:
                if not replace:
                    raise ValueError(f"Fuer {date} sind bereits Eintraege im Ledger vorhanden")
                continue
            existing.append(line)

    lines = list(existing)
    for pick in picks:
        entry = ledger_entry(pick)
        entry["date"] = date
        entry["status"] = "OPEN"
        lines.append(json.dumps(entry, ensure_ascii=False))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(picks)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ledger.py -v`
Expected: PASS, alle bestehenden Tests weiterhin grün

Run: `uv run pytest`
Expected: PASS, gesamte Suite

**Step 5: Commit**

```bash
git add stockpicker/ledger.py tests/test_ledger.py
git commit -m "feat: slim ledger entries and allow replacing a date"
```

---

### Task 3: `validate.py` auf das Report-Dokument umstellen

**Files:**
- Modify: `validate.py`

**Step 1: Implementation**

`validate.py` — Docstring, Argumente und Ladepfad ersetzen. Die Struktur bleibt, nur `ledger.load_picks` weicht `report.load_document`, `ledger.validate_report` weicht `report.validate_document`, und `--replace` kommt dazu:

```python
#!/usr/bin/env python3
"""Stufe 3: Prueft das Report-Dokument des Modells und schreibt den Ledger.

Verwendung:
    uv run python validate.py out/report-2026-08-09.json
    uv run python validate.py out/report-2026-08-09.json --dry-run
    uv run python validate.py out/report-2026-08-09.json --replace

Die Eingabe ist das JSON-Dokument aus Stufe 2. Es enthaelt neben den
Handelsniveaus die Begruendungen als Markdown-Strings; in den Ledger
wandern nur die Handelsfelder.
"""
```

Im `main()`:

```python
    parser.add_argument("report", help="JSON-Dokument aus Stufe 2")
    parser.add_argument("--candidates", default="candidates.json")
    parser.add_argument("--ledger", default=str(ledger.DEFAULT_LEDGER))
    parser.add_argument("--dry-run", action="store_true", help="Nur pruefen, nichts schreiben")
    parser.add_argument("--replace", action="store_true",
                        help="Vorhandene Eintraege des Datums ersetzen (korrigierter Report)")
    args = parser.parse_args()

    try:
        doc = report.load_document(Path(args.report))
    except (OSError, ValueError) as exc:
        print(f"Eingabe {args.report} nicht lesbar: {exc}", file=sys.stderr)
        return 1

    candidates_data = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    date, picks = doc["date"], doc["picks"]
    candidates = candidates_data["candidates"]
    by_ticker = {c["ticker"]: c for c in candidates}

    errors = report.validate_document(doc, candidates)
```

und weiter unten:

```python
        count = ledger.append_entries(Path(args.ledger), date, picks, replace=args.replace)
```

Import ergänzen: `from stockpicker import ledger, report`.

**Step 2: Verify manually**

```bash
uv run python validate.py --help
```
Expected: Hilfetext nennt `report` und `--replace`

**Step 3: Commit**

```bash
git add validate.py
git commit -m "feat: validate.py reads the JSON report document"
```

---

### Task 4: `PROMPT.md` auf reines JSON umstellen

Das Modell schreibt keinen Markdown-Report mehr. Abschnitt 6 wird vollständig ersetzt, Abschnitt 7 angepasst.

**Files:**
- Modify: `PROMPT.md:200-292`

**Step 1: Implementation**

Abschnitt „## 6 · Output-Format" bis einschließlich des maschinenlesbaren Blocks durch Folgendes ersetzen:

````markdown
## 6 · Output-Format

Gib **ausschließlich ein JSON-Dokument** aus, keinen umgebenden Text und keinen
Codefence. Es wird direkt als `out/report-<datum>.json` gespeichert und von
`validate.py` gelesen. Die Begründungen stehen als Markdown-Strings in den
Feldern und werden im Portal gerendert — Absätze mit `\n\n`, Links als
`[Text](URL)`, Aufzählungen mit `- `.

```json
{
  "date": "2026-08-09",
  "generated_at": "2026-08-09T12:35:34",
  "market_context": "Drei bis fünf Sätze aus Schritt 1, mit Quellenlinks.",
  "note": "Optional: was an diesem Lauf auffällt. Leerer String, wenn nichts.",
  "picks": [
    {
      "ticker": "XYZ",
      "name": "Example Corporation",
      "sector": "Health Care",
      "setup": "VCP_BREAKOUT",
      "rank": 1,
      "score_technical": 8,
      "score_catalyst": 7,
      "score_risk": 7,
      "composite": 76,
      "score_rationale": {
        "technical": "Eine Zeile, warum die Technik diese Zahl bekommt.",
        "catalyst": "Eine Zeile zum Katalysator-Score.",
        "risk": "Eine Zeile zum Risiko-Score."
      },
      "entry": 42.80,
      "stop": 40.10,
      "target1": 48.20,
      "target2": 52.00,
      "risk_reward": 2.0,
      "position_size": 37,
      "catalyst": "FDA-Zulassung 2026-08-06",
      "thesis": "Zwei Sätze: was passiert hier, und warum ist jetzt der Moment.",
      "technicals": "Template, Kursstruktur, Screener-Zahlen im Fließtext. Nenne konkret, welche Kriterien erfüllt sind und welche nicht.",
      "catalyst_detail": "Was, wann, welche Quelle. Einschätzung, wie viel davon der Kurs schon verarbeitet hat.",
      "plan": {
        "entry": "über 42,80 bei RVOL > 1,5",
        "stop": "40,10 — 1,46 ATR unter Entry, unter dem Basistief",
        "target1": "48,20 — Messziel der Basis auf den Pivot addiert",
        "target2": "52,00 — Ausdehnung, falls der Ausbruch trägt"
      },
      "bear_case": "Das stärkste Gegenargument, ernsthaft formuliert. Nicht „der Markt könnte fallen\", sondern der konkrete Grund, warum dieses Setup scheitert.",
      "invalidation": "Ein konkretes Ereignis, unabhängig vom Stop, das die These beendet.",
      "metrics": { "…": "der vollständige Kandidaten-Datensatz aus candidates.json, unverändert kopiert" },
      "sources": [
        { "url": "https://…", "title": "StockTitan", "date": "2026-08-06" }
      ]
    }
  ],
  "rejected": [
    { "ticker": "ABC", "reason": "Earnings in drei Tagen — Blackout." }
  ],
  "checklist": [
    "Jede Zahl trägt Screener oder eine Quelle mit Link",
    "…"
  ],
  "disclaimer": "Keine Anlageberatung. Small Caps sind illiquide und volatil; Gaps können jeden Stop überspringen. Alle Angaben ohne Gewähr."
}
```

Feldregeln:

- `setup` ist einer der vier Template-Namen.
- `date` ist das Datum des Screener-Laufs aus `generated_at`, nicht das heutige.
- `rank` zählt ab 1 in der Reihenfolge des Composite-Scores.
- `position_size` ist `Kontogröße × 0,01 ÷ (Entry − Stop)`, abgerundet, bei 10.000 USD Konto.
- `metrics` ist der Kandidaten-Datensatz des Tickers aus `candidates.json`,
  **wörtlich kopiert**. Rechne dort nichts um und lass nichts weg — das Portal
  zeichnet daraus die Kennzahlenbalken.
- `rejected` enthält je nicht ausgewählten Kandidaten einen Satz mit dem
  Ausschlussgrund.
- `thesis` bis `invalidation` zusammen 600–900 Wörter je Titel.
````

Abschnitt 7 — letzten Haken ersetzen:

```markdown
- [ ] Ausgabe ist ein einziges valides JSON-Dokument ohne umgebenden Text
- [ ] `metrics` je Pick ist der unveränderte Screener-Datensatz des Tickers
```

Die Checkliste wandert zusätzlich als `checklist`-Array ins Dokument.

**Step 2: Verify**

```bash
grep -n "Markdown-Report\|report.md\|picks.json" PROMPT.md
```
Expected: keine Treffer mehr

**Step 3: Commit**

```bash
git add PROMPT.md
git commit -m "docs: stage 2 emits a single JSON document"
```

---

### Task 5: Payload-Bau für den Push

`publish.py` braucht drei Payloads. Diese Task baut sie als reine Funktionen, ohne HTTP — damit sie ohne Netzwerk testbar sind.

**Files:**
- Create: `stockpicker/publish.py`
- Create: `tests/test_publish.py`

**Step 1: Write the failing test**

`tests/test_publish.py`:

```python
import datetime as dt

import pandas as pd
import pytest

from stockpicker import publish
from tests.conftest import make_ohlcv


def test_bars_payload_is_array_of_arrays():
    history = {"XYZ": make_ohlcv([10.0, 11.0, 12.0])}
    payload = publish.bars_payload(history)
    assert list(payload) == ["XYZ"]
    assert len(payload["XYZ"]) == 3
    date, open_, high, low, close, volume = payload["XYZ"][0]
    assert isinstance(date, str) and len(date) == 10
    assert (open_, close, volume) == (10.0, 10.0, 1_000_000.0)
    assert high > close > low


def test_bars_payload_skips_empty_frames():
    assert publish.bars_payload({"XYZ": pd.DataFrame()}) == {}


def test_bars_payload_honours_since():
    history = {"XYZ": make_ohlcv([10.0] * 10)}
    full = publish.bars_payload(history)
    cutoff = dt.date.fromisoformat(full["XYZ"][5][0])
    trimmed = publish.bars_payload(history, since=cutoff)
    assert len(trimmed["XYZ"]) == 5
    assert all(dt.date.fromisoformat(row[0]) > cutoff for row in trimmed["XYZ"])


def test_report_payload_carries_document_and_bars():
    doc = {"date": "2026-08-09", "picks": [{"ticker": "XYZ"}]}
    payload = publish.report_payload(doc, {"XYZ": make_ohlcv([10.0, 11.0])})
    assert payload["date"] == "2026-08-09"
    assert len(payload["bars"]["XYZ"]) == 2


def test_outcomes_payload_maps_evaluate_results():
    results = [{"ticker": "XYZ", "date": "2026-08-09", "status": "TARGET1",
                "r_multiple": 2.06, "setup": "PEAD", "score_catalyst": 8}]
    payload = publish.outcomes_payload(results)
    assert payload == [{"date": "2026-08-09", "ticker": "XYZ",
                        "status": "TARGET1", "r_multiple": 2.06}]


def test_open_picks_selects_unclosed_entries():
    entries = [
        {"ticker": "A", "date": "2026-08-01", "status": "OPEN"},
        {"ticker": "B", "date": "2026-08-01", "status": "TARGET1"},
        {"ticker": "C", "date": "2026-08-02", "status": "OPEN"},
        {"ticker": "A", "date": "2026-08-02", "status": "NOT_TRIGGERED"},
    ]
    assert publish.open_picks(entries) == [
        {"ticker": "A", "date": "2026-08-01", "status": "OPEN"},
        {"ticker": "C", "date": "2026-08-02", "status": "OPEN"},
    ]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_publish.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockpicker.publish'`

**Step 3: Write minimal implementation**

`stockpicker/publish.py` — nur die Payload-Funktionen, HTTP folgt in Task 6:

```python
"""Uebergabe der Pipeline-Ergebnisse an das Portal.

Reine Payload-Funktionen plus ein duenner HTTP-Client. Die Payloads sind
absichtlich von der Uebertragung getrennt: so laesst sich pruefen, was
gesendet wuerde, ohne etwas zu senden.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

BAR_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

CLOSED_STATUS = {"TARGET1", "STOP", "NOT_TRIGGERED"}


def bars_payload(history: dict[str, pd.DataFrame], *,
                 since: dt.date | None = None) -> dict[str, list[list]]:
    """OHLCV je Ticker als Array-of-Arrays.

    Objekte je Bar waeren lesbarer, kosten bei 125 Tagen mal fuenf Titeln
    aber rund das Dreifache an Payload. Die Spaltenreihenfolge ist fix:
    Datum, Open, High, Low, Close, Volume.
    """
    payload: dict[str, list[list]] = {}
    for ticker, frame in history.items():
        if frame is None or frame.empty:
            continue
        rows: list[list] = []
        for index, bar in frame.iterrows():
            date = index.date() if hasattr(index, "date") else index
            if since is not None and date <= since:
                continue
            rows.append([
                date.isoformat(),
                _f(bar.get("Open")), _f(bar.get("High")), _f(bar.get("Low")),
                _f(bar.get("Close")), _f(bar.get("Volume")),
            ])
        if rows:
            payload[ticker] = rows
    return payload


def report_payload(doc: dict, history: dict[str, pd.DataFrame]) -> dict:
    """Das Report-Dokument plus die Kurshistorie seiner Picks."""
    return {**doc, "bars": bars_payload(history)}


def outcomes_payload(results: list[dict]) -> list[dict]:
    """Nur die Felder, die das Portal in picks zurueckschreibt."""
    return [
        {"date": r["date"], "ticker": r["ticker"],
         "status": r["status"], "r_multiple": r.get("r_multiple")}
        for r in results
    ]


def open_picks(entries: list[dict]) -> list[dict]:
    """Ledger-Eintraege, deren Ausgang noch offen ist."""
    return [e for e in entries if e.get("status") not in CLOSED_STATUS]


def _f(value):
    if value is None or pd.isna(value):
        return None
    return float(value)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_publish.py -v`
Expected: PASS, 6 Tests

**Step 5: Commit**

```bash
git add stockpicker/publish.py tests/test_publish.py
git commit -m "feat: payload builders for the portal push"
```

---

### Task 6: HTTP-Client mit Basic Auth und Wiederholung

**Files:**
- Modify: `stockpicker/publish.py`
- Modify: `tests/test_publish.py`

**Step 1: Write the failing test**

An `tests/test_publish.py` anhängen. Die Tests mocken `requests.post` — **kein echter Request**:

```python
class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


def test_credentials_are_read_from_env(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("PORTAL_URL=https://example.com\n"
                   "PORTAL_USER=picker\nPORTAL_PASSWORD=geheim\n", encoding="utf-8")
    monkeypatch.setattr(publish, "DOTENV", env)
    monkeypatch.delenv("PORTAL_URL", raising=False)
    creds = publish.credentials()
    assert creds == publish.Credentials("https://example.com", "picker", "geheim")


def test_missing_credentials_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "DOTENV", tmp_path / "absent.env")
    for name in ("PORTAL_URL", "PORTAL_USER", "PORTAL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="PORTAL_URL"):
        publish.credentials()


def test_post_returns_json_on_success(monkeypatch):
    calls = []

    def fake_post(url, json=None, auth=None, timeout=None):
        calls.append((url, auth, timeout))
        return FakeResponse(200, {"ok": True, "picks": 5})

    monkeypatch.setattr(publish.requests, "post", fake_post)
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    result = publish.post(creds, "/api/reports", {"date": "2026-08-09"})
    assert result == {"ok": True, "picks": 5}
    assert calls[0][0] == "https://example.com/api/reports"
    assert calls[0][1] == ("picker", "geheim")


def test_post_raises_with_details_on_422(monkeypatch):
    monkeypatch.setattr(publish.requests, "post",
                        lambda *a, **k: FakeResponse(422, {"error": "invalid",
                                                           "details": ["XYZ: R:R 1.20"]}))
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    with pytest.raises(publish.PushError) as excinfo:
        publish.post(creds, "/api/reports", {})
    assert "R:R 1.20" in str(excinfo.value)


def test_post_does_not_retry_on_4xx(monkeypatch):
    attempts = []
    monkeypatch.setattr(publish.requests, "post",
                        lambda *a, **k: (attempts.append(1), FakeResponse(422, {}))[1])
    monkeypatch.setattr(publish.time, "sleep", lambda _: None)
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    with pytest.raises(publish.PushError):
        publish.post(creds, "/api/reports", {})
    assert len(attempts) == 1


def test_post_retries_three_times_on_5xx(monkeypatch):
    attempts = []
    monkeypatch.setattr(publish.requests, "post",
                        lambda *a, **k: (attempts.append(1), FakeResponse(503, {}))[1])
    monkeypatch.setattr(publish.time, "sleep", lambda _: None)
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    with pytest.raises(publish.PushError):
        publish.post(creds, "/api/reports", {})
    assert len(attempts) == 3
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_publish.py -v -k "credential or post"`
Expected: FAIL — `AttributeError: module 'stockpicker.publish' has no attribute 'credentials'`

**Step 3: Write minimal implementation**

An `stockpicker/publish.py` anhängen. Das `.env`-Lesen folgt dem Muster aus `stockpicker/earnings.py` — schau dort nach, bevor du es neu erfindest, und ziehe es in eine gemeinsame Funktion, falls es identisch ist:

```python
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

DOTENV = Path(".env")

TIMEOUT = 30
RETRIES = 3
BACKOFF = 2.0


class PushError(RuntimeError):
    """Das Portal hat die Uebergabe abgelehnt oder war nicht erreichbar."""


@dataclass(frozen=True)
class Credentials:
    url: str
    user: str
    password: str


def credentials() -> Credentials:
    """Portal-Zugang aus Umgebung oder .env. Umgebung gewinnt."""
    values = _dotenv_values(DOTENV)
    missing = []
    resolved = {}
    for name in ("PORTAL_URL", "PORTAL_USER", "PORTAL_PASSWORD"):
        value = os.environ.get(name) or values.get(name)
        if not value:
            missing.append(name)
        resolved[name] = value
    if missing:
        raise RuntimeError(
            f"Portal-Zugang unvollstaendig: {', '.join(missing)} fehlt. "
            f"deploy.sh schreibt die Werte beim Deployment in .env."
        )
    return Credentials(resolved["PORTAL_URL"].rstrip("/"),
                       resolved["PORTAL_USER"], resolved["PORTAL_PASSWORD"])


def _dotenv_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def post(creds: Credentials, path: str, payload) -> dict:
    """Sendet und wirft PushError bei allem ausser 2xx.

    4xx sind Fehler im Payload -- eine Wiederholung aendert nichts.
    Wiederholt wird nur bei 5xx und Netzwerkfehlern.
    """
    url = f"{creds.url}{path}"
    last_error = ""

    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.post(url, json=payload,
                                     auth=(creds.user, creds.password), timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"Netzwerkfehler: {exc}"
        else:
            if 200 <= response.status_code < 300:
                return response.json()
            body = _body(response)
            last_error = f"HTTP {response.status_code}: {body}"
            if response.status_code < 500:
                raise PushError(f"{url} -- {last_error}")

        if attempt < RETRIES:
            time.sleep(BACKOFF ** attempt)

    raise PushError(f"{url} -- nach {RETRIES} Versuchen aufgegeben. {last_error}")


def _body(response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:500]
    details = data.get("details")
    if details:
        return f"{data.get('error', 'Fehler')} — " + "; ".join(str(d) for d in details)
    return str(data)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_publish.py -v`
Expected: PASS, 12 Tests

**Step 5: Commit**

```bash
git add stockpicker/publish.py tests/test_publish.py
git commit -m "feat: authenticated HTTP client with retry for the portal push"
```

---

### Task 7: `publish.py` als CLI

**Files:**
- Create: `publish.py` (Projektwurzel, analog zu `screen.py` und `validate.py`)

**Step 1: Implementation**

```python
#!/usr/bin/env python3
"""Uebergibt Report, Kursdaten und Trade-Ausgaenge an das Portal.

Verwendung:
    uv run python publish.py out/report-2026-08-09.json     # Report + Historie
    uv run python publish.py --sync                          # taeglich: Bars + Ausgaenge
    uv run python publish.py out/report-2026-08-09.json --dry-run

Zugangsdaten kommen aus .env (PORTAL_URL, PORTAL_USER, PORTAL_PASSWORD);
deploy.sh schreibt sie dort beim Deployment hinein.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from stockpicker import fetch, ledger, market, outcomes, publish, report

HISTORY_MONTHS = 6


def push_report(creds, path: Path, dry_run: bool) -> int:
    doc = report.load_document(path)
    tickers = [pick["ticker"] for pick in doc["picks"]]

    start = dt.date.fromisoformat(doc["date"]) - dt.timedelta(days=HISTORY_MONTHS * 31)
    history = fetch.fetch_history_range(tickers, start, dt.date.today())
    payload = publish.report_payload(doc, history)

    if dry_run:
        size = len(json.dumps(payload).encode("utf-8"))
        print(f"dry-run: {len(doc['picks'])} Picks, "
              f"{sum(len(rows) for rows in payload['bars'].values())} Bars, "
              f"{size / 1024:.0f} KB — nichts gesendet.")
        return 0

    result = publish.post(creds, "/api/reports", payload)
    print(f"OK: Report {result['date']} mit {result['picks']} Picks und "
          f"{result['bars_written']} Bars uebergeben"
          f"{' (ersetzt)' if result.get('replaced') else ''}.")
    return 0


def sync(creds, ledger_path: Path, days: int, dry_run: bool) -> int:
    entries = publish.open_picks(ledger.read_entries(ledger_path))
    if not entries:
        print("Keine offenen Picks — nichts zu synchronisieren.")
        return 0

    tickers = sorted({entry["ticker"] for entry in entries})
    start = min(dt.date.fromisoformat(e["date"]) for e in entries)
    history = fetch.fetch_history_range(tickers, start, dt.date.today())

    bars = publish.bars_payload(history)
    results = []
    for entry in entries:
        frame = history.get(entry["ticker"])
        if frame is None or frame.empty:
            print(f"  ! Keine Kursdaten fuer {entry['ticker']}", file=sys.stderr)
            continue
        after = frame[frame.index.date > dt.date.fromisoformat(entry["date"])]
        if not after.empty:
            results.append(outcomes.evaluate(entry, after))

    if dry_run:
        print(f"dry-run: {sum(len(r) for r in bars.values())} Bars, "
              f"{len(results)} Ausgaenge — nichts gesendet.")
        return 0

    bar_result = publish.post(creds, "/api/bars", bars)
    outcome_result = publish.post(creds, "/api/outcomes", publish.outcomes_payload(results))
    print(f"OK: {bar_result['written']} Bars, {outcome_result['updated']} Ausgaenge aktualisiert.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", help="JSON-Dokument aus Stufe 2")
    parser.add_argument("--sync", action="store_true",
                        help="Bars und Ausgaenge aller offenen Picks nachliefern")
    parser.add_argument("--ledger", default=str(ledger.DEFAULT_LEDGER))
    parser.add_argument("--days", type=int, default=30, help="Fenster fuer den Sync")
    parser.add_argument("--dry-run", action="store_true", help="Nur zeigen, nichts senden")
    args = parser.parse_args()

    if not args.report and not args.sync:
        parser.error("Entweder ein Report-Dokument oder --sync angeben.")

    try:
        creds = publish.credentials() if not args.dry_run else None
    except RuntimeError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1

    try:
        if args.report:
            code = push_report(creds, Path(args.report), args.dry_run)
            if code:
                return code
        if args.sync:
            return sync(creds, Path(args.ledger), args.days, args.dry_run)
    except publish.PushError as exc:
        print(f"PUSH FEHLGESCHLAGEN: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 2: Verify**

```bash
uv run python publish.py --help
uv run python publish.py            # ohne Argumente
```
Expected: zweiter Aufruf endet mit „Entweder ein Report-Dokument oder --sync angeben."

**Step 3: Commit**

```bash
git add publish.py
git commit -m "feat: publish.py CLI for report and daily sync"
```

---

## Teil B — Portal

Ab hier verlässt du Python. Das Portal liegt unter `portal/` im Repository; deployt wird ausschließlich `portal/htdocs/`.

```
portal/
  htdocs/          das, was auf den Server geht
  src/app.css      Tailwind-Quelle
  tests/           PHP-Tests
  package.json     nur fuer den lokalen Asset-Build
  deploy.env.example
  deploy.sh
```

### Task 8: Portal-Gerüst, Konfiguration, Datenbank

**Files:**
- Create: `portal/htdocs/lib/config.php`, `portal/htdocs/lib/db.php`, `portal/htdocs/lib/migrate.php`
- Create: `portal/tests/bootstrap.php`, `portal/tests/test_db.php`

**Step 1: Write the failing test**

Es gibt kein PHP-Test-Framework im Projekt und es soll keins geben. Ein Runner mit `assert_that` reicht.

`portal/tests/bootstrap.php`:

```php
<?php
// Minimaler Test-Runner. Kein Framework: das Portal hat vier Tabellen und
// vier Endpunkte, PHPUnit waere mehr Konfiguration als Testcode.

$GLOBALS['tests_run'] = 0;
$GLOBALS['tests_failed'] = 0;

function assert_that(bool $condition, string $message): void {
    $GLOBALS['tests_run']++;
    if ($condition) {
        echo "  ok   $message\n";
        return;
    }
    $GLOBALS['tests_failed']++;
    echo "  FAIL $message\n";
}

function assert_equals($expected, $actual, string $message): void {
    assert_that($expected == $actual, $message . ' (erwartet: '
        . json_encode($expected) . ', war: ' . json_encode($actual) . ')');
}

function test_summary(): int {
    $run = $GLOBALS['tests_run'];
    $failed = $GLOBALS['tests_failed'];
    echo "\n$run Zusicherungen, $failed fehlgeschlagen\n";
    return $failed === 0 ? 0 : 1;
}
```

`portal/tests/test_db.php`:

```php
<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/db.php';
require __DIR__ . '/../htdocs/lib/migrate.php';

$pdo = db_connect(':memory:');
migrate($pdo);

$tables = $pdo->query(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
)->fetchAll(PDO::FETCH_COLUMN);

foreach (['bars', 'picks', 'pushes', 'reports', 'schema_version'] as $table) {
    assert_that(in_array($table, $tables, true), "Tabelle $table existiert");
}

assert_equals(1, (int)$pdo->query('SELECT MAX(version) FROM schema_version')->fetchColumn(),
    'Schema steht auf Version 1');

migrate($pdo);
assert_equals(1, (int)$pdo->query('SELECT MAX(version) FROM schema_version')->fetchColumn(),
    'Zweiter Lauf aendert nichts');

exit(test_summary());
```

**Step 2: Run test to verify it fails**

Run: `php portal/tests/test_db.php`
Expected: FAIL — `Failed to open stream: No such file or directory .../lib/db.php`

**Step 3: Write minimal implementation**

`portal/htdocs/lib/config.php`:

```php
<?php
// Der Dateiname der Datenbank wird beim Deployment gewuerfelt. Eine
// bekannt benannte SQLite-Datei unter htdocs ist die eine Nachlaessigkeit,
// die ein einzelner Konfigurationsfehler beim Hoster in einen
// vollstaendigen Datenabfluss verwandelt.
return [
    'db'      => __DIR__ . '/../data/portal.sqlite',
    'log'     => __DIR__ . '/../data/portal.log',
    'title'   => 'stock-picker',
    'account' => 10000.0,   // Kontogroesse fuer die Positionsgroessen-Anzeige
];
```

`portal/htdocs/lib/db.php`:

```php
<?php

function db_connect(string $path): PDO {
    $pdo = new PDO('sqlite:' . $path, null, null, [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    // WAL, damit ein langer Push die Leseseite nicht blockiert; busy_timeout,
    // damit ein gleichzeitiger Request wartet statt "database is locked".
    if ($path !== ':memory:') {
        $pdo->exec('PRAGMA journal_mode = WAL');
    }
    $pdo->exec('PRAGMA busy_timeout = 5000');
    $pdo->exec('PRAGMA foreign_keys = ON');
    return $pdo;
}
```

`portal/htdocs/lib/migrate.php`:

```php
<?php

const MIGRATIONS = [
    1 => <<<'SQL'
        CREATE TABLE reports (
            date           TEXT PRIMARY KEY,
            generated_at   TEXT,
            market_context TEXT,
            note           TEXT,
            rejected       TEXT,     -- JSON
            checklist      TEXT,     -- JSON
            disclaimer     TEXT,
            pushed_at      TEXT NOT NULL
        );

        CREATE TABLE picks (
            report_date     TEXT NOT NULL REFERENCES reports(date) ON DELETE CASCADE,
            ticker          TEXT NOT NULL,
            rank            INTEGER,
            name            TEXT,
            sector          TEXT,
            setup           TEXT,
            score_technical INTEGER,
            score_catalyst  INTEGER,
            score_risk      INTEGER,
            composite       INTEGER,
            score_rationale TEXT,    -- JSON
            entry           REAL, stop REAL, target1 REAL, target2 REAL,
            risk_reward     REAL,
            position_size   INTEGER,
            catalyst        TEXT,
            thesis          TEXT,
            technicals      TEXT,
            catalyst_detail TEXT,
            plan            TEXT,    -- JSON
            bear_case       TEXT,
            invalidation    TEXT,
            metrics         TEXT,    -- JSON
            sources         TEXT,    -- JSON
            status          TEXT NOT NULL DEFAULT 'OPEN',
            r_multiple      REAL,
            triggered_on    TEXT,
            closed_on       TEXT,
            evaluated_at    TEXT,
            PRIMARY KEY (report_date, ticker)
        );
        CREATE INDEX picks_ticker ON picks(ticker);
        CREATE INDEX picks_status ON picks(status);

        CREATE TABLE bars (
            ticker TEXT NOT NULL,
            date   TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (ticker, date)
        );

        CREATE TABLE pushes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint  TEXT NOT NULL,
            at        TEXT NOT NULL,
            bytes     INTEGER,
            status    INTEGER,
            detail    TEXT
        );
    SQL,
];

function migrate(PDO $pdo): int {
    $pdo->exec('CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)');
    $current = (int)$pdo->query('SELECT COALESCE(MAX(version), 0) FROM schema_version')
        ->fetchColumn();

    $applied = 0;
    foreach (MIGRATIONS as $version => $sql) {
        if ($version <= $current) {
            continue;
        }
        $pdo->beginTransaction();
        $pdo->exec($sql);
        $stmt = $pdo->prepare('INSERT INTO schema_version (version, applied_at) VALUES (?, ?)');
        $stmt->execute([$version, gmdate('c')]);
        $pdo->commit();
        $applied++;
    }
    return $applied;
}
```

**Step 4: Run test to verify it passes**

Run: `php portal/tests/test_db.php`
Expected: PASS — „7 Zusicherungen, 0 fehlgeschlagen"

**Step 5: Commit**

```bash
git add portal/htdocs/lib portal/tests
git commit -m "feat: portal database schema and migrations"
```

---

### Task 9: Report-Validierung in PHP

Dieselben harten Regeln wie `stockpicker/ledger.py`. Die Datenbank ist die einzige Instanz, die kein Mensch vorher gesehen hat.

**Files:**
- Create: `portal/htdocs/lib/validate.php`
- Create: `portal/tests/test_validate.php`

**Step 1: Write the failing test**

`portal/tests/test_validate.php`:

```php
<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/validate.php';

function good_pick(array $overrides = []): array {
    return array_merge([
        'ticker' => 'XYZ', 'name' => 'Example', 'sector' => 'Health Care',
        'setup' => 'VCP_BREAKOUT', 'rank' => 1,
        'score_technical' => 8, 'score_catalyst' => 7, 'score_risk' => 7,
        'composite' => 76,
        'score_rationale' => ['technical' => 'a', 'catalyst' => 'b', 'risk' => 'c'],
        'entry' => 42.80, 'stop' => 40.10, 'target1' => 48.20, 'target2' => 52.00,
        'catalyst' => 'FDA', 'thesis' => 't', 'technicals' => 'tl',
        'catalyst_detail' => 'kd',
        'plan' => ['entry' => 'e', 'stop' => 's', 'target1' => 't1', 'target2' => 't2'],
        'bear_case' => 'bc', 'invalidation' => 'inv', 'metrics' => [],
        'sources' => [['url' => 'https://example.com/a']],
    ], $overrides);
}

function good_doc(array $overrides = []): array {
    return array_merge([
        'date' => '2026-08-09', 'generated_at' => '2026-08-09T12:35:34',
        'market_context' => 'mk', 'picks' => [good_pick()],
    ], $overrides);
}

assert_equals([], validate_report(good_doc()), 'Gueltiges Dokument passiert');

$errors = validate_report(good_doc(['date' => '9.8.2026']));
assert_that(count($errors) > 0, 'Datum ohne ISO-Format wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['stop' => 43.5])]]));
assert_that(str_contains(implode(' ', $errors), 'Stop'), 'Stop ueber Entry wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['target1' => 45.0])]]));
assert_that(str_contains(implode(' ', $errors), 'R:R'), 'R:R unter 2 wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['composite' => 55])]]));
assert_that(str_contains(implode(' ', $errors), 'Composite'), 'Composite unter 60 wird abgelehnt');

$errors = validate_report(good_doc(['picks' => array_fill(0, 6, good_pick())]));
assert_that(str_contains(implode(' ', $errors), '5'), 'Mehr als fuenf Picks werden abgelehnt');

$three = [good_pick(['ticker' => 'A']), good_pick(['ticker' => 'B']), good_pick(['ticker' => 'C'])];
$errors = validate_report(good_doc(['picks' => $three]));
assert_that(str_contains(implode(' ', $errors), 'Sektor'), 'Drei Titel je Sektor werden abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['bear_case' => '  '])]]));
assert_that(str_contains(implode(' ', $errors), 'bear_case'), 'Leerer Bear-Case wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [
    good_pick(['sources' => [['url' => 'javascript:alert(1)']]])]]));
assert_that(str_contains(implode(' ', $errors), 'sources'), 'Nicht-HTTP-Quelle wird abgelehnt');

exit(test_summary());
```

**Step 2: Run test to verify it fails**

Run: `php portal/tests/test_validate.php`
Expected: FAIL — Datei `lib/validate.php` fehlt

**Step 3: Write minimal implementation**

`portal/htdocs/lib/validate.php`:

```php
<?php
// Spiegel von stockpicker/ledger.py und stockpicker/report.py. Die Schwellen
// stehen dort in config.py -- aendert sich eine, aendert sie sich hier mit.

const MIN_RISK_REWARD    = 2.0;
const MIN_COMPOSITE      = 60;
const MAX_PICKS          = 5;
const MAX_PER_SECTOR     = 2;

const REQUIRED_PROSE = ['thesis', 'technicals', 'catalyst_detail',
                        'bear_case', 'invalidation'];

function validate_report(array $doc): array {
    $errors = [];

    foreach (['date', 'generated_at', 'market_context', 'picks'] as $field) {
        if (empty($doc[$field])) {
            $errors[] = "Dokument: Feld '$field' fehlt oder ist leer";
        }
    }
    if ($errors) {
        return $errors;
    }
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', (string)$doc['date'])) {
        $errors[] = "Dokument: date '{$doc['date']}' ist kein ISO-Datum";
    }
    if (!is_array($doc['picks'])) {
        return array_merge($errors, ['Dokument: picks ist keine Liste']);
    }
    if (count($doc['picks']) > MAX_PICKS) {
        $errors[] = 'Report enthaelt ' . count($doc['picks'])
            . ' Titel — mehr als erlaubte ' . MAX_PICKS;
    }

    $sectors = [];
    foreach ($doc['picks'] as $index => $pick) {
        $sector = $pick['sector'] ?? 'Unknown';
        $sectors[$sector] = ($sectors[$sector] ?? 0) + 1;
        $errors = array_merge($errors, validate_pick($pick, $index + 1));
    }
    foreach ($sectors as $sector => $count) {
        if ($count > MAX_PER_SECTOR) {
            $errors[] = "Sektor '$sector' mit $count Titeln — erlaubt sind " . MAX_PER_SECTOR;
        }
    }
    return $errors;
}

function validate_pick(array $pick, int $index): array {
    $label = $pick['ticker'] ?? "Pick $index";
    $errors = [];

    foreach (['entry', 'stop', 'target1', 'target2'] as $field) {
        if (!isset($pick[$field]) || !is_numeric($pick[$field]) || $pick[$field] <= 0) {
            $errors[] = "$label: $field fehlt oder ist nicht positiv";
        }
    }
    if ($errors) {
        return $errors;
    }

    [$entry, $stop, $t1, $t2] = [(float)$pick['entry'], (float)$pick['stop'],
                                 (float)$pick['target1'], (float)$pick['target2']];

    if ($stop >= $entry) {
        $errors[] = "$label: Stop $stop liegt nicht unter dem Entry $entry";
    } else {
        $rr = ($t1 - $entry) / ($entry - $stop);
        if ($rr < MIN_RISK_REWARD) {
            $errors[] = sprintf('%s: R:R %.2f unter dem Minimum %.1f',
                $label, $rr, MIN_RISK_REWARD);
        }
    }
    if ($t1 <= $entry) {
        $errors[] = "$label: Target 1 $t1 liegt nicht ueber dem Entry $entry";
    }
    if ($t2 <= $t1) {
        $errors[] = "$label: Target 2 $t2 liegt nicht ueber Target 1 $t1";
    }
    if ((int)($pick['composite'] ?? 0) < MIN_COMPOSITE) {
        $errors[] = "$label: Composite " . ($pick['composite'] ?? 0)
            . ' unter Schwelle ' . MIN_COMPOSITE;
    }

    foreach (REQUIRED_PROSE as $field) {
        if (trim((string)($pick[$field] ?? '')) === '') {
            $errors[] = "$label: Feld '$field' fehlt oder ist leer";
        }
    }

    $sources = $pick['sources'] ?? [];
    if (!$sources) {
        $errors[] = "$label: keine Quellen angegeben";
    }
    foreach ($sources as $source) {
        $url = is_array($source) ? ($source['url'] ?? '') : (string)$source;
        if (!preg_match('#^https?://#i', $url)) {
            $errors[] = "$label: sources enthaelt keine brauchbare URL";
        }
    }
    return $errors;
}
```

**Step 4: Run test to verify it passes**

Run: `php portal/tests/test_validate.php`
Expected: PASS — „9 Zusicherungen, 0 fehlgeschlagen"

**Step 5: Commit**

```bash
git add portal/htdocs/lib/validate.php portal/tests/test_validate.php
git commit -m "feat: server-side report validation"
```

---

### Task 10: Schreibende Repository-Funktionen

**Files:**
- Create: `portal/htdocs/lib/repo.php`
- Create: `portal/tests/test_repo_write.php`

**Step 1: Write the failing test**

`portal/tests/test_repo_write.php`:

```php
<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/db.php';
require __DIR__ . '/../htdocs/lib/migrate.php';
require __DIR__ . '/../htdocs/lib/repo.php';
require __DIR__ . '/fixtures.php';   // liefert fixture_document()

$pdo = db_connect(':memory:');
migrate($pdo);

$doc = fixture_document();
$result = save_report($pdo, $doc);
assert_equals(false, $result['replaced'], 'Erster Push ersetzt nichts');
assert_equals(2, $result['picks'], 'Zwei Picks geschrieben');
assert_that($result['bars_written'] > 0, 'Bars geschrieben');

$again = save_report($pdo, $doc);
assert_equals(true, $again['replaced'], 'Zweiter Push mit gleichem Datum ersetzt');
assert_equals(1, (int)$pdo->query('SELECT COUNT(*) FROM reports')->fetchColumn(),
    'Weiterhin genau ein Report');
assert_equals(2, (int)$pdo->query('SELECT COUNT(*) FROM picks')->fetchColumn(),
    'Weiterhin genau zwei Picks');

// Status ueberlebt eine Reportkorrektur -- sonst loescht ein Nachtrag die Historie.
$pdo->exec("UPDATE picks SET status='TARGET1', r_multiple=2.06 WHERE ticker='XYZ'");
save_report($pdo, $doc);
assert_equals('TARGET1',
    $pdo->query("SELECT status FROM picks WHERE ticker='XYZ'")->fetchColumn(),
    'Status ueberlebt den erneuten Push');

$written = save_bars($pdo, ['XYZ' => [['2026-08-20', 1.0, 2.0, 0.5, 1.5, 100.0]]]);
assert_equals(1, $written, 'Bar geschrieben');
$written = save_bars($pdo, ['XYZ' => [['2026-08-20', 1.0, 2.0, 0.5, 9.9, 100.0]]]);
assert_equals(1, $written, 'Gleiche Bar wird ueberschrieben, nicht dupliziert');
assert_equals(9.9,
    $pdo->query("SELECT close FROM bars WHERE ticker='XYZ' AND date='2026-08-20'")->fetchColumn(),
    'Ueberschriebener Schlusskurs');

$updated = save_outcomes($pdo, [
    ['date' => '2026-08-09', 'ticker' => 'XYZ', 'status' => 'STOP', 'r_multiple' => -1.0],
    ['date' => '2026-08-09', 'ticker' => 'NOPE', 'status' => 'STOP', 'r_multiple' => -1.0],
]);
assert_equals(1, $updated, 'Nur bekannte Picks werden aktualisiert');

exit(test_summary());
```

`portal/tests/fixtures.php` erzeugt ein Dokument mit zwei Picks (`XYZ`, `ABC`, verschiedene Sektoren) und je fünf Bars — baue es analog zu `good_pick()` aus Task 9 und ergänze `bars`.

**Step 2: Run test to verify it fails**

Run: `php portal/tests/test_repo_write.php`
Expected: FAIL — `lib/repo.php` fehlt

**Step 3: Write minimal implementation**

`portal/htdocs/lib/repo.php` — schreibende Hälfte:

```php
<?php

const PICK_COLUMNS = [
    'ticker', 'rank', 'name', 'sector', 'setup',
    'score_technical', 'score_catalyst', 'score_risk', 'composite',
    'score_rationale', 'entry', 'stop', 'target1', 'target2',
    'risk_reward', 'position_size', 'catalyst',
    'thesis', 'technicals', 'catalyst_detail', 'plan',
    'bear_case', 'invalidation', 'metrics', 'sources',
];

const JSON_COLUMNS = ['score_rationale', 'plan', 'metrics', 'sources'];

function save_report(PDO $pdo, array $doc): array {
    $date = $doc['date'];
    $pdo->beginTransaction();
    try {
        $replaced = (bool)$pdo->prepare('SELECT 1 FROM reports WHERE date = ?')
            ->execute([$date]);
        $stmt = $pdo->prepare('SELECT 1 FROM reports WHERE date = ?');
        $stmt->execute([$date]);
        $replaced = (bool)$stmt->fetchColumn();

        // Status vor dem Loeschen retten: eine Reportkorrektur darf die
        // bereits ermittelten Ausgaenge nicht mitnehmen.
        $keep = [];
        $stmt = $pdo->prepare(
            'SELECT ticker, status, r_multiple, triggered_on, closed_on, evaluated_at
             FROM picks WHERE report_date = ?');
        $stmt->execute([$date]);
        foreach ($stmt as $row) {
            $keep[$row['ticker']] = $row;
        }

        $pdo->prepare('DELETE FROM picks WHERE report_date = ?')->execute([$date]);
        $pdo->prepare('DELETE FROM reports WHERE date = ?')->execute([$date]);

        $pdo->prepare(
            'INSERT INTO reports (date, generated_at, market_context, note,
                                  rejected, checklist, disclaimer, pushed_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            $date, $doc['generated_at'] ?? null, $doc['market_context'] ?? '',
            $doc['note'] ?? '', json_encode($doc['rejected'] ?? []),
            json_encode($doc['checklist'] ?? []), $doc['disclaimer'] ?? '',
            gmdate('c'),
        ]);

        $columns = array_merge(['report_date'], PICK_COLUMNS,
            ['status', 'r_multiple', 'triggered_on', 'closed_on', 'evaluated_at']);
        $sql = 'INSERT INTO picks (' . implode(', ', $columns) . ') VALUES ('
             . implode(', ', array_fill(0, count($columns), '?')) . ')';
        $insert = $pdo->prepare($sql);

        foreach ($doc['picks'] as $pick) {
            $values = [$date];
            foreach (PICK_COLUMNS as $column) {
                $value = $pick[$column] ?? null;
                $values[] = in_array($column, JSON_COLUMNS, true)
                    ? json_encode($value ?? [], JSON_UNESCAPED_UNICODE)
                    : $value;
            }
            $previous = $keep[$pick['ticker']] ?? null;
            $values[] = $previous['status'] ?? 'OPEN';
            $values[] = $previous['r_multiple'] ?? null;
            $values[] = $previous['triggered_on'] ?? null;
            $values[] = $previous['closed_on'] ?? null;
            $values[] = $previous['evaluated_at'] ?? null;
            $insert->execute($values);
        }

        $bars = save_bars($pdo, $doc['bars'] ?? [], false);
        $pdo->commit();
    } catch (Throwable $exc) {
        $pdo->rollBack();
        throw $exc;
    }

    return ['date' => $date, 'picks' => count($doc['picks']),
            'bars_written' => $bars, 'replaced' => $replaced];
}

function save_bars(PDO $pdo, array $bars, bool $own_transaction = true): int {
    if (!$bars) {
        return 0;
    }
    if ($own_transaction) {
        $pdo->beginTransaction();
    }
    $stmt = $pdo->prepare(
        'INSERT INTO bars (ticker, date, open, high, low, close, volume)
         VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(ticker, date) DO UPDATE SET
             open = excluded.open, high = excluded.high, low = excluded.low,
             close = excluded.close, volume = excluded.volume');
    $written = 0;
    foreach ($bars as $ticker => $rows) {
        foreach ($rows as $row) {
            $stmt->execute([$ticker, $row[0], $row[1], $row[2], $row[3], $row[4], $row[5]]);
            $written++;
        }
    }
    if ($own_transaction) {
        $pdo->commit();
    }
    return $written;
}

function save_outcomes(PDO $pdo, array $outcomes): int {
    $stmt = $pdo->prepare(
        'UPDATE picks SET status = ?, r_multiple = ?, evaluated_at = ?
         WHERE report_date = ? AND ticker = ?');
    $updated = 0;
    $pdo->beginTransaction();
    foreach ($outcomes as $outcome) {
        $stmt->execute([$outcome['status'], $outcome['r_multiple'] ?? null,
                        gmdate('c'), $outcome['date'], $outcome['ticker']]);
        $updated += $stmt->rowCount();
    }
    $pdo->commit();
    return $updated;
}

function log_push(PDO $pdo, string $endpoint, int $bytes, int $status, string $detail = ''): void {
    $pdo->prepare('INSERT INTO pushes (endpoint, at, bytes, status, detail)
                   VALUES (?, ?, ?, ?, ?)')
        ->execute([$endpoint, gmdate('c'), $bytes, $status, mb_substr($detail, 0, 500)]);
}
```

Beim Schreiben ist mir die doppelte `$replaced`-Zuweisung oben aufgefallen — entferne die erste Zeile, `PDOStatement::execute` gibt `true` auch ohne Treffer zurück und wäre hier immer wahr.

**Step 4: Run test to verify it passes**

Run: `php portal/tests/test_repo_write.php`
Expected: PASS

**Step 5: Commit**

```bash
git add portal/htdocs/lib/repo.php portal/tests/test_repo_write.php portal/tests/fixtures.php
git commit -m "feat: idempotent report, bars and outcome writes"
```

---

### Task 11: API-Front-Controller

**Files:**
- Create: `portal/htdocs/api.php`
- Create: `portal/tests/test_api.php`

**Step 1: Write the failing test**

`portal/tests/test_api.php` startet den eingebauten PHP-Server und spricht ihn per `curl` an. Auth ist hier nicht prüfbar — der eingebaute Server kennt `.htaccess` nicht; sie wird im Deployment geprüft.

```php
<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/fixtures.php';

$root = __DIR__ . '/../htdocs';
$data = sys_get_temp_dir() . '/portal-test-' . getmypid();
mkdir($data . '/data', 0777, true);

// Konfiguration auf das Testverzeichnis umbiegen
$config = "<?php return ['db' => '$data/data/test.sqlite',
    'log' => '$data/data/test.log', 'title' => 'test', 'account' => 10000.0];";
file_put_contents($data . '/config.php', $config);
putenv("PORTAL_CONFIG=$data/config.php");

$port = 8123;
$server = proc_open(
    "php -S 127.0.0.1:$port -t $root",
    [1 => ['file', '/dev/null', 'w'], 2 => ['file', '/dev/null', 'w']],
    $pipes, null, ['PORTAL_CONFIG' => $data . '/config.php']
);
usleep(500000);

function api(string $method, string $path, $body = null): array {
    global $port;
    $ch = curl_init("http://127.0.0.1:$port/api.php?path=$path");
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST  => $method,
        CURLOPT_HTTPHEADER     => ['Content-Type: application/json'],
    ]);
    if ($body !== null) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
    }
    $response = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return [$status, json_decode($response, true)];
}

[$status, $body] = api('GET', 'health');
assert_equals(200, $status, 'health antwortet 200');
assert_equals(0, $body['reports'], 'Leere Datenbank meldet null Reports');

[$status, $body] = api('POST', 'reports', fixture_document());
assert_equals(200, $status, 'Gueltiger Report wird angenommen');
assert_equals(2, $body['picks'], 'Zwei Picks gespeichert');

[$status, $body] = api('POST', 'reports', fixture_document());
assert_equals(200, $status, 'Zweiter Push wird angenommen');
assert_equals(true, $body['replaced'], 'Zweiter Push meldet Ersetzung');

$broken = fixture_document();
$broken['picks'][0]['target1'] = $broken['picks'][0]['entry'] + 0.10;  // R:R weit unter 2
[$status, $body] = api('POST', 'reports', $broken);
assert_equals(422, $status, 'R:R unter 2 wird mit 422 abgelehnt');
assert_that(str_contains(implode(' ', $body['details']), 'R:R'), 'Fehlerdetails nennen R:R');

[$status, $body] = api('POST', 'bars', ['XYZ' => [['2026-09-01', 1, 2, 0.5, 1.5, 100]]]);
assert_equals(200, $status, 'Bars werden angenommen');
assert_equals(1, $body['written'], 'Eine Bar geschrieben');

[$status, $body] = api('POST', 'outcomes',
    [['date' => '2026-08-09', 'ticker' => 'XYZ', 'status' => 'TARGET1', 'r_multiple' => 2.06]]);
assert_equals(200, $status, 'Ausgaenge werden angenommen');
assert_equals(1, $body['updated'], 'Ein Pick aktualisiert');

[$status] = api('GET', 'reports');
assert_equals(405, $status, 'GET auf reports wird abgelehnt');

[$status] = api('POST', 'unbekannt', []);
assert_equals(404, $status, 'Unbekannter Pfad antwortet 404');

proc_terminate($server);
exit(test_summary());
```

**Step 2: Run test to verify it fails**

Run: `php portal/tests/test_api.php`
Expected: FAIL — 404 auf allen Pfaden, `api.php` fehlt

**Step 3: Write minimal implementation**

`portal/htdocs/api.php`:

```php
<?php
declare(strict_types=1);

require __DIR__ . '/lib/db.php';
require __DIR__ . '/lib/migrate.php';
require __DIR__ . '/lib/repo.php';
require __DIR__ . '/lib/validate.php';

header('Content-Type: application/json; charset=utf-8');

$config = require(getenv('PORTAL_CONFIG') ?: __DIR__ . '/lib/config.php');

function respond(int $status, array $body): never {
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function fail(int $status, string $error, array $details = []): never {
    respond($status, ['error' => $error, 'details' => $details]);
}

// Pfad kommt aus dem Rewrite (?path=…) oder aus REQUEST_URI.
$path = $_GET['path'] ?? trim(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH), '/');
$path = preg_replace('#^api/#', '', (string)$path);
$method = $_SERVER['REQUEST_METHOD'];

try {
    $pdo = db_connect($config['db']);
    migrate($pdo);
} catch (Throwable $exc) {
    error_log((string)$exc, 3, $config['log']);
    fail(500, 'Datenbank nicht verfuegbar');
}

if ($path === 'health') {
    if ($method !== 'GET') {
        fail(405, 'Nur GET');
    }
    respond(200, [
        'ok'        => true,
        'reports'   => (int)$pdo->query('SELECT COUNT(*) FROM reports')->fetchColumn(),
        'picks'     => (int)$pdo->query('SELECT COUNT(*) FROM picks')->fetchColumn(),
        'bars'      => (int)$pdo->query('SELECT COUNT(*) FROM bars')->fetchColumn(),
        'last_push' => $pdo->query('SELECT MAX(at) FROM pushes')->fetchColumn() ?: null,
    ]);
}

if (!in_array($path, ['reports', 'bars', 'outcomes'], true)) {
    fail(404, "Unbekannter Pfad '$path'");
}
if ($method !== 'POST') {
    fail(405, 'Nur POST');
}

$raw = file_get_contents('php://input');
if (strlen($raw) > 8 * 1024 * 1024) {
    fail(413, 'Payload groesser als 8 MB');
}
$payload = json_decode($raw, true);
if (!is_array($payload)) {
    fail(400, 'Body ist kein JSON-Objekt: ' . json_last_error_msg());
}

try {
    if ($path === 'reports') {
        $errors = validate_report($payload);
        if ($errors) {
            log_push($pdo, $path, strlen($raw), 422, implode('; ', $errors));
            fail(422, 'Report abgelehnt', $errors);
        }
        $result = save_report($pdo, $payload);
    } elseif ($path === 'bars') {
        $result = ['written' => save_bars($pdo, $payload)];
    } else {
        $result = ['updated' => save_outcomes($pdo, $payload)];
    }
} catch (Throwable $exc) {
    error_log((string)$exc, 3, $config['log']);
    log_push($pdo, $path, strlen($raw), 500, $exc->getMessage());
    fail(500, 'Schreiben fehlgeschlagen');
}

log_push($pdo, $path, strlen($raw), 200);
respond(200, ['ok' => true] + $result);
```

**Step 4: Run test to verify it passes**

Run: `php portal/tests/test_api.php`
Expected: PASS

**Step 5: Commit**

```bash
git add portal/htdocs/api.php portal/tests/test_api.php
git commit -m "feat: portal API front controller"
```

---

### Task 12: Lesende Repository-Funktionen und Performance-Aggregation

**Files:**
- Modify: `portal/htdocs/lib/repo.php`
- Create: `portal/tests/test_repo_read.php`

**Step 1: Write the failing test**

`portal/tests/test_repo_read.php` — die Aggregation muss dieselben Zahlen liefern wie `stockpicker/outcomes.py:_aggregate`:

```php
<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/db.php';
require __DIR__ . '/../htdocs/lib/migrate.php';
require __DIR__ . '/../htdocs/lib/repo.php';
require __DIR__ . '/fixtures.php';

$pdo = db_connect(':memory:');
migrate($pdo);
save_report($pdo, fixture_document());

$reports = list_reports($pdo);
assert_equals(1, count($reports), 'Ein Report gelistet');
assert_equals(2, count($reports[0]['picks']), 'Mit seinen zwei Picks');

$report = get_report($pdo, '2026-08-09');
assert_equals('2026-08-09', $report['date'], 'Report nach Datum gefunden');
assert_that(is_array($report['picks'][0]['metrics']), 'metrics ist dekodiert');
assert_that(is_array($report['picks'][0]['sources']), 'sources ist dekodiert');

assert_equals(null, get_report($pdo, '2020-01-01'), 'Unbekanntes Datum liefert null');

$pick = get_pick($pdo, '2026-08-09', 'XYZ');
assert_equals('XYZ', $pick['ticker'], 'Pick gefunden');
assert_that(is_array($pick['bars']), 'Pick bringt seine Bars mit');
assert_that(is_array($pick['plan']), 'plan ist dekodiert');

// Drei geschlossene Trades: zwei Gewinner mit 2R, ein Verlierer mit -1R.
// Trefferquote 66,7 %, Erwartungswert (2 + 2 - 1) / 3 = 1,0R.
$pdo->exec("UPDATE picks SET status='TARGET1', r_multiple=2.0 WHERE ticker='XYZ'");
$pdo->exec("UPDATE picks SET status='STOP', r_multiple=-1.0 WHERE ticker='ABC'");
save_report($pdo, fixture_document(['date' => '2026-08-10']));
$pdo->exec("UPDATE picks SET status='TARGET1', r_multiple=2.0
            WHERE report_date='2026-08-10' AND ticker='XYZ'");
$pdo->exec("UPDATE picks SET status='NOT_TRIGGERED', r_multiple=0
            WHERE report_date='2026-08-10' AND ticker='ABC'");

$overall = performance_overall($pdo);
assert_equals(3, $overall['trades'], 'Drei geschlossene Trades');
assert_equals(66.7, $overall['win_rate'], 'Trefferquote 66,7 Prozent');
assert_equals(1.0, $overall['expectancy_r'], 'Erwartungswert 1,0R');
assert_equals(1, $overall['not_triggered'], 'Ein nicht ausgeloester Pick');

$by_setup = performance_by($pdo, 'setup');
assert_that(isset($by_setup['VCP_BREAKOUT']), 'Nach Setup gruppiert');

$by_catalyst = performance_by($pdo, 'catalyst_bucket');
assert_that(isset($by_catalyst['stark (7-10)']), 'Katalysator-Buckets wie in outcomes.py');

$curve = cumulative_r($pdo);
assert_equals(3, count($curve), 'Kurve hat einen Punkt je geschlossenem Trade');
assert_equals(3.0, end($curve)['cumulative'], 'Kumulierte R am Ende 3,0');

exit(test_summary());
```

**Step 2: Run test to verify it fails**

Run: `php portal/tests/test_repo_read.php`
Expected: FAIL — `Call to undefined function list_reports()`

**Step 3: Write minimal implementation**

An `portal/htdocs/lib/repo.php` anhängen:

```php
const CLOSED = "('TARGET1','STOP')";

function decode_pick(array $row): array {
    foreach (JSON_COLUMNS as $column) {
        $row[$column] = json_decode($row[$column] ?? '[]', true) ?: [];
    }
    return $row;
}

function list_reports(PDO $pdo, int $limit = 50): array {
    $reports = $pdo->query(
        "SELECT * FROM reports ORDER BY date DESC LIMIT $limit")->fetchAll();
    foreach ($reports as &$report) {
        $report['rejected']  = json_decode($report['rejected'] ?? '[]', true) ?: [];
        $report['checklist'] = json_decode($report['checklist'] ?? '[]', true) ?: [];
        $stmt = $pdo->prepare(
            'SELECT ticker, name, setup, composite, status, r_multiple
             FROM picks WHERE report_date = ? ORDER BY rank');
        $stmt->execute([$report['date']]);
        $report['picks'] = $stmt->fetchAll();
    }
    return $reports;
}

function get_report(PDO $pdo, string $date): ?array {
    $stmt = $pdo->prepare('SELECT * FROM reports WHERE date = ?');
    $stmt->execute([$date]);
    $report = $stmt->fetch();
    if (!$report) {
        return null;
    }
    $report['rejected']  = json_decode($report['rejected'] ?? '[]', true) ?: [];
    $report['checklist'] = json_decode($report['checklist'] ?? '[]', true) ?: [];

    $stmt = $pdo->prepare('SELECT * FROM picks WHERE report_date = ? ORDER BY rank');
    $stmt->execute([$date]);
    $report['picks'] = array_map('decode_pick', $stmt->fetchAll());
    return $report;
}

function get_pick(PDO $pdo, string $date, string $ticker): ?array {
    $stmt = $pdo->prepare('SELECT * FROM picks WHERE report_date = ? AND ticker = ?');
    $stmt->execute([$date, $ticker]);
    $pick = $stmt->fetch();
    if (!$pick) {
        return null;
    }
    $pick = decode_pick($pick);
    $stmt = $pdo->prepare(
        'SELECT date, open, high, low, close, volume FROM bars
         WHERE ticker = ? ORDER BY date');
    $stmt->execute([$ticker]);
    $pick['bars'] = $stmt->fetchAll();
    return $pick;
}

function performance_overall(PDO $pdo, ?string $from = null, ?string $to = null): array {
    $where = date_filter($from, $to);
    $row = $pdo->query(
        "SELECT COUNT(*) AS trades,
                SUM(CASE WHEN status='TARGET1' THEN 1 ELSE 0 END) AS wins,
                COALESCE(SUM(r_multiple), 0) AS total_r
         FROM picks WHERE status IN " . CLOSED . " $where")->fetch();

    $trades = (int)$row['trades'];
    return [
        'trades'        => $trades,
        'win_rate'      => $trades ? round(100.0 * (int)$row['wins'] / $trades, 1) : 0.0,
        'expectancy_r'  => $trades ? round((float)$row['total_r'] / $trades, 2) : 0.0,
        'not_triggered' => (int)$pdo->query(
            "SELECT COUNT(*) FROM picks WHERE status='NOT_TRIGGERED' $where")->fetchColumn(),
        'open'          => (int)$pdo->query(
            "SELECT COUNT(*) FROM picks WHERE status='OPEN' $where")->fetchColumn(),
    ];
}

function performance_by(PDO $pdo, string $dimension,
                        ?string $from = null, ?string $to = null): array {
    // Die Buckets spiegeln outcomes.CATALYST_BUCKETS.
    $key = $dimension === 'catalyst_bucket'
        ? "CASE WHEN score_catalyst >= 7 THEN 'stark (7-10)'
                WHEN score_catalyst >= 4 THEN 'mittel (4-6)'
                ELSE 'schwach (0-3)' END"
        : 'setup';

    $where = date_filter($from, $to);
    $rows = $pdo->query(
        "SELECT $key AS bucket, COUNT(*) AS trades,
                SUM(CASE WHEN status='TARGET1' THEN 1 ELSE 0 END) AS wins,
                COALESCE(SUM(r_multiple), 0) AS total_r
         FROM picks WHERE status IN " . CLOSED . " $where
         GROUP BY bucket ORDER BY bucket")->fetchAll();

    $result = [];
    foreach ($rows as $row) {
        $trades = (int)$row['trades'];
        $result[$row['bucket']] = [
            'trades'       => $trades,
            'win_rate'     => round(100.0 * (int)$row['wins'] / $trades, 1),
            'expectancy_r' => round((float)$row['total_r'] / $trades, 2),
        ];
    }
    return $result;
}

function cumulative_r(PDO $pdo, ?string $from = null, ?string $to = null): array {
    $where = date_filter($from, $to);
    $rows = $pdo->query(
        "SELECT report_date, ticker, r_multiple FROM picks
         WHERE status IN " . CLOSED . " $where
         ORDER BY report_date, ticker")->fetchAll();

    $sum = 0.0;
    $curve = [];
    foreach ($rows as $row) {
        $sum += (float)$row['r_multiple'];
        $curve[] = ['date' => $row['report_date'], 'ticker' => $row['ticker'],
                    'r' => (float)$row['r_multiple'], 'cumulative' => round($sum, 2)];
    }
    return $curve;
}

function date_filter(?string $from, ?string $to): string {
    // Nur ISO-Daten passieren -- die Werte kommen aus der Query-String.
    $clauses = [];
    if ($from && preg_match('/^\d{4}-\d{2}-\d{2}$/', $from)) {
        $clauses[] = "report_date >= '$from'";
    }
    if ($to && preg_match('/^\d{4}-\d{2}-\d{2}$/', $to)) {
        $clauses[] = "report_date <= '$to'";
    }
    return $clauses ? ' AND ' . implode(' AND ', $clauses) : '';
}
```

**Step 4: Run test to verify it passes**

Run: `php portal/tests/test_repo_read.php`
Expected: PASS

**Step 5: Commit**

```bash
git add portal/htdocs/lib/repo.php portal/tests/test_repo_read.php
git commit -m "feat: read queries and performance aggregation"
```

---

### Task 13: Assets bauen

**Files:**
- Create: `portal/package.json`, `portal/src/app.css`, `portal/.gitignore`
- Create: `portal/htdocs/assets/.gitkeep`

**Step 1: Implementation**

`portal/package.json`:

```json
{
  "name": "stock-picker-portal",
  "private": true,
  "scripts": {
    "build": "tailwindcss -i src/app.css -o htdocs/assets/app.css --minify && npm run vendor",
    "vendor": "cp node_modules/flowbite/dist/flowbite.min.js htdocs/assets/ && cp node_modules/lightweight-charts/dist/lightweight-charts.standalone.production.js htdocs/assets/"
  },
  "devDependencies": {
    "@tailwindcss/cli": "^4.0.0",
    "tailwindcss": "^4.0.0",
    "flowbite": "^4.0.0",
    "lightweight-charts": "^5.0.0"
  }
}
```

`portal/src/app.css`:

```css
@import "tailwindcss";
@import "flowbite/src/themes/default";
@plugin "flowbite/plugin";
@source "../htdocs/**/*.php";
@source "../node_modules/flowbite";
@custom-variant dark (&:where(.dark, .dark *));
```

Vier Zeilen, die nicht offensichtlich sind:

- **Der Theme-Import ist Pflicht.** Flowbite 4 liefert seine Design-Tokens über CSS-Theme-Dateien statt über den Plugin. Ohne ihn fehlen `--color-neutral-primary`, `--color-body`, `--color-heading` und die Flowbite-eigene Radius-Skala, und die Markup-Beispiele der Doku greifen ins Leere.
- **`@source "../node_modules/flowbite"`** erfasst die Klassen, die Flowbite erst zur Laufzeit per JavaScript anhängt — Dropdown, Modal, Tooltip-Positionierung. Die stehen in keiner PHP-Datei.
- **`@custom-variant dark`** ist nötig, weil `themes/default.css` den Dark Mode über einen `.dark`-Block schaltet, nicht über `prefers-color-scheme`. Der Tailwind-4-Standard griffe daneben.
- Das Google-Fonts-`@import` für Inter aus der Flowbite-Anleitung bleibt bewusst weg — ein externer Request je Seitenaufruf in einem Portal, das sonst nichts nach außen spricht. `--font-sans` fällt sauber auf `system-ui` zurück.

Flowbites Komponentenklassen entstehen unter Tailwind 4 **bedarfsgesteuert**: Sie landen nur im CSS, wenn sie im gescannten Markup vorkommen. Der Asset-Build muss deshalb zwingend nach dem Schreiben der Views laufen — `deploy.sh` sieht genau diese Reihenfolge vor.

Nebenbefund für alle, die aus älteren Beispielen kopieren: `bg-primary-700` gibt es in Flowbite 4 nicht mehr. Die `primary-*`-Palette gehörte zu Flowbite 2/3 mit `tailwind.config.js` und fällt heute still weg.

`portal/.gitignore`:

```
node_modules/
htdocs/assets/*.css
htdocs/assets/*.js
htdocs/data/
```

Die gebauten Assets landen nicht im Repository — `deploy.sh` baut sie vor jedem Upload. Ein `portal/htdocs/assets/.gitkeep` hält das Verzeichnis.

**Step 2: Verify**

```bash
cd portal && npm install && npm run build && ls -la htdocs/assets/
```
Expected: `app.css`, `flowbite.min.js`, `lightweight-charts.standalone.production.js`

Prüfe die Pfade in `vendor` gegen das tatsächliche `node_modules` — Paketlayouts ändern sich zwischen Major-Versionen. Weichen sie ab, korrigiere sie hier, statt sie im Deploy-Skript zu umgehen.

**Step 3: Commit**

```bash
git add portal/package.json portal/package-lock.json portal/src portal/.gitignore portal/htdocs/assets/.gitkeep
git commit -m "build: tailwind and flowbite asset pipeline"
```

---

### Task 14: Layout, Markdown-Rendering, Übersichtsseite

**Files:**
- Create: `portal/htdocs/index.php`, `portal/htdocs/lib/markdown.php`, `portal/htdocs/lib/Parsedown.php`
- Create: `portal/htdocs/lib/views/layout.php`, `views/reports.php`

**Step 1: Parsedown vendoren**

```bash
curl -L -o portal/htdocs/lib/Parsedown.php \
  https://raw.githubusercontent.com/erusev/parsedown/master/Parsedown.php
```

Prüfe danach, dass die Datei mit `<?php` beginnt und eine Klasse `Parsedown` definiert. Wenn der Download eine HTML-Fehlerseite liefert, lade die Datei aus dem letzten Release-Tag statt aus `master`.

**Step 2: Markdown-Wrapper**

`portal/htdocs/lib/markdown.php`:

```php
<?php
require_once __DIR__ . '/Parsedown.php';

function md(?string $text): string {
    // Die Prosa stammt aus einem Sprachmodell und ist damit nicht
    // vertrauenswuerdiger Input. Safe Mode escaped rohes HTML.
    static $parser = null;
    if ($parser === null) {
        $parser = new Parsedown();
        $parser->setSafeMode(true);
        $parser->setBreaksEnabled(false);
    }
    $html = $parser->text((string)$text);
    // Externe Links oeffnen in neuem Tab, ohne Zugriff auf window.opener.
    return str_replace('<a href="http', '<a target="_blank" rel="noopener noreferrer" href="http', $html);
}

function e(?string $value): string {
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function money(?float $value, int $decimals = 2): string {
    return $value === null ? '—' : number_format($value, $decimals, ',', '.');
}

function status_badge(string $status): string {
    $map = [
        'OPEN'          => ['Offen', 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300'],
        'TARGET1'       => ['Target 1', 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300'],
        'STOP'          => ['Stop', 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300'],
        'NOT_TRIGGERED' => ['Nicht ausgelöst', 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'],
    ];
    [$label, $classes] = $map[$status] ?? [$status, 'bg-gray-100 text-gray-800'];
    return '<span class="text-xs font-medium me-2 px-2.5 py-0.5 rounded-sm ' . $classes . '">'
        . e($label) . '</span>';
}
```

**Step 3: Front-Controller**

`portal/htdocs/index.php`:

```php
<?php
declare(strict_types=1);

require __DIR__ . '/lib/db.php';
require __DIR__ . '/lib/migrate.php';
require __DIR__ . '/lib/repo.php';
require __DIR__ . '/lib/markdown.php';

$config = require(getenv('PORTAL_CONFIG') ?: __DIR__ . '/lib/config.php');
ini_set('display_errors', '0');

try {
    $pdo = db_connect($config['db']);
    migrate($pdo);
} catch (Throwable $exc) {
    error_log((string)$exc, 3, $config['log']);
    http_response_code(500);
    exit('Datenbank nicht verfügbar.');
}

// /r/2026-08-09/CRSR  →  ['r', '2026-08-09', 'CRSR']
$path = $_GET['path'] ?? trim(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH), '/');
$segments = array_values(array_filter(explode('/', (string)$path)));

function render(string $view, array $vars, array $config): void {
    extract($vars);
    $title = $vars['title'] ?? $config['title'];
    ob_start();
    require __DIR__ . "/lib/views/$view.php";
    $content = ob_get_clean();
    require __DIR__ . '/lib/views/layout.php';
}

try {
    if (!$segments) {
        render('reports', ['reports' => list_reports($pdo),
                           'overall' => performance_overall($pdo),
                           'last_push' => $pdo->query('SELECT MAX(at) FROM pushes')->fetchColumn()],
               $config);
    } elseif ($segments[0] === 'performance') {
        $from = $_GET['from'] ?? null;
        $to   = $_GET['to'] ?? null;
        render('performance', [
            'title'       => 'Performance',
            'overall'     => performance_overall($pdo, $from, $to),
            'by_setup'    => performance_by($pdo, 'setup', $from, $to),
            'by_catalyst' => performance_by($pdo, 'catalyst_bucket', $from, $to),
            'curve'       => cumulative_r($pdo, $from, $to),
            'from'        => $from, 'to' => $to,
        ], $config);
    } elseif ($segments[0] === 'r' && count($segments) === 2) {
        $report = get_report($pdo, $segments[1]);
        if (!$report) {
            http_response_code(404);
            exit('Kein Report für dieses Datum.');
        }
        render('report', ['title' => 'Report ' . $report['date'], 'report' => $report], $config);
    } elseif ($segments[0] === 'r' && count($segments) === 3) {
        $pick = get_pick($pdo, $segments[1], $segments[2]);
        if (!$pick) {
            http_response_code(404);
            exit('Kein Pick unter dieser Adresse.');
        }
        render('pick', ['title' => $pick['ticker'] . ' — ' . $segments[1],
                        'pick' => $pick, 'account' => $config['account']], $config);
    } else {
        http_response_code(404);
        exit('Seite nicht gefunden.');
    }
} catch (Throwable $exc) {
    error_log((string)$exc, 3, $config['log']);
    http_response_code(500);
    exit('Unerwarteter Fehler. Details stehen im Log.');
}
```

**Step 4: Layout und Übersicht**

`portal/htdocs/lib/views/layout.php` — HTML-Grundgerüst mit `<html class="dark">`, Flowbite-Navbar mit den drei Links (Reports, Performance) und dem Theme-Umschalter, `assets/app.css` im `<head>`, `assets/flowbite.min.js` und `assets/lightweight-charts.standalone.production.js` vor `</body>`, `$content` im Main. Der Umschalter setzt `localStorage.theme` und toggelt `document.documentElement.classList`.

`portal/htdocs/lib/views/reports.php`:

- vier KPI-Kacheln in einem `grid grid-cols-2 md:grid-cols-4 gap-4`: offene Picks, Trefferquote, Erwartungswert, letzter Push
- darunter je Report eine Flowbite-Karte: Datum als `<h2>` mit Link auf `/r/<date>`, die Picks als Chips (Ticker, Composite, `status_badge`), die ersten 240 Zeichen `market_context` als Anriss

**Step 5: Verify**

```bash
cd portal && php -S 127.0.0.1:8123 -t htdocs
```
Im Browser `http://127.0.0.1:8123/?path=` öffnen. Ohne `mod_rewrite` läuft der eingebaute Server nur über `?path=`; das ist für die lokale Prüfung ausreichend.

Expected: Seite lädt mit dunklem Theme, KPI-Kacheln zeigen Nullen bei leerer Datenbank.

**Step 6: Commit**

```bash
git add portal/htdocs/index.php portal/htdocs/lib/markdown.php portal/htdocs/lib/Parsedown.php portal/htdocs/lib/views
git commit -m "feat: portal layout, markdown rendering and report overview"
```

---

### Task 15: Report-Seite

**Files:**
- Create: `portal/htdocs/lib/views/report.php`

**Step 1: Implementation**

Aufbau von oben nach unten:

1. Kopf: Datum als Überschrift, `generated_at` und `pushed_at` klein daneben
2. `md($report['market_context'])` in einem `prose`-Container
3. `md($report['note'])`, falls nicht leer
4. Übersichtstabelle als Flowbite-Table in einem `overflow-x-auto`-Container: Rang, Ticker (verlinkt auf `/r/<date>/<ticker>`), Setup, Composite, Entry, Stop, Target 1, R:R, Status, Katalysator
5. Je Pick eine Karte mit Miniatur-Chart (`<div class="chart-mini" data-pick='…'>`) und den drei Score-Balken
6. Ausklappbares Flowbite-Accordion „Verworfene Kandidaten" mit `$report['rejected']`
7. Ausklappbare Checkliste
8. `$report['disclaimer']` als Fußnote in kleinerer, gedämpfter Schrift

Alle Zahlen laufen durch `money()`, alle Freitexte durch `e()` oder `md()`. Nie ein Modellfeld ohne eines von beiden ins Markup.

**Step 2: Verify**

Fixture einspielen und Seite aufrufen:

```bash
cd portal && php -S 127.0.0.1:8123 -t htdocs &
uv run python - <<'PY'
import json, urllib.request
doc = json.load(open("../out/report-example.json"))  # oder Fixture aus portal/tests
req = urllib.request.Request("http://127.0.0.1:8123/api.php?path=reports",
                             data=json.dumps(doc).encode(), headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
PY
open "http://127.0.0.1:8123/?path=r/2026-08-09"
```

Expected: Tabelle mit fünf Zeilen, Marktkontext gerendert, Links auf die Detailseiten funktionieren.

**Step 3: Commit**

```bash
git add portal/htdocs/lib/views/report.php
git commit -m "feat: report page"
```

---

### Task 16: Pick-Detailseite mit Chart

Die Seite, wegen der das Portal existiert.

**Files:**
- Create: `portal/htdocs/lib/views/pick.php`
- Create: `portal/htdocs/assets/chart.js`

**Step 1: Die Ansicht**

`portal/htdocs/lib/views/pick.php` — zweispaltiges Grid `grid grid-cols-1 lg:grid-cols-5 gap-6`:

**Linke Spalte (`lg:col-span-3`, `lg:sticky lg:top-4 self-start`):**

- Kopf: Ticker, Firmenname, Sektor, Setup-Badge, Status-Badge
- Chart-Container mit den Daten als JSON im `data-`Attribut:

```php
<div id="chart"
     class="h-[420px] w-full rounded-lg border border-gray-200 dark:border-gray-700"
     data-chart='<?= e(json_encode([
        "bars"    => $pick["bars"],
        "entry"   => (float)$pick["entry"],
        "stop"    => (float)$pick["stop"],
        "target1" => (float)$pick["target1"],
        "target2" => (float)$pick["target2"],
        "pickDate" => $pick["report_date"],
        "composite" => (int)$pick["composite"],
        "triggeredOn" => $pick["triggered_on"],
        "closedOn" => $pick["closed_on"],
        "status" => $pick["status"],
        "earnings" => $pick["metrics"]["next_earnings"] ?? null,
     ], JSON_UNESCAPED_SLASHES)) ?>'></div>
<?php if (!$pick['bars']): ?>
  <p class="mt-2 text-sm text-gray-500">Für diesen Titel liegen keine Kursdaten vor.
     Der nächste <code>publish.py --sync</code> liefert sie nach.</p>
<?php endif; ?>
```

- daneben die **R-Leiter**: ein `relative h-[420px] w-16`-Container, in dem Stop, Entry, Target 1 und Target 2 per `style="bottom: X%"` maßstäblich sitzen. Die Prozentwerte rechnet PHP:

```php
$low  = min($pick['stop'], $pick['entry']) * 0.98;
$high = max($pick['target2'], $pick['entry']) * 1.02;
$pos  = fn(float $price) => 100.0 * ($price - $low) / ($high - $low);
```

  Der aktuelle Kurs ist `end($pick['bars'])['close']` und bekommt einen Zeiger mit der erreichten R-Zahl: `($close - $entry) / ($entry - $stop)`.

- **Score-Zerlegung**: drei Flowbite-Progressbars für Technik, Katalysator und Risiko (Skala 0–10), jeweils mit dem Satz aus `score_rationale` als `<p class="text-sm">` darunter. Der Composite steht als große Zahl daneben.

- **Kennzahlenstreifen** aus `$pick['metrics']`: RS-Perzentil, RSI(14), RVOL, ATR %, Gap %, Abstand zum Pivot. Je ein schmaler Balken mit einer Markierung an der Template-Schwelle. Die Schwellen kommen aus `stockpicker/templates.py` — lies sie dort nach und lege sie als PHP-Konstante `TEMPLATE_THRESHOLDS` in `lib/views/pick.php` ab, gruppiert nach Setup-Namen.

- **Handelsplan** als Definitionsliste: Entry, Stop, Target 1, Target 2, R:R, Positionsgröße bei 1 % Risiko auf `$account`.

**Rechte Spalte (`lg:col-span-2`):**

- These, Technische Lage, Katalysator — je `<h2>` plus `md(...)`
- Bear-Case in einer bernsteinfarbenen Karte:
  `class="rounded-lg border-l-4 border-amber-500 bg-amber-50 dark:bg-amber-950/30 p-4"`
- Invalidierung als roter Callout:
  `class="rounded-lg border-l-4 border-red-500 bg-red-50 dark:bg-red-950/30 p-4"`
- Quellen als Liste: `title` als Linktext, `date` dahinter, `url` als Ziel. Fehlt `title`, steht der Host da.

Kein Accordion, keine Tabs. Bear-Case und Invalidierung sind die Abschnitte, die man am ehesten überspringt; zugeklappt liest sie niemand.

**Step 2: Das Chart-Skript**

`portal/htdocs/assets/chart.js`:

```js
// Zeichnet Kerzen, Volumen, die vier Handelsniveaus und die Marker, die
// den Pick im Verlauf verorten. Die Daten kommen aus dem data-chart-
// Attribut des Containers -- kein zweiter Request.

function themeColors() {
  const dark = document.documentElement.classList.contains('dark');
  return {
    background: dark ? '#111827' : '#ffffff',
    text:       dark ? '#9ca3af' : '#4b5563',
    grid:       dark ? '#1f2937' : '#e5e7eb',
  };
}

function drawChart(container) {
  const data = JSON.parse(container.dataset.chart);
  if (!data.bars || data.bars.length === 0) {
    return;
  }
  const colors = themeColors();

  const chart = LightweightCharts.createChart(container, {
    layout: { background: { color: colors.background }, textColor: colors.text },
    grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
    rightPriceScale: { borderColor: colors.grid, scaleMargins: { top: 0.05, bottom: 0.25 } },
    timeScale: { borderColor: colors.grid, timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    autoSize: true,
  });

  const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: '#16a34a', downColor: '#dc2626',
    borderUpColor: '#16a34a', borderDownColor: '#dc2626',
    wickUpColor: '#16a34a', wickDownColor: '#dc2626',
  });
  candles.setData(data.bars.map(b => ({
    time: b.date, open: +b.open, high: +b.high, low: +b.low, close: +b.close,
  })));

  const volume = chart.addSeries(LightweightCharts.HistogramSeries, {
    priceFormat: { type: 'volume' }, priceScaleId: 'volume',
  });
  chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  volume.setData(data.bars.map(b => ({
    time: b.date, value: +b.volume,
    color: +b.close >= +b.open ? 'rgba(22,163,74,0.4)' : 'rgba(220,38,38,0.4)',
  })));

  const lines = [
    { price: data.stop,    color: '#dc2626', title: 'Stop' },
    { price: data.entry,   color: '#3b82f6', title: 'Entry' },
    { price: data.target1, color: '#16a34a', title: 'Target 1' },
    { price: data.target2, color: '#4ade80', title: 'Target 2' },
  ];
  for (const line of lines) {
    candles.createPriceLine({
      price: line.price, color: line.color, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true, title: line.title,
    });
  }

  const markers = [{
    time: data.pickDate, position: 'belowBar', color: '#3b82f6', shape: 'arrowUp',
    text: `Pick · ${data.composite}`,
  }];
  if (data.triggeredOn) {
    markers.push({ time: data.triggeredOn, position: 'belowBar',
                   color: '#3b82f6', shape: 'circle', text: 'Entry' });
  }
  if (data.closedOn) {
    const win = data.status === 'TARGET1';
    markers.push({ time: data.closedOn, position: 'aboveBar',
                   color: win ? '#16a34a' : '#dc2626', shape: 'circle',
                   text: win ? 'Target 1' : 'Stop' });
  }
  if (data.earnings) {
    markers.push({ time: data.earnings, position: 'aboveBar',
                   color: '#a855f7', shape: 'square', text: 'Earnings' });
  }
  // v5: Marker laufen ueber ein Plugin, nicht mehr ueber setMarkers().
  LightweightCharts.createSeriesMarkers(candles, markers.sort((a, b) =>
    a.time < b.time ? -1 : 1));

  chart.timeScale().fitContent();
  return chart;
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-chart]').forEach(drawChart);
});
```

Prüfe die Marker-API gegen die installierte lightweight-charts-Version, bevor du sie festschreibst: Zwischen v4 und v5 ist `series.setMarkers()` zum Plugin `createSeriesMarkers()` geworden und `addCandlestickSeries()` zu `addSeries(CandlestickSeries, …)`. Maßgeblich ist, was in `node_modules/lightweight-charts/dist/typings.d.ts` steht, nicht was hier steht.

**Step 3: Verify**

Fixture einspielen, `http://127.0.0.1:8123/?path=r/2026-08-09/XYZ` öffnen.

Expected: Kerzen sichtbar, vier beschriftete Preislinien, Marker am Pick-Datum, Score-Balken mit Begründungssätzen, Bear-Case in Bernstein, Invalidierung in Rot. Prüfe die Konsole auf Fehler und schalte das Theme um — der Chart muss beim Neuladen die passenden Farben haben.

**Step 4: Commit**

```bash
git add portal/htdocs/lib/views/pick.php portal/htdocs/assets/chart.js
git commit -m "feat: pick detail page with chart, R ladder and score breakdown"
```

---

### Task 17: Performance-Seite

**Files:**
- Create: `portal/htdocs/lib/views/performance.php`

**Step 1: Implementation**

- Datumsfilter oben: zwei `<input type="date" name="from|to">` in einem GET-Formular
- vier KPI-Kacheln aus `$overall`: geschlossene Trades, Trefferquote, Erwartungswert in R, offen und nicht ausgelöst
- kumulierte R als Linienchart. Wiederverwendung von `chart.js` ist hier nicht möglich, weil es Kerzen zeichnet — ergänze in `chart.js` eine zweite Funktion `drawCurve(container)`, die auf `[data-curve]` reagiert und eine `LineSeries` mit `{time, value}` aus `$curve` zeichnet
- zwei Tabellen aus `$by_setup` und `$by_catalyst`: Name, Trades, Trefferquote, Erwartungswert. Erwartungswert positiv grün, negativ rot
- ist `$overall['trades'] === 0`, statt leerer Tabellen ein Hinweis: „Noch keine geschlossenen Trades. `review.py` und `publish.py --sync` schreiben Ausgänge, sobald das Beobachtungsfenster erreicht ist."

**Step 2: Verify**

`http://127.0.0.1:8123/?path=performance` mit eingespielten Fixture-Ausgängen. Vergleiche die Zahlen gegen `uv run python review.py --days 10` auf denselben Daten — sie müssen übereinstimmen.

**Step 3: Commit**

```bash
git add portal/htdocs/lib/views/performance.php portal/htdocs/assets/chart.js
git commit -m "feat: performance page with cumulative R curve"
```

---

### Task 18: `.htaccess`-Regeln

**Files:**
- Create: `portal/htdocs/.htaccess`, `portal/htdocs/lib/.htaccess`, `portal/htdocs/data/.htaccess`

**Step 1: Implementation**

`portal/htdocs/.htaccess` — `__AUTH_USER_FILE__` ersetzt `deploy.sh` durch den absoluten Pfad auf dem Server:

```apache
AuthType Basic
AuthName "stock-picker"
AuthUserFile __AUTH_USER_FILE__
Require valid-user

<FilesMatch "^\.(htaccess|htpasswd|env)$|\.(sqlite|sqlite-wal|sqlite-shm|db|log)$">
  Require all denied
</FilesMatch>

Options -Indexes
DirectoryIndex index.php

<IfModule mod_rewrite.c>
  RewriteEngine On
  RewriteBase /
  RewriteRule ^api/(.*)$ api.php?path=$1 [QSA,L]
  RewriteCond %{REQUEST_FILENAME} !-f
  RewriteCond %{REQUEST_FILENAME} !-d
  RewriteRule ^(.*)$ index.php?path=$1 [QSA,L]
</IfModule>

<IfModule mod_headers.c>
  Header always set X-Content-Type-Options "nosniff"
  Header always set X-Frame-Options "DENY"
  Header always set Referrer-Policy "no-referrer"
</IfModule>
```

`portal/htdocs/lib/.htaccess`:

```apache
Require all denied
```

Ein `Require all denied` innerhalb eines Blocks mit `Require valid-user` gewinnt nicht automatisch, weil Apache mehrere `Require`-Direktiven als ODER wertet — deshalb steht die Regel in einer eigenen Datei ohne konkurrierendes `Require`.

`data/` bekommt dieselbe Datei, aber **nicht aus dem Repository**. Das Verzeichnis ist gitignored und vom rsync ausgeschlossen, damit ein Update nicht die Datenbank löscht; eine dort abgelegte Datei käme nie auf dem Server an. `deploy.sh` legt sie beim Anlegen des Verzeichnisses mit an (Schritt 4). Unabhängig davon sperrt die `FilesMatch`-Regel der Wurzel bereits jede `.sqlite`- und `.log`-Datei über die Endung — die Verzeichnisregel ist die zweite Sicherung, nicht die einzige.

**Step 2: Commit**

```bash
git add portal/htdocs/.htaccess portal/htdocs/lib/.htaccess portal/htdocs/data/.htaccess
git commit -m "feat: htaccess auth, rewrite and deny rules"
```

---

### Task 19: Deploy-Skript

**Files:**
- Create: `portal/deploy.sh`, `portal/deploy.env.example`

**Step 1: Implementation**

`portal/deploy.env.example`:

```bash
# Kopiere nach deploy.env und trage deine Werte ein. deploy.env ist gitignored.
SSH_HOST=user@example.com
REMOTE_PATH=/home/user/htdocs
BASE_URL=https://picker.example.com
PORTAL_USER=picker
```

`portal/deploy.sh`:

```bash
#!/usr/bin/env bash
# Baut die Assets, laedt das Portal hoch, erzeugt beim ersten Mal ein
# Passwort und prueft danach, dass der Schutz tatsaechlich greift.
set -euo pipefail

cd "$(dirname "$0")"
ROTATE=false
[[ "${1:-}" == "--rotate-password" ]] && ROTATE=true

[[ -f deploy.env ]] || { echo "deploy.env fehlt — kopiere deploy.env.example."; exit 1; }
# shellcheck disable=SC1091
source deploy.env

for var in SSH_HOST REMOTE_PATH BASE_URL PORTAL_USER; do
  [[ -n "${!var:-}" ]] || { echo "deploy.env: $var ist leer."; exit 1; }
done

echo "→ 1/6  Assets bauen"
npm install --silent
npm run build --silent

echo "→ 2/6  Passwort"
HTPASSWD_REMOTE="$REMOTE_PATH/.htpasswd"
if $ROTATE || ! ssh "$SSH_HOST" "test -f '$HTPASSWD_REMOTE'"; then
  PASSWORD="$(openssl rand -base64 18)"
  if command -v htpasswd >/dev/null; then
    HASH_LINE="$(htpasswd -nbB "$PORTAL_USER" "$PASSWORD")"
  elif command -v php >/dev/null; then
    HASH="$(php -r 'echo password_hash($argv[1], PASSWORD_BCRYPT);' "$PASSWORD")"
    HASH_LINE="$PORTAL_USER:$HASH"
  else
    echo "Weder htpasswd noch php gefunden. Installiere apache2-utils oder php-cli."
    echo "Ein Fallback auf openssl passwd -apr1 waere MD5 — dafuer ist dieses"
    echo "Passwort zu langlebig."
    exit 1
  fi
  NEW_PASSWORD=true
else
  echo "   .htpasswd existiert bereits — Passwort bleibt. (--rotate-password erzwingt ein neues)"
  NEW_PASSWORD=false
fi

echo "→ 3/6  Upload"
# Ohne dieses Exclude loescht --delete den gesamten Ledger auf dem Server.
EXCLUDE="data/"
[[ -n "$EXCLUDE" ]] || { echo "EXCLUDE ist leer — Abbruch, sonst faellt data/ dem --delete zum Opfer."; exit 1; }

# .htaccess bekommt den absoluten Pfad zur .htpasswd auf dem Server.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp -R htdocs/. "$TMP/"
sed -i.bak "s#__AUTH_USER_FILE__#$HTPASSWD_REMOTE#" "$TMP/.htaccess" && rm -f "$TMP/.htaccess.bak"

rsync -az --delete --exclude "$EXCLUDE" --exclude '.gitkeep' "$TMP/" "$SSH_HOST:$REMOTE_PATH/"

if [[ "${NEW_PASSWORD}" == true ]]; then
  printf '%s\n' "$HASH_LINE" | ssh "$SSH_HOST" "cat > '$HTPASSWD_REMOTE' && chmod 640 '$HTPASSWD_REMOTE'"
fi

echo "→ 4/6  Erstinstallation prüfen"
ssh "$SSH_HOST" bash -s <<REMOTE
set -e
cd "$REMOTE_PATH"
if [ ! -d data ]; then
  mkdir -p data
  RAND=\$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')
  DB="data/portal-\$RAND.sqlite"
  sed -i "s#/../data/portal.sqlite#/../\$DB#" lib/config.php
  echo "   Datenbank angelegt: \$DB"
fi
# Immer schreiben, nicht nur bei Erstinstallation: die Datei kommt nicht
# aus dem rsync (data/ ist ausgeschlossen), und sie ist die zweite Sicherung
# hinter der FilesMatch-Regel der Wurzel-.htaccess.
printf 'Require all denied\n' > data/.htaccess
chmod 750 data lib
REMOTE

echo "→ 5/6  Schutz prüfen"
check() {
  local url="$1" expected="$2" label="$3" auth="${4:-}"
  local code
  if [[ -n "$auth" ]]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' -u "$auth" "$url")"
  else
    code="$(curl -s -o /dev/null -w '%{http_code}' "$url")"
  fi
  if [[ "$code" != "$expected" ]]; then
    echo "   ✗ $label: erwartet $expected, war $code  ($url)"
    return 1
  fi
  echo "   ✓ $label"
}

FAILED=0
check "$BASE_URL/api/health" 401 "API ohne Zugangsdaten abgewiesen" || FAILED=1
if [[ "${NEW_PASSWORD}" == true ]]; then
  check "$BASE_URL/api/health" 200 "API mit Zugangsdaten erreichbar" "$PORTAL_USER:$PASSWORD" || FAILED=1
fi
check "$BASE_URL/.htpasswd" 403 ".htpasswd gesperrt" || FAILED=1
DBFILE="$(ssh "$SSH_HOST" "ls '$REMOTE_PATH'/data/*.sqlite | head -1 | xargs basename")"
check "$BASE_URL/data/$DBFILE" 403 "Datenbank gesperrt" || FAILED=1

if [[ "$FAILED" != 0 ]]; then
  echo
  echo "Deployment gescheitert: der Schutz greift nicht wie erwartet."
  echo "Das Portal ist hochgeladen, aber moeglicherweise offen. Pruefe die"
  echo ".htaccess-Unterstuetzung des Hosters (AllowOverride) und wiederhole."
  exit 1
fi

echo "→ 6/6  Fertig"
if [[ "${NEW_PASSWORD}" == true ]]; then
  ENV_FILE="../.env"
  grep -v '^PORTAL_' "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null || true
  {
    cat "$ENV_FILE.tmp" 2>/dev/null || true
    echo "PORTAL_URL=$BASE_URL"
    echo "PORTAL_USER=$PORTAL_USER"
    echo "PORTAL_PASSWORD=$PASSWORD"
  } > "$ENV_FILE"
  rm -f "$ENV_FILE.tmp"

  cat <<BANNER

  ┌────────────────────────────────────────────────────────┐
  │  Zugangsdaten — sie werden nur dieses eine Mal gezeigt  │
  ├────────────────────────────────────────────────────────┤
     URL:      $BASE_URL
     Benutzer: $PORTAL_USER
     Passwort: $PASSWORD

     In ../.env geschrieben, publish.py findet sie dort.
  └────────────────────────────────────────────────────────┘

BANNER
else
  echo "   $BASE_URL ist aktuell. Zugangsdaten unveraendert."
fi
```

`chmod +x portal/deploy.sh` nicht vergessen. `portal/deploy.env` und die Zeile `PORTAL_*` in `.env` gehören in `.gitignore` — prüfe, dass `.env` dort bereits steht.

**Korrekturen, die bei der Umsetzung nötig wurden.** Der Entwurf oben hat zwei Fehler, die erst beim *zweiten* Lauf zuschlagen — also nach dem Deployment, das scheinbar funktioniert hat:

- **Die `.htpasswd` liegt in `REMOTE_PATH` und war nicht vom `rsync --delete` ausgenommen.** Sie wird nur beim Erstlauf geschrieben; jedes Update hätte sie gelöscht. Apache läuft dann in `AuthUserFile: cannot open` und liefert 500 — oder, wenn `AllowOverride` nicht greift, ein offenes Portal. Nötig: `--exclude "/.htpasswd"`.
- **`lib/config.php` wird bei jedem rsync mit der Repo-Fassung überschrieben**, die auf `data/portal.sqlite` zeigt. Der gewürfelte Datenbankname wird im Entwurf nur bei der Erstinstallation eingesetzt. Nach dem ersten Update hätte das Portal auf eine leere Datenbank gezeigt, während der Ledger unberührt daneben liegt. Der Remote-Block muss den vorhandenen `data/portal-*.sqlite` bei *jedem* Lauf ermitteln und den Pfad neu setzen.

Dazu drei kleinere Punkte: `sed` mit `#` als Trenner bricht bei einem Pfad mit `#` und geht bei einem `&` im Pfad still daneben (das `&` wird zum Suchtreffer) — Bash-Expansion statt `sed`. Die Nichtleer-Prüfung von `EXCLUDE` schützt nicht, weil sie eine Konstante gegen sich selbst prüft und einen Tippfehler wie `date/` durchwinken würde — stattdessen ein `rsync --delete --dry-run`, dessen Löschliste auf `data/` und `.htpasswd` geprüft wird, bevor irgendetwas übertragen wird. Und die 200-Prüfung sollte das Passwort aus der lokalen `.env` lesen, wenn keins neu erzeugt wurde; sonst belegt ein Update nur, dass das Portal jemanden abweist, nicht dass es die richtige Person durchlässt.

**Step 2: Verify**

```bash
bash -n portal/deploy.sh
npx --yes shellcheck portal/deploy.sh
```
Expected: keine Fehler, keine Meldungen

Ein echter Lauf braucht einen Server. Prüfbar ohne Server sind: der Abbruch bei unerreichbarem Host, bei fehlender `deploy.env`, bei relativem `REMOTE_PATH`; die Platzhalter-Ersetzung in der `.htaccess` gegen einen Pfad mit Sonderzeichen; der rsync-Wächter gegen einen nachgebauten Serverbaum; und `check()` gegen einen lokalen Basic-Auth-Server.

**Step 3: Commit**

```bash
git add portal/deploy.sh portal/deploy.env.example
git commit -m "feat: deployment with password generation and protection checks"
```

---

### Task 20: Test-Runner und Dokumentation

**Files:**
- Create: `portal/tests/run.sh`
- Modify: `README.md`
- Modify: `.gitignore`

**Step 1: Runner**

`portal/tests/run.sh`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

command -v php >/dev/null || { echo "php nicht gefunden — Portal-Tests uebersprungen."; exit 0; }

FAILED=0
for test in test_*.php; do
  echo "── $test"
  php "$test" || FAILED=1
done
exit $FAILED
```

**Step 2: README**

Im Abschnitt „Tagesablauf" den Schritt 2 anpassen (Report ist jetzt `.json`) und einen vierten Schritt ergänzen:

```markdown
# 4 — Ergebnisse ans Portal übergeben
uv run python publish.py out/report-2026-08-09.json

# täglich, für alle offenen Picks
uv run python publish.py --sync
```

Neuen Abschnitt „## Portal" anhängen: was es ist, wie deployt wird (`cp portal/deploy.env.example portal/deploy.env`, ausfüllen, `./portal/deploy.sh`), dass das Passwort einmalig angezeigt und in `.env` geschrieben wird, wie es rotiert wird, und wie die Tests laufen (`bash portal/tests/run.sh`).

In der Aufbau-Tabelle `publish.py` ergänzen und die `PROMPT.md`-Zeile auf „Stufe 2 — Rubrik, Arbeitsablauf und JSON-Format für das Modell" ändern.

**Step 3: `.gitignore`**

Ergänzen:

```
portal/deploy.env
portal/node_modules/
portal/htdocs/data/
portal/htdocs/assets/*.css
portal/htdocs/assets/*.js
```

**Step 4: Verify**

```bash
chmod +x portal/tests/run.sh
bash portal/tests/run.sh
uv run pytest
```
Expected: beide grün

**Step 5: Commit**

```bash
git add portal/tests/run.sh README.md .gitignore
git commit -m "docs: portal usage and test runner"
```

---

## Abschluss

Vor dem ersten scharfen Deployment abarbeiten:

- [ ] `uv run pytest` grün
- [ ] `bash portal/tests/run.sh` grün
- [ ] Ein echter Report durch die volle Kette: `validate.py` → `publish.py --dry-run` → `publish.py`
- [ ] Alle vier Schutzprüfungen im Deployment bestanden
- [ ] Chart in beiden Themes geprüft, Konsole ohne Fehler
- [ ] Performance-Seite gegen `review.py` gegengerechnet — dieselben Zahlen
- [ ] Passwort aus der Terminal-Historie entfernt, falls es dort gelandet ist

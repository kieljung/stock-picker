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

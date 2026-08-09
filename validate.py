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

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stockpicker import ledger, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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

    try:
        candidates_data = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Kandidaten {args.candidates} nicht lesbar: {exc}", file=sys.stderr)
        return 1

    date, picks = doc["date"], doc["picks"]
    candidates = candidates_data["candidates"]
    by_ticker = {c["ticker"]: c for c in candidates}

    errors = report.validate_document(doc, candidates)
    if errors:
        print("VALIDIERUNG FEHLGESCHLAGEN:", file=sys.stderr)
        for error in errors:
            print(f"  ✗ {error}", file=sys.stderr)
        print("\nKein Ledger-Eintrag geschrieben.", file=sys.stderr)
        return 1

    for pick in picks:
        for warning in ledger.warn_pick(pick, by_ticker[pick["ticker"]]):
            print(f"  ! {warning}", file=sys.stderr)

    if args.dry_run:
        print(f"OK: {len(picks)} Picks valide (dry-run, nichts geschrieben).")
        return 0

    try:
        count = ledger.append_entries(Path(args.ledger), date, picks, replace=args.replace)
    except ValueError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        print("Nutze --replace fuer einen korrigierten Report oder --dry-run zum Pruefen.",
              file=sys.stderr)
        return 1
    print(f"OK: {count} Picks fuer {date} in {args.ledger} geschrieben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

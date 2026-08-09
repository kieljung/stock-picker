"""Lesen einer .env im Projektwurzel. Bewusst ohne Zusatzabhaengigkeit.

Format: ``KEY=VALUE`` je Zeile, Kommentare mit ``#`` und Leerzeilen werden
uebersprungen, umschliessende Anfuehrungszeichen entfernt. Die Umgebung hat
immer Vorrang vor der Datei.

Zwei Module brauchen das: ``earnings`` fuer die Kalender-Keys und ``publish``
fuer den Portal-Zugang. Beide halten ihr eigenes ``DOTENV``-Modulattribut, das
Tests umbiegen -- der Pfad wird deshalb hier nie zwischengespeichert.
"""

from __future__ import annotations

import os
from pathlib import Path


def dotenv_values(path: Path | str) -> dict[str, str]:
    """Alle Paare der Datei. Fehlt die Datei, ist das Ergebnis leer."""
    path = Path(path)
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def read_key(name: str, dotenv: Path | str) -> str | None:
    """Ein Wert aus der Umgebung, sonst aus der .env. Leer gilt als fehlend."""
    from_env = os.environ.get(name)
    if from_env and from_env.strip():
        return from_env.strip()
    return dotenv_values(dotenv).get(name) or None

"""Aktien-Splits aus Alpacas Corporate-Actions-Endpunkt.

Warum das noetig ist: der Bar-Cache laedt nach dem ersten Lauf nur noch das
Delta ab der letzten gespeicherten Bar. yfinance rechnet seine Historie bei
einem Split aber rueckwirkend um -- die Bars vor dem Ex-Datum stehen dort
danach in der neuen Stueckelung, die laengst gespeicherten in der alten. Wer
nur das Delta holt, hat beides in derselben Tabelle. BRC Inc. (BRCC) stand
nach dem 1:10-Reverse-Split vom 24.08.2026 mit 0,88 USD am 21.08. und 8,55 USD
am 25.08. im Cache. Jeder gleitende Durchschnitt darauf ist Fiktion, und der
Preisfilter dreht sich um: vor dem Split lag der Titel unter ``min_price``,
nach dem Split darueber -- mit einem Zehnfach-"Ausbruch" in der Historie.

Alpaca statt yfinance, weil yfinance den Split nicht meldet, sondern
stillschweigend anders rechnet. Ein Ex-Datum ist die einzige Angabe, an der
sich der Bruch erkennen laesst, bevor er in den Indikatoren steckt.

Dieses Modul liefert nur die Ereignisse. Was daraus folgt -- Bars verwerfen,
Historie neu laden -- entscheidet ``market.purge_split_history``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from stockpicker import dotenv as dotenv_module

DOTENV = Path(".env")

KEY_NAME = "ALPACA_API_KEY"
SECRET_NAME = "ALPACA_SECRET_KEY"

#: Alle drei aendern die Stueckelung und damit jeden Kurs vor dem Ex-Datum.
SPLIT_TYPES = ("reverse_split", "forward_split", "unit_split")


def read_env_keys(dotenv: Path | None = None) -> tuple[str | None, str | None]:
    """Schluesselpaar aus der Umgebung, sonst aus der .env.

    Modulglobale erst hier aufloesen, nicht als Default-Argument -- sonst
    bindet Python den Pfad zur Definitionszeit und Tests koennen ihn nicht
    umlenken. Dieselbe Falle wie in ``earnings.read_env_key``.
    """
    path = DOTENV if dotenv is None else dotenv
    return (dotenv_module.read_key(KEY_NAME, path),
            dotenv_module.read_key(SECRET_NAME, path))


def parse_splits(payload: dict) -> set[tuple[str, dt.date]]:
    """(Ticker, Ex-Datum) aus einer Corporate-Actions-Antwort.

    Alpaca gruppiert nach Typ (``forward_splits``, ``reverse_splits``,
    ``unit_splits``). Jeder Eintrag nennt das alte Symbol, manche zusaetzlich
    ein neues; beide zaehlen, denn unter beiden koennen Bars im Cache liegen.

    Punkte werden zu Bindestrichen wie in ``universe.parse_holdings_csv``: der
    Cache kennt BRK-B, Alpaca meldet BRK.B. Eintraege ohne brauchbares Ex-Datum
    fallen weg -- ein Split ohne Datum sagt nicht, welche Bars betroffen sind.
    """
    found: set[tuple[str, dt.date]] = set()
    for key, entries in (payload or {}).items():
        if not key.endswith("_splits") or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ex_date = _date(entry.get("ex_date"))
            if ex_date is None:
                continue
            for field in ("symbol", "new_symbol"):
                symbol = (entry.get(field) or "").strip().replace(".", "-")
                if symbol:
                    found.add((symbol, ex_date))
    return found


def load_splits(
    start: dt.date,
    end: dt.date,
    *,
    api_key: str | None = None,
    secret_key: str | None = None,
) -> tuple[set[tuple[str, dt.date]], str]:
    """Alle Splits im Fenster. Wirft nie -- ein Lauf ist mehr wert als eine Pruefung.

    Die Warnung ist leer, solange die Abfrage funktioniert hat. Sie ist der
    einzige Hinweis darauf, dass die Pruefung ausgefallen ist: ohne sie sieht
    ein Lauf ohne Schluessel genauso aus wie einer an einem Tag ohne Splits.
    """
    if api_key is None or secret_key is None:
        from_env = read_env_keys()
        api_key = api_key or from_env[0]
        secret_key = secret_key or from_env[1]

    if not api_key or not secret_key:
        return set(), (f"{KEY_NAME}/{SECRET_NAME} nicht gesetzt — "
                       f"Splits werden nicht geprueft")

    try:
        return parse_splits(_fetch(api_key, secret_key, start, end)), ""
    except Exception as exc:
        return set(), f"Split-Abfrage fehlgeschlagen ({exc}) — Splits werden nicht geprueft"


def _fetch(api_key: str, secret_key: str, start: dt.date, end: dt.date) -> dict:
    """Der rohe Endpunkt-Aufruf, als eigene Funktion fuer die Tests.

    ``raw_data=True`` liefert dieselben Dicts, die auch ueber die Leitung
    kommen -- ``parse_splits`` bleibt damit ohne alpaca-py pruefbar. Ohne
    ``limit`` blaettert alpaca-py von sich aus bis zum Ende durch.

    Der Import steht absichtlich in der Funktion: so kostet ``import splits``
    nichts, und eine fehlende Installation wird zur Warnung aus
    ``load_splits`` statt zum Importfehler beim Start des Screeners.
    """
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest

    client = CorporateActionsClient(api_key=api_key, secret_key=secret_key, raw_data=True)
    return client.get_corporate_actions(
        CorporateActionsRequest(types=list(SPLIT_TYPES), start=start, end=end)
    )


def _date(raw) -> dt.date | None:
    try:
        return dt.date.fromisoformat(raw.strip())
    except (AttributeError, TypeError, ValueError):
        return None

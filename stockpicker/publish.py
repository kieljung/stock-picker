"""Uebergabe der Pipeline-Ergebnisse an das Portal.

Reine Payload-Funktionen plus ein duenner HTTP-Client. Die Payloads sind
absichtlich von der Uebertragung getrennt: so laesst sich pruefen, was
gesendet wuerde, ohne etwas zu senden.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from stockpicker import dotenv as dotenv_module

BAR_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

CLOSED_STATUS = {"TARGET1", "STOP", "NOT_TRIGGERED"}

DOTENV = Path(".env")

CREDENTIAL_NAMES = ("PORTAL_URL", "PORTAL_USER", "PORTAL_PASSWORD")

TIMEOUT = 30
RETRIES = 3
BACKOFF = 2.0


def bars_payload(history: dict[str, pd.DataFrame], *,
                 since: dt.date | None = None) -> dict[str, list[list]]:
    """OHLCV je Ticker als Array-of-Arrays.

    Objekte je Bar waeren lesbarer, kosten bei 125 Tagen mal fuenf Titeln
    aber rund das Dreifache an Payload. Die Spaltenreihenfolge ist fix:
    Datum, Open, High, Low, Close, Volume.

    ``since`` ist exklusiv: geliefert wird nur, was nach diesem Tag liegt --
    das Portal meldet den letzten Tag, den es schon hat.
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
    """Nur die Felder, die das Portal in picks zurueckschreibt.

    Eingabe sind die Rueckgaben von ``outcomes.evaluate``. ``triggered_on``
    und ``closed_on`` gehen mit: die Detailseite setzt daraus die Marker im
    Chart, und ohne sie muesste das Portal die Auswertung nachrechnen.
    """
    return [
        {"date": r["date"], "ticker": r["ticker"],
         "status": r["status"], "r_multiple": r.get("r_multiple"),
         "triggered_on": r.get("triggered_on"), "closed_on": r.get("closed_on")}
        for r in results
    ]


def open_picks(entries: list[dict]) -> list[dict]:
    """Ledger-Eintraege, deren Ausgang noch offen ist."""
    return [e for e in entries if e.get("status") not in CLOSED_STATUS]


def _f(value):
    """Float oder None. Nie NaN.

    ``json.dumps`` schreibt fuer NaN das Literal ``NaN``, das kein gueltiges
    JSON ist -- der PHP-Parser auf der Gegenseite bricht daran ab.
    """
    if value is None or pd.isna(value):
        return None
    return float(value)


# --------------------------------------------------------------------------
# Uebertragung
# --------------------------------------------------------------------------

class PushError(RuntimeError):
    """Das Portal hat die Uebergabe abgelehnt oder war nicht erreichbar."""


@dataclass(frozen=True)
class Credentials:
    url: str
    user: str
    password: str


def credentials() -> Credentials:
    """Portal-Zugang aus Umgebung oder .env. Die Umgebung gewinnt."""
    # DOTENV erst hier lesen, nicht beim Import: Tests biegen das
    # Modulattribut um, damit die Suite nicht von einer lokalen .env abhaengt.
    resolved = {name: dotenv_module.read_key(name, DOTENV) for name in CREDENTIAL_NAMES}
    missing = [name for name, value in resolved.items() if not value]
    if missing:
        raise RuntimeError(
            f"Portal-Zugang unvollstaendig: {', '.join(missing)} fehlt. "
            f"deploy.sh schreibt die Werte beim Deployment in .env."
        )
    return Credentials(resolved["PORTAL_URL"].rstrip("/"),
                       resolved["PORTAL_USER"], resolved["PORTAL_PASSWORD"])


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
            last_error = f"HTTP {response.status_code}: {_body(response)}"
            if response.status_code < 500:
                raise PushError(f"{url} -- {last_error}")

        if attempt < RETRIES:
            time.sleep(BACKOFF ** attempt)

    raise PushError(f"{url} -- nach {RETRIES} Versuchen aufgegeben. {last_error}")


def _body(response) -> str:
    """Die Fehlermeldung des Portals, lesbar gemacht.

    Das Portal antwortet auf abgelehnte Payloads mit ``details`` -- genau die
    Liste, die der Nutzer braucht. Alles andere kommt gekuerzt als Text.
    """
    try:
        data = response.json()
    except ValueError:
        return response.text[:500]
    if not isinstance(data, dict):
        return str(data)[:500]
    details = data.get("details")
    if details:
        return f"{data.get('error', 'Fehler')} — " + "; ".join(str(d) for d in details)
    return str(data)[:500]

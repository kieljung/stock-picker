"""Laedt das Handelsuniversum aus der oeffentlichen iShares-IWM-Holdings-Datei."""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

import requests

# Stand 2026-08-09: Der frueher uebliche ".ajax?fileType=csv"-Endpunkt liefert nur noch
# die HTML-Produktseite. iShares verlinkt die Holdings jetzt unter "latest-holdings.csv".
IWM_HOLDINGS_URL = (
    "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf"
    "/latest-holdings.csv"
)

DEFAULT_CACHE = Path("data/cache/iwm_holdings.csv")

_EXCLUDED_ASSET_CLASSES = {"money market", "cash"}


def _download(url: str, timeout: int) -> str:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "stock-picker/0.1"})
    response.raise_for_status()
    return response.text


def parse_holdings_csv(text: str) -> list[dict]:
    """Ueberspringt die Kopfzeilen der iShares-Datei und liest die Tabelle.

    Cash-, Geldmarkt- und Platzhalterzeilen fliegen raus. Punkte in Tickern
    werden zu Bindestrichen, weil Yahoo Finance BRK-B statt BRK.B erwartet.
    """
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("Ticker,")), None)
    if header_index is None:
        raise ValueError("Keine 'Ticker'-Kopfzeile in der Holdings-Datei gefunden")

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    rows = []
    for row in reader:
        ticker = (row.get("Ticker") or "").strip()
        asset_class = (row.get("Asset Class") or "").strip().lower()
        if not ticker or ticker == "-":
            continue
        if asset_class in _EXCLUDED_ASSET_CLASSES:
            continue
        rows.append(
            {
                "ticker": ticker.replace(".", "-"),
                "name": (row.get("Name") or "").strip(),
                "sector": (row.get("Sector") or "Unknown").strip(),
            }
        )
    return rows


def load_universe(
    *,
    cache_path: Path = DEFAULT_CACHE,
    max_age_days: int = 1,
    timeout: int = 30,
) -> tuple[list[dict], str]:
    """Gibt (Titel, Warnung) zurueck. Warnung ist leer, wenn alles frisch ist."""
    cache_path = Path(cache_path)

    if cache_path.exists() and max_age_days > 0:
        age = dt.datetime.now() - dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
        if age <= dt.timedelta(days=max_age_days):
            return parse_holdings_csv(cache_path.read_text(encoding="utf-8")), ""

    try:
        text = _download(IWM_HOLDINGS_URL, timeout)
    except Exception as exc:
        if cache_path.exists():
            warning = f"IWM-Holdings nicht abrufbar ({exc}) — verwende Cache vom {dt.datetime.fromtimestamp(cache_path.stat().st_mtime):%Y-%m-%d}"
            return parse_holdings_csv(cache_path.read_text(encoding="utf-8")), warning
        raise RuntimeError(f"IWM-Holdings nicht abrufbar und kein Cache vorhanden: {exc}") from exc

    rows = parse_holdings_csv(text)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return rows, ""

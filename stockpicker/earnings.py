"""Earnings-Termine aus mehreren oeffentlichen Kalendern.

Warum mehrere Quellen: yfinance kennt fuer rund ein Fuenftel der gescreenten
Titel keinen kommenden Termin und liegt gelegentlich grob daneben -- in einem
gemessenen Fall um drei Monate. Ein uebersehener Termin ist im Swing-Trading
teuer, weil ein Earnings-Gap jeden Stop ueberspringt und Stufe 3 das nicht
abfangen kann.

Die Zusammenfuehrung nimmt immer den **fruehesten** kommenden Termin. Das kann
einen Blackout nur hinzufuegen, nie entfernen. Die Kosten sind asymmetrisch:
ein ueberfluessiger Blackout kostet eine Gelegenheit, ein uebersehener kostet
einen Gap durch den Stop hindurch.

Kalender kennen nur *kommende* Termine. ``days_since_earnings`` fuer das
PEAD-Setup bleibt Sache von yfinance.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from pathlib import Path

import requests

from stockpicker import dotenv as dotenv_module

DOTENV = Path(".env")
CACHE_DIR = Path("data/cache")

ALPHAVANTAGE_URL = "https://www.alphavantage.co/query"
ALPHAVANTAGE_CACHE = CACHE_DIR / "earnings_alphavantage.csv"

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"
FINNHUB_CACHE = CACHE_DIR / "earnings_finnhub.json"
FINNHUB_WINDOW_DAYS = 30       # Free-Tier deckt einen Monat ab


class CalendarError(RuntimeError):
    """Der Dienst hat statt Nutzdaten eine Fehler- oder Limit-Meldung geliefert."""


# --------------------------------------------------------------------------
# Keys
# --------------------------------------------------------------------------

def read_env_key(name: str, dotenv: Path | None = None) -> str | None:
    """Liest einen Key aus der Umgebung, sonst aus einer .env im Projektwurzel.

    Das Parsen liegt in ``stockpicker.dotenv``, weil ``publish`` denselben
    Mechanismus fuer den Portal-Zugang braucht.
    """
    # Modulglobale erst hier aufloesen, nicht als Default-Argument: sonst
    # bindet Python den Pfad zur Definitionszeit und Tests koennen ihn nicht
    # umlenken -- was einen echten API-Call in der Suite bedeuten wuerde.
    return dotenv_module.read_key(name, DOTENV if dotenv is None else dotenv)


# --------------------------------------------------------------------------
# Alpha Vantage — ein Request deckt den ganzen Markt ab, 25 Requests pro Tag
# --------------------------------------------------------------------------

def _download_alphavantage(api_key: str, horizon: str, timeout: int) -> str:
    response = requests.get(
        ALPHAVANTAGE_URL,
        params={"function": "EARNINGS_CALENDAR", "horizon": horizon, "apikey": api_key},
        timeout=timeout,
        headers={"User-Agent": "stock-picker/0.1"},
    )
    response.raise_for_status()
    return response.text


def parse_alphavantage_csv(text: str) -> dict[str, dt.date]:
    """Ticker -> naechstes Reportdatum.

    Alpha Vantage antwortet bei ungueltigem Key oder erreichtem Tageslimit mit
    einem JSON-Objekt statt CSV. Das still als "keine Termine" zu behandeln
    waere gefaehrlich -- dann liefe der Screener ohne Earnings-Schutz weiter.
    """
    stripped = text.lstrip()
    if not stripped:
        raise CalendarError("Leere Antwort vom Alpha-Vantage-Kalender")
    if stripped.startswith("{"):
        raise CalendarError(f"Alpha Vantage meldet statt CSV: {stripped[:300]}")
    if not stripped.lower().startswith("symbol"):
        raise CalendarError(f"Unerwartetes Format im Alpha-Vantage-Kalender: {stripped[:200]}")

    out: dict[str, dt.date] = {}
    for row in csv.DictReader(io.StringIO(stripped)):
        _add(out, row.get("symbol"), row.get("reportDate"))
    return out


# --------------------------------------------------------------------------
# Finnhub — ein Request deckt ein Monatsfenster ab, 60 Requests pro Minute
# --------------------------------------------------------------------------

def _download_finnhub(api_key: str, start: dt.date, end: dt.date, timeout: int) -> str:
    response = requests.get(
        FINNHUB_URL,
        params={"from": start.isoformat(), "to": end.isoformat(), "token": api_key},
        timeout=timeout,
        headers={"User-Agent": "stock-picker/0.1"},
    )
    response.raise_for_status()
    return response.text


def parse_finnhub_json(text: str) -> dict[str, dt.date]:
    """Ticker -> naechstes Reportdatum aus der Finnhub-Antwort.

    Erwartet ``{"earningsCalendar": [{"symbol": ..., "date": ...}, ...]}``.
    Eine leere Liste ist ein gueltiges Ergebnis -- ein fehlender Schluessel
    dagegen ein Fehler, weil Finnhub Limit- und Auth-Probleme so meldet.
    """
    stripped = text.strip()
    if not stripped:
        raise CalendarError("Leere Antwort vom Finnhub-Kalender")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise CalendarError(f"Finnhub lieferte kein JSON: {stripped[:200]}") from exc
    if not isinstance(payload, dict) or "earningsCalendar" not in payload:
        raise CalendarError(f"Finnhub meldet statt Kalenderdaten: {stripped[:300]}")

    entries = payload.get("earningsCalendar") or []
    out: dict[str, dt.date] = {}
    for entry in entries:
        if isinstance(entry, dict):
            _add(out, entry.get("symbol"), entry.get("date"))
    return out


def _add(out: dict[str, dt.date], symbol, raw_date) -> None:
    """Uebernimmt eine Zeile, wenn Ticker und Datum brauchbar sind.

    Mehrfacheintraege pro Ticker kommen vor; der frueheste Termin gewinnt.
    """
    symbol = (symbol or "").strip().replace(".", "-")
    raw_date = (raw_date or "").strip()
    if not symbol or not raw_date:
        return
    try:
        date = dt.date.fromisoformat(raw_date)
    except ValueError:
        return
    if symbol not in out or date < out[symbol]:
        out[symbol] = date


# --------------------------------------------------------------------------
# Laden mit Cache-Fallback
# --------------------------------------------------------------------------

def _load_with_cache(
    *,
    label: str,
    key_name: str,
    cache_path: Path,
    parse,
    download,
    api_key: str | None,
    max_age_days: int,
) -> tuple[dict[str, dt.date], str]:
    """Gemeinsame Lade-Logik. Wirft nie -- der Screener laeuft auch ohne."""
    cache_path = Path(cache_path)
    api_key = api_key or read_env_key(key_name)

    def from_cache(prefix: str) -> tuple[dict[str, dt.date], str]:
        if not cache_path.exists():
            return {}, f"{prefix} — kein Cache vorhanden, {label} liefert nichts"
        stamp = dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
        try:
            return parse(cache_path.read_text(encoding="utf-8")), f"{prefix} — Cache vom {stamp:%Y-%m-%d}"
        except CalendarError as exc:
            return {}, f"{prefix} — Cache unbrauchbar ({exc})"

    if cache_path.exists() and max_age_days > 0:
        age = dt.datetime.now() - dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
        if age <= dt.timedelta(days=max_age_days):
            try:
                return parse(cache_path.read_text(encoding="utf-8")), ""
            except CalendarError:
                pass  # kaputter Cache -> neu laden

    if not api_key:
        return from_cache(f"{key_name} nicht gesetzt")

    try:
        text = download(api_key)
        calendar = parse(text)
    except Exception as exc:
        return from_cache(f"{label} nicht abrufbar ({exc})")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return calendar, ""


def load_alphavantage_calendar(
    *, api_key: str | None = None, cache_path: Path = ALPHAVANTAGE_CACHE,
    max_age_days: int = 1, horizon: str = "3month", timeout: int = 30,
) -> tuple[dict[str, dt.date], str]:
    return _load_with_cache(
        label="Alpha-Vantage-Kalender",
        key_name="ALPHAVANTAGE_API_KEY",
        cache_path=cache_path,
        parse=parse_alphavantage_csv,
        download=lambda key: _download_alphavantage(key, horizon, timeout),
        api_key=api_key,
        max_age_days=max_age_days,
    )


def load_finnhub_calendar(
    *, api_key: str | None = None, cache_path: Path = FINNHUB_CACHE,
    max_age_days: int = 1, window_days: int = FINNHUB_WINDOW_DAYS, timeout: int = 30,
    today: dt.date | None = None,
) -> tuple[dict[str, dt.date], str]:
    start = today or dt.date.today()
    end = start + dt.timedelta(days=window_days)
    return _load_with_cache(
        label="Finnhub-Kalender",
        key_name="FINNHUB_API_KEY",
        cache_path=cache_path,
        parse=parse_finnhub_json,
        download=lambda key: _download_finnhub(key, start, end, timeout),
        api_key=api_key,
        max_age_days=max_age_days,
    )


def load_all_calendars(**kwargs) -> tuple[dict[str, dict[str, dt.date]], list[str]]:
    """Alle konfigurierten Kalender. Fehlende Quellen fehlen einfach."""
    calendars: dict[str, dict[str, dt.date]] = {}
    warnings: list[str] = []
    for name, loader in (
        ("alphavantage", load_alphavantage_calendar),
        ("finnhub", load_finnhub_calendar),
    ):
        calendar, warning = loader(**kwargs)
        if calendar:
            calendars[name] = calendar
        if warning:
            warnings.append(warning)
    return calendars, warnings


# --------------------------------------------------------------------------
# Zusammenfuehrung
# --------------------------------------------------------------------------

def merge_into_meta(
    meta: dict[str, dict],
    calendars: dict[str, dict[str, dt.date]],
    *,
    today: dt.date | None = None,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Setzt ``next_earnings`` auf den fruehesten bekannten kommenden Termin.

    ``earnings_source`` nennt alle Quellen, die genau dieses Datum liefern --
    etwa ``yfinance+finnhub``. Uebereinstimmung mehrerer Quellen ist das
    verlaessliche Signal; steht dort nur eine, widersprechen sich die anderen.
    """
    today = today or dt.date.today()
    sources = ["yfinance", *sorted(calendars)]
    stats: dict[str, int] = {f"kennt_{name}": 0 for name in sources}
    stats.update({"einig": 0, "uneinig": 0, "nur_eine_quelle": 0, "ohne_termin": 0})

    for ticker, entry in meta.items():
        found: dict[str, dt.date] = {}

        yf_date = entry.get("next_earnings")
        if yf_date and yf_date >= today:
            found["yfinance"] = yf_date
        for name, calendar in calendars.items():
            date = calendar.get(ticker)
            if date and date >= today:
                found[name] = date

        for name in found:
            stats[f"kennt_{name}"] += 1

        if not found:
            entry["next_earnings"] = None
            entry["earnings_source"] = None
            stats["ohne_termin"] += 1
            continue

        earliest = min(found.values())
        agreeing = sorted(name for name, date in found.items() if date == earliest)
        entry["next_earnings"] = earliest
        entry["earnings_source"] = "+".join(agreeing)

        if len(found) == 1:
            stats["nur_eine_quelle"] += 1
        elif len(agreeing) == len(found):
            stats["einig"] += 1
        else:
            stats["uneinig"] += 1

    return meta, stats

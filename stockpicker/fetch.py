"""Kapselung um yfinance. Die einzige Stelle im Projekt mit Marktdaten-Netzwerk.

Getestet gegen yfinance 1.5.2. Diese Version liefert bei ``yf.download`` immer
MultiIndex-Spalten (Ticker, Price) -- auch bei einem einzelnen Ticker -- und
haengt bei ``auto_adjust=False`` zusaetzlich eine "Adj Close"-Spalte an. Beides
wird hier weggeraeumt, damit die Pipeline stets flache Spalten in fester
Reihenfolge bekommt.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import yfinance as yf

BATCH_SIZE = 150

#: Spalten, die die Pipeline erwartet -- exakt diese, in dieser Reihenfolge.
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _normalize(frame: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """Macht einen Roh-Frame flach und auf COLUMNS beschnitten.

    Gibt None zurueck, wenn fuer den Ticker keine brauchbaren Daten vorliegen
    (unbekanntes Symbol, delistet, leerer Zeitraum).
    """
    if frame is None or frame.empty:
        return None

    columns = frame.columns
    if isinstance(columns, pd.MultiIndex):
        # yfinance 1.x: (Ticker, Price). Bei group_by="column" andersherum.
        level = 0
        if ticker not in columns.get_level_values(0):
            if ticker in columns.get_level_values(1):
                level = 1
            else:
                return None
        frame = frame.xs(ticker, axis=1, level=level, drop_level=True)
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(-1)

    if not set(COLUMNS).issubset(frame.columns):
        return None

    frame = frame.loc[:, COLUMNS].dropna(how="all")
    if frame.empty:
        return None

    frame = frame.sort_index()
    frame.columns = list(COLUMNS)
    frame.columns.name = None
    return frame


def fetch_history(tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    """Laedt Tageskerzen in Batches. Titel ohne Daten fehlen im Ergebnis.

    ``auto_adjust=False``: Die Kurse entsprechen den tatsaechlich gehandelten
    Niveaus, damit Entry- und Stop-Levels zu dem passen, was ein Chart zeigt.
    """
    out: dict[str, pd.DataFrame] = {}
    for start in range(0, len(tickers), BATCH_SIZE):
        batch = list(tickers[start : start + BATCH_SIZE])
        if not batch:
            continue
        try:
            raw = yf.download(
                batch,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        for ticker in batch:
            try:
                frame = _normalize(raw, ticker)
            except (KeyError, IndexError, ValueError):
                continue
            if frame is not None:
                out[ticker] = frame
    return out


def fetch_history_range(tickers: list[str], start, end) -> dict[str, pd.DataFrame]:
    """Wie ``fetch_history``, aber fuer ein festes Datumsfenster.

    ``start`` ist inklusiv, ``end`` exklusiv -- die Konvention von yfinance.
    Vertrag identisch zu ``fetch_history``: Dict Ticker -> DataFrame mit exakt
    den Spalten aus ``COLUMNS``; Titel ohne brauchbare Daten fehlen einfach.
    """
    out: dict[str, pd.DataFrame] = {}
    for batch_start in range(0, len(tickers), BATCH_SIZE):
        batch = list(tickers[batch_start : batch_start + BATCH_SIZE])
        if not batch:
            continue
        try:
            raw = yf.download(
                batch,
                start=start,
                end=end,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        for ticker in batch:
            try:
                frame = _normalize(raw, ticker)
            except (KeyError, IndexError, ValueError):
                continue
            if frame is not None:
                out[ticker] = frame
    return out


def fetch_meta(tickers: list[str]) -> dict[str, dict]:
    """Marktkapitalisierung, Short Float sowie letzter und naechster Earnings-Termin.

    yfinance liefert diese Felder unzuverlaessig. Fehlende Werte werden als
    None zurueckgegeben; die Pipeline darf daran nicht scheitern.
    """
    out: dict[str, dict] = {}
    for ticker in tickers:
        info: dict = {}
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            info = {}
        if not isinstance(info, dict):
            info = {}
        out[ticker] = {
            "market_cap": info.get("marketCap"),
            "short_percent_of_float": info.get("shortPercentOfFloat"),
            "next_earnings": _next_earnings(info),
            "last_earnings": _last_earnings(info),
        }
    return out


def _to_date(timestamp) -> dt.date | None:
    if not timestamp:
        return None
    try:
        return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).date()
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def _next_earnings(info: dict) -> dt.date | None:
    """Bevorzugt den naechsten zukuenftigen Termin.

    ``earningsTimestamp`` zeigt bei yfinance 1.5.2 oft auf den zuletzt
    gemeldeten Termin, waehrend ``earningsTimestampStart`` den kommenden
    haelt. Wir nehmen deshalb das frueheste Datum, das nicht in der
    Vergangenheit liegt. Ist kein zukuenftiger Termin bekannt, geben wir None
    zurueck statt eines vergangenen Datums -- ein "naechster" Termin in der
    Vergangenheit wuerde die Analyse in die Irre fuehren.
    """
    keys = ("earningsTimestamp", "earningsTimestampStart", "earningsTimestampEnd")
    dates = [d for d in (_to_date(info.get(key)) for key in keys) if d is not None]
    today = dt.date.today()
    future = [d for d in dates if d >= today]
    return min(future) if future else None


def _last_earnings(info: dict) -> dt.date | None:
    """Der zuletzt gemeldete Termin -- Basis fuer ``days_since_earnings``.

    ``earningsTimestamp`` haelt bei yfinance 1.5.2 in der Regel den zuletzt
    gemeldeten Termin, ``earningsTimestampStart``/``-End`` den kommenden. Wir
    nehmen deshalb das spaeteste Datum, das nicht in der Zukunft liegt, und
    geben None zurueck, wenn es keines gibt. Fehlende oder kaputte Werte
    fuehren nie zu einer Exception.
    """
    keys = ("earningsTimestamp", "earningsTimestampStart", "earningsTimestampEnd")
    dates = [d for d in (_to_date(info.get(key)) for key in keys) if d is not None]
    past = [d for d in dates if d <= dt.date.today()]
    return max(past) if past else None

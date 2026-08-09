"""SQLite-Persistenz fuer Kurshistorien und Metadaten.

Reine Speicherschicht: kein Netzwerk, keine Fetch-Logik. Das macht sie
vollstaendig offline testbar und haelt die Orchestrierung in market.py.

Zweck ist nicht nur Geschwindigkeit. yfinance' ``info``-Endpunkt antwortet
unter Last unvollstaendig -- zwischen zwei Laeufen im Abstand von zwanzig
Minuten schwankte die Zahl der Titel mit bekanntem Earnings-Termin um 62.
Ein gespeicherter Wert von gestern ist fuer einen Filter mit Grenzen bei
300 Mio und 3 Mrd USD ausreichend; ein fehlender wirft den Titel dagegen
aus dem Screening.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pandas as pd

DEFAULT_DB = Path("data/cache/market.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS bars_ticker ON bars(ticker);

CREATE TABLE IF NOT EXISTS meta (
    ticker                 TEXT PRIMARY KEY,
    fetched_at             TEXT NOT NULL,
    market_cap             REAL,
    short_percent_of_float REAL,
    next_earnings          TEXT,
    last_earnings          TEXT
);
"""

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
META_FIELDS = ["market_cap", "short_percent_of_float", "next_earnings", "last_earnings"]


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    """Oeffnet die Datenbank und legt das Schema an, falls noetig."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


# --------------------------------------------------------------------------
# Kursdaten
# --------------------------------------------------------------------------

def last_bar_dates(conn: sqlite3.Connection) -> dict[str, dt.date]:
    """Letztes gespeichertes Datum je Ticker -- die Basis fuer Delta-Ladungen."""
    rows = conn.execute("SELECT ticker, MAX(date) FROM bars GROUP BY ticker").fetchall()
    return {ticker: dt.date.fromisoformat(date) for ticker, date in rows if date}


def save_bars(conn: sqlite3.Connection, history: dict[str, pd.DataFrame]) -> int:
    """Schreibt Bars idempotent. Bereits vorhandene Tage werden ueberschrieben."""
    rows = []
    for ticker, frame in history.items():
        if frame is None or frame.empty:
            continue
        for index, bar in frame.iterrows():
            date = index.date() if hasattr(index, "date") else index
            rows.append((
                ticker, date.isoformat(),
                _f(bar.get("Open")), _f(bar.get("High")), _f(bar.get("Low")),
                _f(bar.get("Close")), _f(bar.get("Volume")),
            ))
    if rows:
        conn.executemany(
            "INSERT INTO bars (ticker, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker, date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume",
            rows,
        )
        conn.commit()
    return len(rows)


def load_bars(conn: sqlite3.Connection, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Vollstaendige Historie je Ticker, aufsteigend sortiert."""
    out: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM bars "
            "WHERE ticker = ? ORDER BY date",
            (ticker,),
        ).fetchall()
        if not rows:
            continue
        frame = pd.DataFrame(rows, columns=["date", *[c.lower() for c in COLUMNS]])
        frame.index = pd.to_datetime(frame.pop("date"))
        frame.columns = COLUMNS
        frame.index.name = None
        out[ticker] = frame
    return out


# --------------------------------------------------------------------------
# Metadaten
# --------------------------------------------------------------------------

def save_meta(conn: sqlite3.Connection, meta: dict[str, dict], *, now: dt.datetime | None = None) -> int:
    """Schreibt Metadaten mit Zeitstempel.

    Felder mit ``None`` ueberschreiben einen vorhandenen Wert **nicht** --
    genau das ist der Schutz gegen die unvollstaendigen yfinance-Antworten.
    """
    now = (now or dt.datetime.now()).isoformat(timespec="seconds")
    written = 0
    for ticker, entry in meta.items():
        conn.execute(
            "INSERT INTO meta (ticker, fetched_at, market_cap, short_percent_of_float, "
            "next_earnings, last_earnings) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "fetched_at=excluded.fetched_at, "
            "market_cap=COALESCE(excluded.market_cap, meta.market_cap), "
            "short_percent_of_float=COALESCE(excluded.short_percent_of_float, meta.short_percent_of_float), "
            "next_earnings=COALESCE(excluded.next_earnings, meta.next_earnings), "
            "last_earnings=COALESCE(excluded.last_earnings, meta.last_earnings)",
            (
                ticker, now,
                _f(entry.get("market_cap")),
                _f(entry.get("short_percent_of_float")),
                _iso(entry.get("next_earnings")),
                _iso(entry.get("last_earnings")),
            ),
        )
        written += 1
    conn.commit()
    return written


def load_meta(
    conn: sqlite3.Connection,
    tickers: list[str],
    *,
    max_age_days: float | None = None,
    now: dt.datetime | None = None,
) -> dict[str, dict]:
    """Gespeicherte Metadaten. ``max_age_days`` filtert auf frische Eintraege."""
    now = now or dt.datetime.now()
    wanted = set(tickers)
    out: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT ticker, fetched_at, market_cap, short_percent_of_float, "
        "next_earnings, last_earnings FROM meta"
    ):
        ticker, fetched_at, cap, short, next_e, last_e = row
        if ticker not in wanted:
            continue
        if max_age_days is not None:
            age = now - dt.datetime.fromisoformat(fetched_at)
            if age > dt.timedelta(days=max_age_days):
                continue
        out[ticker] = {
            "market_cap": cap,
            "short_percent_of_float": short,
            "next_earnings": dt.date.fromisoformat(next_e) if next_e else None,
            "last_earnings": dt.date.fromisoformat(last_e) if last_e else None,
        }
    return out


# --------------------------------------------------------------------------

def _f(value):
    if value is None or value != value:      # None oder NaN
        return None
    return float(value)


def _iso(value):
    return value.isoformat() if isinstance(value, dt.date) else (value or None)

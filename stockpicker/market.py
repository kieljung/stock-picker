"""Orchestriert Cache und Netzwerk: erst nachsehen, dann nur die Luecke holen.

Der Screener laedt sonst bei jedem Lauf zwei Jahre Tageskerzen fuer 1950
Titel und 907 Metadaten-Einzelabfragen -- obwohl sich seit gestern genau eine
Kerze geaendert hat. Wichtiger als die Zeitersparnis ist die Reproduzier-
barkeit: yfinance' ``info`` antwortet unter Last unvollstaendig, und ein
fehlender Wert wirft einen Titel aus dem Screening.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict

import pandas as pd

from stockpicker import fetch, store

# Ueberlappung beim Delta-Laden: deckt lange Wochenenden, Feiertage und
# nachtraegliche Korrekturen ab, ohne die volle Historie zu ziehen.
DELTA_OVERLAP_DAYS = 5


def last_expected_session(today: dt.date) -> dt.date:
    """Der juengste Wochentag am oder vor ``today``.

    Ohne das gilt der Cache am Wochenende als veraltet und jeder Lauf laedt
    umsonst nach. Feiertage sind hier nicht beruecksichtigt -- dann wird
    einmal vergeblich geladen, was die Ueberlappung ohnehin auffaengt.
    """
    while today.weekday() >= 5:      # 5 = Samstag, 6 = Sonntag
        today -= dt.timedelta(days=1)
    return today


def get_history(
    tickers: list[str],
    conn: sqlite3.Connection,
    *,
    full_period: str = "2y",
    today: dt.date | None = None,
    verbose: bool = False,
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    """Historie aus dem Cache, fehlende Zeitraeume werden nachgeladen."""
    today = today or dt.date.today()
    known = store.last_bar_dates(conn)
    expected = last_expected_session(today)

    missing = [t for t in tickers if t not in known]
    stale: dict[dt.date, list[str]] = defaultdict(list)
    for ticker in tickers:
        last = known.get(ticker)
        if last is not None and last < expected:
            stale[last].append(ticker)

    stats = {
        "aus_cache": len(tickers) - len(missing),
        "voll_geladen": 0,
        "delta_geladen": 0,
        "neue_bars": 0,
    }

    if missing:
        if verbose:
            print(f"  {len(missing)} Titel ohne Cache — lade {full_period}")
        fetched = fetch.fetch_history(missing, period=full_period)
        stats["neue_bars"] += store.save_bars(conn, fetched)
        stats["voll_geladen"] = len(fetched)

    # Nach letztem bekanntem Datum gruppieren: die meisten Titel teilen sich
    # dasselbe, also genuegen ein bis zwei Range-Abfragen statt einer je Titel.
    for last_date, group in stale.items():
        start = last_date - dt.timedelta(days=DELTA_OVERLAP_DAYS)
        if verbose:
            print(f"  {len(group)} Titel aktuell bis {last_date} — lade ab {start}")
        fetched = fetch.fetch_history_range(group, start=start, end=today + dt.timedelta(days=1))
        stats["neue_bars"] += store.save_bars(conn, fetched)
        stats["delta_geladen"] += len(fetched)

    return store.load_bars(conn, tickers), stats


def get_meta(
    tickers: list[str],
    conn: sqlite3.Connection,
    *,
    max_age_days: float = 5.0,
    now: dt.datetime | None = None,
    verbose: bool = False,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Metadaten aus dem Cache, veraltete werden neu geholt.

    Eine leere Antwort ueberschreibt keinen gespeicherten Wert -- ``save_meta``
    setzt nur Felder, die tatsaechlich gefuellt sind. Ein Titel, fuer den
    yfinance heute nichts liefert, behaelt damit seine Marktkapitalisierung
    von vorgestern statt aus dem Screening zu fallen.
    """
    now = now or dt.datetime.now()
    fresh = store.load_meta(conn, tickers, max_age_days=max_age_days, now=now)
    outdated = [t for t in tickers if t not in fresh]

    stats = {"aus_cache": len(fresh), "neu_geladen": 0, "nur_aus_cache_gerettet": 0}

    if outdated:
        if verbose:
            print(f"  {len(fresh)} Metadaten aus Cache, {len(outdated)} werden geladen")
        fetched = fetch.fetch_meta(outdated)
        store.save_meta(conn, fetched, now=now)
        stats["neu_geladen"] = len(fetched)

        # Nach dem Schreiben erneut lesen: so gewinnen zusammengefuehrte Werte
        # aus Cache und frischer Antwort, statt dass Luecken durchschlagen.
        merged = store.load_meta(conn, outdated)
        for ticker, entry in merged.items():
            if fetched.get(ticker, {}).get("market_cap") is None and entry.get("market_cap") is not None:
                stats["nur_aus_cache_gerettet"] += 1
        fresh.update(merged)

    return {t: fresh.get(t, {}) for t in tickers}, stats

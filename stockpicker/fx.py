"""EUR/USD-Tageskurse -- die Bruecke zwischen Datenlage und Broker.

Die Pipeline rechnet durchgaengig in USD: yfinance liefert USD, Indikatoren,
Stops und R-Multiples entstehen daraus, und die Auswertung im Ledger
vergleicht USD mit USD. Das bleibt so. Ein in EUR gerechneter Stop wuerde
ausloesen, weil der Euro gestiegen ist -- eine Bewegung, die mit dem Titel
nichts zu tun hat.

Gehandelt wird trotzdem in EUR. Dieses Modul liefert deshalb nur den Kurs,
mit dem das Portal die USD-Niveaus fuer die Orderanzeige umrechnet: eine
Anzeigeschicht ueber unveraenderten Daten.

``rate`` ist immer USD je EUR -- die Notierung, die yfinance unter
``EURUSD=X`` fuehrt. Ein Kurs in EUR ist also ``usd / rate``.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd

from stockpicker import market, store

#: yfinance-Symbol des Kassakurses. Liegt im selben Bar-Cache wie die Titel:
#: dieselbe Delta-Logik, dieselbe Ueberlappung, kein zweites Schema.
SYMBOL = "EURUSD=X"


def get_rates(conn: sqlite3.Connection, *, offline: bool = False,
              verbose: bool = False) -> dict[dt.date, float]:
    """Tageskurse aus dem Cache; fehlende Tage werden nachgeladen.

    ``offline`` liest allein den Cache -- der Vertrag von ``--dry-run``.
    """
    if offline:
        history = store.load_bars(conn, [SYMBOL])
    else:
        history, _ = market.get_history([SYMBOL], conn, verbose=verbose)
    return _series(history.get(SYMBOL))


def _series(frame: pd.DataFrame | None) -> dict[dt.date, float]:
    """Schlusskurse als Datum -> Kurs. Luecken und NaN fallen weg."""
    if frame is None or frame.empty:
        return {}
    rates: dict[dt.date, float] = {}
    for index, bar in frame.iterrows():
        close = bar.get("Close")
        if close is None or pd.isna(close) or float(close) <= 0:
            continue
        date = index.date() if hasattr(index, "date") else index
        rates[date] = float(close)
    return rates


def rate_on(rates: dict[dt.date, float], date: dt.date) -> float | None:
    """Der juengste Kurs am oder vor ``date``.

    Der Devisenmarkt und die US-Boerse haben nicht dieselben Feiertage. Ein
    Report vom Thanksgiving-Freitag faende sonst keinen Kurs, obwohl der vom
    Vortag die richtige Auskunft ist.
    """
    candidates = [d for d in rates if d <= date]
    return rates[max(candidates)] if candidates else None


def to_eur(usd: float | None, rate: float | None) -> float | None:
    """USD-Betrag in EUR. Ohne Kurs gibt es keine Zahl, nur None."""
    if usd is None or rate is None or rate <= 0:
        return None
    return float(usd) / float(rate)

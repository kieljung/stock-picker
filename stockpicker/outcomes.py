"""Auswertung der Ledger-Eintraege gegen den tatsaechlichen Kursverlauf."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

import pandas as pd

from stockpicker.config import TRADE

#: Ausgaenge, die als Trade in Trefferquote und Erwartungswert eingehen.
#:
#: TIME_STOP gehoert dazu, und daran haengt mehr als ein Statuswert: der Stop
#: liegt rund 1,5 ATR vom Entry entfernt, Target 1 rund 3 ATR. Im Fenster von
#: ``hold_days_max`` loest der Stop deshalb viel haeufiger auf als das Ziel.
#: Zaehlte nur, was einen der beiden Punkte beruehrt hat, fielen die zaehen
#: Gewinner als "noch offen" aus der Rechnung und die Bilanz zeigte
#: systematisch zu viel Verlust. Wer nach ``hold_days_max`` noch lebt, wird
#: zum Schlusskurs abgerechnet -- so, wie man ihn auch verkaufen wuerde.
TRADE_STATUS = ("TARGET1", "STOP", "TIME_STOP")

CATALYST_BUCKETS = [
    ("stark (7-10)", 7, 10),
    ("mittel (4-6)", 4, 6),
    ("schwach (0-3)", 0, 3),
]


def evaluate(pick: dict, bars: pd.DataFrame, trigger_window: int = 3, *,
             hold_days: int = TRADE.hold_days_max,
             as_of: dt.date | None = None) -> dict:
    """Bestimmt den Ausgang eines Picks.

    Ablauf: Der Trade gilt als eroeffnet, sobald das Hoch innerhalb der ersten
    `trigger_window` Bars den Entry erreicht. Danach entscheidet, ob zuerst der
    Stop oder Target 1 beruehrt wird. Beruehrt eine einzelne Bar beides, zaehlt
    konservativ der Stop — aus Tagesdaten laesst sich die Reihenfolge nicht
    rekonstruieren.

    Beruehrt er keinen von beiden, entscheidet der Kalender: ist das Fenster
    von ``hold_days`` vorbei, wird der Trade zum letzten Schlusskurs
    abgerechnet (``TIME_STOP``), sonst laeuft er noch (``OPEN``). Der
    Unterschied ist nicht kosmetisch — nur ``TIME_STOP`` zaehlt in der Bilanz,
    ``OPEN`` ist ein Zwischenstand.

    ``as_of`` ist der Tag, bis zu dem Kurse bekannt sind; ohne Angabe heute.
    Es entscheidet der Kalender, nicht die letzte Bar im Rahmen: faellt das
    Fensterende auf ein Wochenende, liegt die letzte Bar davor — der Trade
    bliebe sonst fuer immer „offen".

    ``triggered_on`` und ``closed_on`` nennen die Bars, an denen das passiert
    ist, als ISO-Datum oder None. Das Portal setzt daraus die Marker im Chart;
    ohne sie muesste es die Auswertung nachbauen, um zu wissen, wo sie hin
    gehoeren.
    """
    entry, stop, target1 = pick["entry"], pick["stop"], pick["target1"]
    risk = entry - stop

    trigger_index = None
    for i in range(min(trigger_window, len(bars))):
        if bars["High"].iloc[i] >= entry:
            trigger_index = i
            break

    base = {
        "ticker": pick.get("ticker"),
        "date": pick.get("date"),
        "setup": pick.get("setup"),
        "score_catalyst": pick.get("score_catalyst"),
        "triggered_on": None,
        "closed_on": None,
    }

    if trigger_index is None:
        # Ohne Eroeffnung gibt es weder einen Einstiegs- noch einen
        # Ausstiegspunkt -- beide Daten bleiben leer.
        return {**base, "status": "NOT_TRIGGERED", "r_multiple": 0.0}

    base["triggered_on"] = bar_date(bars, trigger_index)

    for i in range(trigger_index, len(bars)):
        hit_stop = bars["Low"].iloc[i] <= stop
        hit_target = bars["High"].iloc[i] >= target1
        if hit_stop:
            return {**base, "status": "STOP", "r_multiple": -1.0,
                    "closed_on": bar_date(bars, i)}
        if hit_target:
            return {**base, "status": "TARGET1", "closed_on": bar_date(bars, i),
                    "r_multiple": round((target1 - entry) / risk, 2)}

    last_close = float(bars["Close"].iloc[-1])
    r_multiple = round((last_close - entry) / risk, 2)
    if window_expired(pick.get("date"), hold_days, as_of):
        return {**base, "status": "TIME_STOP", "r_multiple": r_multiple,
                "closed_on": bar_date(bars, len(bars) - 1)}
    return {**base, "status": "OPEN", "r_multiple": r_multiple}


def window_expired(pick_date: str | None, hold_days: int,
                   as_of: dt.date | None = None) -> bool:
    """Ob ein Pick seine vollen ``hold_days`` schon hinter sich hat.

    Streng groesser: die letzte Bar, die noch ins Fenster faellt, liegt auf
    ``pick_date + hold_days``. Am Tag selbst kann sie noch fehlen -- der Sync
    laeuft morgens und sieht die Schlusskurse des Vortags. Einen Tag spaeter
    ist das Fenster sicher vollstaendig.
    """
    if not pick_date:
        return False
    end = dt.date.fromisoformat(pick_date) + dt.timedelta(days=hold_days)
    return (as_of or dt.date.today()) > end


def bar_date(bars: pd.DataFrame, index: int) -> str | None:
    """Das Datum einer Bar als ISO-String.

    Der Index ist je nach Herkunft ein Timestamp (yfinance, SQLite-Cache)
    oder ein nacktes date -- beides muss dasselbe Ergebnis liefern.
    """
    label = bars.index[index]
    date = label.date() if hasattr(label, "date") else label
    return date.isoformat() if hasattr(date, "isoformat") else str(date)


def _bucket_for(score) -> str | None:
    if score is None:
        return None
    for name, low, high in CATALYST_BUCKETS:
        if low <= score <= high:
            return name
    return None


def _aggregate(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["status"] in TRADE_STATUS]
    if not closed:
        return {"trades": 0, "win_rate": 0.0, "expectancy_r": 0.0}
    # Gewinner ist, wer im Plus abgerechnet wurde -- nicht nur, wer Target 1
    # beruehrt hat. Ein Zeitstopp kann knapp positiv enden, und ein Trade auf
    # genau 0.0R ist weder Treffer noch Verlust, zaehlt aber als Trade.
    wins = [r for r in closed if r["r_multiple"] > 0]
    total_r = sum(r["r_multiple"] for r in closed)
    return {
        "trades": len(closed),
        "win_rate": round(100.0 * len(wins) / len(closed), 1),
        "expectancy_r": round(total_r / len(closed), 2),
    }


def summarize(results: list[dict]) -> dict:
    by_setup: dict[str, list[dict]] = defaultdict(list)
    by_catalyst: dict[str, list[dict]] = defaultdict(list)

    for result in results:
        by_setup[result.get("setup", "UNKNOWN")].append(result)
        bucket = _bucket_for(result.get("score_catalyst"))
        if bucket:
            by_catalyst[bucket].append(result)

    return {
        "overall": _aggregate(results),
        "by_setup": {name: _aggregate(rows) for name, rows in by_setup.items()},
        "by_catalyst": {name: _aggregate(rows) for name, rows in by_catalyst.items()},
        "not_triggered": sum(1 for r in results if r["status"] == "NOT_TRIGGERED"),
        # Nur die wirklich noch laufenden. Wer sein Fenster hinter sich hat,
        # steht als TIME_STOP in "trades" und nicht mehr hier.
        "open": sum(1 for r in results if r["status"] == "OPEN"),
        "time_stop": sum(1 for r in results if r["status"] == "TIME_STOP"),
    }

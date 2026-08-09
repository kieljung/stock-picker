"""Auswertung der Ledger-Eintraege gegen den tatsaechlichen Kursverlauf."""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

CATALYST_BUCKETS = [
    ("stark (7-10)", 7, 10),
    ("mittel (4-6)", 4, 6),
    ("schwach (0-3)", 0, 3),
]


def evaluate(pick: dict, bars: pd.DataFrame, trigger_window: int = 3) -> dict:
    """Bestimmt den Ausgang eines Picks.

    Ablauf: Der Trade gilt als eroeffnet, sobald das Hoch innerhalb der ersten
    `trigger_window` Bars den Entry erreicht. Danach entscheidet, ob zuerst der
    Stop oder Target 1 beruehrt wird. Beruehrt eine einzelne Bar beides, zaehlt
    konservativ der Stop — aus Tagesdaten laesst sich die Reihenfolge nicht
    rekonstruieren.

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
    return {**base, "status": "OPEN", "r_multiple": round((last_close - entry) / risk, 2)}


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
    closed = [r for r in rows if r["status"] in ("TARGET1", "STOP")]
    if not closed:
        return {"trades": 0, "win_rate": 0.0, "expectancy_r": 0.0}
    wins = [r for r in closed if r["status"] == "TARGET1"]
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
        "open": sum(1 for r in results if r["status"] == "OPEN"),
    }

"""Validierung der Modell-Picks und Fortschreibung des Ledgers.

Der Zweck: verhindern, dass halluzinierte Kursniveaus unbemerkt ins
Gedaechtnis des Systems wandern.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from stockpicker.config import TRADE, TradeConfig

MAX_ENTRY_DEVIATION_PCT = 15.0

DEFAULT_LEDGER = Path("data/ledger.jsonl")


def validate_pick(pick: dict, candidate: dict | None, cfg: TradeConfig = TRADE) -> list[str]:
    """Harte Fehler. Nicht leer heisst: Lauf abbrechen."""
    errors: list[str] = []

    if candidate is None or candidate.get("ticker") != pick.get("ticker"):
        errors.append(f"{pick.get('ticker')}: nicht in der Kandidatenliste des Tages")
        return errors

    entry, stop = pick.get("entry"), pick.get("stop")
    t1, t2 = pick.get("target1"), pick.get("target2")

    for name, value in [("Entry", entry), ("Stop", stop), ("Target 1", t1), ("Target 2", t2)]:
        if not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"{pick['ticker']}: {name} fehlt oder ist nicht positiv")
    if errors:
        return errors

    if stop >= entry:
        errors.append(f"{pick['ticker']}: Stop {stop} liegt nicht unter dem Entry {entry}")
    if t1 <= entry:
        errors.append(f"{pick['ticker']}: Target 1 {t1} liegt nicht ueber dem Entry {entry}")
    if t2 <= t1:
        errors.append(f"{pick['ticker']}: Target 2 {t2} liegt nicht ueber Target 1 {t1}")

    if stop < entry:
        rr = (t1 - entry) / (entry - stop)
        if rr < cfg.min_risk_reward:
            errors.append(f"{pick['ticker']}: R:R {rr:.2f} unter dem Minimum {cfg.min_risk_reward}")

    close = candidate.get("close")
    if close:
        deviation = abs(100.0 * (entry - close) / close)
        if deviation > MAX_ENTRY_DEVIATION_PCT:
            errors.append(
                f"{pick['ticker']}: Entry {entry} weicht {deviation:.1f}% vom letzten "
                f"Schlusskurs {close} ab — vermutlich halluziniert"
            )

    composite = pick.get("composite", 0)
    if composite < cfg.min_composite_score:
        errors.append(f"{pick['ticker']}: Composite {composite} unter Schwelle {cfg.min_composite_score}")

    if not pick.get("sources"):
        errors.append(f"{pick['ticker']}: keine Quellen angegeben")

    return errors


def warn_pick(pick: dict, candidate: dict, cfg: TradeConfig = TRADE) -> list[str]:
    """Weiche Hinweise. Blockieren den Lauf nicht."""
    warnings: list[str] = []
    atr = candidate.get("atr14")
    if atr:
        stop_atr = (pick["entry"] - pick["stop"]) / atr
        if stop_atr < cfg.min_stop_atr:
            warnings.append(
                f"{pick['ticker']}: Stop nur {stop_atr:.2f} ATR entfernt "
                f"(empfohlen >= {cfg.min_stop_atr}) — Rauschen kann ihn abholen"
            )
    if pick.get("score_catalyst", 0) <= 3:
        warnings.append(f"{pick['ticker']}: kein belastbarer Katalysator")
    return warnings


def validate_report(picks: list[dict], candidates: list[dict], cfg: TradeConfig = TRADE) -> list[str]:
    """Regeln, die den Report als Ganzes betreffen."""
    errors: list[str] = []
    by_ticker = {c["ticker"]: c for c in candidates}

    if len(picks) > cfg.max_picks:
        errors.append(f"Report enthaelt {len(picks)} Titel — mehr als erlaubte {cfg.max_picks}")

    sector_counts = Counter(p.get("sector", "Unknown") for p in picks)
    for sector, count in sector_counts.items():
        if count > cfg.max_per_sector:
            errors.append(f"Sektor '{sector}' mit {count} Titeln — erlaubt sind {cfg.max_per_sector}")

    for pick in picks:
        errors.extend(validate_pick(pick, by_ticker.get(pick.get("ticker")), cfg))

    return errors


def append_entries(path: Path, date: str, picks: list[dict], *,
                   replace: bool = False) -> int:
    """Schreibt je Pick eine Zeile.

    Ein Datum darf nur einmal vorkommen. ``replace=True`` schreibt die
    Zeilen des Datums neu, statt abzubrechen -- fuer den Fall, dass ein
    Report korrigiert und erneut eingespielt wird.
    """
    from stockpicker.report import ledger_entry  # zyklische Importe vermeiden

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if json.loads(line).get("date") == date:
                if not replace:
                    raise ValueError(f"Fuer {date} sind bereits Eintraege im Ledger vorhanden")
                continue
            existing.append(line)

    lines = list(existing)
    for pick in picks:
        entry = ledger_entry(pick)
        entry["date"] = date
        entry["status"] = "OPEN"
        lines.append(json.dumps(entry, ensure_ascii=False))

    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    return len(picks)


def read_entries(path: Path = DEFAULT_LEDGER) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

#!/usr/bin/env python3
"""Wertet den Ledger gegen den tatsaechlichen Kursverlauf aus.

Verwendung:
    uv run python review.py                # alle Eintraege aelter als 10 Tage
    uv run python review.py --days 20
"""

from __future__ import annotations

import argparse
import datetime as dt

from stockpicker import fetch, ledger, outcomes
from stockpicker.config import TRADE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Dieselbe Vorgabe wie in publish.py --sync: beide Auswertungen muessen
    # dasselbe Fenster sehen, sonst widerspricht das Portal dem Terminal.
    parser.add_argument("--days", type=int, default=TRADE.hold_days_max,
                        help="Beobachtungsfenster in Kalendertagen")
    parser.add_argument("--ledger", default=str(ledger.DEFAULT_LEDGER))
    args = parser.parse_args()

    entries = ledger.read_entries(args.ledger)
    if not entries:
        print("Ledger ist leer — noch nichts auszuwerten.")
        return 0

    cutoff = dt.date.today() - dt.timedelta(days=args.days)
    due = [e for e in entries if dt.date.fromisoformat(e["date"]) <= cutoff]
    if not due:
        print(f"Kein Eintrag ist aelter als {args.days} Tage. {len(entries)} Eintraege insgesamt.")
        return 0

    results = []
    for entry in due:
        start = dt.date.fromisoformat(entry["date"]) + dt.timedelta(days=1)
        end = start + dt.timedelta(days=args.days)
        # fetch.fetch_history_range liefert bereits flache Spalten in fester
        # Reihenfolge und laesst Titel ohne brauchbare Daten einfach weg.
        bars = fetch.fetch_history_range([entry["ticker"]], start, end).get(entry["ticker"])
        if bars is None or bars.empty:
            print(f"  ! Keine Kursdaten fuer {entry['ticker']} ab {start}")
            continue
        results.append(outcomes.evaluate(entry, bars))

    if not results:
        print("Zu keinem faelligen Eintrag gab es Kursdaten — nichts auszuwerten.")
        return 0

    summary = outcomes.summarize(results)

    print(f"\n=== Auswertung ueber {len(results)} Picks, Fenster {args.days} Tage ===\n")
    overall = summary["overall"]
    print(f"Gesamt: {overall['trades']} geschlossene Trades, "
          f"Trefferquote {overall['win_rate']}%, Erwartungswert {overall['expectancy_r']}R")
    print(f"Nicht ausgeloest: {summary['not_triggered']}  ·  Noch offen: {summary['open']}\n")

    print("Nach Setup:")
    for name, stats in sorted(summary["by_setup"].items()):
        print(f"  {name:<16} {stats['trades']:>3} Trades  "
              f"{stats['win_rate']:>5.1f}%  {stats['expectancy_r']:>+5.2f}R")

    print("\nNach Katalysator-Score:")
    for name, stats in summary["by_catalyst"].items():
        print(f"  {name:<16} {stats['trades']:>3} Trades  "
              f"{stats['win_rate']:>5.1f}%  {stats['expectancy_r']:>+5.2f}R")

    print("\nEinzelergebnisse:")
    for r in sorted(results, key=lambda r: r["r_multiple"], reverse=True):
        print(f"  {r['date']}  {r['ticker']:<6} {r['setup']:<16} "
              f"{r['status']:<14} {r['r_multiple']:>+5.2f}R")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

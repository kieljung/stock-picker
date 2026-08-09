#!/usr/bin/env python3
"""Uebergibt Report, Kursdaten und Trade-Ausgaenge an das Portal.

Verwendung:
    uv run python publish.py out/report-2026-08-09.json     # Report + Historie
    uv run python publish.py --sync                          # taeglich: Bars + Ausgaenge
    uv run python publish.py out/report-2026-08-09.json --dry-run

Zugangsdaten kommen aus .env (PORTAL_URL, PORTAL_USER, PORTAL_PASSWORD);
deploy.sh schreibt sie dort beim Deployment hinein.

Die Kurse kommen aus demselben SQLite-Cache, den der Screener fuellt
(``data/cache/market.db``). Das spart nicht nur Netzwerk -- das Portal zeigt
damit genau die Kerzen, auf denen die Auswahl beruht. Fehlt etwas im Cache,
laedt ``market.get_history`` nur die Luecke nach.

``--dry-run`` laedt grundsaetzlich nichts nach: er liest allein den Cache und
sagt, was gesendet wuerde. Ein Probelauf, der erst das Netz braucht, prueft
nicht mehr, er handelt schon.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from stockpicker import ledger, market, outcomes, publish, report, store
from stockpicker.config import TRADE

#: Wie weit die Historie zurueckreicht, die mit einem Report ins Portal geht.
HISTORY_MONTHS = 6

#: Beobachtungsfenster je Pick in Kalendertagen. Derselbe Wert wie in
#: review.py -- beide Auswertungen muessen dieselbe Antwort geben, sonst
#: widerspricht das Portal dem Terminal.
OBSERVATION_DAYS = TRADE.hold_days_max


# --------------------------------------------------------------------------
# Kursdaten
# --------------------------------------------------------------------------

def load_history(tickers: list[str], conn: sqlite3.Connection, *,
                 offline: bool) -> dict[str, pd.DataFrame]:
    """Historie der Ticker. ``offline`` heisst: nur der Cache, kein Netzwerk."""
    if offline:
        return store.load_bars(conn, tickers)

    history, stats = market.get_history(tickers, conn, verbose=True)
    print(
        f"Kursdaten fuer {len(history)} von {len(tickers)} Titeln: "
        f"{stats['aus_cache']} aus Cache, {stats['voll_geladen']} voll geladen, "
        f"{stats['delta_geladen']} als Delta, {stats['neue_bars']} Bars geschrieben.",
        file=sys.stderr,
    )
    return history


def bar_dates(frame: pd.DataFrame) -> list[dt.date]:
    """Der Index als Liste von Datumswerten, egal ob Timestamp oder date."""
    return [i.date() if hasattr(i, "date") else i for i in frame.index]


def since(frame: pd.DataFrame | None, start: dt.date) -> pd.DataFrame | None:
    """Alle Bars ab ``start`` einschliesslich."""
    if frame is None or frame.empty:
        return frame
    return frame[[d >= start for d in bar_dates(frame)]]


def observation_window(frame: pd.DataFrame, pick_date: dt.date, days: int) -> pd.DataFrame:
    """Die Bars, an denen ein Pick gemessen wird.

    Exakt das Fenster aus review.py: dort begrenzt es den Abruf
    (``start = Datum + 1``, ``end = start + days``, Ende exklusiv), hier den
    Ausschnitt aus dem Cache. Beide sehen damit dieselben Kerzen.
    """
    last = pick_date + dt.timedelta(days=days)
    return frame[[pick_date < d <= last for d in bar_dates(frame)]]


def trim(history: dict[str, pd.DataFrame], start: dt.date) -> dict[str, pd.DataFrame]:
    """Historie auf das Fenster beschneiden, das ins Portal geht.

    Der Cache haelt zwei Jahre, weil der Screener sie braucht. Das Portal
    zeichnet ein halbes Jahr -- der Rest waere nur Payload.
    """
    out: dict[str, pd.DataFrame] = {}
    for ticker, frame in history.items():
        window = since(frame, start)
        if window is not None and not window.empty:
            out[ticker] = window
    return out


# --------------------------------------------------------------------------
# Die beiden Ablaeufe
# --------------------------------------------------------------------------

def push_report(creds, path: Path, conn: sqlite3.Connection, *, dry_run: bool) -> int:
    doc = report.load_document(path)
    tickers = [pick["ticker"] for pick in doc["picks"]]

    start = dt.date.fromisoformat(doc["date"]) - dt.timedelta(days=HISTORY_MONTHS * 31)
    history = load_history(tickers, conn, offline=dry_run)
    payload = publish.report_payload(doc, trim(history, start))

    if dry_run:
        size = len(json.dumps(payload).encode("utf-8"))
        bars = sum(len(rows) for rows in payload["bars"].values())
        print(f"dry-run: Report {doc['date']}, {len(doc['picks'])} Picks, "
              f"{len(payload['bars'])} davon mit Kursdaten im Cache, "
              f"{bars} Bars, {size / 1024:.0f} KB.")
        for ticker in tickers:
            if ticker not in payload["bars"]:
                print(f"  ! {ticker}: keine Kurse im Cache ab {start} — "
                      f"der echte Lauf laedt sie nach")
        _report_cache_age(history)
        print("  Nichts geladen, nichts gesendet.")
        return 0

    result = publish.post(creds, "/api/reports", payload)
    print(f"OK: Report {result['date']} mit {result['picks']} Picks und "
          f"{result['bars_written']} Bars uebergeben"
          f"{' (ersetzt)' if result.get('replaced') else ''}.")
    return 0


def sync(creds, ledger_path: Path, conn: sqlite3.Connection, *,
         days: int, dry_run: bool) -> int:
    entries = publish.open_picks(ledger.read_entries(ledger_path))
    if not entries:
        print("Keine offenen Picks — nichts zu synchronisieren.")
        return 0

    tickers = sorted({entry["ticker"] for entry in entries})
    first = min(dt.date.fromisoformat(entry["date"]) for entry in entries)
    history = load_history(tickers, conn, offline=dry_run)
    bars = publish.bars_payload(trim(history, first))

    results: list[dict] = []
    missing: list[str] = []
    for entry in entries:
        frame = history.get(entry["ticker"])
        if frame is None or frame.empty:
            missing.append(entry["ticker"])
            continue
        window = observation_window(frame, dt.date.fromisoformat(entry["date"]), days)
        if not window.empty:
            results.append(outcomes.evaluate(entry, window))

    for ticker in sorted(set(missing)):
        print(f"  ! Keine Kursdaten fuer {ticker}", file=sys.stderr)

    if dry_run:
        print(f"dry-run: {len(entries)} offene Picks, "
              f"{sum(len(rows) for rows in bars.values())} Bars ab {first} aus dem "
              f"Cache, {len(results)} Ausgaenge im Fenster von {days} Tagen.")
        for row in publish.outcomes_payload(results):
            print(f"  {row['date']}  {row['ticker']:<6} {row['status']:<14} "
                  f"{row['r_multiple']:>+5.2f}R  "
                  f"ausgeloest {row['triggered_on'] or '—'}, "
                  f"geschlossen {row['closed_on'] or '—'}")
        _report_cache_age(history)
        print("  Nichts geladen, nichts gesendet.")
        return 0

    bar_result = publish.post(creds, "/api/bars", bars)
    outcome_result = publish.post(creds, "/api/outcomes", publish.outcomes_payload(results))
    print(f"OK: {bar_result['written']} Bars, "
          f"{outcome_result['updated']} Ausgaenge aktualisiert.")
    return 0


def _report_cache_age(history: dict[str, pd.DataFrame]) -> None:
    """Sagt, wie aktuell der Cache ist -- sonst prueft der Probelauf ins Leere."""
    last = max((bar_dates(f)[-1] for f in history.values() if f is not None and not f.empty),
               default=None)
    if last is None:
        print("  Der Cache haelt zu diesen Titeln nichts. Erst screen.py laufen "
              "lassen oder den Lauf ohne --dry-run starten.")
        return
    expected = market.last_expected_session(dt.date.today())
    hint = "" if last >= expected else \
        f" — die letzte erwartete Sitzung war {expected}, der echte Lauf holt den Rest"
    print(f"  Cache reicht bis {last}{hint}.")


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", nargs="?", help="JSON-Dokument aus Stufe 2")
    parser.add_argument("--sync", action="store_true",
                        help="Bars und Ausgaenge aller offenen Picks nachliefern")
    parser.add_argument("--ledger", default=str(ledger.DEFAULT_LEDGER))
    parser.add_argument("--db", default=str(store.DEFAULT_DB), help="SQLite-Cache")
    parser.add_argument("--days", type=int, default=OBSERVATION_DAYS,
                        help=f"Beobachtungsfenster je Pick in Kalendertagen "
                             f"(Vorgabe {OBSERVATION_DAYS}, wie in review.py)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nur zeigen, was gesendet wuerde. Liest allein den "
                             "Cache, laedt nichts nach und sendet nichts")
    args = parser.parse_args()

    if not args.report and not args.sync:
        parser.error("Entweder ein Report-Dokument oder --sync angeben.")

    creds = None
    if args.dry_run:
        # Der Probelauf sendet nichts, soll den Zugang aber trotzdem pruefen:
        # ein fehlendes Passwort faellt sonst erst im echten Lauf auf.
        try:
            publish.credentials()
            print("Portal-Zugang: vollstaendig.")
        except RuntimeError as exc:
            print(f"WARNUNG: {exc}")
    else:
        try:
            creds = publish.credentials()
        except RuntimeError as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 1

    conn = store.connect(args.db)
    try:
        if args.report:
            code = push_report(creds, Path(args.report), conn, dry_run=args.dry_run)
            if code:
                return code
        if args.sync:
            return sync(creds, Path(args.ledger), conn,
                        days=args.days, dry_run=args.dry_run)
    except publish.PushError as exc:
        print(f"PUSH FEHLGESCHLAGEN: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

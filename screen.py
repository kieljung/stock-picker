#!/usr/bin/env python3
"""Stufe 1: Screent den Russell 2000 und schreibt candidates.json.

Verwendung:
    uv run python screen.py                  # voller Lauf
    uv run python screen.py --limit 200      # Schnelltest auf Teilmenge
    uv run python screen.py --out foo.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stockpicker import earnings, fetch, market, pipeline, store, universe
from stockpicker.config import SCREEN


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Nur die ersten N Titel screenen")
    parser.add_argument("--out", default="candidates.json")
    parser.add_argument("--db", default=str(store.DEFAULT_DB), help="SQLite-Cache")
    parser.add_argument("--no-cache", action="store_true",
                        help="Cache umgehen und alles frisch laden")
    parser.add_argument("--meta-max-age", type=float, default=5.0,
                        help="Wie alt Fundamentaldaten sein duerfen (Tage)")
    args = parser.parse_args()

    warnings: list[str] = []

    rows, warning = universe.load_universe()
    if warning:
        warnings.append(warning)
        print(f"WARNUNG: {warning}", file=sys.stderr)
    if args.limit:
        rows = rows[: args.limit]

    tickers = [r["ticker"] for r in rows]
    sectors = {r["ticker"]: r["sector"] for r in rows}
    print(f"Lade Kursdaten fuer {len(tickers)} Titel ...", file=sys.stderr)

    conn = store.connect(":memory:" if args.no_cache else args.db)
    history, hist_stats = market.get_history(tickers, conn, verbose=True)
    print(
        f"Kursdaten fuer {len(history)} Titel: {hist_stats['aus_cache']} aus Cache, "
        f"{hist_stats['voll_geladen']} voll geladen, {hist_stats['delta_geladen']} als Delta, "
        f"{hist_stats['neue_bars']} Bars geschrieben.",
        file=sys.stderr,
    )

    # Metadaten sind teuer (ein Request pro Titel) -- nur fuer Titel abrufen,
    # die die kursbasierten Filter ueberhaupt passieren koennen. Die Schwellen
    # sind dieselben wie in passes_hard_filters, damit der Vorfilter niemals
    # strenger ist als der eigentliche Screen.
    prefiltered = [t for t, bars in history.items() if _price_prefilter(bars)]
    meta, meta_stats = market.get_meta(prefiltered, conn, max_age_days=args.meta_max_age, verbose=True)
    print(
        f"Metadaten fuer {len(prefiltered)} Titel: {meta_stats['aus_cache']} aus Cache, "
        f"{meta_stats['neu_geladen']} geladen, "
        f"{meta_stats['nur_aus_cache_gerettet']} durch den Cache vor einer Luecke bewahrt.",
        file=sys.stderr,
    )

    # Mehrere oeffentliche Kalender: yfinance kennt fuer rund ein Fuenftel der
    # Titel keinen Termin und liegt gelegentlich grob daneben. Es gewinnt immer
    # der frueheste Termin -- das kann einen Blackout nur hinzufuegen.
    calendars, cal_warnings = earnings.load_all_calendars()
    for warning in cal_warnings:
        warnings.append(warning)
        print(f"WARNUNG: {warning}", file=sys.stderr)
    meta, earnings_stats = earnings.merge_into_meta(meta, calendars)

    coverage = ", ".join(
        f"{name.removeprefix('kennt_')} {count}"
        for name, count in earnings_stats.items()
        if name.startswith("kennt_")
    )
    print(
        f"Earnings-Abdeckung: {coverage} | einig {earnings_stats['einig']}, "
        f"uneinig {earnings_stats['uneinig']}, "
        f"nur eine Quelle {earnings_stats['nur_eine_quelle']}, "
        f"ohne Termin {earnings_stats['ohne_termin']}",
        file=sys.stderr,
    )

    benchmark_history = fetch.fetch_history(["IWM"])
    if "IWM" not in benchmark_history:
        print("FEHLER: Benchmark IWM nicht abrufbar — Abbruch.", file=sys.stderr)
        return 1
    benchmark = benchmark_history["IWM"]

    result = pipeline.run_screen(
        {t: history[t] for t in prefiltered}, meta, sectors, benchmark
    )
    # Die Vorfilterung ist eine Optimierung, keine fachliche Aussage: fuer die
    # Statistik zaehlt das Universum, das tatsaechlich geladen wurde.
    result["stats"]["universe"] = len(history)
    result["stats"]["prefiltered"] = len(prefiltered)
    result["earnings_sources"] = earnings_stats
    result["cache"] = {"history": hist_stats, "meta": meta_stats}
    if warnings:
        result["warnings"] = warnings

    out_path = Path(args.out)
    if out_path.parent != Path(""):
        out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, default=str)

    stats = result["stats"]
    print(
        f"Fertig: {stats['candidates']} Kandidaten aus {stats['universe']} Titeln "
        f"({stats['passed_filters']} durch Filter). Geschrieben nach {out_path}.",
        file=sys.stderr,
    )
    return 0


def _price_prefilter(bars) -> bool:
    """Billige kursbasierte Vorpruefung vor dem teuren Metadaten-Abruf."""
    if len(bars) < SCREEN.min_history_days:
        return False
    close = float(bars["Close"].iloc[-1])
    if not (SCREEN.min_price <= close <= SCREEN.max_price):
        return False
    return float(bars["Volume"].tail(20).mean()) >= SCREEN.min_avg_volume


if __name__ == "__main__":
    raise SystemExit(main())

"""Screening-Pipeline: Kursdaten rein, candidates.json-Struktur raus.

Diese Datei kennt kein Netzwerk. Alle Marktdaten kommen als Argument.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stockpicker import indicators as ind
from stockpicker import templates
from stockpicker.config import SCREEN, ScreenConfig


def compute_features(
    ticker: str,
    bars: pd.DataFrame,
    meta: dict,
    *,
    sector: str,
    days_since_earnings: int = 99,
) -> dict:
    """Berechnet alle im Design gelisteten Kennzahlen. Bewertet nichts."""
    close = bars["Close"]
    last = float(close.iloc[-1])
    atr14 = float(ind.atr(bars, 14).iloc[-1])
    bandwidth, percent_b = ind.bollinger(close, 20, 2.0)
    avg_volume = float(bars["Volume"].tail(20).mean())

    sma20 = float(ind.sma(close, 20).iloc[-1]) if len(close) >= 20 else float("nan")
    sma50 = float(ind.sma(close, 50).iloc[-1]) if len(close) >= 50 else float("nan")
    sma200 = float(ind.sma(close, 200).iloc[-1]) if len(close) >= 200 else float("nan")

    base_length, base_depth = ind.base_metrics(bars)
    gap = ind.gap_pct(bars)
    gap_low_held = bool(bars["Low"].iloc[-1] >= bars["Low"].iloc[-2]) if len(bars) >= 2 else False

    return {
        "ticker": ticker,
        "sector": sector,
        "bars": len(bars),
        "close": round(last, 2),
        # Trend
        "sma10": round(float(ind.sma(close, 10).iloc[-1]), 2) if len(close) >= 10 else None,
        "sma20": round(sma20, 2) if sma20 == sma20 else None,
        "sma50": round(sma50, 2) if sma50 == sma50 else None,
        "sma200": round(sma200, 2) if sma200 == sma200 else None,
        "sma_stack_intact": bool(sma20 > sma50 > sma200) if sma200 == sma200 else False,
        "pct_from_52w_high": round(ind.pct_from_52w_high(bars), 2),
        "pct_from_52w_low": round(ind.pct_from_52w_low(bars), 2),
        # Relative Staerke (Perzentil wird spaeter universumsweit ergaenzt)
        "perf_1m": round(ind.performance_pct(close, 21), 2),
        "perf_3m": round(ind.performance_pct(close, 63), 2),
        "perf_6m": round(ind.performance_pct(close, 126), 2),
        # Momentum
        "rsi14": round(float(ind.rsi(close, 14).iloc[-1]), 2),
        "rsi2": round(float(ind.rsi(close, 2).iloc[-1]), 2),
        "macd_hist": round(float(ind.macd_hist(close).iloc[-1]), 4),
        "adx14": round(float(ind.adx(bars, 14).iloc[-1]), 2),
        # Volatilitaet und Kontraktion
        "atr14": round(atr14, 2),
        "atr_pct": round(100.0 * atr14 / last, 2) if last else None,
        "bb_bandwidth": round(float(bandwidth.iloc[-1]), 2),
        "percent_b": round(float(percent_b.iloc[-1]), 3),
        "range_contraction": round(ind.range_contraction(bars), 3),
        # Volumen
        "avg_volume_20d": int(avg_volume),
        "dollar_volume": round(avg_volume * last, 0),
        "rvol": round(float(ind.rvol(bars, 20).iloc[-1]), 2),
        "up_down_volume_ratio": round(ind.up_down_volume_ratio(bars, 50), 2),
        "obv_slope": round(ind.obv_slope(bars, 20), 4),
        "volume_dryup": bool(bars["Volume"].tail(5).mean() < 0.8 * avg_volume),
        # Struktur
        "distance_to_pivot_pct": round(ind.distance_to_pivot_pct(bars, 20), 2),
        "base_length": base_length,
        "base_depth_pct": base_depth,
        "gap_pct": round(gap, 2),
        "gap_low_held": gap_low_held,
        # Kontext
        "market_cap": meta.get("market_cap"),
        "short_percent_of_float": meta.get("short_percent_of_float"),
        "next_earnings": meta.get("next_earnings").isoformat() if meta.get("next_earnings") else None,
        "days_to_earnings": _days_to(meta.get("next_earnings")),
        "earnings_source": meta.get("earnings_source"),
        "days_since_earnings": days_since_earnings,
    }


def _days_to(date: dt.date | None) -> int | None:
    if date is None:
        return None
    return (date - dt.date.today()).days


#: Platzhalter fuer "kein Earnings-Termin bekannt" -- liegt weit ausserhalb des
#: PEAD-Fensters und schaltet das Template damit stumm ab.
NO_EARNINGS_KNOWN = 99


def _days_since(date: dt.date | None) -> int:
    """Tage seit dem letzten Earnings-Termin, sonst NO_EARNINGS_KNOWN.

    Ein Datum in der Zukunft ist kein gemeldeter Termin und zaehlt als
    unbekannt.
    """
    if date is None:
        return NO_EARNINGS_KNOWN
    days = (dt.date.today() - date).days
    return days if days >= 0 else NO_EARNINGS_KNOWN


#: Platzhalter fuer das RS-Perzentil in _is_vcp_base. Hoch genug, um die
#: RS-Bedingung des Templates zu erfuellen -- als einzige laesst sie sich hier
#: noch nicht beantworten.
RS_UNKNOWN_YET = 100.0


def _is_vcp_base(f: dict) -> bool:
    """Ob der Titel eine VCP-Basis bildet, soweit das jetzt schon feststeht.

    Es sind exakt die Bedingungen aus ``templates.is_vcp_breakout``, nur ohne
    das RS-Perzentil: die Hard-Filter laufen vor der Template-Erkennung und
    muessen das auch, weil der Rang erst aus der Menge der Ueberlebenden
    entsteht. Alle uebrigen Merkmale -- Stack, Basislaenge, Basistiefe,
    Kontraktion, austrocknendes Volumen, Naehe zum Pivot -- stehen schon fest.

    Bewusst der echte Matcher statt einer Handvoll nachgebauter Bedingungen:
    zwei Definitionen einer VCP-Basis waeren zwei Definitionen, die
    auseinanderlaufen koennen.
    """
    return templates.is_vcp_breakout({**f, "rs_percentile": RS_UNKNOWN_YET})


def passes_hard_filters(f: dict, cfg: ScreenConfig = SCREEN) -> tuple[bool, str]:
    """Gibt (bestanden, Ablehnungsgrund) zurueck. Grund ist leer bei Erfolg."""
    if f["bars"] < cfg.min_history_days:
        return False, f"Historie zu kurz ({f['bars']} Bars)"
    if not (cfg.min_price <= f["close"] <= cfg.max_price):
        return False, f"Kurs ausserhalb {cfg.min_price}-{cfg.max_price} USD"
    if f["avg_volume_20d"] < cfg.min_avg_volume:
        return False, f"Volumen zu gering ({f['avg_volume_20d']:,})"
    if f["dollar_volume"] < cfg.min_dollar_volume:
        return False, f"Dollarvolumen zu gering ({f['dollar_volume']:,.0f} USD)"
    cap = f.get("market_cap")
    if cap is not None and not (cfg.min_market_cap <= cap <= cfg.max_market_cap):
        return False, f"Marktkap ausserhalb Range ({cap:,.0f} USD)"
    atr_pct = f.get("atr_pct")
    floor = cfg.min_atr_pct_contracting if _is_vcp_base(f) else cfg.min_atr_pct
    if atr_pct is None or not (floor <= atr_pct <= cfg.max_atr_pct):
        return False, f"ATR% ausserhalb {floor}-{cfg.max_atr_pct} ({atr_pct})"
    days_to = f.get("days_to_earnings")
    if days_to is not None and 0 <= days_to <= cfg.earnings_blackout_days:
        return False, f"Earnings in {days_to} Tagen"
    return True, ""


def add_rs_percentile(features: list[dict], benchmark_perf: dict) -> list[dict]:
    """Relative Staerke nach O'Neil: gewichtete Ueberrendite, dann Perzentilrang."""
    scores = []
    for f in features:
        excess = (
            0.4 * (_num(f.get("perf_1m")) - benchmark_perf.get("1m", 0.0))
            + 0.4 * (_num(f.get("perf_3m")) - benchmark_perf.get("3m", 0.0))
            + 0.2 * (_num(f.get("perf_6m")) - benchmark_perf.get("6m", 0.0))
        )
        scores.append(excess)

    ranks = pd.Series(scores).rank(pct=True) * 100.0
    for f, rank in zip(features, ranks):
        f["rs_percentile"] = round(float(rank), 1)
    return features


def _num(value) -> float:
    return 0.0 if value is None or value != value else float(value)


def select_candidates(features: list[dict], cfg: ScreenConfig = SCREEN) -> list[dict]:
    """Behaelt Titel mit mindestens einem Template-Treffer, reihum je Template.

    Die Templates kommen der Reihe nach dran, und jedes gibt seinen besten
    verbliebenen Titel nach RS-Rang ab, bis der Deckel erreicht ist. Innerhalb
    eines Templates entscheidet weiter allein die relative Staerke.

    Vorher wurde nur nach RS sortiert und abgeschnitten. Das klingt neutral,
    ist es aber nicht: welches Template im aktuellen Markt am haeufigsten
    trifft, besetzt dann die ganze Liste. In den ersten zwoelf Laeufen waren
    das 42 von 51 Picks TREND_PULLBACK und kein einziges VCP_BREAKOUT -- die
    Auswertung nach Setup hatte damit nichts zu vergleichen, und was den
    Deckel nie erreicht, kann sich auch nie als tragfaehig erweisen.

    Der Preis ist bewusst in Kauf genommen: ein seltenes Template kann einen
    Platz vor einem staerkeren TREND_PULLBACK bekommen. Die Kandidatenliste
    ist ein Vorschlag, kein Pick -- Stufe 2 bewertet die Qualitaet, und die
    Composite-Schwelle in validate.py faengt ab, was nicht traegt.
    """
    matched = [f for f in features if f.get("templates")]
    matched.sort(key=lambda f: f.get("rs_percentile", 0.0), reverse=True)

    pools = {name: [f for f in matched if name in f["templates"]]
             for name in templates.TEMPLATE_NAMES}
    selected: list[dict] = []
    taken: set[int] = set()

    while len(selected) < cfg.max_candidates:
        before = len(selected)
        for pool in pools.values():
            while pool and id(pool[0]) in taken:
                pool.pop(0)
            if not pool or len(selected) >= cfg.max_candidates:
                continue
            candidate = pool.pop(0)
            taken.add(id(candidate))
            selected.append(candidate)
        if len(selected) == before:      # kein Template hatte noch etwas
            break

    # Die Liste geht nach RS geordnet an das Modell. Die Quote entscheidet,
    # wer drauf steht, nicht in welcher Reihenfolge er dort gelesen wird.
    selected.sort(key=lambda f: f.get("rs_percentile", 0.0), reverse=True)
    return selected


def run_screen(
    history: dict[str, pd.DataFrame],
    meta: dict[str, dict],
    sectors: dict[str, str],
    benchmark_bars: pd.DataFrame,
    cfg: ScreenConfig = SCREEN,
    open_positions: dict[str, str] | None = None,
) -> dict:
    """Vollstaendiger Screen auf bereits geladenen Daten.

    ``open_positions`` bildet Ticker auf das Datum ihres noch laufenden Picks
    ab. Sie fallen vor allen anderen Filtern heraus: was schon im Depot liegt,
    ist heute keine Gelegenheit mehr, sondern eine zweite Position auf
    dieselbe These. Das zu verhindern ist billiger, als es in Stufe 3
    abzufangen -- ein Titel, den das Modell nie sieht, kostet keinen
    abgebrochenen Lauf.
    """
    open_positions = open_positions or {}
    benchmark_close = benchmark_bars["Close"]
    benchmark_perf = {
        "1m": ind.performance_pct(benchmark_close, 21),
        "3m": ind.performance_pct(benchmark_close, 63),
        "6m": ind.performance_pct(benchmark_close, 126),
    }

    passed: list[dict] = []
    rejections: list[dict] = []

    for ticker, bars in history.items():
        since = open_positions.get(ticker)
        if since:
            rejections.append({"ticker": ticker,
                               "reason": f"Position vom {since} laeuft noch"})
            continue
        ticker_meta = meta.get(ticker, {})
        try:
            f = compute_features(
                ticker,
                bars,
                ticker_meta,
                sector=sectors.get(ticker, "Unknown"),
                days_since_earnings=_days_since(ticker_meta.get("last_earnings")),
            )
        except Exception as exc:
            rejections.append({"ticker": ticker, "reason": f"Berechnung fehlgeschlagen: {exc}"})
            continue
        ok, reason = passes_hard_filters(f, cfg)
        if ok:
            passed.append(f)
        else:
            rejections.append({"ticker": ticker, "reason": reason})

    passed = add_rs_percentile(passed, benchmark_perf)
    for f in passed:
        f["templates"] = templates.match_templates(f)

    candidates = select_candidates(passed, cfg)

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "benchmark": {
            "symbol": "IWM",
            "perf_1m": round(benchmark_perf["1m"], 2),
            "perf_3m": round(benchmark_perf["3m"], 2),
            "perf_6m": round(benchmark_perf["6m"], 2),
            "close": round(float(benchmark_close.iloc[-1]), 2),
            "above_sma50": bool(benchmark_close.iloc[-1] > ind.sma(benchmark_close, 50).iloc[-1]),
        },
        "stats": {
            "universe": len(history),
            "passed_filters": len(passed),
            "rejected": len(rejections),
            "candidates": len(candidates),
        },
        "candidates": candidates,
        # Die Beispielliste ist gedeckelt, damit candidates.json lesbar bleibt.
        # Die Verteilung wird deshalb ueber ALLE Ablehnungen gezaehlt -- sonst
        # zeigt eine Auswertung der Liste nur die ersten 50 Titel.
        "rejection_summary": _rejection_summary(rejections),
        "rejections": rejections[:50],
    }


def _rejection_summary(rejections: list[dict]) -> dict[str, int]:
    """Zaehlt Ablehnungen je Grundart. Der Klammerzusatz faellt weg."""
    counts: dict[str, int] = {}
    for rejection in rejections:
        key = rejection["reason"].split("(")[0].strip()
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))

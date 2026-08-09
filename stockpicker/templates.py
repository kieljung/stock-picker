"""Mechanische Erkennung der vier Setup-Templates aus dem Design.

Diese Funktionen bewerten nichts — sie stellen nur fest, ob die
Mindestkriterien eines Templates erfuellt sind. Die Qualitaetsbewertung
macht das Modell anhand der Rubrik in PROMPT.md.
"""

from __future__ import annotations

TEMPLATE_NAMES = ["VCP_BREAKOUT", "TREND_PULLBACK", "MEAN_REVERSION", "PEAD"]


def _sma_stack_intact(f: dict) -> bool:
    return f["sma20"] > f["sma50"] > f["sma200"]


def is_vcp_breakout(f: dict) -> bool:
    """Minervini VCP / O'Neil Base-Breakout."""
    return (
        _sma_stack_intact(f)
        and f["close"] > f["sma50"]
        and f["rs_percentile"] >= 80.0
        and f["base_length"] >= 15                 # ca. 3 Wochen Basis
        and f["base_depth_pct"] <= 35.0            # tiefere Basen sind Abwaertstrends
        and f["range_contraction"] <= 0.6          # Spanne verengt sich
        and f["volume_dryup"]                      # Volumen trocknet in der Basis aus
        and -5.0 <= f["distance_to_pivot_pct"] <= 1.0
    )


def is_trend_pullback(f: dict) -> bool:
    """Ruecksetzer auf SMA 20 oder 50 im intakten Aufwaertstrend."""
    if not _sma_stack_intact(f) or f["close"] < f["sma200"]:
        return False
    near_sma20 = abs(f["close"] - f["sma20"]) / f["sma20"] <= 0.03
    near_sma50 = abs(f["close"] - f["sma50"]) / f["sma50"] <= 0.03
    return (near_sma20 or near_sma50) and f["rs_percentile"] >= 60.0 and f["rvol"] < 1.0


def is_mean_reversion(f: dict) -> bool:
    """Connors RSI-2 im uebergeordneten Aufwaertstrend."""
    return f["close"] > f["sma200"] and f["rsi2"] < 10.0 and f["percent_b"] < 0.1


def is_pead(f: dict) -> bool:
    """Post-Earnings-Announcement-Drift."""
    return (
        f["days_since_earnings"] <= 3
        and f["gap_pct"] > 5.0
        and f["rvol"] > 3.0
        and f["gap_low_held"]
    )


_MATCHERS = {
    "VCP_BREAKOUT": is_vcp_breakout,
    "TREND_PULLBACK": is_trend_pullback,
    "MEAN_REVERSION": is_mean_reversion,
    "PEAD": is_pead,
}


def match_templates(features: dict) -> list[str]:
    """Alle Templates, deren Mindestkriterien der Titel erfuellt."""
    return [name for name in TEMPLATE_NAMES if _MATCHERS[name](features)]

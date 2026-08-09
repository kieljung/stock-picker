"""Alle Schwellen aus dem Design-Dokument an einer Stelle."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenConfig:
    # Ausschlussfilter
    min_price: float = 3.0
    max_price: float = 100.0
    min_avg_volume: int = 500_000
    min_dollar_volume: float = 5_000_000.0
    min_market_cap: float = 300_000_000.0
    max_market_cap: float = 3_000_000_000.0
    min_atr_pct: float = 3.0
    max_atr_pct: float = 12.0
    min_history_days: int = 250

    # Earnings-Regel
    earnings_blackout_days: int = 5      # Termin in <= 5 Tagen -> Ausschluss
    pead_window_days: int = 3            # Earnings vor <= 3 Tagen -> PEAD-Flag

    # Deckelung
    max_candidates: int = 20


@dataclass(frozen=True)
class TradeConfig:
    account_risk_pct: float = 1.0
    min_risk_reward: float = 2.0
    min_stop_atr: float = 1.5
    hold_days_min: int = 2
    hold_days_max: int = 10
    min_composite_score: int = 60
    max_picks: int = 5
    max_per_sector: int = 2


SCREEN = ScreenConfig()
TRADE = TradeConfig()

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
    # Ein zweiter, tieferer ATR-Boden -- ausschliesslich fuer Titel, die eine
    # VCP-Basis bilden. Der normale Boden von 3 % soll tote Titel aussortieren,
    # traf aber auch das VCP, das sich gerade ueber die schrumpfende Spanne
    # definiert. Eine Basis mit austrocknendem Volumen ist nicht tot, sondern
    # gespannt; unter 1,5 % ist sie es doch.
    min_atr_pct_contracting: float = 1.5
    min_history_days: int = 250

    # Earnings-Regel
    earnings_blackout_days: int = 5      # Termin in <= 5 Tagen -> Ausschluss
    pead_window_days: int = 3            # Earnings vor <= 3 Tagen -> PEAD-Flag

    # Deckelung
    max_candidates: int = 20


@dataclass(frozen=True)
class TradeConfig:
    account_risk_pct: float = 1.0
    # Stop >= 2 ATR, Target 1 bei rund 3 ATR, also R:R ab 1,5. Die Auswertung
    # Aug-Okt 2026 zeigte: mit 1,5 ATR holte das Tagesrauschen 40 % der Trades
    # ab, Ziele jenseits von 2R erreichten nur 11 % im Haltefenster.
    min_risk_reward: float = 1.5
    min_stop_atr: float = 2.0
    hold_days_min: int = 2
    hold_days_max: int = 10
    min_composite_score: int = 60
    max_picks: int = 5
    max_per_sector: int = 2


SCREEN = ScreenConfig()
TRADE = TradeConfig()

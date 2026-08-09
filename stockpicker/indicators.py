"""Reine Indikator-Funktionen. Kein I/O, kein Netzwerk, keine Konfiguration.

Konvention: Eingabe ist ein OHLCV-DataFrame mit den Spalten
Open, High, Low, Close, Volume und einem DatetimeIndex in aufsteigender
Reihenfolge. Funktionen, die eine Series zurueckgeben, behalten den Index.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def _wilder_smooth(series: pd.Series, window: int) -> pd.Series:
    """Wilders Glaettung — die Grundlage von ATR, RSI und ADX."""
    return series.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    ranges = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    return _wilder_smooth(true_range(df), window)


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder_smooth(gain, window)
    avg_loss = _wilder_smooth(loss, window)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 bedeutet: nur Gewinne -> RSI 100. Umgekehrt RSI 0.
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(avg_gain != 0.0, 0.0)
    return out


def macd_hist(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    return macd - macd.ewm(span=signal, adjust=False).mean()


def adx(df: pd.DataFrame, window: int = 14) -> pd.Series:
    up_move = df["High"].diff()
    down_move = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    atr_ = _wilder_smooth(true_range(df), window).replace(0.0, np.nan)
    plus_di = 100.0 * _wilder_smooth(plus_dm, window) / atr_
    minus_di = 100.0 * _wilder_smooth(minus_dm, window) / atr_

    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return _wilder_smooth(dx.fillna(0.0), window)


def bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0):
    """Gibt (Bandbreite in Prozent des Mittelwerts, %B) zurueck."""
    mid = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    bandwidth = 100.0 * (upper - lower) / mid
    span = (upper - lower).replace(0.0, np.nan)
    percent_b = (series - lower) / span
    return bandwidth, percent_b


def rvol(df: pd.DataFrame, window: int = 20) -> pd.Series:
    # Baseline sind die `window` VORHERIGEN Tage - der heutige Tag darf den
    # Vergleichsmassstab nicht selbst verwaessern (sonst waere ein doppeltes
    # Volumen nur 1.90x statt 2.0x).
    baseline = df["Volume"].shift(1).rolling(window, min_periods=window).mean()
    return df["Volume"] / baseline


def up_down_volume_ratio(df: pd.DataFrame, window: int = 50) -> float:
    """Akkumulation nach O'Neil: Volumen an Aufwaertstagen zu Abwaertstagen."""
    recent = df.tail(window)
    direction = recent["Close"].diff()
    up_vol = recent.loc[direction > 0, "Volume"].sum()
    down_vol = recent.loc[direction < 0, "Volume"].sum()
    if down_vol == 0:
        return float("inf") if up_vol > 0 else 1.0
    return float(up_vol / down_vol)


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["Close"].diff().fillna(0.0))
    return (direction * df["Volume"]).cumsum()


def obv_slope(df: pd.DataFrame, window: int = 20) -> float:
    """Steigung der OBV-Regressionsgerade, normiert auf das mittlere Volumen."""
    series = obv(df).tail(window)
    if len(series) < 2:
        return 0.0
    x = np.arange(len(series), dtype=float)
    slope = np.polyfit(x, series.to_numpy(dtype=float), 1)[0]
    mean_vol = df["Volume"].tail(window).mean()
    return float(slope / mean_vol) if mean_vol else 0.0


def range_contraction(df: pd.DataFrame, short: int = 5, long: int = 20) -> float:
    """VCP-Proxy: enge juengste Spanne relativ zur laengeren Spanne."""
    short_range = df["High"].tail(short).max() - df["Low"].tail(short).min()
    long_range = df["High"].tail(long).max() - df["Low"].tail(long).min()
    if long_range == 0:
        return 1.0
    return float(short_range / long_range)


def pct_from_52w_high(df: pd.DataFrame) -> float:
    window = df.tail(TRADING_DAYS_PER_YEAR)
    high = window["High"].max()
    return float(100.0 * (window["Close"].iloc[-1] - high) / high)


def pct_from_52w_low(df: pd.DataFrame) -> float:
    window = df.tail(TRADING_DAYS_PER_YEAR)
    low = window["Low"].min()
    return float(100.0 * (window["Close"].iloc[-1] - low) / low)


def performance_pct(series: pd.Series, lookback: int) -> float:
    if len(series) <= lookback:
        return float("nan")
    past = series.iloc[-1 - lookback]
    return float(100.0 * (series.iloc[-1] - past) / past)


def distance_to_pivot_pct(df: pd.DataFrame, window: int = 20) -> float:
    """Abstand zum Hoch der letzten `window` Tage. 0 heisst: am Pivot."""
    pivot = df["High"].tail(window).max()
    return float(100.0 * (df["Close"].iloc[-1] - pivot) / pivot)


def base_metrics(df: pd.DataFrame, max_lookback: int = 120) -> tuple[int, float]:
    """Laenge und Tiefe der aktuellen Basis nach Minervini.

    Die Basis beginnt am hoechsten Hoch des Lookbacks. Laenge ist die Anzahl
    Bars seit diesem Hoch, Tiefe der prozentuale Rueckgang vom Basis-Hoch zum
    tiefsten Tief danach.

    Die frueher hier verwendete Definition -- rueckwaerts zaehlen, solange die
    kumulierte Spanne unter einem festen Prozentwert bleibt -- war mit dem
    ATR-Filter des Screeners (3-12 %) unvereinbar: ein Titel mit 5 % ATR reisst
    eine 10-%-Schranke nach zwei Tagen, wodurch die geforderten 15 Basis-Tage
    strukturell unerreichbar waren. Diese Definition ist stabil, weil eine
    einzelne weite Bar die Messung nicht kippt.

    Gibt (0, 0.0) zurueck, wenn der Titel gerade sein Hoch macht -- dann
    existiert noch keine Basis.
    """
    window = df.tail(max_lookback)
    highs, lows = window["High"], window["Low"]
    peak_pos = int(highs.to_numpy().argmax())
    length = len(window) - 1 - peak_pos
    base_high = float(highs.iloc[peak_pos])
    if base_high <= 0:
        return 0, 0.0
    trough = float(lows.iloc[peak_pos:].min())
    return length, round(100.0 * (base_high - trough) / base_high, 2)


def gap_pct(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    prev_close = df["Close"].iloc[-2]
    return float(100.0 * (df["Open"].iloc[-1] - prev_close) / prev_close)

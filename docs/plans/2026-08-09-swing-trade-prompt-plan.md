# Small-Cap Swing Trading System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ein arbeitstäglich laufendes System, das aus dem Russell 2000 bis zu fünf Swing-Trading-Kandidaten identifiziert, sie nach anerkannten Methoden begründet und die Picks für spätere Auswertung protokolliert.

**Architecture:** Drei getrennte Stufen. `screen.py` berechnet deterministisch alle Kennzahlen und schreibt `candidates.json`. `PROMPT.md` wird mit dieser Datei in eine Claude-Session mit Websuche gegeben und erzeugt `picks.json` plus Report. `validate.py` prüft die Levels gegen echte Kursdaten und schreibt `data/ledger.jsonl`; `review.py` wertet den Ledger nach N Tagen aus.

**Tech Stack:** Python 3.12 (via uv), pandas, numpy, yfinance, requests, pytest.

**Design-Grundlage:** `docs/plans/2026-08-09-swing-trade-prompt-design.md`

---

## Grundregeln für die Umsetzung

- **Kein Netzwerk in Tests.** Jeder Test arbeitet auf synthetischen DataFrames oder auf gemockten Fetchern. Ein Test, der yfinance aufruft, ist ein Fehler im Test.
- **Erwartungswerte sind nachrechenbar.** Statt erfundener Float-Konstanten prüfen die Tests Eigenschaften, die man von Hand verifizieren kann: ATR auf Bars mit konstanter Spanne, RSI auf monoton steigenden Kursen, Bandbreite auf konstanter Serie.
- **Indikatoren sind reine Funktionen.** `indicators.py` kennt weder Dateien noch Netzwerk noch Konfiguration.
- **Commit nach jeder Task.** Kleine, in sich lauffähige Schritte.

---

## Zielstruktur

```
1-Projects/stock-picker/
├── pyproject.toml
├── README.md
├── PROMPT.md                  ← der eigentliche Deliverable
├── screen.py                  ← CLI Stufe 1
├── validate.py                ← CLI Stufe 3
├── review.py                  ← CLI Auswertung
├── stockpicker/
│   ├── __init__.py
│   ├── config.py
│   ├── universe.py
│   ├── indicators.py
│   ├── templates.py
│   ├── fetch.py
│   ├── pipeline.py
│   ├── ledger.py
│   └── outcomes.py
├── tests/
│   ├── conftest.py
│   ├── test_indicators.py
│   ├── test_templates.py
│   ├── test_universe.py
│   ├── test_pipeline.py
│   ├── test_ledger.py
│   └── test_outcomes.py
├── data/                      ← ledger.jsonl versioniert, cache/ nicht
└── out/                       ← Reports, nicht versioniert
```

---

## Task 1: Projekt-Setup

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `stockpicker/__init__.py`, `tests/conftest.py`, `data/.gitkeep`, `out/.gitkeep`

**Step 1: pyproject.toml anlegen**

```toml
[project]
name = "stock-picker"
version = "0.1.0"
description = "Small-cap swing trading screener and analysis prompt"
requires-python = ">=3.11,<3.14"
dependencies = [
    "yfinance>=0.2.50",
    "pandas>=2.2",
    "numpy>=1.26",
    "requests>=2.31",
]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

**Step 2: .gitignore anlegen**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
data/cache/
out/
candidates.json
picks.json
```

`data/ledger.jsonl` bleibt bewusst versioniert — das ist das Gedächtnis des Systems.

**Step 3: Umgebung erstellen**

Python 3.14 ist im System aktiv, aber die Wheel-Lage für pandas/yfinance ist dort erfahrungsgemäß dünner. Deshalb explizit auf 3.12 pinnen:

Run: `uv venv --python 3.12 && uv sync --all-groups`
Expected: `.venv` wird angelegt, alle Pakete installiert, keine Build-Fehler.

Falls `uv sync` an einem Paket scheitert: Python-Version im `requires-python` und im `uv venv`-Aufruf gemeinsam anpassen, nicht einzelne Pakete pinnen.

**Step 4: Leere Package-Dateien und Verzeichnisse**

```bash
mkdir -p stockpicker tests data out
touch stockpicker/__init__.py data/.gitkeep out/.gitkeep
```

`tests/conftest.py`:

```python
"""Gemeinsame Fixtures. Kein Test in diesem Projekt darf Netzwerk verwenden."""

import numpy as np
import pandas as pd
import pytest


def make_ohlcv(closes, *, high_offset=1.0, low_offset=1.0, volume=1_000_000):
    """Baut einen OHLCV-DataFrame aus einer Liste von Schlusskursen.

    Jede Bar bekommt dieselbe Spanne (high_offset + low_offset), damit
    ATR-Erwartungswerte von Hand nachrechenbar sind.
    """
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(end="2026-08-07", periods=n)
    volumes = volume if np.isscalar(volume) else np.asarray(volume, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + high_offset,
            "Low": closes - low_offset,
            "Close": closes,
            "Volume": np.full(n, volumes) if np.isscalar(volumes) else volumes,
        },
        index=idx,
    )


@pytest.fixture
def flat_series():
    """300 Bars auf konstantem Kurs 50, Spanne je Bar exakt 2.0."""
    return make_ohlcv([50.0] * 300)


@pytest.fixture
def rising_series():
    """300 Bars, jeden Tag +0.5 — sauberer Aufwärtstrend ohne Rücksetzer."""
    return make_ohlcv([50.0 + 0.5 * i for i in range(300)])


@pytest.fixture
def falling_series():
    return make_ohlcv([200.0 - 0.5 * i for i in range(300)])
```

**Step 5: Verifizieren**

Run: `uv run pytest`
Expected: `no tests ran` — kein Fehler, keine Collection-Errors.

**Step 6: Commit**

```bash
git add pyproject.toml .gitignore uv.lock stockpicker/ tests/ data/.gitkeep out/.gitkeep
git commit -m "chore: project scaffold for stock-picker"
```

---

## Task 2: Indikatoren

**Files:**
- Create: `stockpicker/indicators.py`
- Test: `tests/test_indicators.py`

Alle Funktionen nehmen einen OHLCV-DataFrame oder eine Series und geben eine Series zurück. Keine Seiteneffekte.

**Step 1: Failing Tests schreiben**

```python
import numpy as np
import pandas as pd
import pytest

from stockpicker import indicators as ind
from tests.conftest import make_ohlcv


def test_sma_of_constant_series_is_the_constant(flat_series):
    assert ind.sma(flat_series["Close"], 20).iloc[-1] == pytest.approx(50.0)


def test_sma_of_arithmetic_series_is_midpoint():
    # letzte 5 Werte: 10, 11, 12, 13, 14 -> Mittel 12
    s = pd.Series([float(i) for i in range(15)])
    assert ind.sma(s, 5).iloc[-1] == pytest.approx(12.0)


def test_atr_equals_bar_range_when_range_is_constant(flat_series):
    # Jede Bar: High-Low = 2.0, keine Gaps -> True Range konstant 2.0
    assert ind.atr(flat_series, 14).iloc[-1] == pytest.approx(2.0)


def test_rsi_is_100_on_monotonic_rise(rising_series):
    assert ind.rsi(rising_series["Close"], 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_is_0_on_monotonic_fall(falling_series):
    assert ind.rsi(falling_series["Close"], 14).iloc[-1] == pytest.approx(0.0)


def test_rsi2_reacts_faster_than_rsi14():
    # Langer Anstieg, dann zwei scharfe Abwärtstage
    closes = [50.0 + 0.5 * i for i in range(200)] + [140.0, 130.0]
    df = make_ohlcv(closes)
    assert ind.rsi(df["Close"], 2).iloc[-1] < ind.rsi(df["Close"], 14).iloc[-1]


def test_bollinger_bandwidth_is_zero_on_constant_series(flat_series):
    bw, pct_b = ind.bollinger(flat_series["Close"], 20, 2.0)
    assert bw.iloc[-1] == pytest.approx(0.0)


def test_bollinger_percent_b_above_one_when_price_breaks_out():
    closes = [50.0] * 60 + [60.0]
    df = make_ohlcv(closes)
    bw, pct_b = ind.bollinger(df["Close"], 20, 2.0)
    assert pct_b.iloc[-1] > 1.0


def test_macd_hist_positive_in_uptrend(rising_series):
    assert ind.macd_hist(rising_series["Close"]).iloc[-1] > 0


def test_adx_is_high_in_strong_trend(rising_series):
    assert ind.adx(rising_series, 14).iloc[-1] > 25


def test_adx_is_low_in_flat_market():
    # Sägezahn ohne Richtung
    closes = [50.0 + (1.0 if i % 2 else -1.0) for i in range(200)]
    df = make_ohlcv(closes)
    assert ind.adx(df, 14).iloc[-1] < 25


def test_rvol_is_one_when_volume_is_constant(flat_series):
    assert ind.rvol(flat_series, 20).iloc[-1] == pytest.approx(1.0)


def test_rvol_doubles_when_todays_volume_doubles():
    vols = [1_000_000.0] * 60 + [2_000_000.0]
    df = make_ohlcv([50.0] * 61, volume=vols)
    assert ind.rvol(df, 20).iloc[-1] == pytest.approx(2.0)


def test_up_down_volume_ratio_above_one_in_accumulation(rising_series):
    assert ind.up_down_volume_ratio(rising_series, 50) > 1.0


def test_obv_slope_positive_in_uptrend(rising_series):
    assert ind.obv_slope(rising_series, 20) > 0


def test_range_contraction_below_one_when_range_narrows():
    # Erst weite Schwankung, dann enge -> 5-Tage-Range < 20-Tage-Range
    closes = [50.0 + (5.0 if i % 2 else -5.0) for i in range(20)] + [50.0] * 5
    df = make_ohlcv(closes, high_offset=0.1, low_offset=0.1)
    assert ind.range_contraction(df) < 1.0


def test_pct_from_52w_high_is_zero_at_new_high(rising_series):
    assert ind.pct_from_52w_high(rising_series) == pytest.approx(0.0, abs=0.01)


def test_pct_from_52w_high_is_negative_after_pullback():
    closes = [50.0 + 0.5 * i for i in range(260)] + [100.0]
    df = make_ohlcv(closes)
    assert ind.pct_from_52w_high(df) < -10


def test_performance_pct_matches_hand_calculation():
    closes = [100.0] * 20 + [110.0]
    df = make_ohlcv(closes)
    assert ind.performance_pct(df["Close"], 20) == pytest.approx(10.0)


def test_consolidation_days_counts_range_bound_bars():
    # 40 Tage Trend, dann 15 Tage in enger Spanne
    closes = [50.0 + 0.5 * i for i in range(40)] + [70.0 + (0.2 if i % 2 else -0.2) for i in range(15)]
    df = make_ohlcv(closes, high_offset=0.1, low_offset=0.1)
    assert ind.consolidation_days(df, tolerance_pct=5.0) >= 14


def test_distance_to_pivot_is_zero_at_20d_high(rising_series):
    assert ind.distance_to_pivot_pct(rising_series, 20) == pytest.approx(0.0, abs=0.01)


def test_gap_pct_matches_hand_calculation():
    df = make_ohlcv([100.0, 100.0])
    df.iloc[-1, df.columns.get_loc("Open")] = 105.0
    assert ind.gap_pct(df) == pytest.approx(5.0)
```

**Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/test_indicators.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockpicker.indicators'`

**Step 3: Implementierung**

`stockpicker/indicators.py`:

```python
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


def consolidation_days(df: pd.DataFrame, tolerance_pct: float = 5.0, max_lookback: int = 120) -> int:
    """Wie viele Tage bewegt sich der Kurs schon in einer engen Spanne?

    Zaehlt rueckwaerts, solange High und Low innerhalb von tolerance_pct
    um den Median der bisher gezaehlten Bars bleiben.
    """
    window = df.tail(max_lookback)
    highs = window["High"].to_numpy(dtype=float)
    lows = window["Low"].to_numpy(dtype=float)
    count = 0
    for i in range(len(window) - 1, -1, -1):
        hi = highs[i:].max()
        lo = lows[i:].min()
        mid = (hi + lo) / 2.0
        if mid == 0 or 100.0 * (hi - lo) / mid > 2 * tolerance_pct:
            break
        count += 1
    return count


def gap_pct(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    prev_close = df["Close"].iloc[-2]
    return float(100.0 * (df["Open"].iloc[-1] - prev_close) / prev_close)
```

**Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_indicators.py -q`
Expected: PASS, alle Tests grün.

Falls `test_adx_is_low_in_flat_market` scheitert: Sägezahn-Amplitude in dem Test halbieren, nicht die ADX-Implementierung anfassen — Wilders ADX auf einer perfekten Alternation ist ein Grenzfall.

**Step 5: Commit**

```bash
git add stockpicker/indicators.py tests/test_indicators.py tests/conftest.py
git commit -m "feat: pure indicator functions with hand-verifiable tests"
```

---

## Task 3: Konfiguration

**Files:**
- Create: `stockpicker/config.py`

Ein einziger Ort für alle Schwellen aus dem Design. Kein Test nötig — reine Konstanten.

**Step 1: Implementierung**

```python
"""Alle Schwellen aus dem Design-Dokument an einer Stelle."""

from dataclasses import dataclass, field


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
```

**Step 2: Import prüfen**

Run: `uv run python -c "from stockpicker.config import SCREEN, TRADE; print(SCREEN.min_price, TRADE.max_picks)"`
Expected: `3.0 5`

**Step 3: Commit**

```bash
git add stockpicker/config.py
git commit -m "feat: central configuration for screen and trade thresholds"
```

---

## Task 4: Universum laden

**Files:**
- Create: `stockpicker/universe.py`
- Test: `tests/test_universe.py`

Die iShares-Holdings-Datei für IWM ist ein CSV mit mehreren Kopfzeilen vor der eigentlichen Tabelle. Die Parsing-Logik muss das robust überspringen und ist deshalb testwürdig; der Download selbst wird gemockt.

**Step 1: Failing Tests schreiben**

```python
import datetime as dt

import pytest

from stockpicker import universe


RAW_CSV = """iShares Russell 2000 ETF
Fund Holdings as of,"Aug 07, 2026"
Inception Date,"May 22, 2000"

Ticker,Name,Sector,Asset Class,Market Value,Weight (%)
AAOI,APPLIED OPTOELECTRONICS INC,Information Technology,Equity,"12,345,678",0.12
BBIO,BRIDGEBIO PHARMA INC,Health Care,Equity,"23,456,789",0.23
XTSLA,BLK CSH FND TREASURY SL AGENCY,Cash and/or Derivatives,Money Market,"1,000",0.01
-,USD CASH,Cash and/or Derivatives,Cash,"500",0.00
BRK.B,BERKSHIRE HATHAWAY,Financials,Equity,"999",0.00
"""


def test_parse_holdings_extracts_equity_tickers():
    rows = universe.parse_holdings_csv(RAW_CSV)
    tickers = [r["ticker"] for r in rows]
    assert "AAOI" in tickers
    assert "BBIO" in tickers


def test_parse_holdings_drops_cash_and_money_market():
    rows = universe.parse_holdings_csv(RAW_CSV)
    tickers = [r["ticker"] for r in rows]
    assert "XTSLA" not in tickers
    assert "-" not in tickers


def test_parse_holdings_keeps_sector():
    rows = universe.parse_holdings_csv(RAW_CSV)
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["BBIO"]["sector"] == "Health Care"


def test_parse_holdings_normalizes_dotted_tickers_for_yahoo():
    rows = universe.parse_holdings_csv(RAW_CSV)
    tickers = [r["ticker"] for r in rows]
    assert "BRK-B" in tickers


def test_parse_holdings_raises_on_missing_header():
    with pytest.raises(ValueError, match="Ticker"):
        universe.parse_holdings_csv("nur muell\nohne tabelle\n")


def test_load_universe_uses_cache_when_download_fails(tmp_path, monkeypatch):
    cache = tmp_path / "iwm_holdings.csv"
    cache.write_text(RAW_CSV, encoding="utf-8")

    def boom(url, timeout):
        raise OSError("network down")

    monkeypatch.setattr(universe, "_download", boom)
    rows, warning = universe.load_universe(cache_path=cache, max_age_days=0)

    assert [r["ticker"] for r in rows][:2] == ["AAOI", "BBIO"]
    assert "cache" in warning.lower()


def test_load_universe_raises_when_no_cache_and_download_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "_download", lambda url, timeout: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(RuntimeError, match="kein Cache"):
        universe.load_universe(cache_path=tmp_path / "missing.csv", max_age_days=0)
```

**Step 2: Fehlschlag bestätigen**

Run: `uv run pytest tests/test_universe.py -q`
Expected: FAIL — `No module named 'stockpicker.universe'`

**Step 3: Implementierung**

```python
"""Laedt das Handelsuniversum aus der oeffentlichen iShares-IWM-Holdings-Datei."""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

import requests

IWM_HOLDINGS_URL = (
    "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf"
    "/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
)

DEFAULT_CACHE = Path("data/cache/iwm_holdings.csv")

_EXCLUDED_ASSET_CLASSES = {"money market", "cash"}


def _download(url: str, timeout: int) -> str:
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "stock-picker/0.1"})
    response.raise_for_status()
    return response.text


def parse_holdings_csv(text: str) -> list[dict]:
    """Ueberspringt die Kopfzeilen der iShares-Datei und liest die Tabelle.

    Cash-, Geldmarkt- und Platzhalterzeilen fliegen raus. Punkte in Tickern
    werden zu Bindestrichen, weil Yahoo Finance BRK-B statt BRK.B erwartet.
    """
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("Ticker,")), None)
    if header_index is None:
        raise ValueError("Keine 'Ticker'-Kopfzeile in der Holdings-Datei gefunden")

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    rows = []
    for row in reader:
        ticker = (row.get("Ticker") or "").strip()
        asset_class = (row.get("Asset Class") or "").strip().lower()
        if not ticker or ticker == "-":
            continue
        if asset_class in _EXCLUDED_ASSET_CLASSES:
            continue
        rows.append(
            {
                "ticker": ticker.replace(".", "-"),
                "name": (row.get("Name") or "").strip(),
                "sector": (row.get("Sector") or "Unknown").strip(),
            }
        )
    return rows


def load_universe(
    *,
    cache_path: Path = DEFAULT_CACHE,
    max_age_days: int = 1,
    timeout: int = 30,
) -> tuple[list[dict], str]:
    """Gibt (Titel, Warnung) zurueck. Warnung ist leer, wenn alles frisch ist."""
    cache_path = Path(cache_path)

    if cache_path.exists() and max_age_days > 0:
        age = dt.datetime.now() - dt.datetime.fromtimestamp(cache_path.stat().st_mtime)
        if age <= dt.timedelta(days=max_age_days):
            return parse_holdings_csv(cache_path.read_text(encoding="utf-8")), ""

    try:
        text = _download(IWM_HOLDINGS_URL, timeout)
    except Exception as exc:
        if cache_path.exists():
            warning = f"IWM-Holdings nicht abrufbar ({exc}) — verwende Cache vom {dt.datetime.fromtimestamp(cache_path.stat().st_mtime):%Y-%m-%d}"
            return parse_holdings_csv(cache_path.read_text(encoding="utf-8")), warning
        raise RuntimeError(f"IWM-Holdings nicht abrufbar und kein Cache vorhanden: {exc}") from exc

    rows = parse_holdings_csv(text)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return rows, ""
```

**Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_universe.py -q`
Expected: PASS

**Step 5: Einmal echt gegen das Netz prüfen** (kein Test, manueller Check)

Run: `uv run python -c "from stockpicker.universe import load_universe; rows, w = load_universe(); print(len(rows), rows[0], w)"`
Expected: rund 1900–2000 Zeilen, erste Zeile mit Ticker/Name/Sector, leere Warnung.

Falls iShares die URL geändert hat: aktuelle Download-URL auf der IWM-Produktseite nachschlagen und `IWM_HOLDINGS_URL` ersetzen. Die Parsing-Logik bleibt gültig.

**Step 6: Commit**

```bash
git add stockpicker/universe.py tests/test_universe.py
git commit -m "feat: load Russell 2000 universe from iShares holdings with cache fallback"
```

---

## Task 5: Setup-Templates

**Files:**
- Create: `stockpicker/templates.py`
- Test: `tests/test_templates.py`

Templates arbeiten auf einem fertigen Feature-Dict, nicht auf Kursdaten. Das macht sie trivial testbar und hält die Schwellen aus dem Design an einer sichtbaren Stelle.

**Step 1: Failing Tests schreiben**

```python
from stockpicker import templates


def base_features(**overrides):
    """Neutrale Ausgangslage, die kein Template erfuellt."""
    features = {
        "close": 50.0,
        "sma10": 50.0, "sma20": 50.0, "sma50": 50.0, "sma200": 50.0,
        "rs_percentile": 50.0,
        "rsi14": 50.0, "rsi2": 50.0,
        "percent_b": 0.5,
        "distance_to_pivot_pct": -20.0,
        "consolidation_days": 3,
        "range_contraction": 0.9,
        "rvol": 1.0,
        "gap_pct": 0.0,
        "days_since_earnings": 99,
        "atr_pct": 5.0,
        "gap_low_held": False,
        "volume_dryup": False,
    }
    features.update(overrides)
    return features


def test_vcp_breakout_matches_when_all_criteria_hold():
    f = base_features(
        close=52.0, sma20=50.0, sma50=48.0, sma200=45.0,
        rs_percentile=88.0,
        distance_to_pivot_pct=-2.0,
        consolidation_days=18,
        range_contraction=0.45,
        volume_dryup=True,
    )
    assert "VCP_BREAKOUT" in templates.match_templates(f)


def test_vcp_breakout_rejected_when_rs_too_low():
    f = base_features(
        close=52.0, sma20=50.0, sma50=48.0, sma200=45.0,
        rs_percentile=60.0,
        distance_to_pivot_pct=-2.0,
        consolidation_days=18,
        range_contraction=0.45,
        volume_dryup=True,
    )
    assert "VCP_BREAKOUT" not in templates.match_templates(f)


def test_vcp_breakout_rejected_when_far_below_pivot():
    f = base_features(
        rs_percentile=90.0, consolidation_days=18, range_contraction=0.4,
        volume_dryup=True, distance_to_pivot_pct=-15.0,
        close=52.0, sma20=50.0, sma50=48.0, sma200=45.0,
    )
    assert "VCP_BREAKOUT" not in templates.match_templates(f)


def test_trend_pullback_matches_on_sma20_touch():
    f = base_features(
        close=50.5, sma10=51.0, sma20=50.0, sma50=47.0, sma200=42.0,
        rs_percentile=75.0, rvol=0.7,
    )
    assert "TREND_PULLBACK" in templates.match_templates(f)


def test_trend_pullback_rejected_when_sma_stack_broken():
    f = base_features(
        close=50.5, sma10=51.0, sma20=50.0, sma50=52.0, sma200=42.0,
        rs_percentile=75.0, rvol=0.7,
    )
    assert "TREND_PULLBACK" not in templates.match_templates(f)


def test_mean_reversion_matches_connors_criteria():
    f = base_features(close=60.0, sma200=50.0, rsi2=6.0, percent_b=0.05)
    assert "MEAN_REVERSION" in templates.match_templates(f)


def test_mean_reversion_rejected_below_sma200():
    f = base_features(close=40.0, sma200=50.0, rsi2=6.0, percent_b=0.05)
    assert "MEAN_REVERSION" not in templates.match_templates(f)


def test_pead_matches_on_recent_earnings_gap():
    f = base_features(gap_pct=8.0, rvol=4.2, days_since_earnings=1, gap_low_held=True)
    assert "PEAD" in templates.match_templates(f)


def test_pead_rejected_when_gap_low_broken():
    f = base_features(gap_pct=8.0, rvol=4.2, days_since_earnings=1, gap_low_held=False)
    assert "PEAD" not in templates.match_templates(f)


def test_neutral_stock_matches_nothing():
    assert templates.match_templates(base_features()) == []


def test_a_stock_can_match_multiple_templates():
    f = base_features(
        close=52.0, sma10=52.5, sma20=50.0, sma50=48.0, sma200=45.0,
        rs_percentile=90.0, distance_to_pivot_pct=-1.0,
        consolidation_days=20, range_contraction=0.4, volume_dryup=True,
        rvol=0.7,
    )
    assert len(templates.match_templates(f)) >= 2
```

**Step 2: Fehlschlag bestätigen**

Run: `uv run pytest tests/test_templates.py -q`
Expected: FAIL — `No module named 'stockpicker.templates'`

**Step 3: Implementierung**

```python
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
        and f["consolidation_days"] >= 15          # ca. 3 Wochen
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
```

**Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_templates.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add stockpicker/templates.py tests/test_templates.py
git commit -m "feat: mechanical setup template matchers"
```

---

## Task 6: Datenabruf

**Files:**
- Create: `stockpicker/fetch.py`

Dünne Kapselung um yfinance, damit die Pipeline gemockt getestet werden kann. Keine eigene Testdatei — die Schnittstelle wird in Task 7 über den Mock mitgetestet.

**Step 1: Implementierung**

```python
"""Kapselung um yfinance. Die einzige Stelle im Projekt mit Marktdaten-Netzwerk."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import yfinance as yf

BATCH_SIZE = 150


def fetch_history(tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    """Laedt Tageskerzen in Batches. Titel ohne Daten fehlen im Ergebnis."""
    out: dict[str, pd.DataFrame] = {}
    for start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[start : start + BATCH_SIZE]
        raw = yf.download(
            batch,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
        )
        for ticker in batch:
            try:
                df = raw[ticker].dropna(how="all") if len(batch) > 1 else raw.dropna(how="all")
            except KeyError:
                continue
            if df.empty:
                continue
            out[ticker] = df
    return out


def fetch_meta(tickers: list[str]) -> dict[str, dict]:
    """Marktkapitalisierung, Short Float und naechster Earnings-Termin.

    yfinance liefert diese Felder unzuverlaessig. Fehlende Werte werden als
    None zurueckgegeben; die Pipeline darf daran nicht scheitern.
    """
    out: dict[str, dict] = {}
    for ticker in tickers:
        info = {}
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            pass
        out[ticker] = {
            "market_cap": info.get("marketCap"),
            "short_percent_of_float": info.get("shortPercentOfFloat"),
            "next_earnings": _next_earnings(info),
        }
    return out


def _next_earnings(info: dict) -> dt.date | None:
    timestamp = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
    if not timestamp:
        return None
    try:
        return dt.datetime.fromtimestamp(timestamp).date()
    except (OSError, ValueError, TypeError):
        return None
```

**Step 2: Smoke-Test gegen echte Daten** (manuell, kein pytest)

Run: `uv run python -c "from stockpicker.fetch import fetch_history; h = fetch_history(['AAOI','BBIO']); print({k: v.shape for k, v in h.items()})"`
Expected: Zwei Einträge mit rund 500 Zeilen und den Spalten Open/High/Low/Close/Volume.

**Step 3: Commit**

```bash
git add stockpicker/fetch.py
git commit -m "feat: yfinance data access layer"
```

---

## Task 7: Screening-Pipeline

**Files:**
- Create: `stockpicker/pipeline.py`
- Test: `tests/test_pipeline.py`

Die Pipeline bekommt Kursdaten und Metadaten als Argumente, nie über Netzwerk. Dadurch ist sie vollständig testbar.

**Step 1: Failing Tests schreiben**

```python
import datetime as dt

import pytest

from stockpicker import pipeline
from stockpicker.config import ScreenConfig
from tests.conftest import make_ohlcv


def uptrend_bars(n=400, start=20.0, step=0.05, volume=1_000_000):
    return make_ohlcv([start + step * i for i in range(n)], volume=volume)


def make_meta(market_cap=1_000_000_000, next_earnings=None, short=0.05):
    return {"market_cap": market_cap, "short_percent_of_float": short, "next_earnings": next_earnings}


def test_compute_features_returns_all_documented_keys():
    f = pipeline.compute_features("TEST", uptrend_bars(), make_meta(), sector="Health Care")
    for key in ["close", "sma20", "sma50", "sma200", "rsi14", "rsi2", "atr_pct",
                "rvol", "percent_b", "distance_to_pivot_pct", "consolidation_days",
                "range_contraction", "gap_pct", "market_cap", "sector",
                "dollar_volume", "perf_1m", "perf_3m", "perf_6m"]:
        assert key in f, f"fehlender Key: {key}"


def test_hard_filters_reject_penny_stock():
    bars = make_ohlcv([1.5] * 400)
    f = pipeline.compute_features("PENNY", bars, make_meta(), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and "Kurs" in reason


def test_hard_filters_reject_illiquid_stock():
    bars = uptrend_bars(volume=10_000)
    f = pipeline.compute_features("THIN", bars, make_meta(), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and ("Volumen" in reason or "Dollarvolumen" in reason)


def test_hard_filters_reject_market_cap_out_of_range():
    f = pipeline.compute_features("BIG", uptrend_bars(), make_meta(market_cap=50_000_000_000), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and "Marktkap" in reason


def test_hard_filters_reject_short_history():
    f = pipeline.compute_features("NEW", uptrend_bars(n=100), make_meta(), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and "Historie" in reason


def test_hard_filters_accept_normal_small_cap():
    ok, reason = pipeline.passes_hard_filters(
        pipeline.compute_features("GOOD", uptrend_bars(start=40.0, step=0.05), make_meta(), sector="X"),
        ScreenConfig(),
    )
    assert ok, reason


def test_earnings_within_blackout_is_excluded():
    soon = dt.date.today() + dt.timedelta(days=2)
    f = pipeline.compute_features("SOON", uptrend_bars(start=40.0), make_meta(next_earnings=soon), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and "Earnings" in reason


def test_recent_earnings_sets_pead_flag_without_exclusion():
    f = pipeline.compute_features("PEADCO", uptrend_bars(start=40.0), make_meta(), sector="X",
                                  days_since_earnings=1)
    ok, _ = pipeline.passes_hard_filters(f, ScreenConfig())
    assert ok
    assert f["days_since_earnings"] == 1


def test_rs_percentile_ranks_strongest_stock_highest():
    features = [
        {"ticker": "WEAK", "perf_3m": -5.0, "perf_1m": -2.0, "perf_6m": -8.0},
        {"ticker": "MID", "perf_3m": 5.0, "perf_1m": 2.0, "perf_6m": 8.0},
        {"ticker": "STRONG", "perf_3m": 40.0, "perf_1m": 15.0, "perf_6m": 60.0},
    ]
    ranked = pipeline.add_rs_percentile(features, benchmark_perf={"1m": 0.0, "3m": 0.0, "6m": 0.0})
    by_ticker = {f["ticker"]: f for f in ranked}
    assert by_ticker["STRONG"]["rs_percentile"] > by_ticker["MID"]["rs_percentile"]
    assert by_ticker["MID"]["rs_percentile"] > by_ticker["WEAK"]["rs_percentile"]
    assert 0.0 <= by_ticker["WEAK"]["rs_percentile"] <= 100.0


def test_select_candidates_keeps_only_template_matches():
    matched = {"ticker": "A", "templates": ["VCP_BREAKOUT"], "rs_percentile": 90.0}
    unmatched = {"ticker": "B", "templates": [], "rs_percentile": 95.0}
    selected = pipeline.select_candidates([matched, unmatched], ScreenConfig())
    assert [c["ticker"] for c in selected] == ["A"]


def test_select_candidates_caps_at_max_and_sorts_by_rs():
    features = [
        {"ticker": f"T{i}", "templates": ["PEAD"], "rs_percentile": float(i)}
        for i in range(30)
    ]
    selected = pipeline.select_candidates(features, ScreenConfig(max_candidates=5))
    assert len(selected) == 5
    assert [c["ticker"] for c in selected] == ["T29", "T28", "T27", "T26", "T25"]


def test_run_screen_end_to_end_with_injected_data():
    history = {
        "GOOD": uptrend_bars(start=40.0, step=0.05),
        "PENNY": make_ohlcv([1.2] * 400),
    }
    meta = {"GOOD": make_meta(), "PENNY": make_meta()}
    sectors = {"GOOD": "Health Care", "PENNY": "Energy"}
    benchmark = uptrend_bars(start=200.0, step=0.01)

    result = pipeline.run_screen(history, meta, sectors, benchmark)

    assert "PENNY" not in [c["ticker"] for c in result["candidates"]]
    assert result["stats"]["universe"] == 2
    assert result["stats"]["rejected"] >= 1
    assert "generated_at" in result
```

**Step 2: Fehlschlag bestätigen**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: FAIL — `No module named 'stockpicker.pipeline'`

**Step 3: Implementierung**

```python
"""Screening-Pipeline: Kursdaten rein, candidates.json-Struktur raus.

Diese Datei kennt kein Netzwerk. Alle Marktdaten kommen als Argument.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
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
        "consolidation_days": ind.consolidation_days(bars),
        "gap_pct": round(gap, 2),
        "gap_low_held": gap_low_held,
        # Kontext
        "market_cap": meta.get("market_cap"),
        "short_percent_of_float": meta.get("short_percent_of_float"),
        "next_earnings": meta.get("next_earnings").isoformat() if meta.get("next_earnings") else None,
        "days_to_earnings": _days_to(meta.get("next_earnings")),
        "days_since_earnings": days_since_earnings,
    }


def _days_to(date: dt.date | None) -> int | None:
    if date is None:
        return None
    return (date - dt.date.today()).days


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
    if atr_pct is None or not (cfg.min_atr_pct <= atr_pct <= cfg.max_atr_pct):
        return False, f"ATR% ausserhalb {cfg.min_atr_pct}-{cfg.max_atr_pct} ({atr_pct})"
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
    """Behaelt Titel mit mindestens einem Template-Treffer, deckelt nach RS-Rang."""
    matched = [f for f in features if f.get("templates")]
    matched.sort(key=lambda f: f.get("rs_percentile", 0.0), reverse=True)
    return matched[: cfg.max_candidates]


def run_screen(
    history: dict[str, pd.DataFrame],
    meta: dict[str, dict],
    sectors: dict[str, str],
    benchmark_bars: pd.DataFrame,
    cfg: ScreenConfig = SCREEN,
) -> dict:
    """Vollstaendiger Screen auf bereits geladenen Daten."""
    benchmark_close = benchmark_bars["Close"]
    benchmark_perf = {
        "1m": ind.performance_pct(benchmark_close, 21),
        "3m": ind.performance_pct(benchmark_close, 63),
        "6m": ind.performance_pct(benchmark_close, 126),
    }

    passed: list[dict] = []
    rejections: list[dict] = []

    for ticker, bars in history.items():
        try:
            f = compute_features(ticker, bars, meta.get(ticker, {}), sector=sectors.get(ticker, "Unknown"))
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
        "rejections": rejections[:50],
    }
```

**Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: PASS

Wenn `test_hard_filters_accept_normal_small_cap` scheitert, gibt der Ablehnungsgrund im Assert direkt aus, welche Schwelle greift — meistens ATR%, weil die synthetischen Bars eine zu enge oder zu weite Spanne haben. Dann `high_offset`/`low_offset` in der Fixture anpassen, nicht die Konfiguration.

**Step 5: Commit**

```bash
git add stockpicker/pipeline.py tests/test_pipeline.py
git commit -m "feat: screening pipeline with hard filters and RS ranking"
```

---

## Task 8: screen.py als CLI

**Files:**
- Create: `screen.py`

**Step 1: Implementierung**

```python
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

from stockpicker import fetch, pipeline, universe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Nur die ersten N Titel screenen")
    parser.add_argument("--out", default="candidates.json")
    args = parser.parse_args()

    rows, warning = universe.load_universe()
    if warning:
        print(f"WARNUNG: {warning}", file=sys.stderr)
    if args.limit:
        rows = rows[: args.limit]

    tickers = [r["ticker"] for r in rows]
    sectors = {r["ticker"]: r["sector"] for r in rows}
    print(f"Lade Kursdaten fuer {len(tickers)} Titel ...", file=sys.stderr)

    history = fetch.fetch_history(tickers)
    print(f"Kursdaten fuer {len(history)} Titel erhalten.", file=sys.stderr)

    # Metadaten sind teuer (ein Request pro Titel) — nur fuer Titel abrufen,
    # die die kursbasierten Filter ueberhaupt passieren koennen.
    prefiltered = [t for t, bars in history.items() if len(bars) >= 250 and 3.0 <= bars["Close"].iloc[-1] <= 100.0]
    print(f"Lade Metadaten fuer {len(prefiltered)} Titel ...", file=sys.stderr)
    meta = fetch.fetch_meta(prefiltered)

    benchmark = fetch.fetch_history(["IWM"])["IWM"]
    result = pipeline.run_screen(
        {t: history[t] for t in prefiltered}, meta, sectors, benchmark
    )
    if warning:
        result["warnings"] = [warning]

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, default=str)

    stats = result["stats"]
    print(
        f"Fertig: {stats['candidates']} Kandidaten aus {stats['universe']} Titeln "
        f"({stats['passed_filters']} durch Filter). Geschrieben nach {args.out}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 2: Schnelltest auf Teilmenge**

Run: `uv run python screen.py --limit 60 --out out/candidates-test.json`
Expected: Läuft ohne Traceback durch, meldet eine Kandidatenzahl (bei 60 Titeln oft 0–3, das ist normal), schreibt die Datei.

**Step 3: Voller Lauf**

Run: `time uv run python screen.py`
Expected: Läuft durch. Erwartete Dauer 10–25 Minuten, dominiert von `fetch_meta`. Ergebnis: `candidates.json` mit bis zu 20 Kandidaten.

Ist die Laufzeit untragbar, ist der erste Hebel `fetch_meta` — Marktkapitalisierung ließe sich alternativ aus `shares_outstanding × Kurs` in einem Batch schätzen. Erst optimieren, wenn die Zeit tatsächlich stört.

**Step 4: Commit**

```bash
git add screen.py
git commit -m "feat: screen.py CLI for stage 1"
```

---

## Task 9: PROMPT.md

**Files:**
- Create: `PROMPT.md`

Das ist der eigentliche Deliverable. Sieben Blöcke laut Design. Der vollständige Text ist unten; er wird eins zu eins übernommen.

**Step 1: Datei anlegen**

````markdown
# Täglicher Small-Cap-Swing-Analyse-Prompt

## 1 · Rolle und Mandat

Du bist ein disziplinierter Swing-Trading-Analyst für US-Small-Caps. Du bekommst
eine vorgefilterte Kandidatenliste mit fertig berechneten Kennzahlen. Deine
Aufgabe: pro Kandidat den Katalysator recherchieren, alle Kandidaten nach der
Rubrik in Block 5 bewerten und für die besten bis zu fünf ein Handelsdossier
schreiben.

Du gibst keine Anlageberatung. Du lieferst eine begründete Analyse mit
expliziten Einstiegs-, Stop- und Zielniveaus sowie Invalidierungskriterien.

**Konfiguration**
- Kontorisiko pro Trade: 1 %
- Mindest-Chance-Risiko-Verhältnis: 2:1
- Haltedauer: 2–10 Handelstage
- Richtung: ausschließlich Long

## 2 · Harte Verbote

Verstöße machen den gesamten Report unbrauchbar.

1. **Keine Zahl ohne Herkunft.** Jede Zahl im Output stammt entweder aus
   `candidates.json` (dann kennzeichnest du sie mit „Screener") oder aus einer
   Quelle, die du mit Titel, Datum und Link nennst. Du rechnest keine
   Indikatoren selbst nach und erfindest keine Kurse.
2. **Kein Auffüllen auf fünf.** Erreichen nur drei Kandidaten den Composite von
   60, gibst du drei aus und erklärst in einem Absatz, warum der Tag dünn ist.
   Ein sechster Platz existiert nicht, ein aufgerundeter fünfter auch nicht.
3. **Kein erfundener Katalysator.** Findest du nach ehrlicher Suche keinen,
   schreibst du „Kein Katalysator gefunden" und vergibst höchstens 3 Punkte auf
   dieser Achse. Ein starkes Chartbild ist kein Katalysator.
4. **Keine Prognose ohne Invalidierung.** Jedes Kursziel steht neben der
   Bedingung, unter der die These tot ist.
5. **Keine Reihenfolge-Verletzung.** Du recherchierst erst, nachdem du alle
   Kandidaten technisch klassifiziert hast (siehe Block 4).
6. **Maximal zwei Titel je Sektor.**

## 3 · Input

Du bekommst `candidates.json` mit dieser Struktur:

```json
{
  "generated_at": "2026-08-09T08:15:00",
  "benchmark": {"symbol": "IWM", "perf_1m": 2.1, "perf_3m": 5.4, "above_sma50": true},
  "stats": {"universe": 1950, "passed_filters": 210, "candidates": 18},
  "candidates": [
    {
      "ticker": "XYZ", "sector": "Health Care", "close": 42.15,
      "sma10": 41.8, "sma20": 40.9, "sma50": 38.2, "sma200": 33.4,
      "sma_stack_intact": true,
      "pct_from_52w_high": -4.2, "pct_from_52w_low": 68.3,
      "perf_1m": 8.4, "perf_3m": 22.1, "perf_6m": 41.0, "rs_percentile": 91.2,
      "rsi14": 61.2, "rsi2": 44.0, "macd_hist": 0.31, "adx14": 28.4,
      "atr14": 1.85, "atr_pct": 4.4,
      "bb_bandwidth": 8.1, "percent_b": 0.72, "range_contraction": 0.41,
      "avg_volume_20d": 890000, "dollar_volume": 37500000,
      "rvol": 1.3, "up_down_volume_ratio": 1.7, "obv_slope": 0.12,
      "volume_dryup": true,
      "distance_to_pivot_pct": -1.8, "consolidation_days": 21, "gap_pct": 0.4,
      "market_cap": 1250000000, "short_percent_of_float": 0.09,
      "next_earnings": "2026-09-04", "days_to_earnings": 26,
      "days_since_earnings": 99,
      "templates": ["VCP_BREAKOUT"]
    }
  ]
}
```

Alle Werte sind bereits geprüft. Du übernimmst sie, du korrigierst sie nicht.

## 4 · Arbeitsablauf

Arbeite diese sechs Schritte strikt in dieser Reihenfolge ab.

**Schritt 1 — Marktkontext.** Sieh dir `benchmark` an und recherchiere den
aktuellen Zustand des US-Small-Cap-Marktes: IWM-Trend, VIX-Niveau, anstehende
Makrotermine in den nächsten fünf Handelstagen (FOMC, CPI, Arbeitsmarkt). Halte
in drei Sätzen fest, ob das Umfeld Long-Swings begünstigt. Bei IWM unter SMA 50
und steigendem VIX senkst du alle Risiko-Scores um zwei Punkte und sagst das.

**Schritt 2 — Technische Klassifikation.** Gehe jeden Kandidaten durch und
bestimme allein anhand der Screener-Zahlen das *eine* Template, das ihn am
besten beschreibt, plus die Qualität dieser Erfüllung. Noch keine Websuche.
Notiere dir je Kandidat einen vorläufigen Technik-Score.

**Schritt 3 — Katalysator-Recherche.** Suche jetzt pro Kandidat nach dem
Auslöser. Mindestens zwei unabhängige Quellen mit Datum. Regeln in Block 5.

**Schritt 4 — Scoring.** Vergib die drei Achsen-Scores und berechne den
Composite. Der Technik-Score ist der aus Schritt 2; er darf durch die
Recherche nur dann korrigiert werden, wenn die Recherche ein *technisches*
Faktum liefert, das der Screener nicht kennen kann (Aktienplatzierung,
Reverse Split, Übernahmeangebot mit fixem Preis). Sag es dazu, wenn du
korrigierst.

**Schritt 5 — Auswahl.** Sortiere nach Composite. Streiche alles unter 60.
Wende die Sektorgrenze an: bei drei Titeln aus demselben Sektor fällt der
schwächste raus, auch wenn er über 60 liegt. Nimm die besten bis zu fünf.

**Schritt 6 — Dossiers schreiben.** Format nach Block 6.

## 5 · Scoring-Rubrik

Drei Achsen, je 0–10, gewichtet zu einem Composite von 0–100:

`Composite = 4 × Technik + 3,5 × Katalysator + 2,5 × Risiko`

### Achse A — Technisches Setup (Gewicht 40 %)

Ordne genau ein Template zu und bewerte dessen Qualität.

| Template | Kriterien |
|---|---|
| `VCP_BREAKOUT` | Konsolidierung ≥ 15 Handelstage, `range_contraction` ≤ 0,5, `volume_dryup` true, `distance_to_pivot_pct` zwischen −5 und +1, `rs_percentile` ≥ 80, SMA-Stapel intakt |
| `TREND_PULLBACK` | SMA-Stapel intakt, Kurs innerhalb 3 % um SMA 20 oder 50, `rvol` < 1, `rs_percentile` ≥ 60, `rsi14` zwischen 40 und 60 |
| `MEAN_REVERSION` | Kurs > SMA 200, `rsi2` < 10, `percent_b` < 0,1, `adx14` < 30 |
| `PEAD` | `days_since_earnings` ≤ 3, `gap_pct` > 5, `rvol` > 3, `gap_low_held` true |

Punktvergabe: **9–10** alle Kriterien erfüllt und mehrere deutlich übererfüllt.
**7–8** alle erfüllt, keines mit Reserve. **6** Teilerfüllung — das ist die
Obergrenze für jeden Kandidaten, der nicht alle Kriterien seines Templates
erfüllt. **3–5** Template nur grob erkennbar. **0–2** kein sauberes Setup.

### Achse B — Katalysator und Narrativ (Gewicht 35 %)

Bewertet werden Härte und Frische, nicht die Trefferzahl.

| Punkte | Auslöser |
|---|---|
| 9–10 | Harte Unternehmensmeldung mit Zahlenwirkung: Guidance-Anhebung, Großauftrag, Zulassung, Übernahmeangebot, positive Studiendaten |
| 7–8 | Starkes Quartal über Konsens, mehrfache Analysten-Hochstufungen mit Kurszielanhebung |
| 5–6 | Einzelnes Analysten-Upgrade, klare Sektor-Rotation mit Peer-Bestätigung |
| 3–4 | Diffuse positive Presse, Insiderkäufe ohne weiteren Kontext |
| 0–2 | Nichts gefunden, oder ausschließlich Retail-Buzz |

Älter als fünf Handelstage: Score halbieren. Bereits mit über 20 % Kursanstieg
verarbeitet: zwei Punkte abziehen und das im Dossier begründen.

**Quellenregeln.** Zugelassen: Yahoo Finance, Reuters, Bloomberg, CNBC,
Barron's, MarketWatch, Seeking Alpha (nur Faktenteile), SEC-Filings über EDGAR,
Unternehmens-IR. Ausgeschlossen als Faktenquelle: Promo-Newsletter, Stocktwits,
Reddit, Aggregatoren ohne benannte Primärquelle. Jede Quelle mit Datum und Link.

### Achse C — Risiko, Liquidität und Chance-Risiko-Verhältnis (Gewicht 25 %)

Beginne bei 5 Punkten und rechne:

- R:R zum ersten Target ≥ 3:1 → +2 · ≥ 2:1 → +1 · unter 2:1 → Kandidat streichen
- Stopweite ≥ 1,5 ATR → +1 · unter 1,0 ATR → −2 (Rauschen holt den Stop ab)
- `dollar_volume` > 25 Mio USD → +1 · unter 8 Mio → −1
- `short_percent_of_float` > 0,20 → +1 (Squeeze-Potenzial), > 0,40 → −1 (Bilanzrisiko)
- Earnings innerhalb der geplanten Haltedauer → −3
- `atr_pct` > 9 → −1

Ergebnis auf 0–10 begrenzen.

## 6 · Output-Format

### Kopf

```markdown
# Small-Cap Swing Setups — <Datum>

**Marktkontext:** <drei Sätze aus Schritt 1>

| # | Ticker | Setup | Composite | Entry | Stop | Target 1 | R:R | Katalysator |
|---|--------|-------|-----------|-------|------|----------|-----|-------------|
```

### Pro Titel, 600–900 Wörter, feste Reihenfolge

```markdown
## <n>. <TICKER> — <Firmenname> (<Sektor>)

**These**
<Zwei Sätze: was passiert hier, und warum ist genau jetzt der Moment.>

**Score**
- Technik <x>/10 — <eine Zeile>
- Katalysator <x>/10 — <eine Zeile>
- Risiko <x>/10 — <eine Zeile>
- **Composite <xx>/100**

**Technische Lage**
<Template, Kursstruktur, die relevanten Screener-Zahlen im Fließtext. Nenne
konkret, welche Kriterien erfüllt sind und welche nicht.>

**Katalysator**
<Was, wann, welche Quelle. Einschätzung, wie viel davon der Kurs schon
verarbeitet hat. Quellen als Liste mit Datum und Link.>

**Handelsplan**
- Entry: <Bedingung, nicht nur ein Preis — etwa "über 42,80 bei RVOL > 1,5">
- Stop: <Preis> (<x> ATR unter Entry, unter <Struktur, die er verletzt>)
- Target 1: <Preis> — <Herleitung: Widerstand, Messziel, ATR-Vielfaches>
- Target 2: <Preis> — <Herleitung>
- R:R zu Target 1: <x,x>:1
- Positionsgröße bei 1 % Kontorisiko: <Kontogröße × 0,01 ÷ (Entry − Stop)> Stück

**Bear-Case**
<Das stärkste Gegenargument, ernsthaft formuliert. Nicht "der Markt könnte
fallen", sondern der konkrete Grund, warum dieses Setup scheitern könnte.>

**Invalidierung**
<Ein konkretes Ereignis, unabhängig vom Stop, das die These beendet.>
```

### Abschluss

```markdown
## Verworfene Kandidaten
<Je ein Satz pro nicht ausgewähltem Kandidaten mit dem Ausschlussgrund.>

## Risikohinweis
Keine Anlageberatung. Small Caps sind illiquide und volatil; Gaps können jeden
Stop überspringen. Alle Angaben ohne Gewähr.
```

### Maschinenlesbare Zeile am Ende

Hänge nach dem Report einen JSON-Block an, den `validate.py` einliest:

```json
{"date": "2026-08-09", "picks": [
  {"ticker": "XYZ", "setup": "VCP_BREAKOUT", "sector": "Health Care",
   "score_technical": 8, "score_catalyst": 7, "score_risk": 7, "composite": 76,
   "entry": 42.80, "stop": 40.10, "target1": 48.20, "target2": 52.00,
   "catalyst": "FDA-Zulassung 2026-08-06",
   "sources": ["https://..."]}
]}
```

## 7 · Selbstprüfung vor der Ausgabe

Arbeite diese Liste explizit ab und gib sie am Ende als Häkchenliste aus:

- [ ] Jede Zahl trägt „Screener" oder eine Quelle mit Link
- [ ] Kein Titel unter Composite 60 im Report
- [ ] Kein Sektor mit mehr als zwei Titeln
- [ ] Jedes R:R zu Target 1 mindestens 2:1
- [ ] Jeder Stop liegt unter dem Entry, jedes Target darüber
- [ ] Jeder Titel hat Bear-Case und Invalidierung
- [ ] Kandidaten ohne gefundenen Katalysator haben Katalysator-Score ≤ 3
- [ ] JSON-Block am Ende ist valide und enthält genau die Titel des Reports
````

**Step 2: Manuell gegenlesen**

Prüfen, ob die Rubrik-Tabellen dieselben Schwellen tragen wie `stockpicker/templates.py`. Weichen sie ab, gilt das Design-Dokument, und beide Stellen werden angeglichen.

**Step 3: Commit**

```bash
git add PROMPT.md
git commit -m "feat: daily swing analysis prompt with scoring rubric"
```

---

## Task 10: Ledger und Level-Validierung

**Files:**
- Create: `stockpicker/ledger.py`
- Test: `tests/test_ledger.py`

**Step 1: Failing Tests schreiben**

```python
import json

import pytest

from stockpicker import ledger
from stockpicker.config import TradeConfig


def good_pick(**overrides):
    pick = {
        "ticker": "XYZ", "setup": "VCP_BREAKOUT", "sector": "Health Care",
        "score_technical": 8, "score_catalyst": 7, "score_risk": 7, "composite": 76,
        "entry": 42.80, "stop": 40.10, "target1": 48.20, "target2": 52.00,
        "catalyst": "FDA-Zulassung", "sources": ["https://example.com/a"],
    }
    pick.update(overrides)
    return pick


CANDIDATE = {"ticker": "XYZ", "close": 42.15, "atr14": 1.85, "sector": "Health Care"}


def test_valid_pick_passes():
    errors = ledger.validate_pick(good_pick(), CANDIDATE, TradeConfig())
    assert errors == []


def test_stop_above_entry_is_rejected():
    errors = ledger.validate_pick(good_pick(stop=43.50), CANDIDATE, TradeConfig())
    assert any("Stop" in e for e in errors)


def test_target_below_entry_is_rejected():
    errors = ledger.validate_pick(good_pick(target1=41.00), CANDIDATE, TradeConfig())
    assert any("Target 1" in e for e in errors)


def test_target2_below_target1_is_rejected():
    errors = ledger.validate_pick(good_pick(target2=45.00), CANDIDATE, TradeConfig())
    assert any("Target 2" in e for e in errors)


def test_risk_reward_below_minimum_is_rejected():
    # Entry 42.80, Stop 40.10 -> Risiko 2.70; Target 45.00 -> R:R 0.81
    errors = ledger.validate_pick(good_pick(target1=45.00, target2=50.00), CANDIDATE, TradeConfig())
    assert any("R:R" in e for e in errors)


def test_entry_far_from_last_close_is_rejected():
    errors = ledger.validate_pick(good_pick(entry=90.0, stop=80.0, target1=115.0, target2=130.0),
                                  CANDIDATE, TradeConfig())
    assert any("Entry" in e and "Schlusskurs" in e for e in errors)


def test_composite_below_threshold_is_rejected():
    errors = ledger.validate_pick(good_pick(composite=55), CANDIDATE, TradeConfig())
    assert any("Composite" in e for e in errors)


def test_unknown_ticker_is_rejected():
    errors = ledger.validate_pick(good_pick(ticker="NOPE"), CANDIDATE, TradeConfig())
    assert any("Kandidatenliste" in e for e in errors)


def test_tight_stop_produces_warning_not_error():
    # Stop 1.0 ATR entfernt -> Warnung, kein Fehler
    tight = good_pick(stop=42.80 - 1.0 * 1.85, target1=42.80 + 4.0 * 1.85, target2=42.80 + 6.0 * 1.85)
    errors = ledger.validate_pick(tight, CANDIDATE, TradeConfig())
    warnings = ledger.warn_pick(tight, CANDIDATE, TradeConfig())
    assert errors == []
    assert any("ATR" in w for w in warnings)


def test_validate_report_rejects_sector_concentration():
    picks = [good_pick(ticker=f"T{i}") for i in range(3)]
    candidates = [dict(CANDIDATE, ticker=f"T{i}") for i in range(3)]
    errors = ledger.validate_report(picks, candidates, TradeConfig())
    assert any("Sektor" in e for e in errors)


def test_validate_report_rejects_too_many_picks():
    picks = [good_pick(ticker=f"T{i}", sector=f"S{i}") for i in range(6)]
    candidates = [dict(CANDIDATE, ticker=f"T{i}", sector=f"S{i}") for i in range(6)]
    errors = ledger.validate_report(picks, candidates, TradeConfig())
    assert any("mehr als" in e for e in errors)


def test_fewer_than_five_picks_is_allowed():
    picks = [good_pick(ticker="A", sector="S1"), good_pick(ticker="B", sector="S2")]
    candidates = [dict(CANDIDATE, ticker="A", sector="S1"), dict(CANDIDATE, ticker="B", sector="S2")]
    assert ledger.validate_report(picks, candidates, TradeConfig()) == []


def test_append_entries_writes_one_json_line_per_pick(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_entries(path, "2026-08-09", [good_pick(ticker="A", sector="S1"),
                                               good_pick(ticker="B", sector="S2")])
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["ticker"] == "A"
    assert first["date"] == "2026-08-09"
    assert first["status"] == "OPEN"


def test_append_entries_is_idempotent_per_day(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_entries(path, "2026-08-09", [good_pick(ticker="A")])
    with pytest.raises(ValueError, match="bereits"):
        ledger.append_entries(path, "2026-08-09", [good_pick(ticker="A")])
```

**Step 2: Fehlschlag bestätigen**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: FAIL — `No module named 'stockpicker.ledger'`

**Step 3: Implementierung**

```python
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


def append_entries(path: Path, date: str, picks: list[dict]) -> int:
    """Schreibt je Pick eine Zeile. Ein Datum darf nur einmal vorkommen."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("date") == date:
                raise ValueError(f"Fuer {date} sind bereits Eintraege im Ledger vorhanden")

    with path.open("a", encoding="utf-8") as handle:
        for pick in picks:
            entry = dict(pick)
            entry["date"] = date
            entry["status"] = "OPEN"
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return len(picks)


def read_entries(path: Path = DEFAULT_LEDGER) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
```

**Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: PASS

**Step 5: Commit**

```bash
git add stockpicker/ledger.py tests/test_ledger.py
git commit -m "feat: pick validation and ledger persistence"
```

---

## Task 11: validate.py als CLI

**Files:**
- Create: `validate.py`

**Step 1: Implementierung**

```python
#!/usr/bin/env python3
"""Stufe 3: Prueft die Picks des Modells und schreibt sie in den Ledger.

Verwendung:
    uv run python validate.py picks.json
    uv run python validate.py picks.json --candidates candidates.json --dry-run

picks.json ist der JSON-Block vom Ende des Modell-Reports.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from stockpicker import ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("picks", help="JSON-Datei mit dem picks-Block aus dem Report")
    parser.add_argument("--candidates", default="candidates.json")
    parser.add_argument("--ledger", default=str(ledger.DEFAULT_LEDGER))
    parser.add_argument("--dry-run", action="store_true", help="Nur pruefen, nichts schreiben")
    args = parser.parse_args()

    picks_data = json.loads(Path(args.picks).read_text(encoding="utf-8"))
    candidates_data = json.loads(Path(args.candidates).read_text(encoding="utf-8"))

    date = picks_data["date"]
    picks = picks_data["picks"]
    candidates = candidates_data["candidates"]
    by_ticker = {c["ticker"]: c for c in candidates}

    errors = ledger.validate_report(picks, candidates)
    if errors:
        print("VALIDIERUNG FEHLGESCHLAGEN:", file=sys.stderr)
        for error in errors:
            print(f"  ✗ {error}", file=sys.stderr)
        print("\nKein Ledger-Eintrag geschrieben.", file=sys.stderr)
        return 1

    for pick in picks:
        for warning in ledger.warn_pick(pick, by_ticker[pick["ticker"]]):
            print(f"  ! {warning}", file=sys.stderr)

    if args.dry_run:
        print(f"OK: {len(picks)} Picks valide (dry-run, nichts geschrieben).")
        return 0

    count = ledger.append_entries(Path(args.ledger), date, picks)
    print(f"OK: {count} Picks fuer {date} in {args.ledger} geschrieben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Step 2: Gegen ein Beispiel testen**

```bash
mkdir -p out
cat > out/picks-example.json <<'JSON'
{"date": "2026-08-09", "picks": [
  {"ticker": "FAKE", "setup": "VCP_BREAKOUT", "sector": "Health Care",
   "score_technical": 8, "score_catalyst": 7, "score_risk": 7, "composite": 76,
   "entry": 42.80, "stop": 40.10, "target1": 48.20, "target2": 52.00,
   "catalyst": "Test", "sources": ["https://example.com"]}]}
JSON
cat > out/candidates-example.json <<'JSON'
{"candidates": [{"ticker": "FAKE", "close": 42.15, "atr14": 1.85, "sector": "Health Care"}]}
JSON
```

Run: `uv run python validate.py out/picks-example.json --candidates out/candidates-example.json --dry-run`
Expected: `OK: 1 Picks valide (dry-run, nichts geschrieben).`

Dann ein Fehlerfall — Stop über Entry:

Run: `sed 's/40.10/43.50/' out/picks-example.json > out/picks-bad.json && uv run python validate.py out/picks-bad.json --candidates out/candidates-example.json --dry-run; echo "exit=$?"`
Expected: Fehlermeldung mit „Stop … liegt nicht unter dem Entry", `exit=1`.

**Step 3: Commit**

```bash
git add validate.py
git commit -m "feat: validate.py CLI for stage 3"
```

---

## Task 12: Auswertung

**Files:**
- Create: `stockpicker/outcomes.py`, `review.py`
- Test: `tests/test_outcomes.py`

**Step 1: Failing Tests schreiben**

```python
import pytest

from stockpicker import outcomes
from tests.conftest import make_ohlcv


ENTRY_PICK = {
    "ticker": "XYZ", "date": "2026-07-20", "setup": "VCP_BREAKOUT",
    "entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": 120.0,
    "score_catalyst": 7,
}


def bars_from(highs, lows):
    import pandas as pd
    idx = pd.bdate_range(start="2026-07-21", periods=len(highs))
    return pd.DataFrame({"Open": lows, "High": highs, "Low": lows, "Close": lows}, index=idx)


def test_not_triggered_when_price_never_reaches_entry():
    bars = bars_from(highs=[98, 97, 96], lows=[95.5, 95.2, 95.1])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "NOT_TRIGGERED"
    assert result["r_multiple"] == 0.0


def test_target_hit_before_stop_yields_positive_r():
    bars = bars_from(highs=[101, 105, 111], lows=[99, 100, 104])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "TARGET1"
    assert result["r_multiple"] == pytest.approx(2.0)


def test_stop_hit_before_target_yields_minus_one_r():
    bars = bars_from(highs=[101, 102, 103], lows=[99, 96, 94])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "STOP"
    assert result["r_multiple"] == pytest.approx(-1.0)


def test_same_day_stop_and_target_counts_as_stop():
    # Konservativ: bei Unklarheit innerhalb einer Bar zaehlt der Stop
    bars = bars_from(highs=[101, 115], lows=[99, 90])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "STOP"


def test_still_open_when_neither_level_reached():
    bars = bars_from(highs=[101, 103, 104], lows=[99, 100, 101])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "OPEN"


def test_entry_outside_trigger_window_is_not_triggered():
    bars = bars_from(highs=[95, 96, 97, 105], lows=[94, 95, 96, 100])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "NOT_TRIGGERED"


def test_summarize_groups_by_setup():
    results = [
        {"setup": "VCP_BREAKOUT", "status": "TARGET1", "r_multiple": 2.0, "score_catalyst": 8},
        {"setup": "VCP_BREAKOUT", "status": "STOP", "r_multiple": -1.0, "score_catalyst": 5},
        {"setup": "PEAD", "status": "STOP", "r_multiple": -1.0, "score_catalyst": 9},
    ]
    summary = outcomes.summarize(results)
    vcp = summary["by_setup"]["VCP_BREAKOUT"]
    assert vcp["trades"] == 2
    assert vcp["win_rate"] == pytest.approx(50.0)
    assert vcp["expectancy_r"] == pytest.approx(0.5)
    assert summary["by_setup"]["PEAD"]["win_rate"] == pytest.approx(0.0)


def test_summarize_ignores_not_triggered_and_open():
    results = [
        {"setup": "PEAD", "status": "NOT_TRIGGERED", "r_multiple": 0.0, "score_catalyst": 5},
        {"setup": "PEAD", "status": "OPEN", "r_multiple": 0.0, "score_catalyst": 5},
        {"setup": "PEAD", "status": "TARGET1", "r_multiple": 2.0, "score_catalyst": 5},
    ]
    summary = outcomes.summarize(results)
    assert summary["by_setup"]["PEAD"]["trades"] == 1
    assert summary["not_triggered"] == 1
    assert summary["open"] == 1


def test_summarize_groups_by_catalyst_bucket():
    results = [
        {"setup": "PEAD", "status": "TARGET1", "r_multiple": 2.0, "score_catalyst": 9},
        {"setup": "PEAD", "status": "STOP", "r_multiple": -1.0, "score_catalyst": 2},
    ]
    summary = outcomes.summarize(results)
    assert summary["by_catalyst"]["stark (7-10)"]["win_rate"] == pytest.approx(100.0)
    assert summary["by_catalyst"]["schwach (0-3)"]["win_rate"] == pytest.approx(0.0)
```

**Step 2: Fehlschlag bestätigen**

Run: `uv run pytest tests/test_outcomes.py -q`
Expected: FAIL — `No module named 'stockpicker.outcomes'`

**Step 3: Implementierung von `stockpicker/outcomes.py`**

```python
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
    }

    if trigger_index is None:
        return {**base, "status": "NOT_TRIGGERED", "r_multiple": 0.0}

    for i in range(trigger_index, len(bars)):
        hit_stop = bars["Low"].iloc[i] <= stop
        hit_target = bars["High"].iloc[i] >= target1
        if hit_stop:
            return {**base, "status": "STOP", "r_multiple": -1.0}
        if hit_target:
            return {**base, "status": "TARGET1", "r_multiple": round((target1 - entry) / risk, 2)}

    last_close = float(bars["Close"].iloc[-1])
    return {**base, "status": "OPEN", "r_multiple": round((last_close - entry) / risk, 2)}


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
```

**Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_outcomes.py -q`
Expected: PASS

**Step 5: `review.py` implementieren**

```python
#!/usr/bin/env python3
"""Wertet den Ledger gegen den tatsaechlichen Kursverlauf aus.

Verwendung:
    uv run python review.py                # alle Eintraege aelter als 10 Tage
    uv run python review.py --days 20
"""

from __future__ import annotations

import argparse
import datetime as dt

import yfinance as yf

from stockpicker import ledger, outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=10, help="Beobachtungsfenster in Kalendertagen")
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
        bars = yf.download(entry["ticker"], start=start, end=end, interval="1d",
                           auto_adjust=False, progress=False)
        if bars.empty:
            print(f"  ! Keine Kursdaten fuer {entry['ticker']} ab {start}")
            continue
        if isinstance(bars.columns, type(bars.columns)) and bars.columns.nlevels > 1:
            bars.columns = bars.columns.droplevel(1)
        results.append(outcomes.evaluate(entry, bars))

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
```

**Step 6: Auf leerem Ledger prüfen**

Run: `uv run python review.py`
Expected: `Ledger ist leer — noch nichts auszuwerten.`

**Step 7: Commit**

```bash
git add stockpicker/outcomes.py review.py tests/test_outcomes.py
git commit -m "feat: outcome evaluation and review CLI"
```

---

## Task 13: README und Gesamtdurchlauf

**Files:**
- Create: `README.md`

**Step 1: README schreiben**

```markdown
# stock-picker

Täglicher Screener plus Analyse-Prompt für Small-Cap-Swing-Setups im Russell 2000.

**Kein Anlageberatungssystem.** Das Werkzeug strukturiert Recherche und macht
Entscheidungen im Nachhinein überprüfbar. Es prognostiziert keine Kurse.

## Tagesablauf

```bash
# 1 — Screener (10-25 Min, dominiert vom Metadaten-Abruf)
uv run python screen.py

# 2 — candidates.json zusammen mit PROMPT.md in eine Claude-Session mit
#     Websuche geben. Report entsteht, JSON-Block ans Ende kopieren:
#     -> picks.json

# 3 — Levels prüfen und Ledger fortschreiben
uv run python validate.py picks.json

# Wöchentlich — auswerten, was funktioniert hat
uv run python review.py --days 10
```

## Aufbau

| Datei | Rolle |
|---|---|
| `screen.py` | Stufe 1 — Kennzahlen und Hard-Filter, schreibt `candidates.json` |
| `PROMPT.md` | Stufe 2 — Rubrik, Arbeitsablauf, Output-Format für das Modell |
| `validate.py` | Stufe 3 — prüft Levels gegen Kursdaten, schreibt `data/ledger.jsonl` |
| `review.py` | Auswertung — Trefferquote und Erwartungswert je Setup-Typ |

Zahlen berechnet Code, Kontext bewertet das Modell, Levels werden maschinell
gegengeprüft. Details in `docs/plans/2026-08-09-swing-trade-prompt-design.md`.

## Entwicklung

```bash
uv venv --python 3.12 && uv sync --all-groups
uv run pytest
```

Kein Test darf Netzwerk verwenden.

## Kalibrierung

Nach etwa 30 Läufen zeigt `review.py`, welche der vier Setup-Templates tragen.
Die Gewichtung wird dann in `PROMPT.md` angepasst — Block 5, nicht im Code.
```

**Step 2: Gesamte Testsuite laufen lassen**

Run: `uv run pytest -q`
Expected: Alle Tests grün, keine Warnungen über übersprungene Dateien.

**Step 3: Echter Gesamtdurchlauf**

Run: `uv run python screen.py --limit 300 --out candidates.json && uv run python -c "import json; d=json.load(open('candidates.json')); print(d['stats']); print([c['ticker'] for c in d['candidates']])"`
Expected: Stats-Dict und eine Kandidatenliste. Bei 300 Titeln sind 0–5 Kandidaten normal — der Filter ist absichtlich streng.

Kommen bei einem *vollen* Lauf null Kandidaten heraus, ist das eine Information über den Markt, kein Bug. Zur Kontrolle die Ablehnungsgründe ansehen:

Run: `uv run python -c "import json,collections; d=json.load(open('candidates.json')); print(collections.Counter(r['reason'].split('(')[0].strip() for r in d['rejections']).most_common())"`
Expected: Verteilung der Ablehnungsgründe — zeigt, welcher Filter greift.

**Step 4: Commit**

```bash
git add README.md
git commit -m "docs: usage and daily workflow for stock-picker"
```

---

## Abnahmekriterien

- [ ] `uv run pytest` läuft grün, kein Test berührt das Netzwerk
- [ ] `screen.py` erzeugt `candidates.json` mit allen im Design gelisteten Kennzahlen
- [ ] `PROMPT.md` enthält alle sieben Blöcke, und die Template-Schwellen decken sich mit `stockpicker/templates.py`
- [ ] `validate.py` bricht bei Stop über Entry, R:R unter 2:1 und abwegigem Entry mit Exit-Code 1 ab
- [ ] `validate.py` akzeptiert weniger als fünf Picks ohne Beschwerde
- [ ] `review.py` läuft auf leerem Ledger fehlerfrei
- [ ] Ein vollständiger Tagesdurchlauf ist einmal von Hand durchgespielt worden

## Bewusst nicht enthalten

Kein Backtest-Framework, kein Broker-Anschluss, keine Intraday-Daten, keine
Automatisierung der Modell-Stufe, kein Web-Frontend. Der Ledger liefert die
Evidenz; alles weitere wartet, bis diese Evidenz zeigt, dass sich der Aufwand
lohnt.

---

## Nachtrag — was die Umsetzung am Plan korrigiert hat

Der Plan oben ist der Stand vor der Implementierung. Diese Punkte haben sich
beim Bauen und beim ersten echten Lauf als falsch erwiesen und sind im Code
bereits korrigiert.

**Fachliche Fehler**

1. `rvol` verwendete `df["Volume"].shift(0)` — ein No-Op. Die Baseline enthielt
   den heutigen Tag und verdünnte damit genau das Signal, das RVOL misst.
   Korrigiert auf `shift(1)`.
2. `consolidation_days` war mit dem ATR-Filter unvereinbar und machte
   `VCP_BREAKOUT` strukturell unerfüllbar. Ersetzt durch `base_length` und
   `base_depth_pct` nach Minervinis Basis-Definition. Details im Nachtrag des
   Design-Dokuments.
3. `days_since_earnings` wurde nirgends verdrahtet — `PEAD` hätte produktiv nie
   auslösen können. `fetch_meta` liefert jetzt `last_earnings`, `run_screen`
   rechnet die Tage aus.
4. `_next_earnings` fiel auf ein Vergangenheitsdatum zurück, wenn kein
   zukünftiger Termin bekannt war. Gibt jetzt `None` zurück.

**Externe Realität**

5. Der iShares-`.ajax`-Endpunkt liefert HTML statt CSV. Aktuelle Quelle ist
   `https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/latest-holdings.csv`.
6. Installiert ist yfinance 1.5.2, nicht 0.2.x: Spalten kommen immer als
   MultiIndex, `auto_adjust=False` bringt eine zusätzliche `Adj Close`-Spalte,
   und ungültige Ticker erscheinen als NaN-Spalten statt einen `KeyError` zu
   werfen. `fetch._normalize` deckt das ab.
7. `earningsTimestamp` ist der zuletzt gemeldete Termin, nicht der nächste.

**Ergänzungen gegenüber dem Plan**

8. `rejection_summary` zählt alle Ablehnungen; die im Plan vorgesehene Liste ist
   auf 50 Einträge gedeckelt und verschluckt genau die seltenen Filter.
9. `validate.py` liest den Picks-Block auch aus einem Markdown-Report mit
   ```json-Fence — so, wie das Modell ihn tatsächlich liefert.
10. `review.py` nutzt `fetch.fetch_history_range` statt yfinance direkt.
11. Ein zweiter `validate.py`-Lauf am selben Tag meldet den Konflikt lesbar
    statt per Traceback.
12. `pythonpath = ["."]` in `pyproject.toml`, sonst ist das Package in Tests
    nicht importierbar.

**Laufzeit**

Der volle Lauf über 1950 Titel liegt bei rund 10 Minuten, nicht bei den im Plan
veranschlagten 10–25. Die Metadaten-Optimierung wird vorerst nicht gebraucht.

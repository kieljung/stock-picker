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


def test_pct_from_52w_high_is_zero_at_new_high():
    # high_offset=0: die letzte Bar schliesst exakt auf ihrem eigenen Hoch.
    # Mit dem Standard-Fixture (high_offset=1.0) liegt der Close bauartbedingt
    # 1.0 unter dem 52W-High, ein Ergebnis von 0.0 ist dort unmoeglich.
    df = make_ohlcv([50.0 + 0.5 * i for i in range(300)], high_offset=0.0)
    assert ind.pct_from_52w_high(df) == pytest.approx(0.0, abs=0.01)


def test_pct_from_52w_high_is_negative_after_pullback():
    closes = [50.0 + 0.5 * i for i in range(260)] + [100.0]
    df = make_ohlcv(closes)
    assert ind.pct_from_52w_high(df) < -10


def test_performance_pct_matches_hand_calculation():
    closes = [100.0] * 20 + [110.0]
    df = make_ohlcv(closes)
    assert ind.performance_pct(df["Close"], 20) == pytest.approx(10.0)


def test_base_length_is_zero_at_a_new_high():
    # high_offset/low_offset 0: die Peak-Bar hat keine eigene Spanne, die als
    # Basis-Tiefe zaehlen wuerde.
    df = make_ohlcv([50.0 + 0.5 * i for i in range(300)], high_offset=0.0, low_offset=0.0)
    length, depth = ind.base_metrics(df)
    assert length == 0
    assert depth == pytest.approx(0.0, abs=0.01)


def test_base_length_counts_bars_since_the_peak():
    # 40 Tage Anstieg auf das Hoch, danach 15 Tage seitwaerts darunter
    closes = [50.0 + 0.5 * i for i in range(40)] + [66.0] * 15
    df = make_ohlcv(closes, high_offset=0.1, low_offset=0.1)
    length, _ = ind.base_metrics(df)
    assert length == 15


def test_base_depth_measures_drawdown_from_the_peak():
    # Hoch bei 100 (High 100.0), danach Tief bei 80 -> 20 % Tiefe
    closes = [100.0] + [80.0] * 10
    df = make_ohlcv(closes, high_offset=0.0, low_offset=0.0)
    _, depth = ind.base_metrics(df)
    assert depth == pytest.approx(20.0)


def test_base_metrics_survive_a_single_wide_bar():
    """Die alte Definition kippte bei einer weiten Bar; diese darf das nicht."""
    closes = [50.0 + 0.5 * i for i in range(40)] + [66.0] * 8 + [64.0] + [66.0] * 6
    df = make_ohlcv(closes, high_offset=0.1, low_offset=0.1)
    length, depth = ind.base_metrics(df)
    assert length == 15
    assert depth < 10.0


def test_distance_to_pivot_is_zero_at_20d_high():
    # Analog: nur wenn der letzte Close auf dem eigenen High liegt, ist der
    # Abstand zum 20-Tage-Pivot exakt 0.
    df = make_ohlcv([50.0 + 0.5 * i for i in range(300)], high_offset=0.0)
    assert ind.distance_to_pivot_pct(df, 20) == pytest.approx(0.0, abs=0.01)


def test_gap_pct_matches_hand_calculation():
    df = make_ohlcv([100.0, 100.0])
    df.iloc[-1, df.columns.get_loc("Open")] = 105.0
    assert ind.gap_pct(df) == pytest.approx(5.0)

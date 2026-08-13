"""Offline-Tests fuer die SQLite-Persistenz. Kein Netzwerk."""

import datetime as dt

import pandas as pd
import pytest

from stockpicker import store
from tests.conftest import make_ohlcv


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    yield c
    c.close()


def test_save_and_load_roundtrip_preserves_values(conn):
    df = make_ohlcv([10.0, 11.0, 12.0])
    store.save_bars(conn, {"AAA": df})
    loaded = store.load_bars(conn, ["AAA"])["AAA"]

    assert list(loaded.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(loaded) == 3
    assert loaded["Close"].tolist() == [10.0, 11.0, 12.0]
    assert loaded["High"].tolist() == [11.0, 12.0, 13.0]


def test_load_bars_returns_ascending_index(conn):
    store.save_bars(conn, {"AAA": make_ohlcv([10.0, 11.0, 12.0])})
    loaded = store.load_bars(conn, ["AAA"])["AAA"]
    assert loaded.index.is_monotonic_increasing


def test_save_bars_is_idempotent(conn):
    df = make_ohlcv([10.0, 11.0])
    store.save_bars(conn, {"AAA": df})
    store.save_bars(conn, {"AAA": df})
    assert len(store.load_bars(conn, ["AAA"])["AAA"]) == 2


def test_save_bars_overwrites_corrected_values(conn):
    """Nachtraegliche Kurskorrekturen muessen den alten Wert ersetzen."""
    store.save_bars(conn, {"AAA": make_ohlcv([10.0, 11.0])})
    corrected = make_ohlcv([10.0, 99.0])
    store.save_bars(conn, {"AAA": corrected})
    assert store.load_bars(conn, ["AAA"])["AAA"]["Close"].tolist() == [10.0, 99.0]


def test_last_bar_dates_reports_the_newest_day_per_ticker(conn):
    store.save_bars(conn, {"AAA": make_ohlcv([1.0] * 5), "BBB": make_ohlcv([1.0] * 3)})
    dates = store.last_bar_dates(conn)
    assert dates["AAA"] == dt.date(2026, 8, 7)
    assert dates["BBB"] == dt.date(2026, 8, 7)


def test_last_bar_dates_is_empty_on_fresh_db(conn):
    assert store.last_bar_dates(conn) == {}


def test_load_bars_skips_unknown_tickers(conn):
    store.save_bars(conn, {"AAA": make_ohlcv([1.0, 2.0])})
    assert set(store.load_bars(conn, ["AAA", "NOPE"])) == {"AAA"}


def test_save_bars_ignores_empty_frames(conn):
    assert store.save_bars(conn, {"AAA": pd.DataFrame()}) == 0
    assert store.load_bars(conn, ["AAA"]) == {}


def test_save_bars_stores_nan_as_null(conn):
    df = make_ohlcv([10.0, 11.0])
    df.iloc[1, df.columns.get_loc("Volume")] = float("nan")
    store.save_bars(conn, {"AAA": df})
    assert pd.isna(store.load_bars(conn, ["AAA"])["AAA"]["Volume"].iloc[1])


def test_save_bars_skips_a_bar_without_a_close(conn):
    df = make_ohlcv([10.0, 11.0, 12.0])
    df.iloc[1, df.columns.get_loc("Close")] = float("nan")
    assert store.save_bars(conn, {"AAA": df}) == 2

    loaded = store.load_bars(conn, ["AAA"])["AAA"]
    assert loaded["Close"].tolist() == [10.0, 12.0]
    assert not loaded["Close"].isna().any()


def test_save_bars_does_not_overwrite_a_good_close_with_nan(conn):
    """Der Fall vom 2026-08-11: Yahoo liefert die Bar ohne Schlusskurs nach."""
    store.save_bars(conn, {"AAA": make_ohlcv([10.0, 11.0, 12.0])})

    broken = make_ohlcv([10.0, 11.0, 12.0])
    broken.iloc[1, broken.columns.get_loc("Close")] = float("nan")
    store.save_bars(conn, {"AAA": broken})

    assert store.load_bars(conn, ["AAA"])["AAA"]["Close"].tolist() == [10.0, 11.0, 12.0]


# --- Metadaten -------------------------------------------------------------

def test_meta_roundtrip(conn):
    meta = {"AAA": {"market_cap": 1e9, "short_percent_of_float": 0.12,
                    "next_earnings": dt.date(2026, 9, 1), "last_earnings": dt.date(2026, 6, 1)}}
    store.save_meta(conn, meta)
    loaded = store.load_meta(conn, ["AAA"])["AAA"]
    assert loaded["market_cap"] == 1e9
    assert loaded["next_earnings"] == dt.date(2026, 9, 1)
    assert loaded["last_earnings"] == dt.date(2026, 6, 1)


def test_meta_none_does_not_overwrite_a_known_value(conn):
    """Der Kern des Cache-Nutzens: eine leere Antwort darf nichts loeschen."""
    store.save_meta(conn, {"AAA": {"market_cap": 1e9, "next_earnings": dt.date(2026, 9, 1)}})
    store.save_meta(conn, {"AAA": {"market_cap": None, "next_earnings": None}})
    loaded = store.load_meta(conn, ["AAA"])["AAA"]
    assert loaded["market_cap"] == 1e9
    assert loaded["next_earnings"] == dt.date(2026, 9, 1)


def test_meta_new_value_does_overwrite(conn):
    store.save_meta(conn, {"AAA": {"market_cap": 1e9}})
    store.save_meta(conn, {"AAA": {"market_cap": 2e9}})
    assert store.load_meta(conn, ["AAA"])["AAA"]["market_cap"] == 2e9


def test_meta_max_age_filters_out_stale_rows(conn):
    old = dt.datetime(2026, 8, 1, 12, 0)
    store.save_meta(conn, {"AAA": {"market_cap": 1e9}}, now=old)
    now = dt.datetime(2026, 8, 9, 12, 0)
    assert store.load_meta(conn, ["AAA"], max_age_days=5, now=now) == {}
    assert store.load_meta(conn, ["AAA"], max_age_days=30, now=now)["AAA"]["market_cap"] == 1e9


def test_meta_without_max_age_returns_everything(conn):
    store.save_meta(conn, {"AAA": {"market_cap": 1e9}}, now=dt.datetime(2020, 1, 1))
    assert "AAA" in store.load_meta(conn, ["AAA"])


def test_meta_refresh_updates_the_timestamp(conn):
    old, new = dt.datetime(2026, 8, 1), dt.datetime(2026, 8, 9)
    store.save_meta(conn, {"AAA": {"market_cap": 1e9}}, now=old)
    store.save_meta(conn, {"AAA": {"market_cap": None}}, now=new)
    # Wert bleibt, Zeitstempel wird frisch -> gilt wieder als aktuell
    assert store.load_meta(conn, ["AAA"], max_age_days=1, now=new)["AAA"]["market_cap"] == 1e9


def test_meta_ignores_tickers_not_asked_for(conn):
    store.save_meta(conn, {"AAA": {"market_cap": 1.0}, "BBB": {"market_cap": 2.0}})
    assert set(store.load_meta(conn, ["AAA"])) == {"AAA"}

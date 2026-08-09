"""Offline-Tests fuer die Normalisierung der yfinance-Rohdaten. Kein Netzwerk."""

import datetime as dt

import numpy as np
import pandas as pd

from stockpicker import fetch


def raw_frame(tickers, *, rows=5, adj_close=True, nan_for=()):
    """Baut einen Frame so, wie yfinance 1.5.2 ihn liefert: MultiIndex (Ticker, Price)."""
    price_fields = ["Open", "High", "Low", "Close"]
    if adj_close:
        price_fields.append("Adj Close")
    price_fields.append("Volume")
    index = pd.bdate_range(end="2026-08-07", periods=rows)
    data = {}
    for ticker in tickers:
        for i, field in enumerate(price_fields):
            values = np.full(rows, np.nan) if ticker in nan_for else np.arange(rows, dtype=float) + i
            data[(ticker, field)] = values
    columns = pd.MultiIndex.from_tuples(list(data), names=["Ticker", "Price"])
    return pd.DataFrame(data, index=index, columns=columns)


def test_normalize_flattens_multiindex_and_drops_adj_close():
    raw = raw_frame(["AAOI", "BBIO"])
    out = fetch._normalize(raw, "AAOI")
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert out.columns.name is None
    assert not isinstance(out.columns, pd.MultiIndex)
    assert out.shape == (5, 5)


def test_normalize_handles_single_ticker_frame():
    """yfinance 1.5.2 liefert auch bei einem Ticker MultiIndex-Spalten."""
    out = fetch._normalize(raw_frame(["AAOI"]), "AAOI")
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_normalize_returns_none_for_unknown_ticker():
    assert fetch._normalize(raw_frame(["AAOI"]), "ZZZZNOTREAL") is None


def test_normalize_returns_none_for_all_nan_ticker():
    raw = raw_frame(["AAOI", "DEAD"], nan_for=("DEAD",))
    assert fetch._normalize(raw, "DEAD") is None
    assert fetch._normalize(raw, "AAOI") is not None


def test_normalize_sorts_index_ascending():
    raw = raw_frame(["AAOI"]).iloc[::-1]
    out = fetch._normalize(raw, "AAOI")
    assert out.index.is_monotonic_increasing


def test_normalize_accepts_flat_columns():
    flat = raw_frame(["AAOI"]).xs("AAOI", axis=1, level=0)
    out = fetch._normalize(flat, "AAOI")
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_normalize_accepts_group_by_column_layout():
    """group_by="column" dreht die Level um: (Price, Ticker)."""
    raw = raw_frame(["AAOI", "BBIO"]).swaplevel(axis=1)
    out = fetch._normalize(raw, "BBIO")
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_normalize_returns_none_for_empty_frame():
    assert fetch._normalize(pd.DataFrame(), "AAOI") is None
    assert fetch._normalize(None, "AAOI") is None


def test_fetch_history_range_normalizes_and_passes_window(monkeypatch):
    """Der Range-Fetch nutzt dieselbe Normalisierung wie fetch_history."""
    calls = {}

    def fake_download(batch, **kwargs):
        calls["batch"] = list(batch)
        calls["kwargs"] = kwargs
        return raw_frame(["AAOI", "BBIO"])

    monkeypatch.setattr(fetch.yf, "download", fake_download)
    out = fetch.fetch_history_range(["AAOI", "BBIO"], dt.date(2026, 7, 21), dt.date(2026, 8, 1))

    assert set(out) == {"AAOI", "BBIO"}
    assert list(out["AAOI"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert calls["kwargs"]["start"] == dt.date(2026, 7, 21)
    assert calls["kwargs"]["end"] == dt.date(2026, 8, 1)
    assert calls["kwargs"]["auto_adjust"] is False
    assert calls["kwargs"]["interval"] == "1d"


def test_fetch_history_range_skips_tickers_without_data(monkeypatch):
    monkeypatch.setattr(
        fetch.yf, "download", lambda batch, **kw: raw_frame(["AAOI", "DEAD"], nan_for=("DEAD",))
    )
    out = fetch.fetch_history_range(["AAOI", "DEAD", "ZZZZNOTREAL"], "2026-07-21", "2026-08-01")
    assert set(out) == {"AAOI"}


def test_fetch_history_range_survives_download_error(monkeypatch):
    def boom(batch, **kwargs):
        raise RuntimeError("Netzwerk weg")

    monkeypatch.setattr(fetch.yf, "download", boom)
    assert fetch.fetch_history_range(["AAOI"], "2026-07-21", "2026-08-01") == {}


def test_fetch_history_range_handles_empty_result(monkeypatch):
    monkeypatch.setattr(fetch.yf, "download", lambda batch, **kw: pd.DataFrame())
    assert fetch.fetch_history_range(["AAOI"], "2026-07-21", "2026-08-01") == {}
    assert fetch.fetch_history_range([], "2026-07-21", "2026-08-01") == {}


def test_next_earnings_prefers_future_date():
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)
    info = {
        "earningsTimestamp": past.timestamp(),
        "earningsTimestampStart": future.timestamp(),
        "earningsTimestampEnd": future.timestamp(),
    }
    assert fetch._next_earnings(info) == future.date()


def test_next_earnings_none_when_missing():
    assert fetch._next_earnings({}) is None
    assert fetch._next_earnings({"earningsTimestamp": None}) is None
    assert fetch._next_earnings({"earningsTimestamp": "kaputt"}) is None


def test_last_earnings_prefers_most_recent_past_date():
    """earningsTimestamp haelt den zuletzt gemeldeten Termin."""
    recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    older = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=95)
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)
    info = {
        "earningsTimestamp": recent.timestamp(),
        "earningsTimestampStart": future.timestamp(),
        "earningsTimestampEnd": older.timestamp(),
    }
    assert fetch._last_earnings(info) == recent.date()


def test_last_earnings_ignores_future_only_dates():
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=10)
    info = {"earningsTimestamp": future.timestamp(), "earningsTimestampStart": future.timestamp()}
    assert fetch._last_earnings(info) is None


def test_last_earnings_none_when_missing_or_broken():
    assert fetch._last_earnings({}) is None
    assert fetch._last_earnings({"earningsTimestamp": None}) is None
    assert fetch._last_earnings({"earningsTimestamp": "kaputt"}) is None


def test_next_earnings_is_none_when_only_past_dates_are_known():
    """Ein 'naechster' Termin in der Vergangenheit waere irrefuehrend."""
    past = dt.datetime(2020, 5, 1, tzinfo=dt.timezone.utc).timestamp()
    assert fetch._next_earnings({"earningsTimestamp": past}) is None

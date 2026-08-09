"""Offline-Tests fuer die EUR-Anzeigeschicht. Netzwerk ist gemockt."""

import datetime as dt

import pandas as pd
import pytest

import publish as publish_cli
from stockpicker import fx, market, publish, store
from tests.conftest import make_ohlcv


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    yield c
    c.close()


def fx_frame(rates: list[float], last: dt.date = dt.date(2026, 8, 7)) -> pd.DataFrame:
    frame = make_ohlcv(rates)
    frame.index = pd.bdate_range(end=last, periods=len(rates))
    return frame


# --- Cache-Anbindung -------------------------------------------------------

def test_get_rates_offline_reads_cache_only(conn, monkeypatch):
    store.save_bars(conn, {fx.SYMBOL: fx_frame([1.08, 1.09, 1.10])})
    monkeypatch.setattr(market, "get_history",
                        lambda *a, **k: pytest.fail("kein Netzwerk erwartet"))

    rates = fx.get_rates(conn, offline=True)

    assert len(rates) == 3
    assert rates[dt.date(2026, 8, 7)] == pytest.approx(1.10)


def test_get_rates_without_cache_is_empty_not_an_error(conn):
    assert fx.get_rates(conn, offline=True) == {}


def test_get_rates_uses_the_shared_delta_cache(conn, monkeypatch):
    """Der Kurs liegt im selben Bar-Cache wie die Titel -- also darf ein
    zweiter Lauf am selben Tag nichts nachladen."""
    calls = []
    monkeypatch.setattr(market, "get_history",
                        lambda tickers, c, **k: (calls.append(list(tickers)) or
                                                 ({fx.SYMBOL: fx_frame([1.09])}, {})))

    fx.get_rates(conn)

    assert calls == [[fx.SYMBOL]]


def test_series_skips_gaps_and_nonsense():
    frame = fx_frame([1.08, 1.09, 1.10])
    frame.loc[frame.index[1], "Close"] = float("nan")
    frame.loc[frame.index[2], "Close"] = 0.0

    assert list(fx._series(frame).values()) == [pytest.approx(1.08)]
    assert fx._series(None) == {}
    assert fx._series(pd.DataFrame()) == {}


# --- Kurswahl --------------------------------------------------------------

def test_rate_on_takes_the_most_recent_earlier_day():
    rates = {dt.date(2026, 8, 5): 1.08, dt.date(2026, 8, 7): 1.10}

    assert fx.rate_on(rates, dt.date(2026, 8, 7)) == 1.10
    # Devisenfeiertag: der Kurs des Vortags ist die richtige Auskunft.
    assert fx.rate_on(rates, dt.date(2026, 8, 6)) == 1.08
    # Vor dem ersten bekannten Kurs wird nicht geraten.
    assert fx.rate_on(rates, dt.date(2026, 8, 4)) is None
    assert fx.rate_on({}, dt.date(2026, 8, 7)) is None


def test_to_eur_divides_by_the_usd_per_eur_quote():
    assert fx.to_eur(21.80, 1.09) == pytest.approx(20.0)
    # Ohne Kurs gibt es keine Zahl -- nie den unveraenderten USD-Betrag.
    assert fx.to_eur(21.80, None) is None
    assert fx.to_eur(None, 1.09) is None
    assert fx.to_eur(21.80, 0.0) is None


# --- Payload ---------------------------------------------------------------

def test_fx_payload_is_sorted_array_of_arrays():
    rates = {dt.date(2026, 8, 7): 1.10, dt.date(2026, 8, 5): 1.08}

    assert publish.fx_payload(rates) == [["2026-08-05", 1.08], ["2026-08-07", 1.10]]


def test_fx_payload_honours_since_exclusively():
    rates = {dt.date(2026, 8, 5): 1.08, dt.date(2026, 8, 7): 1.10}

    assert publish.fx_payload(rates, since=dt.date(2026, 8, 5)) == [["2026-08-07", 1.10]]


def test_report_payload_carries_rates():
    doc = {"date": "2026-08-09", "picks": [{"ticker": "XYZ"}]}
    history = {"XYZ": make_ohlcv([10.0, 11.0])}

    payload = publish.report_payload(doc, history, {dt.date(2026, 8, 7): 1.09})
    assert payload["fx"] == [["2026-08-07", 1.09]]

    # Ohne Kurse bleibt der Schluessel weg: ein Report ohne EUR-Anzeige ist
    # zulaessig, ein leeres fx-Feld waere nur Ballast.
    assert "fx" not in publish.report_payload(doc, history)
    assert "fx" not in publish.report_payload(doc, history, {})


def test_rates_since_keeps_one_earlier_day_as_fallback():
    """Ein Report am Devisenfeiertag braucht den Kurs des Vortags -- sonst
    liegt der aelteste mitgelieferte Kurs hinter dem Reportdatum."""
    rates = {dt.date(2026, 8, 3): 1.07, dt.date(2026, 8, 5): 1.08,
             dt.date(2026, 8, 7): 1.10}

    kept = publish_cli.rates_since(rates, dt.date(2026, 8, 6))

    assert sorted(kept) == [dt.date(2026, 8, 5), dt.date(2026, 8, 7)]

"""Offline-Tests fuer die Cache-Orchestrierung. Netzwerk ist gemockt."""

import datetime as dt

import pytest

from stockpicker import fetch, market, store
from tests.conftest import make_ohlcv


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    yield c
    c.close()


def bars_ending(last: dt.date, n=5):
    import pandas as pd
    df = make_ohlcv([10.0 + i for i in range(n)])
    df.index = pd.bdate_range(end=last, periods=n)
    return df


# --- Historie --------------------------------------------------------------

def test_first_run_fetches_full_history(conn, monkeypatch):
    calls = {}

    def fake_full(tickers, period="2y"):
        calls["period"] = period
        calls["tickers"] = list(tickers)
        return {t: bars_ending(dt.date(2026, 8, 7)) for t in tickers}

    monkeypatch.setattr(fetch, "fetch_history", fake_full)
    monkeypatch.setattr(fetch, "fetch_history_range", lambda *a, **k: pytest.fail("kein Delta erwartet"))

    history, stats = market.get_history(["AAA", "BBB"], conn, today=dt.date(2026, 8, 7))

    assert calls["period"] == "2y"
    assert sorted(calls["tickers"]) == ["AAA", "BBB"]
    assert stats["voll_geladen"] == 2 and stats["aus_cache"] == 0
    assert len(history["AAA"]) == 5


def test_second_run_on_same_day_hits_cache_only(conn, monkeypatch):
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 7))})
    monkeypatch.setattr(fetch, "fetch_history", lambda *a, **k: pytest.fail("kein Download erwartet"))
    monkeypatch.setattr(fetch, "fetch_history_range", lambda *a, **k: pytest.fail("kein Download erwartet"))

    history, stats = market.get_history(["AAA"], conn, today=dt.date(2026, 8, 7))

    assert stats == {"aus_cache": 1, "voll_geladen": 0, "delta_geladen": 0, "neue_bars": 0}
    assert len(history["AAA"]) == 5


def test_next_day_fetches_only_the_delta(conn, monkeypatch):
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 7))})
    seen = {}

    def fake_range(tickers, start, end):
        seen["start"], seen["end"], seen["tickers"] = start, end, list(tickers)
        return {"AAA": bars_ending(dt.date(2026, 8, 10), n=2)}

    monkeypatch.setattr(fetch, "fetch_history", lambda *a, **k: pytest.fail("keine Vollladung erwartet"))
    monkeypatch.setattr(fetch, "fetch_history_range", fake_range)

    history, stats = market.get_history(["AAA"], conn, today=dt.date(2026, 8, 10))

    assert seen["tickers"] == ["AAA"]
    assert seen["start"] == dt.date(2026, 8, 2)      # 5 Tage Ueberlappung
    assert seen["end"] == dt.date(2026, 8, 11)
    assert stats["delta_geladen"] == 1
    assert history["AAA"].index.max().date() == dt.date(2026, 8, 10)


def test_delta_groups_tickers_by_their_last_known_date(conn, monkeypatch):
    """Ein Range-Request je Gruppe, nicht einer je Titel."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 7)),
                           "BBB": bars_ending(dt.date(2026, 8, 7)),
                           "CCC": bars_ending(dt.date(2026, 8, 5))})
    groups = []

    def fake_range(tickers, start, end):
        groups.append(sorted(tickers))
        return {}

    monkeypatch.setattr(fetch, "fetch_history_range", fake_range)
    market.get_history(["AAA", "BBB", "CCC"], conn, today=dt.date(2026, 8, 10))

    assert sorted(groups) == [["AAA", "BBB"], ["CCC"]]


def test_mixed_new_and_cached_tickers(conn, monkeypatch):
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 10))})
    monkeypatch.setattr(fetch, "fetch_history",
                        lambda tickers, period="2y": {t: bars_ending(dt.date(2026, 8, 10)) for t in tickers})
    monkeypatch.setattr(fetch, "fetch_history_range", lambda *a, **k: {})

    history, stats = market.get_history(["AAA", "NEU"], conn, today=dt.date(2026, 8, 10))

    assert stats["aus_cache"] == 1 and stats["voll_geladen"] == 1
    assert set(history) == {"AAA", "NEU"}


def test_history_survives_a_ticker_the_source_no_longer_has(conn, monkeypatch):
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 7))})
    monkeypatch.setattr(fetch, "fetch_history_range", lambda *a, **k: {})   # delisted
    history, _ = market.get_history(["AAA"], conn, today=dt.date(2026, 8, 10))
    assert len(history["AAA"]) == 5      # alte Historie bleibt nutzbar


# --- Metadaten -------------------------------------------------------------

def test_meta_uses_cache_within_max_age(conn, monkeypatch):
    now = dt.datetime(2026, 8, 9, 12, 0)
    store.save_meta(conn, {"AAA": {"market_cap": 1e9}}, now=dt.datetime(2026, 8, 8, 12, 0))
    monkeypatch.setattr(fetch, "fetch_meta", lambda *a, **k: pytest.fail("kein Download erwartet"))

    meta, stats = market.get_meta(["AAA"], conn, max_age_days=5, now=now)

    assert meta["AAA"]["market_cap"] == 1e9
    assert stats == {"aus_cache": 1, "neu_geladen": 0, "nur_aus_cache_gerettet": 0}


def test_meta_refetches_when_stale(conn, monkeypatch):
    now = dt.datetime(2026, 8, 9, 12, 0)
    store.save_meta(conn, {"AAA": {"market_cap": 1e9}}, now=dt.datetime(2026, 7, 1))
    monkeypatch.setattr(fetch, "fetch_meta", lambda tickers: {"AAA": {"market_cap": 2e9}})

    meta, stats = market.get_meta(["AAA"], conn, max_age_days=5, now=now)

    assert meta["AAA"]["market_cap"] == 2e9
    assert stats["neu_geladen"] == 1


def test_meta_keeps_last_known_value_when_the_source_returns_nothing(conn, monkeypatch):
    """Der eigentliche Zweck: yfinance-Aussetzer duerfen keinen Titel kosten."""
    now = dt.datetime(2026, 8, 9, 12, 0)
    store.save_meta(conn, {"AAA": {"market_cap": 1e9, "next_earnings": dt.date(2026, 9, 1)}},
                    now=dt.datetime(2026, 7, 1))
    monkeypatch.setattr(fetch, "fetch_meta",
                        lambda tickers: {"AAA": {"market_cap": None, "short_percent_of_float": None,
                                                 "next_earnings": None, "last_earnings": None}})

    meta, stats = market.get_meta(["AAA"], conn, max_age_days=5, now=now)

    assert meta["AAA"]["market_cap"] == 1e9
    assert meta["AAA"]["next_earnings"] == dt.date(2026, 9, 1)
    assert stats["nur_aus_cache_gerettet"] == 1


def test_meta_returns_an_entry_for_every_requested_ticker(conn, monkeypatch):
    monkeypatch.setattr(fetch, "fetch_meta", lambda tickers: {})
    meta, _ = market.get_meta(["AAA", "BBB"], conn)
    assert set(meta) == {"AAA", "BBB"}
    assert meta["AAA"] == {}


def test_last_expected_session_steps_back_over_the_weekend():
    assert market.last_expected_session(dt.date(2026, 8, 9)) == dt.date(2026, 8, 7)   # So -> Fr
    assert market.last_expected_session(dt.date(2026, 8, 8)) == dt.date(2026, 8, 7)   # Sa -> Fr
    assert market.last_expected_session(dt.date(2026, 8, 7)) == dt.date(2026, 8, 7)   # Fr
    assert market.last_expected_session(dt.date(2026, 8, 10)) == dt.date(2026, 8, 10)  # Mo


def test_weekend_run_does_not_refetch_fridays_data(conn, monkeypatch):
    """Sonntags ist Freitag der aktuellste Stand — nichts nachzuladen."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 7))})
    monkeypatch.setattr(fetch, "fetch_history_range", lambda *a, **k: pytest.fail("kein Download erwartet"))
    monkeypatch.setattr(fetch, "fetch_history", lambda *a, **k: pytest.fail("kein Download erwartet"))

    _, stats = market.get_history(["AAA"], conn, today=dt.date(2026, 8, 9))
    assert stats["delta_geladen"] == 0


def test_monday_run_does_fetch_the_delta(conn, monkeypatch):
    """Am Handelstag muss der Cache dagegen nachladen."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 7))})
    called = []

    def fake_range(tickers, start, end):
        called.append(list(tickers))
        return {"AAA": bars_ending(dt.date(2026, 8, 10), n=1)}

    monkeypatch.setattr(fetch, "fetch_history_range", fake_range)
    _, stats = market.get_history(["AAA"], conn, today=dt.date(2026, 8, 10))

    assert called == [["AAA"]]
    assert stats["delta_geladen"] == 1


# --- Splits ----------------------------------------------------------------

def events(monkeypatch, *pairs, warning=""):
    """Ersetzt die Alpaca-Abfrage durch eine feste Antwort und merkt das Fenster."""
    seen = {}

    def fake_load(start, end):
        seen["start"], seen["end"] = start, end
        return {(t, dt.date.fromisoformat(d)) for t, d in pairs}, warning

    monkeypatch.setattr(market.splits, "load_splits", fake_load)
    return seen


def test_split_purges_the_cache_and_forces_a_full_reload(conn, monkeypatch):
    """Der eigentliche Zweck: nach einem Split darf kein Delta mehr angehaengt werden."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 27))})
    events(monkeypatch, ("AAA", "2026-08-24"))

    purged, warning = market.purge_split_history(["AAA"], conn, today=dt.date(2026, 8, 28))

    assert purged == ["AAA"] and warning == ""
    assert store.last_bar_dates(conn) == {}          # Historie ist weg

    monkeypatch.setattr(fetch, "fetch_history_range", lambda *a, **k: pytest.fail("kein Delta erwartet"))
    monkeypatch.setattr(fetch, "fetch_history",
                        lambda tickers, period="2y": {t: bars_ending(dt.date(2026, 8, 28)) for t in tickers})
    _, stats = market.get_history(["AAA"], conn, today=dt.date(2026, 8, 28))
    assert stats["voll_geladen"] == 1


def test_the_same_split_purges_only_once(conn, monkeypatch):
    """Sonst laedt derselbe Split jeden Tag aufs Neue zwei Jahre Historie."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 27))})
    events(monkeypatch, ("AAA", "2026-08-24"))
    market.purge_split_history(["AAA"], conn, today=dt.date(2026, 8, 28))

    # So wie get_history es gleich danach tut: die Historie ist wieder da.
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 28))})

    purged, _ = market.purge_split_history(["AAA"], conn, today=dt.date(2026, 8, 31))
    assert purged == []
    assert store.last_bar_dates(conn) == {"AAA": dt.date(2026, 8, 28)}


def test_a_second_split_purges_again(conn, monkeypatch):
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 27))})
    events(monkeypatch, ("AAA", "2026-08-24"))
    market.purge_split_history(["AAA"], conn, today=dt.date(2026, 8, 28))
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 28))})

    events(monkeypatch, ("AAA", "2026-08-24"), ("AAA", "2026-08-31"))
    purged, _ = market.purge_split_history(["AAA"], conn, today=dt.date(2026, 8, 31))
    assert purged == ["AAA"]


def test_split_of_another_ticker_leaves_the_cache_alone(conn, monkeypatch):
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 27)),
                           "BBB": bars_ending(dt.date(2026, 8, 27))})
    events(monkeypatch, ("BBB", "2026-08-24"), ("ZZZ", "2026-08-25"))

    purged, _ = market.purge_split_history(["AAA", "BBB"], conn, today=dt.date(2026, 8, 28))

    assert purged == ["BBB"]
    assert set(store.last_bar_dates(conn)) == {"AAA"}


def test_split_of_an_uncached_ticker_is_noted_but_purges_nothing(conn, monkeypatch):
    """Seine Historie kommt gleich frisch -- und damit schon angepasst."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 27))})
    events(monkeypatch, ("NEU", "2026-08-24"))

    purged, _ = market.purge_split_history(["AAA", "NEU"], conn, today=dt.date(2026, 8, 28))

    assert purged == []
    assert ("NEU", dt.date(2026, 8, 24)) in store.recorded_splits(conn)


def test_empty_cache_asks_nothing(conn, monkeypatch):
    """Ohne Historie gibt es nichts zu invalidieren -- auch keinen Request."""
    monkeypatch.setattr(market.splits, "load_splits",
                        lambda *a, **k: pytest.fail("keine Abfrage erwartet"))
    assert market.purge_split_history(["AAA"], conn, today=dt.date(2026, 8, 28)) == ([], "")


def test_first_run_looks_back_over_the_whole_cache(conn, monkeypatch):
    """Die Tabelle ist leer: alles, was der Cache haelt, kann betroffen sein."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 27))})
    seen = events(monkeypatch)

    market.purge_split_history(["AAA"], conn, today=dt.date(2026, 8, 28))

    assert seen["start"] == dt.date(2026, 8, 28) - dt.timedelta(days=market.INITIAL_SPLIT_LOOKBACK_DAYS)
    assert seen["end"] == dt.date(2026, 8, 28)


def test_later_runs_only_ask_for_the_gap(conn, monkeypatch):
    """Was davor liegt, hat ein frueherer Lauf schon gesehen."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 27)),
                           "BBB": bars_ending(dt.date(2026, 8, 25))})
    store.record_splits(conn, {("IRGENDWER", dt.date(2026, 7, 1))})
    seen = events(monkeypatch)

    market.purge_split_history(["AAA", "BBB"], conn, today=dt.date(2026, 8, 28))

    # Aeltester Cache-Stand minus die Ueberlappung des Delta-Laufs.
    assert seen["start"] == dt.date(2026, 8, 20)


def test_a_stale_ticker_does_not_widen_the_window_forever(conn, monkeypatch):
    store.save_bars(conn, {"ALT": bars_ending(dt.date(2026, 1, 5))})
    store.record_splits(conn, {("IRGENDWER", dt.date(2026, 7, 1))})
    seen = events(monkeypatch)

    market.purge_split_history(["ALT"], conn, today=dt.date(2026, 8, 28))

    assert seen["start"] == dt.date(2026, 8, 28) - dt.timedelta(days=market.INITIAL_SPLIT_LOOKBACK_DAYS)


def test_a_failed_query_keeps_the_cache_and_the_warning(conn, monkeypatch):
    """Nichts loeschen, nichts vermerken: der naechste Lauf sieht den Split erneut."""
    store.save_bars(conn, {"AAA": bars_ending(dt.date(2026, 8, 27))})
    events(monkeypatch, warning="Split-Abfrage fehlgeschlagen (403)")

    purged, warning = market.purge_split_history(["AAA"], conn, today=dt.date(2026, 8, 28))

    assert purged == []
    assert warning == "Split-Abfrage fehlgeschlagen (403)"
    assert store.last_bar_dates(conn) == {"AAA": dt.date(2026, 8, 27)}
    assert store.recorded_splits(conn) == set()

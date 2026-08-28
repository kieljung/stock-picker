"""Offline-Tests fuer die Split-Abfrage. Netzwerk ist gemockt."""

import datetime as dt

import pytest

from stockpicker import splits


# --- Parsen ----------------------------------------------------------------

def test_parse_reads_every_split_type():
    payload = {
        "forward_splits": [{"symbol": "APH", "ex_date": "2026-09-03"}],
        "reverse_splits": [{"symbol": "BRCC", "ex_date": "2026-08-24"}],
        "unit_splits": [{"symbol": "XYZ", "ex_date": "2026-08-20"}],
    }
    assert splits.parse_splits(payload) == {
        ("APH", dt.date(2026, 9, 3)),
        ("BRCC", dt.date(2026, 8, 24)),
        ("XYZ", dt.date(2026, 8, 20)),
    }


def test_parse_takes_the_new_symbol_too():
    """Nach dem Ex-Datum kommen die Bars unter dem neuen Kuerzel."""
    payload = {"reverse_splits": [
        {"symbol": "ALCE", "new_symbol": "ALCED", "ex_date": "2026-08-20"}]}
    assert splits.parse_splits(payload) == {
        ("ALCE", dt.date(2026, 8, 20)), ("ALCED", dt.date(2026, 8, 20))}


def test_parse_normalises_dots_like_the_universe_does():
    """Der Cache kennt BRK-B, Alpaca meldet BRK.B."""
    payload = {"forward_splits": [{"symbol": "BRK.B", "ex_date": "2026-08-20"}]}
    assert splits.parse_splits(payload) == {("BRK-B", dt.date(2026, 8, 20))}


def test_parse_skips_entries_without_a_usable_ex_date():
    payload = {"reverse_splits": [
        {"symbol": "AAA", "ex_date": None},
        {"symbol": "BBB", "ex_date": "irgendwann"},
        {"symbol": "", "ex_date": "2026-08-20"},
        {"symbol": "CCC", "ex_date": "2026-08-20"},
    ]}
    assert splits.parse_splits(payload) == {("CCC", dt.date(2026, 8, 20))}


def test_parse_ignores_other_corporate_actions_and_junk():
    payload = {"cash_dividends": [{"symbol": "AAA", "ex_date": "2026-08-20"}],
               "reverse_splits": "kaputt"}
    assert splits.parse_splits(payload) == set()
    assert splits.parse_splits({}) == set()
    assert splits.parse_splits(None) == set()


# --- Laden -----------------------------------------------------------------

def test_load_without_keys_warns_instead_of_calling(monkeypatch):
    """Ohne Schluessel darf nichts ueber die Leitung gehen."""
    monkeypatch.setattr(splits, "_fetch", lambda *a, **k: pytest.fail("kein Aufruf erwartet"))

    events, warning = splits.load_splits(dt.date(2026, 8, 1), dt.date(2026, 8, 28))

    assert events == set()
    assert "ALPACA_API_KEY" in warning


def test_load_passes_the_window_through(monkeypatch):
    seen = {}

    def fake_fetch(api_key, secret_key, start, end):
        seen.update(key=api_key, secret=secret_key, start=start, end=end)
        return {"reverse_splits": [{"symbol": "BRCC", "ex_date": "2026-08-24"}]}

    monkeypatch.setattr(splits, "_fetch", fake_fetch)

    events, warning = splits.load_splits(dt.date(2026, 8, 1), dt.date(2026, 8, 28),
                                         api_key="k", secret_key="s")

    assert events == {("BRCC", dt.date(2026, 8, 24))}
    assert warning == ""
    assert seen == {"key": "k", "secret": "s",
                    "start": dt.date(2026, 8, 1), "end": dt.date(2026, 8, 28)}


def test_load_reads_the_keys_from_the_dotenv(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("ALPACA_API_KEY=aus-datei\nALPACA_SECRET_KEY=geheim\n")
    monkeypatch.setattr(splits, "DOTENV", dotenv)
    monkeypatch.setattr(splits, "_fetch", lambda key, secret, start, end: {
        "forward_splits": [{"symbol": key, "ex_date": "2026-08-20"}]})

    events, warning = splits.load_splits(dt.date(2026, 8, 1), dt.date(2026, 8, 28))

    assert events == {("aus-datei", dt.date(2026, 8, 20))}
    assert warning == ""


def test_load_turns_a_broken_request_into_a_warning(monkeypatch):
    """Der Screener laeuft auch ohne die Pruefung -- aber nicht stillschweigend."""
    def boom(*a, **k):
        raise RuntimeError("403 forbidden")

    monkeypatch.setattr(splits, "_fetch", boom)

    events, warning = splits.load_splits(dt.date(2026, 8, 1), dt.date(2026, 8, 28),
                                         api_key="k", secret_key="s")

    assert events == set()
    assert "403 forbidden" in warning

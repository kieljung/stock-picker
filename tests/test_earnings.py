"""Offline-Tests fuer die Earnings-Kalender. Kein Netzwerk."""

import datetime as dt
import json

import pytest

from stockpicker import earnings
from stockpicker.earnings import CalendarError

AV_CSV = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
ACHC,ACADIA HEALTHCARE,2026-08-12,2026-06-30,0.45,USD
WNC,WABASH NATIONAL,2026-08-20,2026-06-30,0.31,USD
BRK.B,BERKSHIRE HATHAWAY,2026-09-01,2026-06-30,,USD
ACHC,ACADIA HEALTHCARE,2026-11-05,2026-09-30,0.51,USD
BROKEN,NO DATE,,2026-06-30,,USD
BADDATE,BAD,not-a-date,2026-06-30,,USD
"""

FH_JSON = json.dumps({"earningsCalendar": [
    {"symbol": "ACHC", "date": "2026-08-12", "hour": "amc", "epsEstimate": 0.45},
    {"symbol": "MITK", "date": "2026-08-14", "hour": "bmo", "epsEstimate": 0.11},
    {"symbol": "BRK.B", "date": "2026-09-01", "hour": "amc"},
    {"symbol": "MITK", "date": "2026-11-20", "hour": "amc"},
    {"symbol": "NODATE", "date": None},
    {"symbol": "", "date": "2026-08-14"},
]})


def _future(days):
    return dt.date.today() + dt.timedelta(days=days)


# --- Alpha Vantage ---------------------------------------------------------

def test_av_parse_extracts_ticker_and_date():
    assert earnings.parse_alphavantage_csv(AV_CSV)["WNC"] == dt.date(2026, 8, 20)


def test_av_parse_keeps_earliest_date_per_ticker():
    assert earnings.parse_alphavantage_csv(AV_CSV)["ACHC"] == dt.date(2026, 8, 12)


def test_av_parse_normalizes_dotted_tickers():
    assert "BRK-B" in earnings.parse_alphavantage_csv(AV_CSV)


def test_av_parse_skips_rows_without_usable_date():
    cal = earnings.parse_alphavantage_csv(AV_CSV)
    assert "BROKEN" not in cal and "BADDATE" not in cal


def test_av_parse_raises_on_json_error_response():
    with pytest.raises(CalendarError, match="statt CSV"):
        earnings.parse_alphavantage_csv('{"Error Message": "Invalid API call"}')


def test_av_parse_raises_on_rate_limit_note():
    with pytest.raises(CalendarError, match="statt CSV"):
        earnings.parse_alphavantage_csv('{"Information": "25 requests/day limit"}')


def test_av_parse_raises_on_empty_and_on_html():
    with pytest.raises(CalendarError, match="Leere Antwort"):
        earnings.parse_alphavantage_csv("   ")
    with pytest.raises(CalendarError, match="Unerwartetes Format"):
        earnings.parse_alphavantage_csv("<html>nope</html>")


# --- Finnhub ---------------------------------------------------------------

def test_fh_parse_extracts_ticker_and_date():
    assert earnings.parse_finnhub_json(FH_JSON)["ACHC"] == dt.date(2026, 8, 12)


def test_fh_parse_keeps_earliest_date_per_ticker():
    assert earnings.parse_finnhub_json(FH_JSON)["MITK"] == dt.date(2026, 8, 14)


def test_fh_parse_normalizes_dotted_tickers():
    assert "BRK-B" in earnings.parse_finnhub_json(FH_JSON)


def test_fh_parse_skips_unusable_rows():
    cal = earnings.parse_finnhub_json(FH_JSON)
    assert "NODATE" not in cal and "" not in cal


def test_fh_parse_accepts_empty_calendar():
    """Ein leeres Fenster ist ein gueltiges Ergebnis, kein Fehler."""
    assert earnings.parse_finnhub_json('{"earningsCalendar": []}') == {}


def test_fh_parse_raises_when_key_is_missing_from_payload():
    """So meldet Finnhub Auth- und Limit-Probleme — darf nicht still durchgehen."""
    with pytest.raises(CalendarError, match="statt Kalenderdaten"):
        earnings.parse_finnhub_json(json.dumps({"error": "You don't have access to this resource."}))


def test_fh_parse_raises_on_non_json():
    with pytest.raises(CalendarError, match="kein JSON"):
        earnings.parse_finnhub_json("429 Too Many Requests")


def test_fh_parse_raises_on_empty_response():
    with pytest.raises(CalendarError, match="Leere Antwort"):
        earnings.parse_finnhub_json("  ")


# --- Laden -----------------------------------------------------------------

def test_load_without_key_and_without_cache_warns(tmp_path):
    cal, warning = earnings.load_finnhub_calendar(cache_path=tmp_path / "x.json", max_age_days=0)
    assert cal == {}
    assert "FINNHUB_API_KEY nicht gesetzt" in warning


def test_load_without_key_falls_back_to_cache(tmp_path):
    cache = tmp_path / "x.json"
    cache.write_text(FH_JSON, encoding="utf-8")
    cal, warning = earnings.load_finnhub_calendar(cache_path=cache, max_age_days=0)
    assert cal["ACHC"] == dt.date(2026, 8, 12)
    assert "Cache" in warning


def test_load_uses_fresh_cache_without_download(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings, "_download_finnhub", lambda *a, **k: pytest.fail("kein Download erwartet"))
    cache = tmp_path / "x.json"
    cache.write_text(FH_JSON, encoding="utf-8")
    cal, warning = earnings.load_finnhub_calendar(api_key="k", cache_path=cache, max_age_days=1)
    assert cal["ACHC"] == dt.date(2026, 8, 12)
    assert warning == ""


def test_load_writes_cache_after_download(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings, "_download_finnhub", lambda *a, **k: FH_JSON)
    cache = tmp_path / "sub" / "x.json"
    cal, warning = earnings.load_finnhub_calendar(api_key="k", cache_path=cache, max_age_days=0)
    assert cal["ACHC"] == dt.date(2026, 8, 12)
    assert warning == "" and cache.read_text(encoding="utf-8") == FH_JSON


def test_load_never_raises_on_download_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings, "_download_finnhub",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    cal, warning = earnings.load_finnhub_calendar(api_key="k", cache_path=tmp_path / "x.json", max_age_days=0)
    assert cal == {} and "nicht abrufbar" in warning


def test_load_falls_back_when_service_returns_error_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings, "_download_finnhub", lambda *a, **k: '{"error": "limit"}')
    cal, warning = earnings.load_finnhub_calendar(api_key="k", cache_path=tmp_path / "x.json", max_age_days=0)
    assert cal == {} and "nicht abrufbar" in warning


def test_finnhub_window_starts_today_and_spans_the_configured_days(monkeypatch, tmp_path):
    seen = {}

    def fake(key, start, end, timeout):
        seen["start"], seen["end"] = start, end
        return FH_JSON

    monkeypatch.setattr(earnings, "_download_finnhub", fake)
    earnings.load_finnhub_calendar(api_key="k", cache_path=tmp_path / "x.json",
                                   max_age_days=0, window_days=30, today=dt.date(2026, 8, 9))
    assert seen["start"] == dt.date(2026, 8, 9)
    assert seen["end"] == dt.date(2026, 9, 8)


# --- Key-Lesen -------------------------------------------------------------

def test_read_env_key_prefers_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "from-env")
    dotenv = tmp_path / ".env"
    dotenv.write_text("FINNHUB_API_KEY=from-file\n", encoding="utf-8")
    assert earnings.read_env_key("FINNHUB_API_KEY", dotenv=dotenv) == "from-env"


def test_read_env_key_falls_back_to_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("# Kommentar\n\nFINNHUB_API_KEY=\"from-file\"\n", encoding="utf-8")
    assert earnings.read_env_key("FINNHUB_API_KEY", dotenv=dotenv) == "from-file"


def test_read_env_key_handles_missing_newline_at_eof(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("FINNHUB_API_KEY=abc123", encoding="utf-8")
    assert earnings.read_env_key("FINNHUB_API_KEY", dotenv=dotenv) == "abc123"


def test_read_env_key_returns_none_when_absent(tmp_path):
    assert earnings.read_env_key("FINNHUB_API_KEY", dotenv=tmp_path / "nope.env") is None


# --- Zusammenfuehrung ------------------------------------------------------

def test_merge_takes_the_earliest_date_across_sources():
    meta = {"A": {"next_earnings": _future(30)}}
    calendars = {"alphavantage": {"A": _future(9)}, "finnhub": {"A": _future(2)}}
    merged, stats = earnings.merge_into_meta(meta, calendars)
    assert merged["A"]["next_earnings"] == _future(2)
    assert merged["A"]["earnings_source"] == "finnhub"
    assert stats["uneinig"] == 1


def test_merge_never_lets_a_later_date_win():
    """Der spaetere Termin duerfte einen Blackout entfernen — genau das nicht."""
    meta = {"A": {"next_earnings": _future(2)}}
    merged, _ = earnings.merge_into_meta(meta, {"finnhub": {"A": _future(40)}})
    assert merged["A"]["next_earnings"] == _future(2)
    assert merged["A"]["earnings_source"] == "yfinance"


def test_merge_lists_every_agreeing_source():
    meta = {"A": {"next_earnings": _future(5)}}
    calendars = {"alphavantage": {"A": _future(5)}, "finnhub": {"A": _future(5)}}
    merged, stats = earnings.merge_into_meta(meta, calendars)
    assert merged["A"]["earnings_source"] == "alphavantage+finnhub+yfinance"
    assert stats["einig"] == 1


def test_merge_flags_a_lone_source():
    merged, stats = earnings.merge_into_meta({"A": {"next_earnings": None}},
                                             {"finnhub": {"A": _future(3)}})
    assert merged["A"]["earnings_source"] == "finnhub"
    assert stats["nur_eine_quelle"] == 1
    assert stats["kennt_finnhub"] == 1
    assert stats["kennt_yfinance"] == 0


def test_merge_discards_past_dates_from_every_source():
    past = dt.date.today() - dt.timedelta(days=10)
    merged, stats = earnings.merge_into_meta({"A": {"next_earnings": past}},
                                             {"finnhub": {"A": past}})
    assert merged["A"]["next_earnings"] is None
    assert merged["A"]["earnings_source"] is None
    assert stats["ohne_termin"] == 1


def test_merge_marks_source_none_when_nobody_knows():
    merged, stats = earnings.merge_into_meta({"A": {"next_earnings": None}}, {})
    assert merged["A"]["earnings_source"] is None
    assert stats["ohne_termin"] == 1


def test_merge_counts_coverage_per_source():
    meta = {"A": {"next_earnings": _future(5)}, "B": {"next_earnings": None}}
    calendars = {"finnhub": {"B": _future(7)}}
    _, stats = earnings.merge_into_meta(meta, calendars)
    assert stats["kennt_yfinance"] == 1
    assert stats["kennt_finnhub"] == 1

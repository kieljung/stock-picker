import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from stockpicker import outcomes, publish
from tests.conftest import make_ohlcv


def test_bars_payload_is_array_of_arrays():
    history = {"XYZ": make_ohlcv([10.0, 11.0, 12.0])}
    payload = publish.bars_payload(history)
    assert list(payload) == ["XYZ"]
    assert len(payload["XYZ"]) == 3
    date, open_, high, low, close, volume = payload["XYZ"][0]
    assert isinstance(date, str) and len(date) == 10
    assert (open_, close, volume) == (10.0, 10.0, 1_000_000.0)
    assert high > close > low


def test_bars_payload_skips_empty_frames():
    assert publish.bars_payload({"XYZ": pd.DataFrame()}) == {}


def test_bars_payload_honours_since():
    history = {"XYZ": make_ohlcv([10.0] * 10)}
    full = publish.bars_payload(history)
    cutoff = dt.date.fromisoformat(full["XYZ"][5][0])
    trimmed = publish.bars_payload(history, since=cutoff)
    assert len(trimmed["XYZ"]) == 4
    assert all(dt.date.fromisoformat(row[0]) > cutoff for row in trimmed["XYZ"])


def test_bars_payload_serialises_nan_as_null():
    frame = make_ohlcv([10.0, 11.0])
    frame.loc[frame.index[1], "Volume"] = np.nan
    payload = publish.bars_payload({"XYZ": frame})
    assert payload["XYZ"][1][5] is None
    text = json.dumps(payload)
    assert "NaN" not in text
    assert json.loads(text)["XYZ"][1][5] is None


def test_report_payload_carries_document_and_bars():
    doc = {"date": "2026-08-09", "picks": [{"ticker": "XYZ"}]}
    payload = publish.report_payload(doc, {"XYZ": make_ohlcv([10.0, 11.0])})
    assert payload["date"] == "2026-08-09"
    assert len(payload["bars"]["XYZ"]) == 2


def test_outcomes_payload_maps_evaluate_results():
    results = [{"ticker": "XYZ", "date": "2026-08-09", "status": "TARGET1",
                "r_multiple": 2.06, "setup": "PEAD", "score_catalyst": 8,
                "triggered_on": "2026-08-10", "closed_on": "2026-08-14"}]
    payload = publish.outcomes_payload(results)
    assert payload == [{"date": "2026-08-09", "ticker": "XYZ",
                        "status": "TARGET1", "r_multiple": 2.06,
                        "triggered_on": "2026-08-10", "closed_on": "2026-08-14"}]


def test_outcomes_payload_keeps_missing_dates_as_null():
    results = [{"ticker": "XYZ", "date": "2026-08-09", "status": "NOT_TRIGGERED",
                "r_multiple": 0.0, "triggered_on": None, "closed_on": None}]
    assert publish.outcomes_payload(results)[0]["triggered_on"] is None
    assert publish.outcomes_payload(results)[0]["closed_on"] is None


def test_outcomes_payload_takes_the_dates_evaluate_produces():
    # Vertrag zwischen evaluate und Payload: was dort entsteht, kommt hier an.
    pick = {"ticker": "XYZ", "date": "2026-07-20", "setup": "PEAD",
            "entry": 100.0, "stop": 95.0, "target1": 110.0}
    bars = make_ohlcv([98.0, 105.0, 112.0], high_offset=1.0, low_offset=1.0)
    result = outcomes.evaluate(pick, bars, trigger_window=3)
    row = publish.outcomes_payload([result])[0]
    assert row["status"] == "TARGET1"
    assert row["triggered_on"] == bars.index[1].date().isoformat()
    assert row["closed_on"] == bars.index[2].date().isoformat()


def test_open_picks_selects_unclosed_entries():
    entries = [
        {"ticker": "A", "date": "2026-08-01", "status": "OPEN"},
        {"ticker": "B", "date": "2026-08-01", "status": "TARGET1"},
        {"ticker": "C", "date": "2026-08-02", "status": "OPEN"},
        {"ticker": "A", "date": "2026-08-02", "status": "NOT_TRIGGERED"},
    ]
    assert publish.open_picks(entries) == [
        {"ticker": "A", "date": "2026-08-01", "status": "OPEN"},
        {"ticker": "C", "date": "2026-08-02", "status": "OPEN"},
    ]


class FakeResponse:
    """Minimale requests.Response-Attrappe. Kein Test sendet echtes HTTP."""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


def test_credentials_are_read_from_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("PORTAL_URL=https://example.com\n"
                   "PORTAL_USER=picker\nPORTAL_PASSWORD=geheim\n", encoding="utf-8")
    monkeypatch.setattr(publish, "DOTENV", env)
    creds = publish.credentials()
    assert creds == publish.Credentials("https://example.com", "picker", "geheim")


def test_credentials_prefer_environment_over_dotenv(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("PORTAL_URL=https://aus-datei\n"
                   "PORTAL_USER=picker\nPORTAL_PASSWORD=geheim\n", encoding="utf-8")
    monkeypatch.setattr(publish, "DOTENV", env)
    monkeypatch.setenv("PORTAL_URL", "https://aus-umgebung")
    assert publish.credentials().url == "https://aus-umgebung"


def test_credentials_strip_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "DOTENV", tmp_path / "absent.env")
    monkeypatch.setenv("PORTAL_URL", "https://example.com/")
    monkeypatch.setenv("PORTAL_USER", "picker")
    monkeypatch.setenv("PORTAL_PASSWORD", "geheim")
    assert publish.credentials().url == "https://example.com"


def test_missing_credentials_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(publish, "DOTENV", tmp_path / "absent.env")
    with pytest.raises(RuntimeError, match="PORTAL_URL"):
        publish.credentials()


def test_post_returns_json_on_success(monkeypatch):
    calls = []

    def fake_post(url, json=None, auth=None, timeout=None):
        calls.append((url, auth, timeout))
        return FakeResponse(200, {"ok": True, "picks": 5})

    monkeypatch.setattr(publish.requests, "post", fake_post)
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    result = publish.post(creds, "/api/reports", {"date": "2026-08-09"})
    assert result == {"ok": True, "picks": 5}
    assert calls[0][0] == "https://example.com/api/reports"
    assert calls[0][1] == ("picker", "geheim")


def test_post_raises_with_details_on_422(monkeypatch):
    monkeypatch.setattr(publish.requests, "post",
                        lambda *a, **k: FakeResponse(422, {"error": "invalid",
                                                           "details": ["XYZ: R:R 1.20"]}))
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    with pytest.raises(publish.PushError) as excinfo:
        publish.post(creds, "/api/reports", {})
    assert "R:R 1.20" in str(excinfo.value)


def test_post_does_not_retry_on_4xx(monkeypatch):
    attempts = []
    monkeypatch.setattr(publish.requests, "post",
                        lambda *a, **k: (attempts.append(1), FakeResponse(422, {}))[1])
    monkeypatch.setattr(publish.time, "sleep", lambda _: None)
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    with pytest.raises(publish.PushError):
        publish.post(creds, "/api/reports", {})
    assert len(attempts) == 1


def test_post_retries_three_times_on_5xx(monkeypatch):
    attempts = []
    monkeypatch.setattr(publish.requests, "post",
                        lambda *a, **k: (attempts.append(1), FakeResponse(503, {}))[1])
    monkeypatch.setattr(publish.time, "sleep", lambda _: None)
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    with pytest.raises(publish.PushError):
        publish.post(creds, "/api/reports", {})
    assert len(attempts) == 3


def test_post_retries_on_network_error(monkeypatch):
    import requests as real_requests

    attempts = []

    def fake_post(*a, **k):
        attempts.append(1)
        raise real_requests.ConnectionError("kein Netz")

    monkeypatch.setattr(publish.requests, "post", fake_post)
    monkeypatch.setattr(publish.time, "sleep", lambda _: None)
    creds = publish.Credentials("https://example.com", "picker", "geheim")
    with pytest.raises(publish.PushError, match="kein Netz"):
        publish.post(creds, "/api/reports", {})
    assert len(attempts) == 3

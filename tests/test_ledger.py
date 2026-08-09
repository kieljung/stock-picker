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


def test_append_entries_writes_only_ledger_fields(tmp_path):
    path = tmp_path / "ledger.jsonl"
    pick = good_pick(thesis="lange Prosa", metrics={"close": 42.15})
    ledger.append_entries(path, "2026-08-09", [pick])
    written = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert written["entry"] == 42.80
    assert "thesis" not in written
    assert "metrics" not in written


def test_append_entries_replaces_date_when_asked(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_entries(path, "2026-08-09", [good_pick(), good_pick(ticker="ABC")])
    ledger.append_entries(path, "2026-08-09", [good_pick(entry=44.0)], replace=True)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["entry"] == 44.0


def test_replace_keeps_entries_of_other_dates(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger.append_entries(path, "2026-08-08", [good_pick(ticker="OLD")])
    ledger.append_entries(path, "2026-08-09", [good_pick()])
    ledger.append_entries(path, "2026-08-09", [good_pick(entry=44.0)], replace=True)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["date"] for line in lines] == ["2026-08-08", "2026-08-09"]

import json

import pytest

from stockpicker import report


def good_pick(**overrides):
    pick = {
        "ticker": "XYZ", "name": "Example Corp", "sector": "Health Care",
        "setup": "VCP_BREAKOUT", "rank": 1,
        "score_technical": 8, "score_catalyst": 7, "score_risk": 7, "composite": 76,
        "score_rationale": {"technical": "a", "catalyst": "b", "risk": "c"},
        "entry": 42.80, "stop": 40.10, "target1": 48.20, "target2": 52.00,
        "risk_reward": 2.0, "position_size": 37,
        "catalyst": "FDA-Zulassung 2026-08-06",
        "thesis": "t", "technicals": "tl", "catalyst_detail": "kd",
        "plan": {"entry": "e", "stop": "s", "target1": "t1", "target2": "t2"},
        "bear_case": "bc", "invalidation": "inv",
        "metrics": {"close": 42.15, "atr14": 1.85},
        "sources": [{"url": "https://example.com/a", "title": "Example", "date": "2026-08-06"}],
    }
    pick.update(overrides)
    return pick


def good_document(**overrides):
    doc = {
        "date": "2026-08-09",
        "generated_at": "2026-08-09T12:35:34",
        "market_context": "mk",
        "note": "n",
        "picks": [good_pick()],
        "rejected": [{"ticker": "ABC", "reason": "Earnings in drei Tagen"}],
        "checklist": ["Jede Zahl traegt Screener oder Quelle"],
        "disclaimer": "Keine Anlageberatung.",
    }
    doc.update(overrides)
    return doc


CANDIDATES = {"candidates": [{"ticker": "XYZ", "close": 42.15, "atr14": 1.85,
                              "sector": "Health Care"}]}


def test_valid_document_passes():
    assert report.validate_document(good_document(), CANDIDATES["candidates"]) == []


def test_missing_top_level_field_is_reported():
    doc = good_document()
    del doc["market_context"]
    errors = report.validate_document(doc, CANDIDATES["candidates"])
    assert any("market_context" in e for e in errors)


def test_missing_prose_field_is_reported():
    pick = good_pick()
    del pick["bear_case"]
    errors = report.validate_document(good_document(picks=[pick]), CANDIDATES["candidates"])
    assert any("bear_case" in e for e in errors)


def test_empty_prose_field_is_reported():
    errors = report.validate_document(
        good_document(picks=[good_pick(invalidation="   ")]), CANDIDATES["candidates"])
    assert any("invalidation" in e for e in errors)


def test_level_errors_from_ledger_are_included():
    # Stop ueber Entry -- die Regel lebt in ledger.validate_pick, nicht hier.
    errors = report.validate_document(
        good_document(picks=[good_pick(stop=43.50)]), CANDIDATES["candidates"])
    assert any("Stop" in e for e in errors)


def test_source_without_url_is_reported():
    errors = report.validate_document(
        good_document(picks=[good_pick(sources=[{"title": "ohne URL"}])]),
        CANDIDATES["candidates"])
    assert any("sources" in e for e in errors)


def test_source_with_non_http_url_is_reported():
    errors = report.validate_document(
        good_document(picks=[good_pick(sources=[{"url": "javascript:alert(1)"}])]),
        CANDIDATES["candidates"])
    assert any("sources" in e for e in errors)


def test_plain_string_sources_are_accepted_for_compatibility():
    errors = report.validate_document(
        good_document(picks=[good_pick(sources=["https://example.com/a"])]),
        CANDIDATES["candidates"])
    assert errors == []


def test_load_document_reads_json_file(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(good_document()), encoding="utf-8")
    assert report.load_document(path)["date"] == "2026-08-09"


def test_load_document_rejects_markdown(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# Report\n\nkein JSON", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        report.load_document(path)


def test_ledger_entry_strips_prose():
    entry = report.ledger_entry(good_pick())
    assert entry["entry"] == 42.80
    assert "thesis" not in entry
    assert "metrics" not in entry
    assert entry["sources"] == ["https://example.com/a"]

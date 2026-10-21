import datetime as dt

import pytest

from stockpicker import outcomes
from tests.conftest import make_ohlcv


ENTRY_PICK = {
    "ticker": "XYZ", "date": "2026-07-20", "setup": "VCP_BREAKOUT",
    "entry": 100.0, "stop": 95.0, "target1": 110.0, "target2": 120.0,
    "score_catalyst": 7,
}

# ENTRY_PICK ist auf den 2026-07-20 datiert, das Fenster von hold_days_max=10
# endet damit am 2026-07-30.
INSIDE_WINDOW = dt.date(2026, 7, 25)
AFTER_WINDOW = dt.date(2026, 7, 31)


def bars_from(highs, lows):
    import pandas as pd
    idx = pd.bdate_range(start="2026-07-21", periods=len(highs))
    # Eroeffnung in der Mitte der Spanne: keine Bar springt ueber einen Level,
    # die Gap-Fuellung bleibt den Tests ueberlassen, die sie ausdruecklich setzen.
    opens = [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": lows}, index=idx)


def test_not_triggered_when_price_never_reaches_entry():
    bars = bars_from(highs=[98, 97, 96], lows=[95.5, 95.2, 95.1])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "NOT_TRIGGERED"
    assert result["r_multiple"] == 0.0


def test_target_hit_before_stop_yields_positive_r():
    bars = bars_from(highs=[101, 105, 111], lows=[99, 100, 104])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "TARGET1"
    assert result["r_multiple"] == pytest.approx(2.0)


def test_stop_hit_before_target_yields_minus_one_r():
    bars = bars_from(highs=[101, 102, 103], lows=[99, 96, 94])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "STOP"
    assert result["r_multiple"] == pytest.approx(-1.0)


def test_gap_above_entry_fills_at_the_open():
    # Stop-Buy bei 100, Eroeffnung bei 102: gefuellt wird zu 102, und das
    # Ziel bei 110 bringt nur noch (110 - 102) / 5 = 1,6R.
    bars = bars_from(highs=[104, 111], lows=[101, 104])
    bars.iloc[0, bars.columns.get_loc("Open")] = 102.0
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "TARGET1"
    assert result["r_multiple"] == pytest.approx(1.6)


def test_gap_below_stop_fills_at_the_open():
    # Eroeffnung bei 92 unter dem Stop von 95: Ausgang zu 92, also -1,6R.
    bars = bars_from(highs=[101, 93], lows=[99, 91])
    bars.iloc[1, bars.columns.get_loc("Open")] = 92.0
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "STOP"
    assert result["r_multiple"] == pytest.approx(-1.6)


def test_same_day_stop_and_target_counts_as_stop():
    # Konservativ: bei Unklarheit innerhalb einer Bar zaehlt der Stop
    bars = bars_from(highs=[101, 115], lows=[99, 90])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "STOP"


def test_still_open_when_neither_level_reached():
    # Mitten im Fenster: der Trade laeuft noch und zaehlt nicht in der Bilanz.
    bars = bars_from(highs=[101, 103, 104], lows=[99, 100, 101])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3, as_of=INSIDE_WINDOW)
    assert result["status"] == "OPEN"


def test_entry_outside_trigger_window_is_not_triggered():
    bars = bars_from(highs=[95, 96, 97, 105], lows=[94, 95, 96, 100])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "NOT_TRIGGERED"


def test_summarize_groups_by_setup():
    results = [
        {"setup": "VCP_BREAKOUT", "status": "TARGET1", "r_multiple": 2.0, "score_catalyst": 8},
        {"setup": "VCP_BREAKOUT", "status": "STOP", "r_multiple": -1.0, "score_catalyst": 5},
        {"setup": "PEAD", "status": "STOP", "r_multiple": -1.0, "score_catalyst": 9},
    ]
    summary = outcomes.summarize(results)
    vcp = summary["by_setup"]["VCP_BREAKOUT"]
    assert vcp["trades"] == 2
    assert vcp["win_rate"] == pytest.approx(50.0)
    assert vcp["expectancy_r"] == pytest.approx(0.5)
    assert summary["by_setup"]["PEAD"]["win_rate"] == pytest.approx(0.0)


def test_summarize_ignores_not_triggered_and_open():
    results = [
        {"setup": "PEAD", "status": "NOT_TRIGGERED", "r_multiple": 0.0, "score_catalyst": 5},
        {"setup": "PEAD", "status": "OPEN", "r_multiple": 0.0, "score_catalyst": 5},
        {"setup": "PEAD", "status": "TARGET1", "r_multiple": 2.0, "score_catalyst": 5},
    ]
    summary = outcomes.summarize(results)
    assert summary["by_setup"]["PEAD"]["trades"] == 1
    assert summary["not_triggered"] == 1
    assert summary["open"] == 1


# --- Zeitstopp --------------------------------------------------------------
# Der Stop liegt naeher am Entry als das Ziel; im Fenster loest er deshalb
# haeufiger auf. Zaehlte nur, was einen der beiden Punkte beruehrt hat, fiele
# der zaehe Gewinner als "offen" aus der Bilanz und der schnelle Verlust nicht.

def test_expired_window_is_settled_at_the_last_close():
    bars = bars_from(highs=[101, 103, 104], lows=[99, 100, 101])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3, as_of=AFTER_WINDOW)
    assert result["status"] == "TIME_STOP"
    # Schlusskurs der letzten Bar ist 101, Risiko 5 -> +0.2R
    assert result["r_multiple"] == pytest.approx(0.2)
    assert result["closed_on"] == "2026-07-23"


def test_time_stop_counts_as_a_trade():
    results = [
        {"setup": "PEAD", "status": "TIME_STOP", "r_multiple": 0.2, "score_catalyst": 5},
        {"setup": "PEAD", "status": "STOP", "r_multiple": -1.0, "score_catalyst": 5},
    ]
    summary = outcomes.summarize(results)
    assert summary["by_setup"]["PEAD"]["trades"] == 2
    assert summary["time_stop"] == 1
    assert summary["open"] == 0


def test_positive_time_stop_counts_as_a_win():
    # Ohne Target 1 beruehrt zu haben: im Plus abgerechnet ist ein Treffer.
    results = [{"setup": "PEAD", "status": "TIME_STOP", "r_multiple": 0.4,
                "score_catalyst": 5}]
    assert outcomes.summarize(results)["overall"]["win_rate"] == pytest.approx(100.0)


def test_time_stop_at_exactly_zero_is_a_trade_but_no_win():
    results = [{"setup": "PEAD", "status": "TIME_STOP", "r_multiple": 0.0,
                "score_catalyst": 5}]
    overall = outcomes.summarize(results)["overall"]
    assert overall["trades"] == 1
    assert overall["win_rate"] == pytest.approx(0.0)


def test_window_expires_a_day_after_the_last_possible_bar():
    # Die letzte Bar des Fensters liegt auf 2026-07-30. An dem Tag selbst kann
    # sie noch fehlen -- der Sync laeuft morgens.
    assert not outcomes.window_expired("2026-07-20", 10, dt.date(2026, 7, 30))
    assert outcomes.window_expired("2026-07-20", 10, dt.date(2026, 7, 31))


def test_pick_without_date_never_expires():
    assert not outcomes.window_expired(None, 10, dt.date(2030, 1, 1))


def test_summarize_groups_by_catalyst_bucket():
    results = [
        {"setup": "PEAD", "status": "TARGET1", "r_multiple": 2.0, "score_catalyst": 9},
        {"setup": "PEAD", "status": "STOP", "r_multiple": -1.0, "score_catalyst": 2},
    ]
    summary = outcomes.summarize(results)
    assert summary["by_catalyst"]["stark (7-10)"]["win_rate"] == pytest.approx(100.0)
    assert summary["by_catalyst"]["schwach (0-3)"]["win_rate"] == pytest.approx(0.0)


# --- Trigger- und Schlussdatum ---------------------------------------------
# Das Portal zeichnet daraus die Marker im Chart, deshalb muss evaluate die
# beiden Daten herausgeben und nicht nur intern kennen.

def test_target_reports_trigger_and_close_date():
    bars = bars_from(highs=[101, 105, 111], lows=[99, 100, 104])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["triggered_on"] == "2026-07-21"
    assert result["closed_on"] == "2026-07-23"


def test_stop_reports_the_bar_that_closed_the_trade():
    bars = bars_from(highs=[95, 101, 103], lows=[94, 99, 94])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["status"] == "STOP"
    assert result["triggered_on"] == "2026-07-22"
    assert result["closed_on"] == "2026-07-23"


def test_open_trade_has_a_trigger_date_but_no_close_date():
    bars = bars_from(highs=[101, 103, 104], lows=[99, 100, 101])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3, as_of=INSIDE_WINDOW)
    assert result["status"] == "OPEN"
    assert result["triggered_on"] == "2026-07-21"
    assert result["closed_on"] is None


def test_not_triggered_has_neither_date():
    bars = bars_from(highs=[98, 97, 96], lows=[95.5, 95.2, 95.1])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["triggered_on"] is None
    assert result["closed_on"] is None


def test_dates_survive_a_plain_date_index():
    import datetime as dt

    import pandas as pd
    bars = bars_from(highs=[101, 111], lows=[99, 104])
    bars.index = pd.Index([dt.date(2026, 7, 21), dt.date(2026, 7, 22)])
    result = outcomes.evaluate(ENTRY_PICK, bars, trigger_window=3)
    assert result["triggered_on"] == "2026-07-21"
    assert result["closed_on"] == "2026-07-22"

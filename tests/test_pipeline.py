import datetime as dt


from stockpicker import pipeline, templates
from stockpicker.config import ScreenConfig
from tests.conftest import make_ohlcv


def uptrend_bars(n=400, start=20.0, step=0.05, volume=1_000_000):
    return make_ohlcv([start + step * i for i in range(n)], volume=volume)


def make_meta(market_cap=1_000_000_000, next_earnings=None, short=0.05):
    return {"market_cap": market_cap, "short_percent_of_float": short, "next_earnings": next_earnings}


def test_compute_features_returns_all_documented_keys():
    f = pipeline.compute_features("TEST", uptrend_bars(), make_meta(), sector="Health Care")
    for key in ["close", "sma20", "sma50", "sma200", "rsi14", "rsi2", "atr_pct",
                "rvol", "percent_b", "distance_to_pivot_pct", "base_length", "base_depth_pct",
                "range_contraction", "gap_pct", "market_cap", "sector",
                "dollar_volume", "perf_1m", "perf_3m", "perf_6m"]:
        assert key in f, f"fehlender Key: {key}"


def test_hard_filters_reject_penny_stock():
    bars = make_ohlcv([1.5] * 400)
    f = pipeline.compute_features("PENNY", bars, make_meta(), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and "Kurs" in reason


def test_hard_filters_reject_illiquid_stock():
    bars = uptrend_bars(volume=10_000)
    f = pipeline.compute_features("THIN", bars, make_meta(), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and ("Volumen" in reason or "Dollarvolumen" in reason)


def test_hard_filters_reject_market_cap_out_of_range():
    f = pipeline.compute_features("BIG", uptrend_bars(), make_meta(market_cap=50_000_000_000), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and "Marktkap" in reason


def test_hard_filters_reject_short_history():
    f = pipeline.compute_features("NEW", uptrend_bars(n=100), make_meta(), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and "Historie" in reason


def test_hard_filters_accept_normal_small_cap():
    ok, reason = pipeline.passes_hard_filters(
        pipeline.compute_features("GOOD", uptrend_bars(start=40.0, step=0.05), make_meta(), sector="X"),
        ScreenConfig(),
    )
    assert ok, reason


def test_earnings_within_blackout_is_excluded():
    soon = dt.date.today() + dt.timedelta(days=2)
    f = pipeline.compute_features("SOON", uptrend_bars(start=40.0), make_meta(next_earnings=soon), sector="X")
    ok, reason = pipeline.passes_hard_filters(f, ScreenConfig())
    assert not ok and "Earnings" in reason


def test_recent_earnings_sets_pead_flag_without_exclusion():
    f = pipeline.compute_features("PEADCO", uptrend_bars(start=40.0), make_meta(), sector="X",
                                  days_since_earnings=1)
    ok, _ = pipeline.passes_hard_filters(f, ScreenConfig())
    assert ok
    assert f["days_since_earnings"] == 1


def test_rs_percentile_ranks_strongest_stock_highest():
    features = [
        {"ticker": "WEAK", "perf_3m": -5.0, "perf_1m": -2.0, "perf_6m": -8.0},
        {"ticker": "MID", "perf_3m": 5.0, "perf_1m": 2.0, "perf_6m": 8.0},
        {"ticker": "STRONG", "perf_3m": 40.0, "perf_1m": 15.0, "perf_6m": 60.0},
    ]
    ranked = pipeline.add_rs_percentile(features, benchmark_perf={"1m": 0.0, "3m": 0.0, "6m": 0.0})
    by_ticker = {f["ticker"]: f for f in ranked}
    assert by_ticker["STRONG"]["rs_percentile"] > by_ticker["MID"]["rs_percentile"]
    assert by_ticker["MID"]["rs_percentile"] > by_ticker["WEAK"]["rs_percentile"]
    assert 0.0 <= by_ticker["WEAK"]["rs_percentile"] <= 100.0


def test_select_candidates_keeps_only_template_matches():
    matched = {"ticker": "A", "templates": ["VCP_BREAKOUT"], "rs_percentile": 90.0}
    unmatched = {"ticker": "B", "templates": [], "rs_percentile": 95.0}
    selected = pipeline.select_candidates([matched, unmatched], ScreenConfig())
    assert [c["ticker"] for c in selected] == ["A"]


def test_select_candidates_caps_at_max_and_sorts_by_rs():
    features = [
        {"ticker": f"T{i}", "templates": ["PEAD"], "rs_percentile": float(i)}
        for i in range(30)
    ]
    selected = pipeline.select_candidates(features, ScreenConfig(max_candidates=5))
    assert len(selected) == 5
    assert [c["ticker"] for c in selected] == ["T29", "T28", "T27", "T26", "T25"]


def test_run_screen_end_to_end_with_injected_data():
    history = {
        "GOOD": uptrend_bars(start=40.0, step=0.05),
        "PENNY": make_ohlcv([1.2] * 400),
    }
    meta = {"GOOD": make_meta(), "PENNY": make_meta()}
    sectors = {"GOOD": "Health Care", "PENNY": "Energy"}
    benchmark = uptrend_bars(start=200.0, step=0.01)

    result = pipeline.run_screen(history, meta, sectors, benchmark)

    assert "PENNY" not in [c["ticker"] for c in result["candidates"]]
    assert result["stats"]["universe"] == 2
    assert result["stats"]["rejected"] >= 1
    assert "generated_at" in result


def pead_bars(n=400, start=40.0, step=0.05, volume=1_000_000):
    """Aufwaertstrend, dessen letzte Bar mit Volumenschub nach oben gappt.

    Damit erfuellt der Titel alle PEAD-Kriterien ausser ``days_since_earnings``
    -- das muss aus den Metadaten kommen.
    """
    bars = uptrend_bars(n=n, start=start, step=step, volume=volume)
    prev_close = float(bars["Close"].iloc[-2])
    gap_open = prev_close * 1.10
    bars.iloc[-1, bars.columns.get_loc("Open")] = gap_open
    bars.iloc[-1, bars.columns.get_loc("Low")] = gap_open - 0.5
    bars.iloc[-1, bars.columns.get_loc("Close")] = gap_open + 0.5
    bars.iloc[-1, bars.columns.get_loc("High")] = gap_open + 1.0
    bars.iloc[-1, bars.columns.get_loc("Volume")] = volume * 5.0
    return bars


def test_run_screen_passes_days_since_earnings_from_meta():
    """Ohne diese Durchreichung koennte das PEAD-Template nie ausloesen."""
    history = {"FRESH": pead_bars()}
    meta = {"FRESH": {**make_meta(), "last_earnings": dt.date.today() - dt.timedelta(days=1)}}
    benchmark = uptrend_bars(start=200.0, step=0.01)

    result = pipeline.run_screen(history, meta, {"FRESH": "Health Care"}, benchmark)

    by_ticker = {f["ticker"]: f for f in result["candidates"]}
    assert "FRESH" in by_ticker, result["rejections"]
    assert by_ticker["FRESH"]["days_since_earnings"] == 1
    assert "PEAD" in by_ticker["FRESH"]["templates"]


def test_run_screen_defaults_days_since_earnings_without_meta():
    """Ohne last_earnings bleibt es bei 99 -- und PEAD schlaegt nicht an."""
    history = {"STALE": pead_bars()}
    meta = {"STALE": make_meta()}  # kein last_earnings
    benchmark = uptrend_bars(start=200.0, step=0.01)

    result = pipeline.run_screen(history, meta, {"STALE": "Health Care"}, benchmark)

    assert result["stats"]["passed_filters"] == 1, result["rejections"]
    assert result["candidates"] == []
    assert pipeline._days_since(None) == 99
    assert pipeline._days_since(dt.date.today() + dt.timedelta(days=5)) == 99


def test_run_screen_summary_counts_all_rejections_not_just_first_fifty():
    history = {f"P{i}": make_ohlcv([1.2] * 400) for i in range(60)}
    history["GOOD"] = uptrend_bars(start=40.0, step=0.05)
    meta = {t: make_meta() for t in history}
    sectors = {t: "X" for t in history}

    result = pipeline.run_screen(history, meta, sectors, uptrend_bars(start=200.0, step=0.01))

    assert len(result["rejections"]) == 50
    assert result["rejection_summary"]["Kurs ausserhalb 3.0-100.0 USD"] == 60


# --- Template-Quote ---------------------------------------------------------
# Vorher entschied allein der RS-Rang, wer auf die Kandidatenliste kommt. Das
# ueberlaesst die Liste dem Template, das im aktuellen Markt am haeufigsten
# trifft -- in den ersten zwoelf Laeufen 42 von 51 Picks TREND_PULLBACK und
# kein einziges VCP_BREAKOUT.

def _feature(ticker, rs, templates_):
    return {"ticker": ticker, "rs_percentile": rs, "templates": templates_}


def test_select_candidates_gives_every_template_a_turn():
    features = [_feature(f"TP{i}", 90 - i, ["TREND_PULLBACK"]) for i in range(10)]
    features.append(_feature("MR", 20.0, ["MEAN_REVERSION"]))
    cfg = ScreenConfig(max_candidates=3)

    picked = {f["ticker"] for f in pipeline.select_candidates(features, cfg)}
    # Der schwache MEAN_REVERSION kommt mit, obwohl zehn Titel besseren RS
    # haben: sonst waere das Setup nie auswertbar.
    assert "MR" in picked
    assert len(picked) == 3


def test_select_candidates_orders_by_rs_within_a_template():
    features = [_feature("LOW", 40.0, ["TREND_PULLBACK"]),
                _feature("HIGH", 95.0, ["TREND_PULLBACK"])]
    picked = pipeline.select_candidates(features, ScreenConfig(max_candidates=1))
    assert [f["ticker"] for f in picked] == ["HIGH"]


def test_select_candidates_fills_the_cap_from_one_template_if_alone():
    features = [_feature(f"TP{i}", 90 - i, ["TREND_PULLBACK"]) for i in range(10)]
    picked = pipeline.select_candidates(features, ScreenConfig(max_candidates=4))
    assert len(picked) == 4


def test_select_candidates_never_picks_a_title_twice():
    # Ein Titel kann mehrere Templates treffen und darf trotzdem nur einen
    # Platz belegen.
    features = [_feature("BOTH", 90.0, ["VCP_BREAKOUT", "TREND_PULLBACK"]),
                _feature("TP", 80.0, ["TREND_PULLBACK"])]
    picked = pipeline.select_candidates(features, ScreenConfig(max_candidates=5))
    assert [f["ticker"] for f in picked] == ["BOTH", "TP"]


def test_select_candidates_skips_titles_without_a_template():
    features = [_feature("NONE", 99.0, []), _feature("TP", 50.0, ["TREND_PULLBACK"])]
    picked = pipeline.select_candidates(features, ScreenConfig(max_candidates=5))
    assert [f["ticker"] for f in picked] == ["TP"]


# --- ATR-Boden gegen die VCP-Basis ------------------------------------------
# Der Boden von 3 % soll tote Titel aussortieren. Er traf aber auch das VCP,
# das sich gerade ueber die schrumpfende Spanne definiert: an einem gemessenen
# Tag scheiterten drei der acht VCP-Treffer im Universum genau daran.

def _liquid(**overrides):
    """Ein Titel, der alle Hard-Filter ausser dem ATR-Boden sicher passiert.

    Die Vorgabe ist bewusst *keine* VCP-Basis -- die Basis ist zu kurz und das
    Volumen trocknet nicht aus. Wer sie testen will, schaltet sie mit
    ``_vcp_base()`` ein.
    """
    base = {"bars": 300, "close": 50.0, "avg_volume_20d": 1_000_000,
            "dollar_volume": 50_000_000.0, "market_cap": 1_000_000_000.0,
            "atr_pct": 5.0, "days_to_earnings": None,
            "sma20": 49.0, "sma50": 48.0, "sma200": 40.0,
            "base_length": 5, "base_depth_pct": 20.0,
            "range_contraction": 1.0, "volume_dryup": False,
            "distance_to_pivot_pct": -20.0}
    return {**base, **overrides}


def _vcp_base(**overrides):
    """Wie _liquid, aber mit allen VCP-Merkmalen ausser dem RS-Perzentil."""
    return _liquid(**{"base_length": 20, "base_depth_pct": 25.0,
                      "range_contraction": 0.5, "volume_dryup": True,
                      "distance_to_pivot_pct": -2.0, **overrides})


def test_quiet_title_without_a_base_still_fails_the_atr_floor():
    ok, reason = pipeline.passes_hard_filters(_liquid(atr_pct=1.7))
    assert not ok
    assert "ATR%" in reason


def test_vcp_base_passes_the_lower_atr_floor():
    ok, _ = pipeline.passes_hard_filters(_vcp_base(atr_pct=1.7))
    assert ok


def test_the_lower_floor_needs_the_whole_base_not_just_the_contraction():
    # Enge Spanne und trockenes Volumen allein genuegen nicht -- ohne Basis
    # von 15 Bars und ohne Naehe zum Pivot ist es kein VCP, sondern ein
    # ruhiger Titel.
    ok, reason = pipeline.passes_hard_filters(
        _liquid(atr_pct=1.7, volume_dryup=True, range_contraction=0.5))
    assert not ok
    assert "ATR%" in reason


def test_base_does_not_rescue_a_dead_title():
    ok, reason = pipeline.passes_hard_filters(_vcp_base(atr_pct=0.6))
    assert not ok
    assert "ATR%" in reason


def test_base_does_not_lift_the_atr_ceiling():
    ok, reason = pipeline.passes_hard_filters(_vcp_base(atr_pct=15.0))
    assert not ok
    assert "ATR%" in reason


def test_base_test_matches_the_template_apart_from_rs():
    # Die Ausnahme darf nicht weiter greifen als das Template selbst.
    base = _vcp_base()
    assert templates.is_vcp_breakout({**base, "rs_percentile": 95.0})
    assert not templates.is_vcp_breakout({**_liquid(), "rs_percentile": 95.0})


# --- Laufende Positionen ----------------------------------------------------

def test_run_screen_drops_titles_with_a_live_position():
    history = {"AAA": uptrend_bars(), "BBB": uptrend_bars()}
    result = pipeline.run_screen(
        history, {}, {"AAA": "Health Care", "BBB": "Health Care"},
        uptrend_bars(), open_positions={"AAA": "2026-08-20"})

    assert "AAA" not in {c["ticker"] for c in result["candidates"]}
    reasons = {r["ticker"]: r["reason"] for r in result["rejections"]}
    assert "2026-08-20" in reasons["AAA"]

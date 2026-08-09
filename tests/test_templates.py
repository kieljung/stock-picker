from stockpicker import templates


def base_features(**overrides):
    """Neutrale Ausgangslage, die kein Template erfuellt."""
    features = {
        "close": 50.0,
        "sma10": 50.0, "sma20": 50.0, "sma50": 50.0, "sma200": 50.0,
        "rs_percentile": 50.0,
        "rsi14": 50.0, "rsi2": 50.0,
        "percent_b": 0.5,
        "distance_to_pivot_pct": -20.0,
        "base_length": 3, "base_depth_pct": 12.0,
        "range_contraction": 0.9,
        "rvol": 1.0,
        "gap_pct": 0.0,
        "days_since_earnings": 99,
        "atr_pct": 5.0,
        "gap_low_held": False,
        "volume_dryup": False,
    }
    features.update(overrides)
    return features


def test_vcp_breakout_matches_when_all_criteria_hold():
    f = base_features(
        close=52.0, sma20=50.0, sma50=48.0, sma200=45.0,
        rs_percentile=88.0,
        distance_to_pivot_pct=-2.0,
        base_length=18,
        range_contraction=0.45,
        volume_dryup=True,
    )
    assert "VCP_BREAKOUT" in templates.match_templates(f)


def test_vcp_breakout_rejected_when_rs_too_low():
    f = base_features(
        close=52.0, sma20=50.0, sma50=48.0, sma200=45.0,
        rs_percentile=60.0,
        distance_to_pivot_pct=-2.0,
        base_length=18,
        range_contraction=0.45,
        volume_dryup=True,
    )
    assert "VCP_BREAKOUT" not in templates.match_templates(f)


def test_vcp_breakout_rejected_when_far_below_pivot():
    f = base_features(
        rs_percentile=90.0, base_length=18, range_contraction=0.4,
        volume_dryup=True, distance_to_pivot_pct=-15.0,
        close=52.0, sma20=50.0, sma50=48.0, sma200=45.0,
    )
    assert "VCP_BREAKOUT" not in templates.match_templates(f)


def test_trend_pullback_matches_on_sma20_touch():
    f = base_features(
        close=50.5, sma10=51.0, sma20=50.0, sma50=47.0, sma200=42.0,
        rs_percentile=75.0, rvol=0.7,
    )
    assert "TREND_PULLBACK" in templates.match_templates(f)


def test_trend_pullback_rejected_when_sma_stack_broken():
    f = base_features(
        close=50.5, sma10=51.0, sma20=50.0, sma50=52.0, sma200=42.0,
        rs_percentile=75.0, rvol=0.7,
    )
    assert "TREND_PULLBACK" not in templates.match_templates(f)


def test_mean_reversion_matches_connors_criteria():
    f = base_features(close=60.0, sma200=50.0, rsi2=6.0, percent_b=0.05)
    assert "MEAN_REVERSION" in templates.match_templates(f)


def test_mean_reversion_rejected_below_sma200():
    f = base_features(close=40.0, sma200=50.0, rsi2=6.0, percent_b=0.05)
    assert "MEAN_REVERSION" not in templates.match_templates(f)


def test_pead_matches_on_recent_earnings_gap():
    f = base_features(gap_pct=8.0, rvol=4.2, days_since_earnings=1, gap_low_held=True)
    assert "PEAD" in templates.match_templates(f)


def test_pead_rejected_when_gap_low_broken():
    f = base_features(gap_pct=8.0, rvol=4.2, days_since_earnings=1, gap_low_held=False)
    assert "PEAD" not in templates.match_templates(f)


def test_neutral_stock_matches_nothing():
    assert templates.match_templates(base_features()) == []


def test_a_stock_can_match_multiple_templates():
    # close liegt 1 % ueber dem SMA 20: nah genug am Pivot fuer den
    # VCP-Breakout und zugleich ein Ruecksetzer auf den SMA 20.
    f = base_features(
        close=50.5, sma10=51.0, sma20=50.0, sma50=48.0, sma200=45.0,
        rs_percentile=90.0, distance_to_pivot_pct=-1.0,
        base_length=20, range_contraction=0.4, volume_dryup=True,
        rvol=0.7,
    )
    assert len(templates.match_templates(f)) >= 2

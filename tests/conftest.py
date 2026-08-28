"""Gemeinsame Fixtures. Kein Test in diesem Projekt darf Netzwerk verwenden."""

import numpy as np
import pandas as pd
import pytest


def make_ohlcv(closes, *, high_offset=1.0, low_offset=1.0, volume=1_000_000):
    """Baut einen OHLCV-DataFrame aus einer Liste von Schlusskursen.

    Jede Bar bekommt dieselbe Spanne (high_offset + low_offset), damit
    ATR-Erwartungswerte von Hand nachrechenbar sind.
    """
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(end="2026-08-07", periods=n)
    volumes = np.full(n, float(volume)) if np.isscalar(volume) else np.asarray(volume, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + high_offset,
            "Low": closes - low_offset,
            "Close": closes,
            "Volume": volumes,
        },
        index=idx,
    )


@pytest.fixture
def flat_series():
    """300 Bars auf konstantem Kurs 50, Spanne je Bar exakt 2.0."""
    return make_ohlcv([50.0] * 300)


@pytest.fixture
def rising_series():
    """300 Bars, jeden Tag +0.5 — sauberer Aufwaertstrend ohne Ruecksetzer."""
    return make_ohlcv([50.0 + 0.5 * i for i in range(300)])


@pytest.fixture
def falling_series():
    return make_ohlcv([200.0 - 0.5 * i for i in range(300)])


@pytest.fixture(autouse=True)
def isolate_from_local_credentials(monkeypatch, tmp_path):
    """Kein Test darf von einer lokalen .env oder gesetzten Keys abhaengen.

    Ohne das haengt das Ergebnis davon ab, ob auf der Maschine gerade ein
    Alpha-Vantage-Key oder ein Portal-Zugang liegt -- die Suite waere bei dir
    gruen und in CI rot.
    """
    from stockpicker import earnings, publish, splits

    for name in ("ALPHAVANTAGE_API_KEY", "FINNHUB_API_KEY",
                 "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                 "PORTAL_URL", "PORTAL_USER", "PORTAL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for module in (earnings, publish, splits):
        monkeypatch.setattr(module, "DOTENV", tmp_path / "absent.env")

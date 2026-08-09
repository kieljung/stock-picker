import datetime as dt

import pytest

from stockpicker import universe


RAW_CSV = """iShares Russell 2000 ETF
Fund Holdings as of,"Aug 07, 2026"
Inception Date,"May 22, 2000"

Ticker,Name,Sector,Asset Class,Market Value,Weight (%)
AAOI,APPLIED OPTOELECTRONICS INC,Information Technology,Equity,"12,345,678",0.12
BBIO,BRIDGEBIO PHARMA INC,Health Care,Equity,"23,456,789",0.23
XTSLA,BLK CSH FND TREASURY SL AGENCY,Cash and/or Derivatives,Money Market,"1,000",0.01
-,USD CASH,Cash and/or Derivatives,Cash,"500",0.00
BRK.B,BERKSHIRE HATHAWAY,Financials,Equity,"999",0.00
"""


def test_parse_holdings_extracts_equity_tickers():
    rows = universe.parse_holdings_csv(RAW_CSV)
    tickers = [r["ticker"] for r in rows]
    assert "AAOI" in tickers
    assert "BBIO" in tickers


def test_parse_holdings_drops_cash_and_money_market():
    rows = universe.parse_holdings_csv(RAW_CSV)
    tickers = [r["ticker"] for r in rows]
    assert "XTSLA" not in tickers
    assert "-" not in tickers


def test_parse_holdings_keeps_sector():
    rows = universe.parse_holdings_csv(RAW_CSV)
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["BBIO"]["sector"] == "Health Care"


def test_parse_holdings_normalizes_dotted_tickers_for_yahoo():
    rows = universe.parse_holdings_csv(RAW_CSV)
    tickers = [r["ticker"] for r in rows]
    assert "BRK-B" in tickers


def test_parse_holdings_raises_on_missing_header():
    with pytest.raises(ValueError, match="Ticker"):
        universe.parse_holdings_csv("nur muell\nohne tabelle\n")


def test_load_universe_uses_cache_when_download_fails(tmp_path, monkeypatch):
    cache = tmp_path / "iwm_holdings.csv"
    cache.write_text(RAW_CSV, encoding="utf-8")

    def boom(url, timeout):
        raise OSError("network down")

    monkeypatch.setattr(universe, "_download", boom)
    rows, warning = universe.load_universe(cache_path=cache, max_age_days=0)

    assert [r["ticker"] for r in rows][:2] == ["AAOI", "BBIO"]
    assert "cache" in warning.lower()


def test_load_universe_raises_when_no_cache_and_download_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "_download", lambda url, timeout: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(RuntimeError, match="kein Cache"):
        universe.load_universe(cache_path=tmp_path / "missing.csv", max_age_days=0)

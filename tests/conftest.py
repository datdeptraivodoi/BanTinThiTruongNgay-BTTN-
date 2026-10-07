"""Report regression tests use stored data, without fetching chart prices."""
import pytest


@pytest.fixture(autouse=True)
def offline_chart_sources(monkeypatch):
    monkeypatch.setattr("bttn.candlestick.fetch_tradingview_candles", lambda: {})
    monkeypatch.setattr("bttn.candlestick.fetch_yahoo_fallback_candles", lambda key: [])

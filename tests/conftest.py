"""Report regression tests use stored data, without fetching chart prices."""
import pytest


@pytest.fixture(autouse=True)
def offline_chart_sources(monkeypatch):
    monkeypatch.setattr("bttn.candlestick.fetch_tradingview_candles", lambda: {})
    monkeypatch.setattr("bttn.candlestick.fetch_yahoo_fallback_candles", lambda key: [])
    # Test hồi quy giữ mplfinance để không cần Node/Chrome; chart TradingView có test riêng
    monkeypatch.setenv("BTTN_CHART_ENGINE", "mpl")

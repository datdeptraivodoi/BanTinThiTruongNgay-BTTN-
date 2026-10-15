"""Biểu đồ kiểu TradingView (Lightweight Charts)."""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bttn import tv_charts

ROOT = Path(__file__).resolve().parents[1]


def _frame(start=1.14, vol=0.004, n=260):
    rng = np.random.default_rng(7)
    idx = pd.bdate_range(end="2026-10-14", periods=n, tz="Asia/Ho_Chi_Minh")
    close = start * np.exp(np.cumsum(rng.normal(0, vol, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.002,
                         "Low": np.minimum(open_, close) * 0.998, "Close": close, "Volume": 0}, index=idx)


def test_currency_spec_has_indicators_and_zones():
    spec = tv_charts.build_currency_spec(_frame())
    assert len(spec["candles"]) == tv_charts.SESSIONS
    assert len(spec["lines"]) == 4 and len(spec["zones"]) == 2
    assert spec["priceDecimals"] == 4
    assert "MA89" in spec["legend"] and "KC " in spec["legend"] and "HT " in spec["legend"]
    assert spec["candles"][-1]["time"] == "2026-10-14"


def test_commodity_spec_and_jpy_decimals():
    spec = tv_charts.build_commodity_spec(_frame(start=66, vol=0.015))
    assert "Tenkan" in spec["legend"] and spec["priceDecimals"] == 2
    assert tv_charts.build_currency_spec(_frame(start=152))["priceDecimals"] == 2


def test_too_little_data_returns_none():
    assert tv_charts.build_currency_spec(_frame(n=3)) is None


def test_disabled_engine_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("BTTN_CHART_ENGINE", "mpl")
    assert tv_charts.render_tv_chart(_frame(), tmp_path / "x.png", 8.83, 6.8) is False


def _can_render():
    if not shutil.which("node") or not (ROOT / "node_modules" / "puppeteer-core").is_dir():
        return False
    probe = subprocess.run(["node", "-e", "require('fs')"], capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _can_render(), reason="cần Node, npm install và Chrome")
def test_renders_png_at_word_size(monkeypatch, tmp_path):
    monkeypatch.setenv("BTTN_CHART_ENGINE", "tradingview")
    out = tmp_path / "eurusd.png"
    if not tv_charts.render_tv_chart(_frame(), out, 8.83, 6.8, "currency"):
        pytest.skip("không tìm thấy Chrome trên máy test")
    from PIL import Image
    w, h = Image.open(out).size
    assert (w, h) == (round(8.83 * tv_charts.CM_TO_PX) * 3, round(6.8 * tv_charts.CM_TO_PX) * 3)

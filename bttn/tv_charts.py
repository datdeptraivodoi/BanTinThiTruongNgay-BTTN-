"""Biểu đồ nến kiểu TradingView (Lightweight Charts) cho bản tin.

Dữ liệu và chỉ báo tính bằng Python (giống bản mplfinance cũ); việc vẽ do
scripts/render_tv_chart.mjs đảm nhận. Nếu không có Node/Chrome hoặc vẽ lỗi,
hàm trả False để bản tin quay về mplfinance.

Đặt BTTN_CHART_ENGINE=mpl để luôn dùng mplfinance.
"""
import json
import logging
import math
import os
import subprocess
from pathlib import Path

import pandas as pd

from .candlestick import calculate_currency_indicators, calculate_ichimoku_indicators

LOG = logging.getLogger("bttn.tv_charts")
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_tv_chart.mjs"
CM_TO_PX = 96 / 2.54
SESSIONS = 65  # khoảng 3 tháng


def enabled() -> bool:
    return os.environ.get("BTTN_CHART_ENGINE", "tradingview").lower() != "mpl"


def _decimals(price: float) -> int:
    return 2 if price > 20 else 4


def _series(df: pd.DataFrame, col: str) -> list[dict]:
    out = []
    for ts, v in df[col].items():
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        out.append({"time": ts.strftime("%Y-%m-%d"), "value": round(float(v), 6)})
    return out


def _candles(df: pd.DataFrame) -> list[dict]:
    return [{"time": ts.strftime("%Y-%m-%d"), "open": float(r.Open), "high": float(r.High),
             "low": float(r.Low), "close": float(r.Close)} for ts, r in df.iterrows()]


def _span(color, text):
    return f'<span style="color:{color}">{text}</span>'


def build_currency_spec(df: pd.DataFrame) -> dict | None:
    """Nến + MA89 + MA200 + Bollinger(20,2) + vùng kháng cự/hỗ trợ (3 đỉnh/đáy gần nhất)."""
    if df.empty or len(df) < 5:
        return None
    d = calculate_currency_indicators(df).iloc[-SESSIONS:]
    highs = d["High"].nlargest(3).values
    r_max, r_min = float(highs[0]), float(highs[-1])
    lows = d["Low"].nsmallest(3).values
    s_min, s_max = float(lows[0]), float(lows[-1])
    if r_min == r_max:
        r_min = r_max * 0.997
    if s_min == s_max:
        s_max = s_min * 1.003
    dec = _decimals(r_max)
    f = f"{{:.{dec}f}}"
    legend = (_span("#2962FF", "MA89") + " · " + _span("#E65100", "MA200") + " · "
              + _span("#787B86", "BB(20,2)") + "<br>"
              + _span("#f23645", f"KC {f.format(r_min)}–{f.format(r_max)}") + " · "
              + _span("#089981", f"HT {f.format(s_min)}–{f.format(s_max)}"))
    return {
        "candles": _candles(d),
        "lines": [
            {"color": "#2962FF", "width": 1, "style": 0, "data": _series(d, "MA89")},
            {"color": "#E65100", "width": 2, "style": 0, "data": _series(d, "MA200")},
            {"color": "#787B86", "width": 1, "style": 2, "data": _series(d, "BB_Upper")},
            {"color": "#787B86", "width": 1, "style": 2, "data": _series(d, "BB_Lower")},
        ],
        "zones": [
            {"from": r_min, "to": r_max, "color": "rgba(242,54,69,0.14)"},
            {"from": s_min, "to": s_max, "color": "rgba(8,153,129,0.14)"},
        ],
        "legend": legend,
        "priceDecimals": dec,
        "topMargin": 0.3,   # chú thích 2 dòng: chừa chỗ để không che vùng giá cao nhất
    }


def build_commodity_spec(df: pd.DataFrame) -> dict | None:
    """Nến + Tenkan, Kijun, Senkou A/B (Ichimoku rút gọn 9-26-52)."""
    if df.empty or len(df) < 5:
        return None
    d = calculate_ichimoku_indicators(df).iloc[-SESSIONS:]
    legend = (_span("#2962FF", "Tenkan") + " · " + _span("#E65100", "Kijun") + " · "
              + _span("#089981", "Span A") + " · " + _span("#f23645", "Span B"))
    return {
        "candles": _candles(d),
        "lines": [
            {"color": "#2962FF", "width": 1, "style": 0, "data": _series(d, "Tenkan")},
            {"color": "#E65100", "width": 2, "style": 0, "data": _series(d, "Kijun")},
            {"color": "rgba(8,153,129,0.8)", "width": 1, "style": 1, "data": _series(d, "SpanA")},
            {"color": "rgba(242,54,69,0.8)", "width": 1, "style": 1, "data": _series(d, "SpanB")},
        ],
        "zones": [],
        "legend": legend,
        "priceDecimals": _decimals(float(d["Close"].iloc[-1])),
        "topMargin": 0.18,
    }


def render_tv_chart(df: pd.DataFrame, output_path: Path, width_cm: float, height_cm: float,
                    chart_type: str = "currency", timeout: int = 60) -> bool:
    """Vẽ PNG đúng kích thước khung trong Word. Trả False nếu không vẽ được."""
    if not enabled() or not SCRIPT.is_file():
        return False
    spec = build_currency_spec(df) if chart_type == "currency" else build_commodity_spec(df)
    if not spec:
        return False
    from .layout import FONT, SIZE
    spec.update({
        "output": str(Path(output_path).resolve()),
        "width": round(width_cm * CM_TO_PX),
        "height": round(height_cm * CM_TO_PX),
        "scale": 3,
        "fontFamily": FONT,
        "fontSize": round(SIZE * 96 / 72),  # 11 pt ở kích thước thật trong Word
    })
    try:
        proc = subprocess.run(["node", str(SCRIPT)], input=json.dumps(spec), cwd=str(ROOT),
                              capture_output=True, text=True, timeout=timeout)
        result = json.loads(proc.stdout or "{}")
    except Exception as exc:
        LOG.warning("Không vẽ được chart TradingView (%s), dùng mplfinance", exc)
        return False
    if not result.get("ok") or not Path(output_path).is_file():
        LOG.warning("Không vẽ được chart TradingView: %s", result.get("error") or proc.stderr[-300:])
        return False
    return True

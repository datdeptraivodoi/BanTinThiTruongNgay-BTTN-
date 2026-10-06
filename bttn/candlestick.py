"""Module for fetching TradingView candles and rendering candlestick charts with technical indicators."""
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
import requests

LOG = logging.getLogger("bttn.candlestick")
ROOT = Path(__file__).resolve().parent.parent
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

YAHOO_FALLBACK_SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "USDJPY": "USDJPY=X",
    "BRENT": "BZ=F",
    "ARABICA": "KC=F",
}


def fetch_tradingview_candles() -> dict[str, list[dict]]:
    """Executes the Node.js bridge to fetch 300 daily candles from TradingView."""
    bridge_script = ROOT / "scripts" / "fetch_tradingview.js"
    if not bridge_script.is_file():
        LOG.warning("TradingView bridge script not found at %s", bridge_script)
        return {}

    try:
        proc = subprocess.run(
            ["node", str(bridge_script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=35,
            check=True,
        )
        data = json.loads(proc.stdout)
        results = {}
        for key, info in data.items():
            if info.get("success") and info.get("candles"):
                results[key] = info["candles"]
        LOG.info("Fetched TradingView candles for: %s", list(results.keys()))
        return results
    except Exception as exc:
        LOG.warning("Failed to fetch TradingView candles via Node.js bridge: %s", exc)
        return {}


def fetch_yahoo_fallback_candles(key: str) -> list[dict]:
    """Fetches daily candles from Yahoo Finance as a resilient fallback."""
    symbol = YAHOO_FALLBACK_SYMBOLS.get(key)
    if not symbol:
        return []
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?interval=1d&range=1y"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()["chart"]["result"][0]
        timestamps = data["timestamp"]
        q = data["indicators"]["quote"][0]
        candles = []
        for t, o, h, l, c, v in zip(timestamps, q["open"], q["high"], q["low"], q["close"], q["volume"]):
            if o is not None and h is not None and l is not None and c is not None:
                candles.append({
                    "time": t,
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v or 0),
                })
        LOG.info("Fetched %d fallback candles from Yahoo for %s", len(candles), key)
        return candles
    except Exception as exc:
        LOG.warning("Failed to fetch Yahoo fallback candles for %s: %s", key, exc)
        return []


def candles_to_dataframe(candles: list[dict]) -> pd.DataFrame:
    """Converts a list of candle dicts [{time, open, high, low, close, volume}] to OHLCV DataFrame."""
    if not candles:
        return pd.DataFrame()
    rows = []
    for c in candles:
        dt = datetime.fromtimestamp(c["time"], timezone.utc).astimezone(VN_TZ)
        rows.append({
            "Date": dt,
            "Open": float(c["open"]),
            "High": float(c["high"]),
            "Low": float(c["low"]),
            "Close": float(c["close"]),
            "Volume": float(c.get("volume", 0)),
        })
    df = pd.DataFrame(rows)
    df.drop_duplicates(subset=["Date"], keep="last", inplace=True)
    df.sort_values(by="Date", inplace=True)
    df.set_index("Date", inplace=True)
    return df


def get_symbol_dataframe(key: str, snapshot=None, tv_cache: dict | None = None) -> tuple[pd.DataFrame, str]:
    """Retrieves candle DataFrame for a symbol from TradingView, fixture, or Yahoo fallback."""
    if tv_cache and key in tv_cache and tv_cache[key]:
        return candles_to_dataframe(tv_cache[key]), "TradingView"

    if snapshot and getattr(snapshot, "purpose", None) == "fixture":
        # Offline fixture test mode: build DataFrame from fixture series
        obs = snapshot.observations.get(key)
        if obs and obs.series:
            rows = []
            for p in obs.series:
                v = float(p.value)
                rows.append({
                    "Date": p.at.astimezone(VN_TZ),
                    "Open": v,
                    "High": v * 1.001,
                    "Low": v * 0.999,
                    "Close": v,
                    "Volume": 0.0,
                })
            df = pd.DataFrame(rows).drop_duplicates(subset=["Date"], keep="last").sort_values("Date").set_index("Date")
            return df, "Kiểm thử"

    # Try fetching from Yahoo fallback if online
    candles = fetch_yahoo_fallback_candles(key)
    if candles:
        return candles_to_dataframe(candles), "Yahoo Finance"

    return pd.DataFrame(), "Chưa có nguồn"


def calculate_currency_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculates MA 89, MA 200, and Bollinger Bands (20, 2)."""
    df = df.copy()
    df["MA89"] = df["Close"].rolling(window=89, min_periods=1).mean()
    df["MA200"] = df["Close"].rolling(window=200, min_periods=1).mean()
    df["BB_Mid"] = df["Close"].rolling(window=20, min_periods=1).mean()
    df["BB_Std"] = df["Close"].rolling(window=20, min_periods=1).std()
    df["BB_Upper"] = df["BB_Mid"] + 2 * df["BB_Std"]
    df["BB_Lower"] = df["BB_Mid"] - 2 * df["BB_Std"]
    return df


def calculate_ichimoku_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculates simplified Ichimoku Cloud (Tenkan 9, Kijun 26, Senkou A & B 52)."""
    df = df.copy()
    nine_high = df["High"].rolling(window=9, min_periods=1).max()
    nine_low = df["Low"].rolling(window=9, min_periods=1).min()
    df["Tenkan"] = (nine_high + nine_low) / 2

    twenty_six_high = df["High"].rolling(window=26, min_periods=1).max()
    twenty_six_low = df["Low"].rolling(window=26, min_periods=1).min()
    df["Kijun"] = (twenty_six_high + twenty_six_low) / 2

    df["SpanA"] = (df["Tenkan"] + df["Kijun"]) / 2
    fifty_two_high = df["High"].rolling(window=52, min_periods=1).max()
    fifty_two_low = df["Low"].rolling(window=52, min_periods=1).min()
    df["SpanB"] = (fifty_two_high + fifty_two_low) / 2
    return df


def render_currency_chart(df: pd.DataFrame, output_path: Path, title: str, source_label: str = "TradingView") -> bool:
    """Renders Candlestick + MA89 + MA200 + Bollinger Bands + Support & Resistance Zones.
    
    Target shape size in Word: width = 9.0 cm, height = 6.89 cm.
    Aspect ratio: 9.0 / 6.89 ~= 1.306.
    """
    if df.empty or len(df) < 5:
        return False

    df_calc = calculate_currency_indicators(df)
    # Take last 65 sessions (~3 months)
    display_df = df_calc.iloc[-65:].copy()

    mc = mpf.make_marketcolors(up="#26a69a", down="#ef5350", edge="inherit", wick="inherit")
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=":", gridcolor="#e8e8e8", facecolor="white", figcolor="white")

    ap = [
        mpf.make_addplot(display_df["MA89"], color="#2962FF", width=1.1, label="MA89"),
        mpf.make_addplot(display_df["MA200"], color="#E65100", width=1.3, label="MA200"),
        mpf.make_addplot(display_df["BB_Upper"], color="#787B86", linestyle="--", width=0.8),
        mpf.make_addplot(display_df["BB_Lower"], color="#787B86", linestyle="--", width=0.8),
    ]

    fig, axlist = mpf.plot(
        display_df,
        type="candle",
        addplot=ap,
        style=s,
        figsize=(5.6, 4.28),
        returnfig=True,
        volume=False,
        tight_layout=True,
        datetime_format="%d/%m",
        ylabel="",
    )

    ax = axlist[0]

    # Calculate Support and Resistance Zones from swing highs and lows in 3-month window
    highs = display_df["High"].nlargest(3).values
    r_max = float(highs[0])
    r_min = float(highs[-1]) if len(highs) > 1 else r_max * 0.995
    if r_min == r_max:
        r_min = r_max * 0.997

    lows = display_df["Low"].nsmallest(3).values
    s_min = float(lows[0])
    s_max = float(lows[-1]) if len(lows) > 1 else s_min * 1.005
    if s_min == s_max:
        s_max = s_min * 1.003

    # Draw shaded Support & Resistance zones
    ax.axhspan(r_min, r_max, color="#ef5350", alpha=0.14)
    ax.axhspan(s_min, s_max, color="#26a69a", alpha=0.14)

    # Text annotations
    ax.text(0.02, 0.94, f"Kháng cự: {r_min:.4f} - {r_max:.4f}", transform=ax.transAxes, fontsize=6.8, color="#c62828", weight="bold")
    ax.text(0.02, 0.05, f"Hỗ trợ: {s_min:.4f} - {s_max:.4f}", transform=ax.transAxes, fontsize=6.8, color="#2e7d32", weight="bold")

    # Legend / Indicators note
    ax.text(0.55, 0.94, "MA89 (xanh) | MA200 (cam) | BB(20,2)", transform=ax.transAxes, fontsize=6.3, color="#1565C0")

    ax.tick_params(labelsize=6.8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return True


def render_commodity_chart(df: pd.DataFrame, output_path: Path, title: str, source_label: str = "TradingView") -> bool:
    """Renders Candlestick + simplified Ichimoku Cloud (9, 26, 52).
    
    Target shape size in Word: width = 9.0 cm, height = 6.5 cm.
    Aspect ratio: 9.0 / 6.5 ~= 1.385.
    """
    if df.empty or len(df) < 5:
        return False

    df_calc = calculate_ichimoku_indicators(df)
    # Take last 65 sessions (~3 months)
    display_df = df_calc.iloc[-65:].copy()

    mc = mpf.make_marketcolors(up="#26a69a", down="#ef5350", edge="inherit", wick="inherit")
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=":", gridcolor="#e8e8e8", facecolor="white", figcolor="white")

    ap = [
        mpf.make_addplot(display_df["Tenkan"], color="#2962FF", width=1.0),
        mpf.make_addplot(display_df["Kijun"], color="#E65100", width=1.2),
        mpf.make_addplot(display_df["SpanA"], color="#26a69a", width=0.8, linestyle=":"),
        mpf.make_addplot(display_df["SpanB"], color="#ef5350", width=0.8, linestyle=":"),
    ]

    fig, axlist = mpf.plot(
        display_df,
        type="candle",
        addplot=ap,
        style=s,
        figsize=(5.6, 4.04),
        returnfig=True,
        volume=False,
        tight_layout=True,
        datetime_format="%d/%m",
        ylabel="",
    )

    ax = axlist[0]
    x_indices = range(len(display_df))
    sp_a = display_df["SpanA"].values
    sp_b = display_df["SpanB"].values

    ax.fill_between(
        x_indices, sp_a, sp_b,
        where=(sp_a >= sp_b),
        color="#26a69a", alpha=0.18, interpolate=True
    )
    ax.fill_between(
        x_indices, sp_a, sp_b,
        where=(sp_a < sp_b),
        color="#ef5350", alpha=0.18, interpolate=True
    )

    ax.text(0.02, 0.94, "Mây Ichimoku (9, 26, 52)", transform=ax.transAxes, fontsize=6.8, color="#1565C0", weight="bold")
    ax.tick_params(labelsize=6.8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return True

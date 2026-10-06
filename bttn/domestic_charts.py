import json
import logging
import os
import sqlite3
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

LOG = logging.getLogger("bttn.domestic_charts")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = Path(r"D:\TyGia\MasterData\market_master.db")


def get_db_connection(db_path=None):
    candidate = Path(db_path) if db_path else Path(os.environ.get("MARKET_DB_PATH", DEFAULT_DB_PATH))
    if candidate.is_file():
        try:
            return sqlite3.connect(candidate)
        except Exception as exc:
            LOG.warning("Failed to connect to %s: %s", candidate, exc)
    return None


def render_interbank_chart(output_path: Path, db_path=None) -> bool:
    """Renders Chart 1: Diễn biến lãi suất trên thị trường liên ngân hàng (ON, 1W, 2W)."""
    dates = []
    on_series, w1_series, w2_series = [], [], []

    conn = get_db_connection(db_path)
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT date, vnibor_on, vnibor_1w, vnibor_2w
                FROM fx_interbank_history
                WHERE vnibor_on IS NOT NULL
                ORDER BY date DESC
                LIMIT 22
            """)
            rows = cur.fetchall()
            conn.close()
            if rows:
                rows.reverse()
                for r in rows:
                    raw_date = r[0]
                    # format YYYY-MM-DD -> DD/MM/YYYY
                    if "-" in raw_date:
                        parts = raw_date.split("-")
                        d_str = f"{int(parts[2])}/{int(parts[1])}/{parts[0]}"
                    else:
                        d_str = raw_date
                    dates.append(d_str)
                    on_series.append(float(r[1]) if r[1] is not None else 0.0)
                    w1_series.append(float(r[2]) if r[2] is not None else 0.0)
                    w2_series.append(float(r[3]) if r[3] is not None else 0.0)
        except Exception as exc:
            LOG.warning("Error reading fx_interbank_history from db: %s", exc)

    if not dates:
        fallback_file = ROOT / "config" / "domestic_history.json"
        if fallback_file.is_file():
            try:
                data = json.loads(fallback_file.read_text(encoding="utf-8"))
                ib = data.get("interbank", {})
                dates = ib.get("dates", [])
                on_series = ib.get("series", {}).get("ON", [])
                w1_series = ib.get("series", {}).get("1W", [])
                w2_series = ib.get("series", {}).get("2W", [])
            except Exception as exc:
                LOG.warning("Error loading fallback interbank data: %s", exc)

    if not dates or not on_series:
        return False

    fig, ax = plt.subplots(figsize=(5.6, 2.7), dpi=200)

    ax.plot(dates, on_series, color="#2E75B6", linewidth=2.0, label="ON")
    ax.plot(dates, w1_series, color="#C00000", linewidth=2.0, label="1W")
    ax.plot(dates, w2_series, color="#70AD47", linewidth=2.0, label="2W")

    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.16), ncol=3, frameon=False, fontsize=8, handlelength=2.5)
    ax.tick_params(axis="y", labelsize=7.5)
    ax.tick_params(axis="x", labelsize=7, rotation=45)

    # Set x-ticks frequency to avoid overcrowding
    step = max(1, len(dates) // 10)
    tick_indices = list(range(0, len(dates), step))
    if tick_indices[-1] != len(dates) - 1:
        tick_indices.append(len(dates) - 1)
    ax.set_xticks(tick_indices)
    ax.set_xticklabels([dates[i] for i in tick_indices])

    ax.grid(True, linestyle="--", alpha=0.35, color="#D0D0D0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#666666")
    ax.spines["bottom"].set_color("#666666")

    fig.tight_layout(pad=0.6)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)
    return True


def render_usdvnd_chart(output_path: Path, db_path=None) -> bool:
    """Renders Chart 2: Diễn biến tỷ giá USD-VND thị trường liên ngân hàng (Ask, Midpoint, Spread)."""
    dates = []
    ask_series, mid_series, spread_series = [], [], []

    conn = get_db_connection(db_path)
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT date, close_bid, close_ask
                FROM fx_interbank_history
                WHERE close_ask IS NOT NULL
                ORDER BY date DESC
                LIMIT 22
            """)
            rows = cur.fetchall()
            conn.close()
            if rows:
                rows.reverse()
                for r in rows:
                    raw_date = r[0]
                    if "-" in raw_date:
                        parts = raw_date.split("-")
                        d_str = f"{int(parts[2])}/{int(parts[1])}/{parts[0]}"
                    else:
                        d_str = raw_date
                    dates.append(d_str)
                    bid = float(r[1]) if r[1] is not None else 0.0
                    ask = float(r[2]) if r[2] is not None else 0.0
                    ask_series.append(ask)
                    # Midpoint from benchmark matches ~25.600 - 25.645 or midpoint
                    mid = (bid + ask) / 2.0 if bid > 0 else ask - 5.0
                    mid_series.append(mid)
                    spread_series.append(max(0.0, ask - bid) if bid > 0 else 10.0)
        except Exception as exc:
            LOG.warning("Error reading usdvnd from db: %s", exc)

    if not dates:
        fallback_file = ROOT / "config" / "domestic_history.json"
        if fallback_file.is_file():
            try:
                data = json.loads(fallback_file.read_text(encoding="utf-8"))
                uv = data.get("usdvnd", {})
                dates = uv.get("dates", [])
                ser = uv.get("series", {})
                ask_series = ser.get("Ask", [])
                mid_series = ser.get("Midpoint", [])
                spread_series = ser.get("Ask-Bid", [])
            except Exception as exc:
                LOG.warning("Error loading fallback usdvnd data: %s", exc)

    if not dates or not ask_series:
        return False

    fig, ax1 = plt.subplots(figsize=(5.6, 2.7), dpi=200)
    ax2 = ax1.twinx()

    l1 = ax1.plot(dates, ask_series, color="#2E75B6", linewidth=2.0, label="Ask")
    l2 = ax1.plot(dates, mid_series, color="#70AD47", linewidth=2.0, label="Midpoint")
    l3 = ax2.plot(dates, spread_series, color="#C00000", linewidth=2.0, label="Ask-Bid")

    lines = l1 + l2 + l3
    labels = [line.get_label() for line in lines]
    ax1.legend(lines, labels, loc="upper center", bbox_to_anchor=(0.5, 1.16), ncol=3, frameon=False, fontsize=8, handlelength=2.5)

    ax1.tick_params(axis="y", labelsize=7.5)
    ax2.tick_params(axis="y", labelsize=7.5)
    ax1.tick_params(axis="x", labelsize=7, rotation=45)

    step = max(1, len(dates) // 10)
    tick_indices = list(range(0, len(dates), step))
    if tick_indices[-1] != len(dates) - 1:
        tick_indices.append(len(dates) - 1)
    ax1.set_xticks(tick_indices)
    ax1.set_xticklabels([dates[i] for i in tick_indices])

    ax1.grid(True, linestyle="--", alpha=0.35, color="#D0D0D0")
    ax1.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)
    ax1.spines["left"].set_color("#666666")
    ax2.spines["right"].set_color("#666666")
    ax1.spines["bottom"].set_color("#666666")

    # Range adjustments for right axis
    if spread_series:
        ax2.set_ylim(0, max(max(spread_series) * 1.4, 15))

    fig.tight_layout(pad=0.6)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)
    return True


def render_bond_yield_chart(output_path: Path) -> bool:
    """Renders Chart 3: Diễn biến lãi suất trái phiếu thị trường liên ngân hàng (1y, 2y, 3y, 5y, 7y)."""
    bond_file = ROOT / "config" / "bond_yield_history.json"
    if not bond_file.is_file():
        return False

    try:
        data = json.loads(bond_file.read_text(encoding="utf-8"))
        dates = data.get("dates", [])
        series_dict = data.get("series", {})
    except Exception as exc:
        LOG.warning("Failed to load bond yield history: %s", exc)
        return False

    if not dates or not series_dict:
        return False

    fig, ax = plt.subplots(figsize=(5.6, 2.7), dpi=200)

    colors = {
        "1y": "#2E75B6",
        "2y": "#C00000",
        "3y": "#92D050",
        "5y": "#7030A0",
        "7y": "#3FA9F5",
    }

    for name in ["1y", "2y", "3y", "5y", "7y"]:
        if name in series_dict:
            ax.plot(dates, series_dict[name], color=colors.get(name, "#333333"), linewidth=1.8, label=name)

    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.16), ncol=5, frameon=False, fontsize=8, handlelength=2.5)
    ax.tick_params(axis="y", labelsize=7.5)
    ax.tick_params(axis="x", labelsize=7, rotation=45)

    step = max(1, len(dates) // 10)
    tick_indices = list(range(0, len(dates), step))
    if tick_indices[-1] != len(dates) - 1:
        tick_indices.append(len(dates) - 1)
    ax.set_xticks(tick_indices)
    ax.set_xticklabels([dates[i] for i in tick_indices])

    ax.grid(True, linestyle="--", alpha=0.35, color="#D0D0D0")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#666666")
    ax.spines["bottom"].set_color("#666666")

    fig.tight_layout(pad=0.6)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)
    return True

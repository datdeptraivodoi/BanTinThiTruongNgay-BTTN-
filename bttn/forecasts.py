"""Market forecast generators for BTTN report sections."""
import random
import re
from decimal import Decimal
from typing import Any

from .models import ReportContent, Snapshot
from .trader_quotes import trader_observation


def build_interbank_forecast(snapshot: Snapshot, content: ReportContent | None = None) -> str:
    """Generate interbank money market forecast.

    Standard format:
    'Dự kiến: lãi suất ON nhiều khả năng đi ngang quanh {rate}%, lãi suất trái phiếu đi ngang.'
    Allowed rates: 1,5%; 2,5%; 3,5%; 4,5%; 5,5%; 6,5%
    Selection rule: choose candidate closest to the current ON rate in the narrative.
    If equidistant (e.g. 4.0% between 3.5% and 4.5%), choose randomly between them.
    """
    candidates = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5]
    on_rate = None

    if content and hasattr(content, "interbank") and content.interbank and content.interbank.paragraphs:
        text = " ".join(content.interbank.paragraphs)
        m = re.search(r"qua\s+đêm[^\d]*(\d+(?:[.,]\d+)?)\s*%", text, re.I)
        if m:
            try:
                on_rate = float(m.group(1).replace(",", "."))
            except ValueError:
                pass

    if on_rate is None:
        obs = snapshot.observations.get("VND_ON")
        if obs and obs.value:
            try:
                on_rate = float(obs.value)
            except (ValueError, TypeError):
                pass

    if on_rate is None:
        on_rate = 2.5

    min_dist = min(abs(c - on_rate) for c in candidates)
    closest = [c for c in candidates if abs(abs(c - on_rate) - min_dist) < 1e-6]
    selected = random.choice(closest)
    selected_str = f"{selected:.1f}%".replace(".", ",")

    return f"Dự kiến: lãi suất ON nhiều khả năng đi ngang quanh {selected_str}, lãi suất trái phiếu đi ngang."



def build_usdvnd_forecast(snapshot: Snapshot) -> str:
    """Generate 14-word USD-VND forecast based on buy/sell rates (-30 / +70 points).

    Standard format:
    'Dự kiến: tỷ giá USD-VND có thể diễn biến dao động quanh ngưỡng 25.700-26.150.'
    """
    mb_buy = snapshot.observations.get("MB_BUY")
    mb_sell = snapshot.observations.get("MB_SELL")
    if mb_buy and mb_sell and mb_buy.value and mb_sell.value:
        b_val = float(mb_buy.value)
        s_val = float(mb_sell.value)
    else:
        t_bid = trader_observation(snapshot, "INTERBANK_BID")
        t_ask = trader_observation(snapshot, "INTERBANK_ASK")
        if t_bid and t_ask and t_bid.value and t_ask.value:
            b_val = float(t_bid.value)
            s_val = float(t_ask.value)
        else:
            b_val, s_val = 25730.0, 26080.0

    buy_target = int(round(b_val - 30))
    sell_target = int(round(s_val + 70))
    low_str = f"{buy_target:,}".replace(",", ".")
    high_str = f"{sell_target:,}".replace(",", ".")
    return f"Dự kiến: tỷ giá USD-VND có thể diễn biến dao động quanh ngưỡng {low_str}-{high_str}."


def build_eurusd_forecast(snapshot: Snapshot, content: ReportContent | None = None) -> str:
    """Generate EUR-USD forecast with a 120-pip range around spot, biased by sentiment.

    Standard format:
    'Dự kiến: Tỷ giá EUR-USD có thể dao động đi ngang giá trong biên độ vừa quanh khu vực 1,1150-1,1240.'
    """
    obs = snapshot.observations.get("EURUSD")
    spot = float(obs.value) if obs and obs.value else 1.1184

    # Assess sentiment from content text if available, or daily change
    bullish_cues = ["tăng", "phục hồi", "khởi sắc", "bứt phá", "vượt", "hỗ trợ", "tích cực", "mạnh", "ổn định", "bullish"]
    bearish_cues = ["giảm", "suy yếu", "sụt", "áp lực", "thấp", "tiêu cực", "rơi", "lao dốc", "bearish"]
    score = 0
    if content and hasattr(content, "eur_usd") and content.eur_usd and content.eur_usd.paragraphs:
        text = " ".join(content.eur_usd.paragraphs).lower()
        b_cnt = sum(text.count(w) for w in bullish_cues)
        s_cnt = sum(text.count(w) for w in bearish_cues)
        score = b_cnt - s_cnt
    elif obs and obs.daily_pct is not None:
        score = 1 if obs.daily_pct > 0 else (-1 if obs.daily_pct < 0 else 0)

    # Total width is 120 pips (0.0120)
    if score > 0:
        # Bullish bias: +80 pips / -40 pips
        low = spot - 0.0040
        high = spot + 0.0080
    elif score < 0:
        # Bearish bias: +40 pips / -80 pips
        low = spot - 0.0080
        high = spot + 0.0040
    else:
        # Neutral bias: +-60 pips
        low = spot - 0.0060
        high = spot + 0.0060

    low_str = f"{low:.4f}".replace(".", ",")
    high_str = f"{high:.4f}".replace(".", ",")
    return f"Dự kiến: Tỷ giá EUR-USD có thể dao động đi ngang giá trong biên độ vừa quanh khu vực {low_str}-{high_str}."


def build_asia_forecast(snapshot: Snapshot) -> str:
    """Generate Asian FX forecast: USD/JPY spot + 40 pips, USD/CNH spot + 30 pips.

    Standard format:
    'Dự kiến: Tỷ giá USD-JPY dao động quanh 158,55; tỷ giá USD-CNH có thể đi ngang quanh 6,7150.'
    """
    jpy_obs = snapshot.observations.get("USDJPY")
    jpy_spot = float(jpy_obs.value) if jpy_obs and jpy_obs.value else 158.15
    jpy_target = jpy_spot + 0.40
    jpy_str = f"{jpy_target:.2f}".replace(".", ",")

    cnh_obs = snapshot.observations.get("USDCNH") or snapshot.observations.get("USDCNY")
    cnh_spot = float(cnh_obs.value) if cnh_obs and cnh_obs.value else 6.7120
    cnh_target = cnh_spot + 0.0030
    cnh_str = f"{cnh_target:.4f}".replace(".", ",")

    return f"Dự kiến: Tỷ giá USD-JPY dao động quanh {jpy_str}; tỷ giá USD-CNH có thể đi ngang quanh {cnh_str}."


def build_energy_metals_forecast(snapshot: Snapshot) -> str:
    """Generate energy and metals forecast: Brent $10 range, Gold $200 range around spot.

    Standard format:
    'Dự kiến: Giá dầu Brent dao động từ $100-$108/thùng. Giá vàng biến động từ $4.070-$4.281/ounce.'
    """
    brent_obs = snapshot.observations.get("BRENT")
    brent_spot = float(brent_obs.value) if brent_obs and brent_obs.value else 100.0
    brent_low = int(round(brent_spot - 5))
    brent_high = int(round(brent_spot + 5))

    gold_obs = snapshot.observations.get("GOLD")
    gold_spot = float(gold_obs.value) if gold_obs and gold_obs.value else 4140.0
    gold_low = int(round(gold_spot - 100))
    gold_high = int(round(gold_spot + 100))
    gold_low_str = f"{gold_low:,}".replace(",", ".")
    gold_high_str = f"{gold_high:,}".replace(",", ".")

    return f"Dự kiến: Giá dầu Brent dao động từ ${brent_low}-${brent_high}/thùng. Giá vàng biến động từ ${gold_low_str}-${gold_high_str}/ounce."


def build_coffee_forecast(snapshot: Snapshot) -> str:
    """Generate coffee forecast approximately around spot prices.

    Standard format:
    'Dự kiến: Giá Arabica giao dịch quanh 295,2 USc/lbs, giá Robusta dao động quanh ngưỡng 3.470 USD/T.'
    """
    ara_obs = snapshot.observations.get("ARABICA")
    robu_obs = snapshot.observations.get("ROBUSTA")
    ara_spot = float(ara_obs.value) if ara_obs and ara_obs.value else 295.2
    robu_spot = float(robu_obs.value) if robu_obs and robu_obs.value else 3470.0

    ara_str = f"{ara_spot:.1f}".replace(".", ",")
    robu_str = f"{int(round(robu_spot)):,}".replace(",", ".")

    return f"Dự kiến: Giá Arabica giao dịch quanh {ara_str} USc/lbs, giá Robusta dao động quanh ngưỡng {robu_str} USD/T."

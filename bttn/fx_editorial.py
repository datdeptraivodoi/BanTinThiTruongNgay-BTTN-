"""Shared EUR/JPY opening rules for prompts, Python editing and validation."""
import re
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .models import Snapshot

EUR_SECOND_OPENING = "Về phía Châu Âu,"


def fx_opening(snapshot: "Snapshot", section: str) -> str:
    from .models import previous_weekday

    local_day = snapshot.as_of.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    pair_id = {"eur_usd": "EURUSD", "japan": "USDJPY"}[section]
    observation = snapshot.observations.get(pair_id)
    previous_day = previous_weekday(local_day)
    if observation is not None:
        if observation.prev_value is not None and observation.prev_trading_date is not None:
            previous_day = observation.prev_trading_date
        elif observation.prev_value is None and len(observation.series) >= 2:
            # Match the point used to resolve the _prev placeholder, including holidays.
            previous_day = observation.series[-2].at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    today, previous = local_day.strftime("%d.%m.%Y"), previous_day.strftime("%d.%m.%Y")
    # A quote level alone cannot establish that the market is flat. Use neutral prose.
    if section == "eur_usd":
        return (
            f"Trong phiên giao dịch hôm qua, tính đến ngày {previous}, "
            "tỷ giá EUR-USD đóng cửa quanh mức {{EURUSD_prev}}. "
            f"Trong phiên {today}, tỷ giá EUR-USD giao dịch quanh mức {{{{EURUSD}}}}."
        )
    return (
        f"Trong phiên hôm qua ngày {previous}, "
        "tỷ giá USD-JPY đóng cửa ở mức {{USDJPY_prev}}. "
        f"Trong phiên giao dịch hôm nay {today}, tỷ giá USD-JPY giao dịch quanh mức {{{{USDJPY}}}}."
    )


def replace_fx_opening(text: str, snapshot: "Snapshot", section: str) -> str:
    """Replace dated quote templates while preserving all following commentary."""
    opening = fx_opening(snapshot, section)
    if text.startswith(opening):
        return text
    pair = {"eur_usd": "EUR-USD", "japan": "USD-JPY"}[section]
    quote_sentence = re.compile(
        rf"^(?:Trong phiên\b.*?tỷ giá {pair} "
        r"(?:đóng cửa (?:quanh|ở)|(?:ổn định|đi ngang|giao dịch) quanh) mức|Tỷ giá) "
        r"\{\{[A-Za-z0-9_ ]+\}\}[.!?]$",
        re.I,
    )
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    # Remove only leading quote-template sentences, never a news paragraph.
    for _ in range(2):
        if not sentences or not quote_sentence.fullmatch(sentences[0]):
            break
        sentences.pop(0)
    return f"{opening} {' '.join(sentences)}".strip()

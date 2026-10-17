"""Tin Trading Economics của cả tuần cho trang 2 bản tiếng Anh.

Đọc đúng danh sách tin mà các trang tradingeconomics.com/<nước>/news và /commodities tải về
(endpoint ws/stream.ashx), không vượt qua lớp chặn nào. Lấy tin trong tuần (thứ Hai – thứ Sáu,
giờ Việt Nam), bỏ tin cổ phiếu/chỉ số chứng khoán và các bản tin "Commodities Updates" tự động.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from ..tradingeconomics_news import is_stock_market_article

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
STREAM = "https://tradingeconomics.com/ws/stream.ashx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}
QUERIES = {
    "united-states": {"c": "united states"},
    "euro-area": {"c": "euro area"},
    "germany": {"c": "germany"},
    "japan": {"c": "japan"},
    "commodity": {"i": "commodity"},
}


def topic_of(url: str) -> str | None:
    """currency_us / currency_eu / currency_jp / macro_us / macro_eu / macro_jp / coffee / energy."""
    path = url.split("tradingeconomics.com")[-1].strip("/").lower()
    if path.startswith("commodity/"):
        name = path.split("/")[1]
        if name in ("coffee", "robusta"):
            return "coffee"
        if name in ("brent-crude-oil", "crude-oil", "gasoline"):
            return "energy"
        return None
    region = {"united-states": "us", "euro-area": "eu", "germany": "eu", "japan": "jp"}.get(path.split("/")[0])
    if not region:
        return None
    return f"currency_{region}" if path.endswith("/currency") else f"macro_{region}"


def parse_stream(records, monday: date, friday: date) -> list[dict]:
    start = datetime.combine(monday, time.min, VN_TZ)
    end = datetime.combine(friday, time.max, VN_TZ)
    items = []
    for rec in records or []:
        try:
            title = BeautifulSoup(rec.get("title") or "", "html.parser").get_text(" ", strip=True)
            body = BeautifulSoup(rec.get("description") or "", "html.parser").get_text(" ", strip=True)
            url = rec["url"] if rec["url"].startswith("http") else "https://tradingeconomics.com" + rec["url"]
            at = datetime.fromisoformat(rec["date"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        topic = topic_of(url)
        if not topic or not title or len(body.split()) < 20 or not start <= at <= end:
            continue
        if is_stock_market_article(title, url) or "commodities updates" in title.lower():
            continue
        items.append({"id": str(rec.get("id") or url), "title": title, "text": body, "url": url,
                      "published_at": at, "topic": topic})
    return items


def collect_week(monday: date, friday: date, *, session=None, size=80) -> tuple[dict[str, list[dict]], list[str]]:
    session = session or requests
    found, errors, seen = {}, [], set()
    for name, params in QUERIES.items():
        try:
            resp = session.get(STREAM, params={"start": 0, "size": size, **params},
                               headers={**HEADERS, "Referer": f"https://tradingeconomics.com/{name}/news"},
                               timeout=(10, 25))
            if resp.status_code != 200:
                errors.append(f"{name}: HTTP {resp.status_code}")
                continue
            records = resp.json()
        except Exception as exc:  # noqa: BLE001 - ghi lại lỗi nguồn, không dừng cả bản tin
            errors.append(f"{name}: {type(exc).__name__}")
            continue
        for item in parse_stream(records, monday, friday):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            found.setdefault(item["topic"], []).append(item)
    for items in found.values():
        items.sort(key=lambda x: x["published_at"], reverse=True)
    return found, errors


def week_window(friday: date) -> tuple[date, date]:
    monday = friday - timedelta(days=friday.weekday())
    return monday, monday + timedelta(days=4)

"""Trading Economics calendar: fallback when ForexFactory cannot be reached (Rules CAL-03).

The public page https://tradingeconomics.com/calendar is server-rendered and
filtered by cookies, as the page's own controls do:

* ``calendar-importance=2``          Medium and High impact (2–3 stars)
* ``calendar-countries=usa,emu,...`` ISO3 country list
* ``cal-custom-range=YYYY-MM-DD|YYYY-MM-DD``  the week
* ``cal-timezone-offset=420``        times shown in Vietnam time (UTC+7)

Checked in a browser on 25/09/2026: each event is a ``tr[data-event]`` row.
The first cell carries the date as a class (``2026-09-29``) and the time plus
importance (``calendar-date-3``); then ISO2 code (``td.calendar-iso``), event
name (``a.calendar-event``), reference period (``span.calendar-reference``),
``#actual``, ``#previous``, ``#revised``, ``#consensus`` and ``#forecast``
(Trading Economics' own forecast).

Items are returned in the same shape as the ForexFactory items so the calendar
builder treats both sources alike.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

PAGE_URL = "https://tradingeconomics.com/calendar"
SITE = "https://tradingeconomics.com"
VN_OFFSET_MINUTES = 420
VN_TZ = timezone(timedelta(minutes=VN_OFFSET_MINUTES))

# ISO3 cookie value, ISO2 on the page, currency code used by the builder.
COUNTRIES = {
    "usa": ("US", "USD"),
    "emu": ("EA", "EUR"),
    "chn": ("CN", "CNY"),
    "jpn": ("JP", "JPY"),
    "gbr": ("GB", "GBP"),
    "can": ("CA", "CAD"),
    "aus": ("AU", "AUD"),
}
ISO2_TO_CURRENCY = {iso2: currency for iso2, currency in COUNTRIES.values()}


def cookies_for(monday: date, *, importance: int = 2) -> dict[str, str]:
    friday = monday + timedelta(days=4)
    return {
        "calendar-importance": str(importance),
        "calendar-countries": ",".join(COUNTRIES),
        "cal-custom-range": f"{monday.isoformat()}|{friday.isoformat()}",
        "cal-timezone-offset": str(VN_OFFSET_MINUTES),
    }


def _text(node: Any) -> str | None:
    if node is None:
        return None
    text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
    return text or None


def _time(value: str | None) -> tuple[int, int] | None:
    match = re.search(r"(\d{1,2}):(\d{2})\s*([AP]M)?", value or "", re.I)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    meridiem = (match.group(3) or "").upper()
    if meridiem == "PM" and hour != 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0
    return hour, minute


def parse_calendar(html: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    items = []
    for row in soup.select("#calendar tr[data-event]"):
        first = row.find("td")
        if first is None:
            continue
        day = next((cls for cls in first.get("class", []) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cls)), None)
        if day is None:
            continue
        stamp = first.find("span")
        classes = " ".join(stamp.get("class", [])) if stamp else ""
        level = re.search(r"calendar-date-(\d)", classes)
        importance = int(level.group(1)) if level else 0
        iso2 = _text(row.select_one("td.calendar-iso"))
        title = _text(row.select_one("a.calendar-event"))
        if not iso2 or not title:
            continue
        clock = _time(_text(stamp))
        moment = datetime.fromisoformat(day).replace(tzinfo=VN_TZ)
        if clock:
            moment = moment.replace(hour=clock[0], minute=clock[1])
        consensus = _text(row.select_one("#consensus"))
        te_forecast = _text(row.select_one("#forecast"))
        # When the previous figure was revised, the cell shows the revised value and the
        # tooltip "Previous revised from X" keeps the first release.
        shown_previous = _text(row.select_one("#previous"))
        marker = row.select_one("#revised")
        original = re.search(r"revised from\s*(.+)$", (marker.get("title") or "") if marker else "", re.I)
        previous, revised = (original.group(1).strip(), shown_previous) if original else (shown_previous, None)
        link = row.get("data-url") or ""
        items.append({
            "title": title,
            "country": ISO2_TO_CURRENCY.get(iso2.upper(), iso2.upper()),
            "iso2": iso2.upper(),
            "date": moment.isoformat(),
            "allDay": clock is None,
            "impact": {3: "High", 2: "Medium", 1: "Low"}.get(importance, ""),
            "period": _text(row.select_one("span.calendar-reference")),
            "actual": _text(row.select_one("#actual")),
            "previous": previous,
            "revision": revised,
            # Market consensus first; Trading Economics' own forecast only when there is none.
            "forecast": consensus or te_forecast,
            "forecastKind": "consensus" if consensus else ("te_forecast" if te_forecast else None),
            "sourceName": "Trading Economics",
            "sourceUrl": SITE + link if link.startswith("/") else PAGE_URL,
            "from": "te",
        })
    return items


def read_week(monday: date, *, session: Any = None, timeout: int = 45) -> tuple[list[dict[str, Any]], str]:
    """High and Medium events of the 7 economies for one Monday-Friday week."""
    import requests

    http = session or requests.Session()
    try:
        response = http.get(
            PAGE_URL,
            cookies=cookies_for(monday),
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
    except Exception as exc:
        return [], f"te {exc.__class__.__name__}"
    if response.status_code != 200:
        return [], f"te http {response.status_code}"
    items = parse_calendar(response.text)
    friday = monday + timedelta(days=4)
    # Keep only the requested week and 2–3-star rows, whatever the page returned.
    items = [
        item for item in items
        if item["impact"] in {"High", "Medium"} and item["country"] in ISO2_TO_CURRENCY.values()
        and monday.isoformat() <= item["date"][:10] <= friday.isoformat()
    ]
    if not items and "calendar" not in response.text:
        return [], "te: calendar not found"
    return items, f"te ({len(items)})"

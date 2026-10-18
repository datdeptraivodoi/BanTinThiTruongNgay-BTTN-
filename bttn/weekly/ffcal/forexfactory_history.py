"""Events of any week (next week, or past weeks with actuals) from the ForexFactory calendar page.

The weekly export has no actual values. For a week that has ended, the page
https://www.forexfactory.com/calendar?week=sep14.2026 shows actual, forecast,
previous and revised previous. This module reads that page and returns items in
the same shape as the export, plus ``actual`` and ``revision``.

Two readers, in order:
1. the calendar state embedded in the page (``calendarComponentStates``), which
   carries a UTC timestamp for every event;
2. the HTML table, when the embedded state is missing. Its times depend on the
   page's time zone, so these items carry only currency/title/date and are used
   to fill actual values into rows that already exist, never to create rows.

The page sits behind bot protection; plain HTTP is tried first and a real
Chrome (Selenium) second. Parsing has not been verified against the live page
from the cloud workspace, so the first run on the laptop is the acceptance test.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

PAGE = "https://www.forexfactory.com/calendar?week={slug}"
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def week_slug(monday: date) -> str:
    return f"{MONTHS[monday.month - 1]}{monday.day}.{monday.year}"


def _text(value: Any) -> str | None:
    text = re.sub(r"<[^>]+>", "", str(value or "")).strip()
    return text or None


def _impact(value: Any) -> str:
    text = str(value or "").lower()
    for level in ("high", "medium", "low", "holiday"):
        if level in text:
            return level.capitalize()
    if "red" in text:
        return "High"
    if "ora" in text:
        return "Medium"
    if "yel" in text:
        return "Low"
    return ""


def _js_object_to_json(text: str) -> str:
    """Quote bare keys of a JS object literal so json.loads can read it."""
    return re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', text)


def _balanced(text: str, start: int) -> str | None:
    depth, in_string, escape = 0, False, False
    quote = ""
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                in_string = False
            continue
        if char in "\"'":
            in_string, quote = True, char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def parse_embedded_state(html: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in re.finditer(r"calendarComponentStates\[\d+\]\s*=\s*\{", html):
        block = _balanced(html, match.end() - 1)
        if not block:
            continue
        try:
            state = json.loads(block)
        except ValueError:
            try:
                state = json.loads(_js_object_to_json(block))
            except ValueError:
                continue
        for day in state.get("days", []) if isinstance(state, dict) else []:
            for event in day.get("events", []) if isinstance(day, dict) else []:
                stamp = event.get("dateline")
                title = _text(event.get("name") or event.get("title"))
                if not isinstance(stamp, (int, float)) or not title:
                    continue
                items.append({
                    "title": title,
                    "country": str(event.get("currency") or event.get("country") or "").upper(),
                    "date": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                    "impact": _impact(event.get("impactName") or event.get("impactClass") or event.get("impact")),
                    "forecast": _text(event.get("forecast")),
                    "previous": _text(event.get("previous")),
                    "actual": _text(event.get("actual")),
                    "revision": _text(event.get("revision")),
                    "from": "embedded",
                })
    return items


def parse_table(html: str, monday: date) -> list[dict[str, Any]]:
    """Fallback: rows of the calendar table (date only, no reliable time zone)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    items, current = [], None
    for row in soup.select("tr.calendar__row"):
        cell = row.select_one("td.calendar__date")
        if cell and cell.get_text(strip=True):
            found = re.search(r"([A-Za-z]{3})\s*(\d{1,2})", cell.get_text(" ", strip=True).split(" ", 1)[-1])
            if found and found.group(1).lower() in MONTHS:
                month = MONTHS.index(found.group(1).lower()) + 1
                year = monday.year + (1 if month < monday.month and monday.month == 12 else 0)
                current = date(year, month, int(found.group(2)))
        title = row.select_one(".calendar__event-title")
        currency = row.select_one("td.calendar__currency")
        if current is None or title is None or currency is None:
            continue
        impact_span = row.select_one("td.calendar__impact span")
        impact = _impact(" ".join(impact_span.get("class", [])) + " " + str(impact_span.get("title", ""))) if impact_span else ""

        def value(name: str) -> str | None:
            node = row.select_one(f"td.calendar__{name}")
            return _text(node.get_text(" ", strip=True)) if node else None

        previous = row.select_one("td.calendar__previous")
        revised = previous.select_one("[class*=revised]") if previous else None
        items.append({
            "title": title.get_text(" ", strip=True),
            "country": currency.get_text(strip=True).upper(),
            "day": current.isoformat(),
            "impact": impact,
            "forecast": value("forecast"),
            "previous": value("previous"),
            "actual": value("actual"),
            "revision": _text(revised.get("title")) if revised is not None and revised.get("title") else None,
            "from": "table",
        })
    return items


def fetch_week_html(monday: date, *, use_browser: bool = True, timeout: int = 45) -> tuple[str | None, str]:
    """HTML of the week page and how it was obtained (or why it failed)."""
    url = PAGE.format(slug=week_slug(monday))
    try:
        import requests

        response = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if response.status_code == 200 and "calendar__row" in response.text:
            return response.text, "http"
        http_status = f"http {response.status_code}"
    except Exception as exc:  # fall through to the browser
        http_status = f"http {exc.__class__.__name__}"
    if not use_browser:
        return None, http_status
    try:
        import os
        import time

        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        # Some bot checks reject headless Chrome; MASTERDATA_FF_HEADLESS=0 opens a minimized window instead.
        if os.getenv("MASTERDATA_FF_HEADLESS", "1").strip() != "0":
            options.add_argument("--headless=new")
        else:
            options.add_argument("--start-minimized")
        options.add_argument("--window-size=1400,1000")
        options.add_argument("--no-first-run")
        driver = webdriver.Chrome(options=options)
        try:
            driver.set_page_load_timeout(timeout)
            driver.get(url)
            for _ in range(timeout // 3):
                if "calendar__row" in driver.page_source:
                    break
                time.sleep(3)
            html = driver.page_source
        finally:
            driver.quit()
        if "calendar__row" in html:
            return html, "browser"
        return None, f"{http_status}; browser: calendar not found (blocked?)"
    except Exception as exc:
        return None, f"{http_status}; browser {exc.__class__.__name__}: {exc}"[:300]


def read_week(monday: date, *, use_browser: bool = True) -> tuple[list[dict[str, Any]], str]:
    html, how = fetch_week_html(monday, use_browser=use_browser)
    if html is None:
        return [], how
    items = parse_embedded_state(html)
    if items:
        return items, f"{how}+embedded"
    items = parse_table(html, monday)
    return items, f"{how}+table" if items else f"{how}: no events parsed"

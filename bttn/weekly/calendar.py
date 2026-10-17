"""Lịch sự kiện quan trọng tuần kế tiếp (trang 3), hoàn toàn bằng Python, không dùng AI.

Dùng chung logic với trang MB Treasury Research (DuAnMasterData, cổng 5180): file xuất lịch
ForexFactory, khi thiếu tuần sau thì trang lịch Trading Economics (trang tĩnh, gọi như trình duyệt
thông thường); dòng Việt Nam theo lịch NSO và Cục Hải quan. Tên tiếng Việt lấy từ bảng tên
config/weekly_calendar/forexfactory_titles_vi.json. Bản tin lấy tối đa 15 dòng như file mẫu.
"""
from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

from . import layout
from .content import Calendar, CalendarEvent

ROOT = Path(__file__).resolve().parents[2]
CAL_ROOT = ROOT / "data" / "weekly" / "calendar"
MAX_ROWS = 15


def _num(value, lang):
    if value in (None, ""):
        return ""
    text = str(value).strip()
    return text.replace(".", ",") if lang == "vi" else text


def _name(row: dict, lang: str) -> str:
    name = row.get("nameVi" if lang == "vi" else "nameEn") or ""
    country = row.get("countryVi" if lang == "vi" else "countryEn") or ""
    prefix = f"{country}: "
    return name[len(prefix):] if country and name.startswith(prefix) else name


def to_calendars(pack: dict, next_monday: date) -> dict[str, Calendar]:
    events = pack.get("events") or []
    vn = [e for e in events if str(e.get("countryCode")) == "VN"]
    intl = sorted((e for e in events if str(e.get("countryCode")) != "VN"),
                  key=lambda e: (0 if e.get("impact") == "high" else 1, -float(e.get("priorityScore") or 0)))
    chosen = vn + intl[: max(0, MAX_ROWS - len(vn))]
    chosen.sort(key=lambda e: (e.get("localDate") or "", e.get("localTime") or "", -float(e.get("priorityScore") or 0)))
    out = {}
    for lang in ("vi", "en"):
        rows, last_day = [], None
        for e in chosen[:MAX_ROWS]:
            day = date.fromisoformat(e["localDate"])
            rows.append(CalendarEvent(
                date=day.strftime("%d.%m.%Y") if day != last_day else "",
                event=_name(e, lang),
                country=e.get("countryVi" if lang == "vi" else "countryEn") or "",
                forecast=_num(e.get("forecast"), lang),
                previous=_num(e.get("previous"), lang),
                bold=e.get("impact") == "high" or str(e.get("countryCode")) == "VN",
            ))
            last_day = day
        out[lang] = Calendar(title=layout.calendar_title(next_monday, lang), events=rows)
    return out


def build_calendar(next_monday: date, *, fetch=True, writer=None) -> tuple[dict[str, Calendar], list[dict]]:
    from .ffcal.forexfactory_calendar import write_calendar_packs

    (CAL_ROOT / "config").mkdir(parents=True, exist_ok=True)
    for name in ("forexfactory_titles_vi.json", "economic_calendar_seed.json"):
        shutil.copy2(ROOT / "config" / "weekly_calendar" / name, CAL_ROOT / "config" / name)
    issues = []
    try:
        result = (writer or write_calendar_packs)(CAL_ROOT, today=next_monday - timedelta(days=3), fetch=fetch,
                                                  backfill=False, use_browser=False)
        pack = next((p for p in result["packs"] if p["week"]["startDate"] == next_monday.isoformat()), None)
    except Exception as exc:  # noqa: BLE001 - lịch lỗi không chặn cả bản tin
        pack = None
        issues.append({"severity": "warning", "code": "CALENDAR_SOURCE", "message": f"{type(exc).__name__}: {exc}"})
    if not pack or not pack.get("events"):
        issues.append({"severity": "warning", "code": "CALENDAR_EMPTY",
                       "message": "Chưa lấy được lịch sự kiện tuần sau (ForexFactory/Trading Economics)."})
        empty = {lang: Calendar(title=layout.calendar_title(next_monday, lang), events=[]) for lang in ("vi", "en")}
        return empty, issues
    for note in (pack.get("quality") or {}).get("untranslatedTitles", []):
        issues.append({"severity": "warning", "code": "CALENDAR_UNTRANSLATED", "message": f"Chưa có tên tiếng Việt: {note}"})
    return to_calendars(pack, next_monday), issues

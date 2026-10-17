"""Vietnam Customs release schedule ("Lịch công bố thông tin") for the calendar.

The public page https://www.customs.gov.vn/index.jsp?pageId=445 loads its
"Lịch công bố thông tin" box from GetFEItemTKHQ: ``arrTTSapCongBo`` lists the
upcoming releases, ``arrTTMoiCongBo`` the latest ones. Each item has the report
title (TIEU_DE), the data month (NGAY_CONG_BO, MM-YYYY) and the preliminary
release date (NGAY_SO_BO, DD/MM/YYYY). The full searchable list (pageId=449)
needs a captcha and is not used.

Every fetch is merged into a local history file, so past weeks keep the days
on which Customs published even after they leave the box.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

API_URL = "https://www.customs.gov.vn/bridge?url=/customs/api/GetFEItemTKHQ"
PAGE_URL = "https://www.customs.gov.vn/index.jsp?pageId=445&group=C%C3%94NG%20B%E1%BB%90%20V%C3%80%20TH%C3%94NG%20TIN"
REQUEST = {
    "ma": None, "top": 40, "chuyen_muc_id": -1, "sap_cong_bo_id": 190, "moi_cong_bo_id": 1,
    "marquee_chuyen_muc_id": None, "show_chi_tieu": 0, "take": 40, "language": "TIENG_VIET",
}


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_schedule(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Releases from a GetFEItemTKHQ response, upcoming and latest together."""
    data = payload.get("d") or {}
    rows = []
    for key in ("arrTTSapCongBo", "arrTTMoiCongBo"):
        for item in data.get(key) or []:
            day = _date(item.get("NGAY_SO_BO"))
            title = re.sub(r"\s+", " ", str(item.get("TIEU_DE") or "")).strip()
            if day is None or not title:
                continue
            rows.append({
                "id": item.get("MA"),
                "date": day.isoformat(),
                "month": str(item.get("NGAY_CONG_BO") or ""),
                "title": title,
            })
    return rows


def fetch_schedule(session: Any = None) -> list[dict[str, Any]]:
    import requests

    http = session or requests.Session()
    # The site's own jQuery call posts the JSON text as a form body.
    response = http.post(
        API_URL,
        data=json.dumps(REQUEST),
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": PAGE_URL,
            "User-Agent": "Mozilla/5.0 (MB Treasury Research internal calendar)",
        },
        timeout=30,
    )
    response.raise_for_status()
    return parse_schedule(response.json())


def merge_history(path: Path, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add rows to the history file (key: id, else date+title); returns all rows."""
    history = load_history(path)
    known = {(row.get("id") or f"{row['date']}|{row['title']}"): row for row in history}
    for row in rows:
        known[row.get("id") or f"{row['date']}|{row['title']}"] = row
    merged = sorted(known.values(), key=lambda row: (row["date"], row["title"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"updatedAt": datetime.now(timezone.utc).isoformat(), "source": PAGE_URL,
                                     "releases": merged}, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)
    return merged


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("releases", []))
    except ValueError:
        return []


def release_days(history: Iterable[dict[str, Any]], start: date, end: date) -> dict[date, list[str]]:
    days: dict[date, list[str]] = {}
    for row in history:
        day = date.fromisoformat(row["date"])
        if start <= day <= end and row["title"] not in days.setdefault(day, []):
            days[day].append(row["title"])
    return dict(sorted(days.items()))


def summarize(titles: list[str]) -> str:
    """One Vietnamese line for everything Customs publishes on a day."""
    parts: list[str] = []

    def add(text: str) -> None:
        if text not in parts:
            parts.append(text)

    for title in titles:
        lowered = title.lower()
        month = re.search(r"tháng\s+0?(\d{1,2})/(\d{4})", lowered) or re.search(r"/0?(\d{1,2})/(\d{4})", lowered)
        label = f"{int(month.group(1))}/{month.group(2)}" if month else ""
        if "từ ngày 01" in lowered or "từ ngày 1/" in lowered:
            add(f"sơ bộ kỳ 1 tháng {label}")
        elif "từ ngày 16" in lowered:
            add(f"sơ bộ kỳ 2 tháng {label}")
        elif "tỉnh" in lowered or "fdi" in lowered or "nước/vùng" in lowered:
            add(f"chi tiết theo tỉnh/thành, FDI và thị trường tháng {label}")
        elif "phương thức vận tải" in lowered:
            quarter = re.search(r"quý\s+([ivx]+)/(\d{4})", lowered)
            add(f"theo phương thức vận tải quý {quarter.group(1).upper()}/{quarter.group(2)}" if quarter else "theo phương thức vận tải")
        elif month:
            add(f"sơ bộ tháng {label}")
        else:
            add(title)
    return "Cục Hải quan công bố số liệu xuất nhập khẩu: " + "; ".join(parts)

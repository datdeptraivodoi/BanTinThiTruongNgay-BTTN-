"""Lợi suất TPCP Việt Nam cho bản tin tuần: ghép VBMA (thứ Sáu, đủ 1–30 năm, công bố chậm 1 tuần)
với VIRA (phiên thứ Hai – thứ Năm, kỳ hạn 3/5/7/10/15 năm, đăng sáng hôm sau).

VIRA "Bản tin Kinh tế - Tài chính ngày" có câu dạng "Lợi suất TPCP trên thị trường thứ cấp …, chốt phiên:
3Y 3,72%; 5Y 4,29%; 7Y 4,34%; 10Y 4,46%; 15Y 4,57%." cho phiên làm việc liền trước ngày đăng. Đối chiếu
tháng 9–10/2026, số VIRA khớp số VBMA trong khoảng 1 điểm cơ bản.

VIRA không có kỳ hạn 1 và 2 năm: ước tính bằng số VBMA gần nhất cộng mức thay đổi kỳ hạn 3 năm từ phiên
VBMA đó đến phiên VIRA (lợi suất ngắn hạn thường đi cùng 3 năm). Phiên nào có số VBMA thì luôn dùng VBMA.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import vbma

VIRA_LIST = "https://vira.org.vn/tin/Ban-tin-Kinh-te-Tai-chinh-ngay.html"
VIRA_STORE = Path(__file__).resolve().parents[2] / "data" / "weekly" / "vira_yields.json"
VIRA_SENTENCE = re.compile(r"Lợi suất TPCP[^.]*?chốt phiên[^:]*:\s*((?:\d{1,2}Y\s*\d+,\d+\s*%\s*;?\s*)+)", re.I)
VIRA_CELL = re.compile(r"(\d{1,2})Y\s*(\d+,\d+)\s*%")
TABLE_TENORS = ("1N", "2N", "3N", "5N", "7N", "10N", "15N")
MAX_GAP_BP = 5          # VIRA và VBMA lệch quá mức này thì báo trong email review


def parse_vira_yields(text: str) -> dict[str, float]:
    m = VIRA_SENTENCE.search(" ".join(text.split()))
    if not m:
        return {}
    return {f"{int(t)}N": float(v.replace(",", ".")) for t, v in VIRA_CELL.findall(m.group(1))}


def previous_workday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def fetch_vira(monday: date, friday: date, *, fetch=None) -> tuple[dict[date, dict[str, float]], list[str]]:
    """Lợi suất các phiên trong tuần theo bản tin ngày của VIRA (đăng từ thứ Ba đến thứ Sáu)."""
    from bs4 import BeautifulSoup

    fetch = fetch or vbma._get
    try:
        soup = BeautifulSoup(fetch(VIRA_LIST).decode("utf-8", "replace"), "html.parser")
    except Exception as exc:  # noqa: BLE001
        return {}, [f"Không mở được bản tin ngày của VIRA ({type(exc).__name__})"]
    links = []
    for a in soup.select(".story__title a"):
        url = urljoin(VIRA_LIST, a.get("href", ""))
        if urlparse(url).hostname == "vira.org.vn" and url not in links:
            links.append(url)
    out, notes = {}, []
    for url in links[:10]:
        try:
            page = BeautifulSoup(fetch(url).decode("utf-8", "replace"), "html.parser")
            meta = page.find("meta", property="article:published_time")
            published = datetime.fromisoformat(meta["content"]).date()
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Không đọc được bản tin VIRA {url.rsplit('/', 1)[-1]} ({type(exc).__name__})")
            continue
        if published < monday:
            break
        if published > friday:
            continue
        body = page.select_one("#abody") or page
        row = parse_vira_yields(body.get_text(" ", strip=True))
        if row:
            out[previous_workday(published)] = row
    return out, notes


def load(path: Path) -> dict[date, dict[str, float]]:
    if not path.is_file():
        return {}
    return {date.fromisoformat(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def save(store: dict[date, dict[str, float]], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k.isoformat(): store[k] for k in sorted(store)}, indent=1), encoding="utf-8")
    tmp.replace(path)


def combine(vbma_rows: dict[date, dict], vira_rows: dict[date, dict]) -> tuple[dict[date, dict], list[str]]:
    """Ghép hai nguồn theo ngày. Ngày có VBMA dùng VBMA; ngày chỉ có VIRA thì ước tính 1N/2N."""
    out = {d: dict(r) for d, r in vbma_rows.items()}
    notes = []
    fridays = sorted(vbma_rows)
    for day, row in sorted(vira_rows.items()):
        if day in out:
            gap = max((abs(row[t] - out[day][t]) * 100 for t in row if t in out[day]), default=0)
            if gap > MAX_GAP_BP:
                notes.append(f"Lợi suất TPCP phiên {day:%d/%m}: VIRA lệch VBMA {gap:.0f} điểm cơ bản")
            continue
        base = [d for d in fridays if d <= day]
        full = dict(row)
        if base and "3N" in row and "3N" in vbma_rows[base[-1]]:
            ref = vbma_rows[base[-1]]
            shift = row["3N"] - ref["3N"]
            for t in ("1N", "2N"):
                if t in ref:
                    full[t] = round(ref[t] + shift, 2)
            gap = max((abs(row[t] - ref[t]) * 100 for t in row if t in ref), default=0)
            if gap > 25:
                notes.append(f"Lợi suất TPCP phiên {day:%d/%m} (VIRA) lệch số VBMA {base[-1]:%d/%m} tới {gap:.0f} điểm cơ bản")
        out[day] = full
    return out, notes


def refresh(friday: date, *, fetch=None, vbma_path: Path = vbma.STORE, vira_path: Path = VIRA_STORE):
    """Cập nhật kho VBMA và VIRA trên VPS rồi ghép. Trả về (số theo ngày, ghi chú cho email review)."""
    monday = friday - timedelta(days=friday.weekday())
    kwargs = {"fetch": fetch} if fetch else {}
    vb, notes = vbma.refresh(friday, path=vbma_path, **kwargs)
    vira_store = load(vira_path)
    fresh, more = fetch_vira(monday - timedelta(days=7), friday, **kwargs)
    notes += more
    if fresh:
        vira_store.update(fresh)
        save(vira_store, vira_path)
    elif not any(monday <= d <= friday for d in vira_store):
        notes.append("Chưa lấy được lợi suất TPCP tuần này từ VIRA; dùng số VBMA gần nhất")
    rows, more = combine(vb, vira_store)
    return rows, notes + more


def week_view(rows: dict[date, dict], monday: date, friday: date):
    """(phiên mới nhất trong kỳ, số phiên đó, số tuần trước để so sánh) hoặc None."""
    current = [d for d in rows if monday <= d <= friday]
    before = [d for d in rows if d < monday]
    if not current or not before:
        return None
    last, prev = max(current), max(before)
    return last, rows[last], rows[prev]

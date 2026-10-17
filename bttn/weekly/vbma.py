"""Lợi suất TPCP Việt Nam theo báo cáo tuần của VBMA (https://vbma.org.vn/vi/reports/weekly).

Mỗi báo cáo tuần là một file PDF; trang "Thị trường TPCP thứ cấp" có bảng "BIẾN ĐỘNG LỢI SUẤT PHÒNG
GIAO DỊCH VBMA": cột 1N, 2N, 3N, 5N, 7N, 10N, 15N, 20N, 30N; dòng thứ Sáu tuần báo cáo, thứ Sáu tuần
trước và cùng kỳ năm trước. Bảng được dựng lại theo vị trí chữ trên trang (không dựa vào thứ tự chữ
trong file PDF, vốn bị xáo trộn).

VBMA đăng báo cáo vào khoảng thứ Ba tuần sau, nên khi bản tin tuần chạy (thứ Sáu) số mới nhất là của
thứ Sáu tuần trước. Lợi suất TPCP ít thay đổi nên các phiên sau đó được giữ bằng số mới nhất và email
review ghi rõ ngày số liệu VBMA.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import unquote, urljoin

LIST_URL = "https://vbma.org.vn/vi/reports/weekly"
TENORS = ("1N", "2N", "3N", "5N", "7N", "10N", "15N", "20N", "30N")
CHART_TENORS = ("1N", "2N", "3N", "5N", "7N")       # biểu đồ bản tin tuần: 1, 2, 3, 5, 7 năm
TABLE_TITLE = re.compile(r"BIẾN\s+ĐỘNG\s+LỢI\s+SUẤT", re.I)
DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
PCT = re.compile(r"^(-?\d+(?:\.\d+)?)%$")
REPORT = re.compile(r"(\d{2})(\d{2})(\d{4})\s*-\s*(\d{2})(\d{2})(\d{4})[^/]*\.pdf$", re.I)


@dataclass
class Report:
    start: date
    end: date
    url: str


def list_reports(html: str, base: str = LIST_URL) -> list[Report]:
    """Các báo cáo tuần trên trang danh sách, mới nhất trước (tên file: ddmmyyyy - ddmmyyyy ... .pdf)."""
    out = {}
    for href in re.findall(r"""href=["']([^"']+\.pdf)["']""", html, re.I):
        url = urljoin(base, href)
        m = REPORT.search(unquote(url))
        if not m:
            continue
        d1, m1, y1, d2, m2, y2 = (int(x) for x in m.groups())
        out[url] = Report(date(y1, m1, d1), date(y2, m2, d2), url)
    return sorted(out.values(), key=lambda r: r.end, reverse=True)


def parse_yield_table(words: list[dict]) -> dict[date, dict[str, float]]:
    """words: [{text, x0, top}] của một trang. Trả về {ngày: {kỳ hạn: lợi suất %}}."""
    title = next((w for w in words if TABLE_TITLE.search(w["text"])), None)
    if title is None:
        # Một số trình đọc PDF tách tiêu đề thành từng chữ: dò chữ "BIẾN" rồi "ĐỘNG" cùng dòng
        title = next((w for w in words if w["text"].upper() == "BIẾN" and any(
            v["text"].upper() == "ĐỘNG" and abs(v["top"] - w["top"]) < 3 for v in words)), None)
    if title is None:
        return {}
    area = [w for w in words if title["top"] < w["top"] < title["top"] + 130]
    rows: dict[float, list[dict]] = {}
    for w in area:
        key = next((k for k in rows if abs(k - w["top"]) < 3), w["top"])
        rows.setdefault(key, []).append(w)
    header = None
    for top in sorted(rows):
        labels = [w for w in rows[top] if w["text"] in TENORS]
        if len(labels) >= 5:
            header = {w["text"]: w["x0"] for w in labels}
            break
    if not header:
        return {}
    out = {}
    for top in sorted(rows):
        cells = sorted(rows[top], key=lambda w: w["x0"])
        when = next((DATE.match(w["text"]) for w in cells if DATE.match(w["text"])), None)
        values = [(w["x0"], float(PCT.match(w["text"]).group(1))) for w in cells if PCT.match(w["text"])]
        if not when or len(values) < 5:
            continue
        day = date(int(when.group(3)), int(when.group(2)), int(when.group(1)))
        row = {}
        for x, v in values:
            tenor = min(header, key=lambda t: abs(header[t] - x))
            if abs(header[tenor] - x) <= 25:
                row[tenor] = v
        out[day] = row
    return out


def pdf_yields(pdf_bytes: bytes) -> dict[date, dict[str, float]]:
    """Đọc PDF báo cáo tuần (PyMuPDF), tìm trang có bảng lợi suất."""
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            if not TABLE_TITLE.search(page.get_text()):
                continue
            words = [{"text": w[4], "x0": w[0], "top": w[1]} for w in page.get_text("words")]
            table = parse_yield_table(words)
            if table:
                return table
    return {}


def _get(url: str, http=None) -> bytes:
    if http is not None:
        return http.get(url).content
    import requests

    resp = requests.get(url, timeout=(10, 40), headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0 Safari/537.36", "Accept-Language": "vi,en;q=0.8"})
    resp.raise_for_status()
    return resp.content


def latest_yields(friday: date, *, http=None, fetch=_get, weeks: int = 1) -> tuple[dict[date, dict[str, float]], list[str]]:
    """Lợi suất từ các báo cáo VBMA mới nhất kết thúc trước hoặc đúng thứ Sáu của kỳ bản tin.

    weeks > 1 dùng để nạp lại lịch sử (mỗi báo cáo cho 2 thứ Sáu: tuần báo cáo và tuần trước đó).
    """
    notes = []
    try:
        reports = [r for r in list_reports(fetch(LIST_URL, http).decode("utf-8", "replace")) if r.end <= friday]
    except Exception as exc:  # noqa: BLE001 - nguồn lỗi thì báo trong email review
        return {}, [f"Không mở được trang báo cáo tuần VBMA ({type(exc).__name__}: {exc})"]
    if not reports:
        return {}, ["Trang VBMA chưa có báo cáo tuần nào trước kỳ bản tin"]
    out: dict[date, dict[str, float]] = {}
    for rep in reports[:weeks]:
        try:
            table = pdf_yields(fetch(rep.url, http))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Không đọc được báo cáo VBMA {rep.start:%d/%m}-{rep.end:%d/%m/%Y} ({type(exc).__name__})")
            continue
        if not table:
            notes.append(f"Báo cáo VBMA {rep.start:%d/%m}-{rep.end:%d/%m/%Y} không có bảng lợi suất như mọi tuần")
        for day, row in table.items():
            if 0 <= (rep.end - day).days <= 7:   # bỏ dòng cùng kỳ năm trước
                out.setdefault(day, row)
    return out, notes


def chart_label(day: date) -> str:
    """Nhãn trục ngày như biểu đồ trong file mẫu: 9/10/2026 (không có số 0 đứng đầu)."""
    return f"{day.day}/{day.month}/{day.year}"


def parse_label(label: str) -> date | None:
    try:
        return datetime.strptime(label, "%d/%m/%Y").date()
    except ValueError:
        return None

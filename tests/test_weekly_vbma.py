"""Lợi suất TPCP từ báo cáo tuần VBMA: đọc bảng theo vị trí chữ, nối vào biểu đồ, câu trong đoạn trái phiếu."""
import json
from datetime import date
from pathlib import Path

from bttn.weekly import vbma
from bttn.weekly.history import bonds_chart

FIX = Path(__file__).parent / "fixtures" / "vbma_weekly_20261009_p5.json"
WORDS = json.loads(FIX.read_text(encoding="utf-8"))["words"]
ROW_0910 = {"1N": 3.54, "2N": 3.64, "3N": 3.72, "5N": 4.29, "7N": 4.34, "10N": 4.46, "15N": 4.58, "20N": 4.67, "30N": 4.73}


def test_parse_table_from_real_report_layout():
    table = vbma.parse_yield_table(WORDS)
    assert set(table) == {date(2026, 10, 9), date(2026, 10, 2), date(2025, 10, 9)}
    assert table[date(2026, 10, 9)] == ROW_0910
    assert table[date(2026, 10, 2)]["10N"] == 4.47 and table[date(2026, 10, 2)]["3N"] == 3.71


def test_pdf_reader_finds_table():
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for w in WORDS:
        page.insert_text((w["x0"], w["top"] + 7), w["text"], fontsize=7,
                         fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", fontname="dv")
    table = vbma.pdf_yields(doc.tobytes())
    assert table[date(2026, 10, 9)] == ROW_0910


LIST_HTML = """<a href="https://vbma.org.vn/storage/reports/October2026/05102026%20-%2009102026%20BAO%20CAO%20TUAN%20TTTP.pdf">Tải xuống</a>
<a href="/storage/reports/October2026/28092026%20-%2002102026%20BAO%20CAO%20TUAN%20TTTP.pdf">Tải xuống</a>
<a href="/storage/reports/other.pdf">x</a>"""


def test_list_reports_newest_first():
    reps = vbma.list_reports(LIST_HTML)
    assert [(r.start, r.end) for r in reps] == [(date(2026, 10, 5), date(2026, 10, 9)), (date(2026, 9, 28), date(2026, 10, 2))]
    assert reps[1].url.startswith("https://vbma.org.vn/storage/")


def test_latest_yields_uses_reports_up_to_friday(monkeypatch):
    monkeypatch.setattr(vbma, "pdf_yields", lambda b: vbma.parse_yield_table(WORDS) if b == b"pdf0910" else {})
    calls = []

    def fetch(url, http=None):
        calls.append(url)
        return LIST_HTML.encode() if url == vbma.LIST_URL else (b"pdf0910" if "05102026" in url else b"other")

    out, notes = vbma.latest_yields(date(2026, 10, 16), fetch=fetch, weeks=2)
    assert sorted(out) == [date(2026, 10, 2), date(2026, 10, 9)]          # bỏ dòng cùng kỳ năm trước
    assert "không có bảng lợi suất" in notes[0]
    out, _ = vbma.latest_yields(date(2026, 10, 8), fetch=fetch)             # báo cáo 05-09/10 chưa thuộc kỳ
    assert "05102026" not in "".join(calls[-1:])


def test_latest_yields_reports_source_error():
    def broken(url, http=None):
        raise OSError("blocked")
    out, notes = vbma.latest_yields(date(2026, 10, 16), fetch=broken)
    assert out == {} and "VBMA" in notes[0]


def test_chart_rebuilt_from_vbma_fridays():
    row_0210 = {"1N": 3.53, "2N": 3.64, "3N": 3.71, "5N": 4.29, "7N": 4.34}
    chart = bonds_chart({date(2026, 10, 2): row_0210, date(2026, 10, 9): ROW_0910}, date(2026, 10, 16))
    assert chart.categories[0] == "2/10/2026" and chart.categories[-1] == "16/10/2026"
    assert len(chart.categories) == 11                                       # chỉ ngày làm việc
    i = chart.categories.index("8/10/2026")
    assert [s[i] for s in chart.series] == [3.53, 3.64, 3.71, 4.29, 4.34]    # trước 9/10: số thứ Sáu 2/10
    assert [s[-1] for s in chart.series] == [3.54, 3.64, 3.72, 4.29, 4.34]   # sau 9/10: giữ số mới nhất


def test_chart_keeps_about_one_year():
    from datetime import timedelta
    fridays = {date(2026, 10, 9) - timedelta(days=7 * k): ROW_0910 for k in range(60)}
    chart = bonds_chart(fridays, date(2026, 10, 16))
    assert len(chart.categories) == 261 and chart.categories[-1] == "16/10/2026"


def test_refresh_backfills_once_then_only_new_reports(tmp_path, monkeypatch):
    asked = []

    def fake_latest(friday, *, http=None, fetch=None, weeks=1, pause=0):
        asked.append(weeks)
        from datetime import timedelta
        return {friday - timedelta(days=7 * k): ROW_0910 for k in range(1, weeks + 1)}, []

    monkeypatch.setattr(vbma, "latest_yields", fake_latest)
    store, _ = vbma.refresh(date(2026, 10, 16), path=tmp_path / "v.json")
    assert asked == [53] and len(store) == 53
    store, _ = vbma.refresh(date(2026, 10, 23), path=tmp_path / "v.json")
    assert asked == [53, 2] and len(store) == 54


def test_list_pages_followed_for_backfill(monkeypatch):
    monkeypatch.setattr(vbma, "pdf_yields", lambda b: {})
    pages = []

    def fetch(url, http=None):
        if "storage" in url:
            return b"pdf"
        pages.append(url)
        return (LIST_HTML if url == vbma.LIST_URL else "").encode()

    vbma.latest_yields(date(2026, 10, 16), fetch=fetch, weeks=53)
    assert pages == [vbma.LIST_URL, vbma.LIST_PAGE_URL.format(n=2)]


def test_list_reports_raw_hrefs_with_spaces():
    html = """<a href="/storage/reports/October2026/05102026 - 09102026 BAO CAO TUAN TTTP.pdf">x</a>
    <a href="/storage/reports/December2025/13102025-17102025  BAO CAO TUAN TTTP.pdf">x</a>"""
    reps = vbma.list_reports(html)
    assert [(r.start, r.end) for r in reps] == [(date(2026, 10, 5), date(2026, 10, 9)), (date(2025, 10, 13), date(2025, 10, 17))]
    assert reps[1].url == "https://vbma.org.vn/storage/reports/December2025/13102025-17102025  BAO CAO TUAN TTTP.pdf"


def test_older_layout_shifted_columns():
    # Báo cáo 19-23/01/2026: cột lệch vài điểm so với 2026-10, vẫn khớp đúng kỳ hạn
    xs = {"1N": 127, "2N": 178, "3N": 228, "5N": 278, "7N": 327, "10N": 370, "15N": 418, "20N": 471, "30N": 523}
    vals = {"1N": 121, "2N": 172, "3N": 222, "5N": 271, "7N": 321, "10N": 366, "15N": 415, "20N": 467, "30N": 519}
    row = [2.99, 3.11, 3.15, 3.62, 3.70, 4.07, 4.20, 4.30, 4.34]
    words = [{"text": "BIẾN ĐỘNG LỢI SUẤT PHÒNG GIAO DỊCH VBMA", "x0": 45, "top": 400}]
    words += [{"text": t, "x0": x, "top": 416} for t, x in xs.items()]
    words += [{"text": "23/1/2026", "x0": 51, "top": 431}] + [{"text": f"{v:.2f}%", "x0": vals[t], "top": 431} for t, v in zip(xs, row)]
    assert vbma.parse_yield_table(words)[date(2026, 1, 23)] == dict(zip(xs, row))

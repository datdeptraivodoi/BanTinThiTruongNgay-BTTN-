"""Lợi suất TPCP từ báo cáo tuần VBMA: đọc bảng theo vị trí chữ, nối vào biểu đồ, câu trong đoạn trái phiếu."""
import json
from datetime import date
from pathlib import Path

from bttn.weekly import vbma
from bttn.weekly.content import ChartData
from bttn.weekly.history import update_bonds_vbma

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


def test_chart_carries_latest_vbma_value_forward():
    chart = ChartData(categories=["8/10/2026", "9/10/2026"], series=[[3.6, 3.6], [3.8, 3.8], [3.9, 3.9], [4.25, 4.25], [4.5, 4.5]])
    update_bonds_vbma(chart, {date(2026, 10, 9): ROW_0910}, date(2026, 10, 16))
    assert chart.categories == ["8/10/2026", "9/10/2026", "12/10/2026", "13/10/2026", "14/10/2026", "15/10/2026", "16/10/2026"]
    assert [s[1] for s in chart.series] == [3.54, 3.64, 3.72, 4.29, 4.34]    # phiên 9/10 sửa theo VBMA
    assert [s[-1] for s in chart.series] == [3.54, 3.64, 3.72, 4.29, 4.34]
    assert chart.series[0][0] == 3.6                                         # phiên chưa có số VBMA giữ nguyên

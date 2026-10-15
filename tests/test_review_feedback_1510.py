"""Phản hồi review 15/10: giá MBBank, bảng dự báo, lịch sự kiện, Term SOFR, VIRA, bố cục theo mẫu."""
import json
from datetime import date
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

from bttn import rendering
from bttn.models import Snapshot
from bttn.rendering import (
    TEMPLATE_GRID,
    TEMPLATE_SPANS,
    calendar_events,
    forecast_rows,
    render_forecast_table,
    term_sofr_rows,
)
from bttn.sources import _webgia_hex, parse_vira_sections, parse_webgia_mb

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def snap():
    return Snapshot.model_validate_json((ROOT / "tests/fixtures/snapshot.json").read_text(encoding="utf-8"))


def test_webgia_hex_roundtrip():
    assert _webgia_hex("3235XQ2eZ38W30") == "25.80"  # chữ hoa là nhiễu, bị loại


def test_parse_webgia_mb_requires_today_and_reads_usd_row():
    html = ("<p>Tỷ giá MBBank cập nhật lúc 09:15 15/10/2026</p><table>"
            "<tr><th>Mã</th><th>Tên</th><th>Mua TM</th><th>Mua CK</th><th>Bán</th></tr>"
            "<tr><td>USD (50-100)</td><td>Đô la Mỹ</td><td>25.900</td><td>25.940</td><td>26.290</td></tr></table>")
    assert parse_webgia_mb(html, date(2026, 10, 15)) == (25940, 26290)
    with pytest.raises(ValueError):
        parse_webgia_mb(html, date(2026, 10, 16))


def test_forecast_rows_follow_config_and_go_blank_when_stale(snap, monkeypatch):
    cfg = {"updated": snap.as_of.date().isoformat(), "max_age_days": 45,
           "rows": [{"name": "USD-VND", "1m": "26,150", "6m": "26,400", "note": "Ghi chú"}]}
    monkeypatch.setattr(rendering, "_config_json", lambda name: cfg)
    assert forecast_rows(snap) == [("USD-VND", "26,150", "26,400", "Ghi chú")]
    cfg["updated"] = "2020-01-01"
    assert forecast_rows(snap)[0][1:3] == ("—", "—")
    assert snap.issues[-1].code == "FORECAST_STALE"


def test_forecast_table_matches_template_header(snap):
    cell = Document().add_table(rows=1, cols=1).cell(0, 0)
    render_forecast_table(cell, snap)
    assert cell.paragraphs[0].text == "Dự báo các chỉ số chính" or "Dự báo các chỉ số chính" in [p.text for p in cell.paragraphs]
    table = cell.tables[0]
    assert [c.text for c in table.rows[0].cells] == ["", "1 tháng", "6 tháng", "Ghi chú"]
    assert [r.cells[0].text for r in table.rows[1:]] == ["USD-VND", "EUR-USD", "USD-JPY", "LiborUSD 6M"]


def test_calendar_uses_only_the_current_week(snap, monkeypatch):
    cfg = {"week_start": "2026-09-21", "week_end": "2026-09-25", "events": [
        {"date": "2026-09-22", "event": "A", "country": "Mỹ", "high_impact": True},
        {"date": "2026-09-22", "event": "B", "country": "Mỹ"},
        {"date": "2026-09-23", "event": "C", "country": "Nhật"}]}
    monkeypatch.setattr(rendering, "_config_json", lambda name: cfg)
    week, events = calendar_events(snap)
    assert [e[0] for e in events] == ["22.09.2026", "", "23.09.2026"]
    cfg["week_start"], cfg["week_end"] = "2026-10-12", "2026-10-16"
    assert calendar_events(snap) == (None, [])


def test_term_sofr_only_when_recent(snap, monkeypatch):
    cfg = {"date": snap.as_of.date().isoformat(), "1M": 4.1, "3M": 4.05, "6M": None}
    monkeypatch.setattr(rendering, "_config_json", lambda name: cfg)
    rows = term_sofr_rows(snap)
    assert rows[0][1] == "4.10000%" and rows[2][1] == "—"
    cfg["date"] = "2026-09-01"
    assert all(r[1] == "—" for r in term_sofr_rows(snap))


def test_config_files_are_valid_json():
    for name in ("forecasts.json", "economic_calendar.json", "term_sofr.json", "policy_rates.json"):
        json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


def test_vira_sections_split_fx_and_money_market():
    text = ("Thị trường ngoại tệ (menu)\nBản tin\nThị trường ngoại tệ:\nTỷ giá trên thị trường tự do ở mức 26.020/26.120.\n"
            "Thị trường tiền tệ LNH:\nLãi suất qua đêm giảm.\nNghiệp vụ thị trường mở:\nNHNN hút ròng 764,91 tỷ đồng.\n"
            "Thị trường chứng khoán: VN-Index giảm.")
    out = parse_vira_sections(text)
    assert out["fx"] == "Tỷ giá trên thị trường tự do ở mức 26.020/26.120."
    assert out["lnh"] == "Lãi suất qua đêm giảm.\n\nNHNN hút ròng 764,91 tỷ đồng."


def test_template_geometry_matches_uploaded_template():
    assert sum(TEMPLATE_GRID) == 15877
    for spans in TEMPLATE_SPANS.values():
        assert sum(spans) == len(TEMPLATE_GRID)
    widths = lambda row: [sum(TEMPLATE_GRID[sum(TEMPLATE_SPANS[row][:i]):sum(TEMPLATE_SPANS[row][:i + 1])])  # noqa: E731
                          for i in range(len(TEMPLATE_SPANS[row]))]
    assert widths(3)[:2] == [5157, 4610]
    assert widths(7)[:2] == [5297, 5221]
    assert widths(11) == [5323, 5323, 5231]


def test_rendered_word_keeps_template_columns(tmp_path):
    from bttn.pipeline import main
    out = tmp_path / "out"
    assert main(["--create-only", "--snapshot", str(ROOT / "tests/fixtures/snapshot.json"),
                 "--content", str(ROOT / "tests/fixtures/content.json"), "--output-dir", str(out)]) == 0
    doc = Document(next(out.rglob("BTTN-*.docx")))
    page1 = doc.tables[0]
    grid = [int(g.get(qn("w:w"))) for g in page1._tbl.tblGrid.findall(qn("w:gridCol"))]
    assert grid == TEMPLATE_GRID
    texts = "".join(t.text or "" for t in doc.element.iter(qn("w:t")))
    assert "Dự báo các chỉ số chính" in texts
    assert "Tỷ giá USD-VND ngân hàng" in texts

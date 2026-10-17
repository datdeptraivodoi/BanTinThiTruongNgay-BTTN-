"""Đọc nội dung từ một bản tin tuần đã có (ví dụ file mẫu) thành WeeklyContent.

Dùng để kiểm tra vòng lặp mẫu -> nội dung -> file mới, và làm dữ liệu khởi đầu khi soạn tay.
"""
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from . import layout
from .content import (
    Calendar,
    CalendarEvent,
    ChartData,
    Indicators,
    Section,
    SmallTable,
    WeeklyContent,
)
from .render import A_NS, C_NS, FORECAST_COLOR, R_NS, _cells, _rows, paragraphs, run_color, tc_at, text_of


def _cell_text(tc):
    return " ".join(text_of(p, br=" ").strip() for p in paragraphs(tc) if text_of(p).strip())


def _section(tc):
    texty = [p for p in paragraphs(tc) if text_of(p).strip()]
    forecast = [p for p in texty[1:] if run_color(p) == FORECAST_COLOR]
    body = [text_of(p, br=" ").strip() for p in texty[1:] if p not in forecast]
    fc = " ".join(" ".join(text_of(p, br=" ").split()) for p in forecast)
    return Section(body=[" ".join(b.split()) for b in body], forecast=fc.strip())


def _news(tc):
    items = []
    for p in [p for p in paragraphs(tc) if text_of(p).strip()][1:]:
        for line in text_of(p).split("\n"):
            line = line.strip()
            if line:
                items.append(line[1:].strip() if line.startswith("-") else line)
    return items


def _chart(doc, tc):
    chart_el = tc.find(".//{%s}chart" % C_NS)
    part = doc.part.related_parts[chart_el.get("{%s}id" % R_NS)]
    root = etree.fromstring(part.blob)
    series, categories = [], None
    for ser in root.findall(".//{%s}ser" % C_NS):
        cat = ser.find("{%s}cat" % C_NS)
        cache = cat.find(".//{%s}strCache" % C_NS)
        if cache is None:
            cache = cat.find(".//{%s}numCache" % C_NS)
        n = int(cache.find("{%s}ptCount" % C_NS).get("val"))
        cats = [""] * n
        for pt in cache.findall("{%s}pt" % C_NS):
            cats[int(pt.get("idx"))] = pt.find("{%s}v" % C_NS).text
        categories = categories or cats
        vcache = ser.find("{%s}val" % C_NS).find(".//{%s}numCache" % C_NS)
        vals = [None] * int(vcache.find("{%s}ptCount" % C_NS).get("val"))
        for pt in vcache.findall("{%s}pt" % C_NS):
            vals[int(pt.get("idx"))] = float(pt.find("{%s}v" % C_NS).text)
        series.append(vals)
    return ChartData(categories=categories, series=series)


def extract_weekly(docx_path: Path, lang: str, image_dir: Path | None = None) -> WeeklyContent:
    doc = Document(docx_path)
    table = doc.tables[0]
    week = [p for p in paragraphs(tc_at(table, layout.WEEK_LABEL)) if text_of(p).strip()][0]
    ind_rows = _rows(tc_at(table, layout.INDICATORS).find(qn("w:tbl")))
    indicators = Indicators(
        headers=[_cell_text(c) for c in _cells(ind_rows[0])[1:4]],
        rows=[[_cell_text(c) for c in _cells(r)[1:4]] for r in ind_rows[1:7]],
    )
    small = {}
    for name, pos in layout.SMALL_TABLES.items():
        rows = _rows(tc_at(table, pos).find(qn("w:tbl")))
        small[name] = SmallTable(values=[_cell_text(c) for c in _cells(rows[1])[:3]],
                                 changes=[_cell_text(c) for c in _cells(rows[2])[:3]])
    images = {}
    if image_dir is not None:
        image_dir.mkdir(parents=True, exist_ok=True)
        for name, pos in layout.IMAGES.items():
            blip = tc_at(table, pos).find(".//{%s}blip" % A_NS)
            part = doc.part.related_parts[blip.get("{%s}embed" % R_NS)]
            out = image_dir / f"{name}.png"
            out.write_bytes(part.blob)
            images[name] = str(out)
    cal_tc = tc_at(table, layout.CALENDAR)
    events = []
    for tr in _rows(cal_tc.find(qn("w:tbl")))[1:]:
        vals = [_cell_text(c) for c in _cells(tr)]
        if not any(vals):
            continue
        bold = any(r.find(qn("w:rPr")) is not None and r.find(qn("w:rPr")).find(qn("w:b")) is not None
                   for r in tr.iter(qn("w:r")) if r.find(qn("w:t")) is not None)
        events.append(CalendarEvent(date=vals[0], event=vals[1], country=vals[2], forecast=vals[3],
                                    previous=vals[4], bold=bold))
    cal_title = [p for p in paragraphs(cal_tc) if text_of(p).strip()][0]
    return WeeklyContent(
        lang=lang,
        week_label=" ".join(text_of(week).split()),
        sections={name: _section(tc_at(table, pos)) for name, pos in layout.SECTIONS.items()},
        news=_news(tc_at(table, layout.NEWS)),
        indicators=indicators,
        small_tables=small,
        charts={name: _chart(doc, tc_at(table, pos)) for name, pos in layout.CHARTS.items()},
        images=images,
        calendar=Calendar(title=" ".join(text_of(cal_title).split()), events=events),
    )

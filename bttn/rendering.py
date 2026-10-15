import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

from .domestic_charts import render_bond_yield_chart, render_interbank_chart, render_usdvnd_chart
from .forecasts import (
    build_asia_forecast,
    build_coffee_forecast,
    build_energy_metals_forecast,
    build_eurusd_forecast,
    build_interbank_forecast,
    build_usdvnd_forecast,
)
from .layout import FONT, SIZE, normalize_typography, split_pages, style_run, styled_table
from .trader_quotes import TENORS, trader_observation
from .validation import format_value, resolve

NAVY = "245794"
BLUE = "0070C0"


def cells(row):
    result, seen = [], set()
    for cell in row.cells:
        if cell._tc not in seen:
            seen.add(cell._tc)
            result.append(cell)
    return result


def clear(cell):
    for child in list(cell._tc):
        if child.tag != qn("w:tcPr"):
            cell._tc.remove(child)
    cell.add_paragraph()


def paragraph(cell, text, bold=False, color=BLUE):
    p = cell.paragraphs[0] if len(cell.paragraphs) == 1 and not cell.paragraphs[0].text and not len(cell.paragraphs[0]._p.findall(qn("w:r"))) else cell.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT if bold else WD_ALIGN_PARAGRAPH.JUSTIFY
    run = p.add_run(text)
    style_run(run)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    return p


def heading(cell, title):
    paragraph(cell, title, bold=True, color=NAVY)


def grid(cell, headers, rows, ratios=None):
    table = cell.add_table(rows=1, cols=len(headers))
    table.autofit = False
    total = (cell.width or Inches(3.5)) - Inches(.10)
    ratios = ratios or [1 / len(headers)] * len(headers)
    widths = [int(total * ratio) for ratio in ratios]
    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(total / 635)))
    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:type"), "dxa")
    for column, width in zip(table.columns, widths):
        column.width = width
    for index, label in enumerate(headers):
        c = table.rows[0].cells[index]
        c.width = widths[index]
        paragraph(c, label, bold=True, color="FFFFFF")
        shade = OxmlElement("w:shd")
        shade.set(qn("w:fill"), "4F81BD")
        c._tc.get_or_add_tcPr().append(shade)
    for values in rows:
        for index, value in enumerate(values):
            # Created once per row below.
            if index == 0:
                row = table.add_row()
                for c, width in zip(row.cells, widths):
                    c.width = width
            p = paragraph(row.cells[index], str(value), color=NAVY)
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT if index else WD_ALIGN_PARAGRAPH.LEFT
    # Required paragraph after a nested table must not consume a blank line.
    tail = cell.paragraphs[-1]
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.space_before = Pt(0)
    if not tail.text:
        tail.paragraph_format.line_spacing = Pt(1)
        for run in tail.runs:
            run.font.size = Pt(1)
    return table


def value(snapshot, key):
    obs = (trader_observation(snapshot, key) if key.startswith(("INTERBANK_", "ALM_SWAP_"))
           else snapshot.observations.get(key))
    return format_value(obs) if obs else "—"


def render_swap_table(cell, snapshot):
    title = paragraph(cell, "Lãi suất SWAP", bold=True, color=NAVY)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    headers = ["Kỳ hạn", "Mua %", "Bán %"]
    rows = [
        [t, value(snapshot, f"ALM_SWAP_{t}_BID"), value(snapshot, f"ALM_SWAP_{t}_ASK")]
        for t in TENORS
    ]
    width = int((cell.width or Inches(3.5)) - Inches(.08))
    table = cell.add_table(rows=len(rows) + 1, cols=3)
    table.autofit = False
    col_width = int(width / 3)
    for column in table.columns:
        column.width = col_width
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    for j, label in enumerate(headers):
        c = table.cell(0, j)
        c.width = col_width
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_together = True
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(label)
        style_run(r)
        r.bold = True
        r.font.color.rgb = RGBColor.from_string("FFFFFF")
        shade = OxmlElement("w:shd")
        shade.set(qn("w:fill"), "4F81BD")
        c._tc.get_or_add_tcPr().append(shade)
        margins = OxmlElement("w:tcMar")
        for name in ("top", "bottom", "left", "right"):
            edge = OxmlElement("w:" + name)
            edge.set(qn("w:w"), "10")
            edge.set(qn("w:type"), "dxa")
            margins.append(edge)
        c._tc.get_or_add_tcPr().append(margins)
    for i, values in enumerate(rows, start=1):
        table.rows[i]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for j, val in enumerate(values):
            c = table.cell(i, j)
            c.width = col_width
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_together = True
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            r = p.add_run(str(val))
            style_run(r)
            r.bold = (j == 0)
            r.font.color.rgb = RGBColor.from_string("000000")
            margins = OxmlElement("w:tcMar")
            for name in ("top", "bottom", "left", "right"):
                edge = OxmlElement("w:" + name)
                edge.set(qn("w:w"), "10")
                edge.set(qn("w:type"), "dxa")
                margins.append(edge)
            c._tc.get_or_add_tcPr().append(margins)
    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(width / 635)))
    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:type"), "dxa")
    tail = cell.paragraphs[-1]
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.space_before = Pt(0)
    if not tail.text:
        tail.paragraph_format.line_spacing = Pt(1)
        for run in tail.runs:
            run.font.size = Pt(1)
    return table


def render_commodities_table(cell, snapshot):
    """Render page 3 commodities table with size 10pt Times New Roman matching X9:AA32 in Excel."""
    date_str_dot = snapshot.as_of.strftime("%d.%m.%Y")
    date_str_slash = snapshot.as_of.strftime("%d/%m/%Y")
    title = paragraph(cell, f"Bảng giá hàng hóa ngày {date_str_dot}", bold=True, color=NAVY)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    title.paragraph_format.space_after = Pt(2)

    from .tradingeconomics_commodities import get_commodities_table_data
    rows_data = get_commodities_table_data(snapshot)

    headers = [
        "",
        f"Giá ngày\n{date_str_slash}",
        "Thay đổi\nngày (%)",
        "Thay đổi\nnăm (%)",
    ]
    ratios = [0.44, 0.22, 0.17, 0.17]
    width = int((cell.width or Inches(3.5)) - Inches(.08))
    widths = [int(width * r) for r in ratios]

    table = cell.add_table(rows=len(rows_data) + 1, cols=4)
    table.autofit = False
    for col, w in zip(table.columns, widths):
        col.width = w
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    for j, label in enumerate(headers):
        c = table.cell(0, j)
        c.width = widths[j]
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT if j == 0 else WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.keep_together = True
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1
        if label:
            r = p.add_run(label)
            style_run(r, size=8)
            r.bold = True
            r.font.color.rgb = RGBColor.from_string("000000")
        else:
            r = p.add_run("")
            style_run(r, size=8)
        margins = OxmlElement("w:tcMar")
        for name in ("top", "bottom", "left", "right"):
            edge = OxmlElement("w:" + name)
            edge.set(qn("w:w"), "10")
            edge.set(qn("w:type"), "dxa")
            margins.append(edge)
        c._tc.get_or_add_tcPr().append(margins)

    for i, row_item in enumerate(rows_data, start=1):
        table.rows[i]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        if row_item.get("category"):
            for j in range(4):
                c = table.cell(i, j)
                c.width = widths[j]
                p = c.paragraphs[0]
                p.paragraph_format.keep_together = True
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1
                if j == 0:
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    r = p.add_run(row_item["label"])
                    style_run(r, size=8)
                    r.bold = True
                    r.font.color.rgb = RGBColor.from_string("000000")
                else:
                    r = p.add_run("")
                    style_run(r, size=8)
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "99CCFF")
                c._tc.get_or_add_tcPr().append(shade)
                margins = OxmlElement("w:tcMar")
                for name in ("top", "bottom", "left", "right"):
                    edge = OxmlElement("w:" + name)
                    edge.set(qn("w:w"), "10")
                    edge.set(qn("w:type"), "dxa")
                    margins.append(edge)
                c._tc.get_or_add_tcPr().append(margins)
        elif row_item.get("separator"):
            trPr = table.rows[i]._tr.get_or_add_trPr()
            trHeight = OxmlElement("w:trHeight")
            trHeight.set(qn("w:val"), "140")
            trHeight.set(qn("w:hRule"), "exact")
            trPr.append(trHeight)
            for j in range(4):
                c = table.cell(i, j)
                c.width = widths[j]
                p = c.paragraphs[0]
                p.paragraph_format.keep_together = True
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = Pt(5)
                r = p.add_run("")
                style_run(r, size=8)
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "99CCFF")
                c._tc.get_or_add_tcPr().append(shade)
        else:
            values = [row_item["label"], row_item["price"], row_item["pct_day"], row_item["pct_yoy"]]
            for j, val in enumerate(values):
                c = table.cell(i, j)
                c.width = widths[j]
                p = c.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT if j == 0 else WD_ALIGN_PARAGRAPH.RIGHT
                p.paragraph_format.keep_together = True
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(0)
                r = p.add_run(str(val))
                style_run(r, size=8)
                r.bold = True
                val_str = str(val).strip()
                if j >= 2:
                    if val_str.startswith("-"):
                        r.font.color.rgb = RGBColor.from_string("800000")
                    elif val_str != "—" and val_str:
                        r.font.color.rgb = RGBColor.from_string("008000")
                    else:
                        r.font.color.rgb = RGBColor.from_string("000000")
                else:
                    r.font.color.rgb = RGBColor.from_string("000000")
                margins = OxmlElement("w:tcMar")
                for name in ("top", "bottom", "left", "right"):
                    edge = OxmlElement("w:" + name)
                    edge.set(qn("w:w"), "10")
                    edge.set(qn("w:type"), "dxa")
                    margins.append(edge)
                c._tc.get_or_add_tcPr().append(margins)

    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(width / 635)))
    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:type"), "dxa")
    tail = cell.paragraphs[-1]
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.space_before = Pt(0)
    if not tail.text:
        tail.paragraph_format.line_spacing = Pt(1)
        for run in tail.runs:
            run.font.size = Pt(1)
    return table


def render_contact_info(cell):
    """Render Contact Information 2-column table below commodities table on Page 3."""
    p_title = cell.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_title.paragraph_format.space_before = Pt(2)
    p_title.paragraph_format.space_after = Pt(1)
    p_title.paragraph_format.line_spacing = 1
    r_title = p_title.add_run("Thông tin liên hệ")
    style_run(r_title, size=11)
    r_title.bold = True
    r_title.font.color.rgb = RGBColor.from_string(NAVY)

    width = int((cell.width or Inches(3.5)) - Inches(.08))
    col_w = int(width / 2)
    table = cell.add_table(rows=1, cols=2)
    table.autofit = False
    table.columns[0].width = col_w
    table.columns[1].width = col_w
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))

    contacts = [
        ("Hoa Hùng Cường", "Phụ trách Bộ phận Phân tích", "ĐT: 024 6266 1088 (ex: 2532)", "Email: cuonghh@mbbank.com.vn"),
        ("Nguyễn Hữu Đạt", "Bộ phận Phân tích", "ĐT: 037 404 3517 (ex: 2533)", "Email: datnh1@mbbank.com.vn"),
    ]

    for j, (name, role, phone, email) in enumerate(contacts):
        c = table.cell(0, j)
        c.width = col_w
        lines = [
            (name, True, "0070C0", 10),
            (role, False, "000000", 9),
            (phone, False, "000000", 9),
            (email, False, "000000", 8),
        ]
        for idx, (text_val, is_bold, color_hex, font_size) in enumerate(lines):
            p = c.paragraphs[0] if idx == 0 and not c.paragraphs[0].text else c.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.keep_together = True
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1
            r = p.add_run(text_val)
            style_run(r, size=font_size)
            r.bold = is_bold
            r.font.color.rgb = RGBColor.from_string(color_hex)
        margins = OxmlElement("w:tcMar")
        for edge_name in ("top", "bottom", "left", "right"):
            edge = OxmlElement("w:" + edge_name)
            edge.set(qn("w:w"), "10")
            edge.set(qn("w:type"), "dxa")
            margins.append(edge)
        c._tc.get_or_add_tcPr().append(margins)

    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(width / 635)))
    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:type"), "dxa")
    tail = cell.paragraphs[-1]
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.space_before = Pt(0)
    if not tail.text:
        tail.paragraph_format.line_spacing = Pt(1)
        for run in tail.runs:
            run.font.size = Pt(1)
    return table


def render_calendar_events_table(cell):
    """Render Important Events calendar table on page 2 (Row 7 Col 0)."""
    title = paragraph(cell, "Sự kiện quan trọng (12.10 – 16.10.2026)", bold=True, color=NAVY)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(0)
    title.paragraph_format.space_after = Pt(1)

    headers = ["Ngày", "Sự kiện", "Nước"]
    events = [
        ("13.10.2026", "Biên bản họp RBA", "Australia", True),
        ("", "Doanh số bán nhà cũ", "Mỹ", True),
        ("14.10.2026", "Lạm phát CPI (so cùng kỳ)", "Trung Quốc", False),
        ("", "Lạm phát lõi (so tháng trước)", "Mỹ", False),
        ("", "Lạm phát lõi (so cùng kỳ)", "Mỹ", False),
        ("", "Lạm phát CPI (so tháng trước)", "Mỹ", False),
        ("", "Lạm phát CPI (so cùng kỳ)", "Mỹ", False),
        ("15.10.2026", "Tăng trưởng GDP", "Anh Quốc", False),
        ("15.10.2026", "Chỉ số giá sản xuất (PPI)", "Mỹ", False),
        ("", "Doanh số bán lẻ (so tháng trước)", "Mỹ", False),
        ("16.10.2026", "Chỉ số lạm phát HICP MoM", "Châu Âu", False),
        ("", "Chỉ số lạm phát HICP YoY", "Châu Âu", False),
    ]

    ratios = [0.26, 0.50, 0.24]
    width = int((cell.width or Inches(3.5)) - Inches(.08))
    widths = [int(width * r) for r in ratios]

    table = cell.add_table(rows=len(events) + 1, cols=3)
    table.autofit = False
    for col, w in zip(table.columns, widths):
        col.width = w
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))

    for j, label in enumerate(headers):
        c = table.cell(0, j)
        c.width = widths[j]
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_together = True
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1
        r = p.add_run(label)
        style_run(r, size=10)
        r.bold = True
        r.font.color.rgb = RGBColor.from_string("FFFFFF")
        shade = OxmlElement("w:shd")
        shade.set(qn("w:fill"), "4F81BD")
        c._tc.get_or_add_tcPr().append(shade)
        margins = OxmlElement("w:tcMar")
        for name in ("top", "bottom", "left", "right"):
            edge = OxmlElement("w:" + name)
            edge.set(qn("w:w"), "10")
            edge.set(qn("w:type"), "dxa")
            margins.append(edge)
        c._tc.get_or_add_tcPr().append(margins)

    for i, (d_str, ev_str, co_str, is_high_impact) in enumerate(events, start=1):
        table.rows[i]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        row_vals = [d_str, ev_str, co_str]
        for j, val in enumerate(row_vals):
            c = table.cell(i, j)
            c.width = widths[j]
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT if j < 2 else WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_together = True
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1
            r = p.add_run(val)
            style_run(r, size=10)
            r.bold = is_high_impact
            r.font.color.rgb = RGBColor.from_string("245794" if is_high_impact else "4F81BD")
            margins = OxmlElement("w:tcMar")
            for name in ("top", "bottom", "left", "right"):
                edge = OxmlElement("w:" + name)
                edge.set(qn("w:w"), "10")
                edge.set(qn("w:type"), "dxa")
                margins.append(edge)
            c._tc.get_or_add_tcPr().append(margins)

    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(width / 635)))
    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:type"), "dxa")
    tail = cell.paragraphs[-1]
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.space_before = Pt(0)
    if not tail.text:
        tail.paragraph_format.line_spacing = Pt(1)
        for run in tail.runs:
            run.font.size = Pt(1)
    return table


def render_international_money_market(cell):
    """Render International Money Market and World Stock Market on page 2 (Row 8 Col 0)."""
    width = int((cell.width or Inches(3.5)) - Inches(.08))

    # 1. Heading: Thị trường tiền tệ quốc tế
    p_h1 = cell.paragraphs[0] if not cell.paragraphs[0].text else cell.add_paragraph()
    p_h1.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_h1.paragraph_format.space_before = Pt(0)
    p_h1.paragraph_format.space_after = Pt(2)
    p_h1.paragraph_format.line_spacing = 1
    r_h1 = p_h1.add_run("Thị trường tiền tệ quốc tế")
    style_run(r_h1, size=11)
    r_h1.bold = True
    r_h1.font.color.rgb = RGBColor.from_string(NAVY)

    # 2. Table: Lãi suất cơ bản
    policy_headers = ["Lãi suất cơ bản", "Giá trị", "Thay đổi"]
    policy_rates = [
        ("FED", "3,75-4,00%", "+0,25%"),
        ("BOE", "3,75%", "0,00%"),
        ("BOJ", "1,25%", "+0,25%"),
        ("RBA", "4,60%", "+0,25%"),
        ("ECB", "2,50%", "+0,25%"),
    ]
    t_policy_ratios = [0.38, 0.34, 0.28]
    t_policy_widths = [int(width * r) for r in t_policy_ratios]

    t_policy = cell.add_table(rows=len(policy_rates) + 1, cols=3)
    t_policy.autofit = False
    for col, w in zip(t_policy.columns, t_policy_widths):
        col.width = w
    t_policy.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))

    for j, label in enumerate(policy_headers):
        c = t_policy.cell(0, j)
        c.width = t_policy_widths[j]
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT if j == 0 else WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_together = True
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1
        r = p.add_run(label)
        style_run(r, size=10)
        r.bold = True
        r.font.color.rgb = RGBColor.from_string("FFFFFF")
        shade = OxmlElement("w:shd")
        shade.set(qn("w:fill"), "4F81BD")
        c._tc.get_or_add_tcPr().append(shade)
        margins = OxmlElement("w:tcMar")
        for name in ("top", "bottom", "left", "right"):
            edge = OxmlElement("w:" + name)
            edge.set(qn("w:w"), "10")
            edge.set(qn("w:type"), "dxa")
            margins.append(edge)
        c._tc.get_or_add_tcPr().append(margins)

    for i, (cb_name, cb_val, cb_chg) in enumerate(policy_rates, start=1):
        t_policy.rows[i]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for j, val in enumerate([cb_name, cb_val, cb_chg]):
            c = t_policy.cell(i, j)
            c.width = t_policy_widths[j]
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT if j == 0 else WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_together = True
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1
            r = p.add_run(val)
            style_run(r, size=10)
            r.bold = True
            if j == 2:
                if val.startswith("+"):
                    r.font.color.rgb = RGBColor.from_string("00B050")
                elif "0,00" in val:
                    r.font.color.rgb = RGBColor.from_string("FFC000")
                else:
                    r.font.color.rgb = RGBColor.from_string("C00000")
            else:
                r.font.color.rgb = RGBColor.from_string("000000")
            margins = OxmlElement("w:tcMar")
            for name in ("top", "bottom", "left", "right"):
                edge = OxmlElement("w:" + name)
                edge.set(qn("w:w"), "10")
                edge.set(qn("w:type"), "dxa")
                margins.append(edge)
            c._tc.get_or_add_tcPr().append(margins)

    t_policy._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(width / 635)))
    t_policy._tbl.tblPr.find(qn("w:tblW")).set(qn("w:type"), "dxa")

    # 3. Label: Đơn vị %
    p_unit = cell.add_paragraph()
    p_unit.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_unit.paragraph_format.space_before = Pt(3)
    p_unit.paragraph_format.space_after = Pt(0)
    p_unit.paragraph_format.line_spacing = 1
    r_u = p_unit.add_run("   Đơn vị %")
    style_run(r_u, size=10)
    r_u.font.color.rgb = RGBColor.from_string("000000")

    # 4. Heading: LIBOR – SOFR
    p_sofr = cell.add_paragraph()
    p_sofr.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_sofr.paragraph_format.space_before = Pt(0)
    p_sofr.paragraph_format.space_after = Pt(1)
    p_sofr.paragraph_format.line_spacing = 1
    r_s = p_sofr.add_run("LIBOR – SOFR")
    style_run(r_s, size=10)
    r_s.bold = True
    r_s.font.color.rgb = RGBColor.from_string(NAVY)

    # 5. Table: SOFR - Vnibor
    sofr_rows = [
        ("SOFRUSD1M", "3.91666%", "Vnibor1M", "5.20000%"),
        ("SOFRUSD3M", "4.02975%", "Vnibor3M", "6.75000%"),
        ("SOFRUSD6M", "4.18252%", "Vnibor6M", "7.90000%"),
    ]
    t_sofr_ratios = [0.28, 0.22, 0.26, 0.24]
    t_sofr_widths = [int(width * r) for r in t_sofr_ratios]
    t_sofr = cell.add_table(rows=len(sofr_rows), cols=4)
    t_sofr.autofit = False
    for col, w in zip(t_sofr.columns, t_sofr_widths):
        col.width = w

    for i, vals in enumerate(sofr_rows):
        t_sofr.rows[i]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for j, val in enumerate(vals):
            c = t_sofr.cell(i, j)
            c.width = t_sofr_widths[j]
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.keep_together = True
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1
            r = p.add_run(val)
            style_run(r, size=10)
            r.bold = (j in (0, 2))
            r.font.color.rgb = RGBColor.from_string("000000")
            margins = OxmlElement("w:tcMar")
            for name in ("top", "bottom", "left", "right"):
                edge = OxmlElement("w:" + name)
                edge.set(qn("w:w"), "10")
                edge.set(qn("w:type"), "dxa")
                margins.append(edge)
            c._tc.get_or_add_tcPr().append(margins)

    t_sofr._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(width / 635)))
    t_sofr._tbl.tblPr.find(qn("w:tblW")).set(qn("w:type"), "dxa")

    # 6. Heading: Thị trường chứng khoán thế giới
    p_h2 = cell.add_paragraph()
    p_h2.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_h2.paragraph_format.space_before = Pt(3)
    p_h2.paragraph_format.space_after = Pt(1)
    p_h2.paragraph_format.line_spacing = 1
    r_h2 = p_h2.add_run("Thị trường chứng khoán thế giới")
    style_run(r_h2, size=11)
    r_h2.bold = True
    r_h2.font.color.rgb = RGBColor.from_string(NAVY)

    # 7. Table: Thị trường chứng khoán thế giới
    stock_headers = ["", "Giá trị", "Thay đổi"]
    stock_rows = [
        ("DOW", "51,185.13", "-0.66%"),
        ("NIKKEI", "69,042.11", "-1.42%"),
        ("DAX", "24,959.19", "-0.58%"),
    ]
    t_stock_ratios = [0.34, 0.36, 0.30]
    t_stock_widths = [int(width * r) for r in t_stock_ratios]
    t_stock = cell.add_table(rows=len(stock_rows) + 1, cols=3)
    t_stock.autofit = False
    for col, w in zip(t_stock.columns, t_stock_widths):
        col.width = w
    t_stock.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))

    for j, label in enumerate(stock_headers):
        c = t_stock.cell(0, j)
        c.width = t_stock_widths[j]
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT if j == 0 else WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_together = True
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1
        r = p.add_run(label)
        style_run(r, size=10)
        r.bold = True
        r.font.color.rgb = RGBColor.from_string("FFFFFF")
        shade = OxmlElement("w:shd")
        shade.set(qn("w:fill"), "4F81BD")
        c._tc.get_or_add_tcPr().append(shade)
        margins = OxmlElement("w:tcMar")
        for name in ("top", "bottom", "left", "right"):
            edge = OxmlElement("w:" + name)
            edge.set(qn("w:w"), "10")
            edge.set(qn("w:type"), "dxa")
            margins.append(edge)
        c._tc.get_or_add_tcPr().append(margins)

    for i, (idx_name, idx_val, idx_chg) in enumerate(stock_rows, start=1):
        t_stock.rows[i]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for j, val in enumerate([idx_name, idx_val, idx_chg]):
            c = t_stock.cell(i, j)
            c.width = t_stock_widths[j]
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT if j == 0 else WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.keep_together = True
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1
            r = p.add_run(val)
            style_run(r, size=10)
            r.bold = True
            if j == 0:
                r.font.color.rgb = RGBColor.from_string("000000")
            else:
                if val.startswith("-"):
                    r.font.color.rgb = RGBColor.from_string("C00000")
                elif val.startswith("+"):
                    r.font.color.rgb = RGBColor.from_string("00B050")
                else:
                    r.font.color.rgb = RGBColor.from_string("000000")
            margins = OxmlElement("w:tcMar")
            for name in ("top", "bottom", "left", "right"):
                edge = OxmlElement("w:" + name)
                edge.set(qn("w:w"), "10")
                edge.set(qn("w:type"), "dxa")
                margins.append(edge)
            c._tc.get_or_add_tcPr().append(margins)

    t_stock._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(width / 635)))
    t_stock._tbl.tblPr.find(qn("w:tblW")).set(qn("w:type"), "dxa")

    tail = cell.paragraphs[-1]
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.space_before = Pt(0)
    if not tail.text:
        tail.paragraph_format.line_spacing = Pt(1)
        for run in tail.runs:
            run.font.size = Pt(1)
    return cell


def render_sbv_block(cell, snapshot):
    title = paragraph(cell, "Tỷ giá USD-VND của NHNN", bold=True, color=NAVY)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    styled_table(cell, [
        ["Tỷ giá Trung Tâm", "Sàn", "Trần"],
        [value(snapshot, k) for k in ["SBV_CENTRAL", "SBV_FLOOR", "SBV_CEILING"]],
        ["", "Mua", "Bán"],
        ["", value(snapshot, "SBV_BUY"), value(snapshot, "SBV_SELL")],
    ], [.44, .28, .28], blue_cells={(0, 0), (0, 1), (0, 2), (1, 0), (2, 1), (2, 2)})
    title = paragraph(cell, "Tỷ giá USD-VND liên ngân hàng\n(tham khảo)", bold=True, color=NAVY)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    def pair(suffix):
        buy = snapshot.observations.get("MB_BUY" + suffix)
        sell = snapshot.observations.get("MB_SELL" + suffix)
        if buy and sell:
            return format_value(buy) + "/" + format_value(sell)
        bid, ask = [trader_observation(snapshot, "INTERBANK_" + side + suffix) for side in ("BID", "ASK")]
        if not bid or not ask or bid.value > ask.value or bid.source_id != ask.source_id:
            return "—"
        return format_value(bid) + "/" + format_value(ask)
    styled_table(cell, [["Hôm qua", "Hôm nay"], [pair("_PREV"), pair("")]], [.5, .5],
                 blue_cells={(0, 0), (0, 1)}, red_cells={(1, 1)})


def plot_trader_fx(cell, snapshot, directory):
    heading(cell, "Báo giá USD-VND liên ngân hàng")
    data = []
    for side, label in [("BID", "Mua"), ("ASK", "Bán")]:
        obs = trader_observation(snapshot, "INTERBANK_" + side)
        if obs and obs.series:
            data.append((label, [p.at for p in obs.series], [float(p.value) for p in obs.series]))
    path = directory / "usdvnd_trader.png"
    chart(path, data, "", height=1.25)
    p = cell.add_paragraph()
    p.add_run().add_picture(str(path), width=Inches(3.45), height=Inches(1.25))


def plot_bonds(cell, snapshot, directory):
    heading(cell, "Lợi suất trái phiếu chuẩn mười năm")
    points = [(label, snapshot.observations.get("BOND_" + country)) for country, label in
              [("Vietnam", "VN"), ("United States", "US"), ("Germany", "DE"), ("Japan", "JP"), ("China", "CN")]]
    points = [(label, obs) for label, obs in points if obs]
    path = directory / "bond_yield.png"
    data = [("10Y", [label for label, obs in points], [float(obs.value) for label, obs in points])] if points else []
    chart(path, data, "%/năm", categorical=True, height=1.25)
    cell.add_paragraph().add_run().add_picture(str(path), width=Inches(3.45), height=Inches(1.25))


def narrative(cell, title, section, snapshot, show_source=True, forecast=None):
    heading(cell, title)
    for p in section.paragraphs:
        paragraph(cell, resolve(p, snapshot, safe=True))
    sources = [snapshot.sources[s] for s in section.source_ids if s in snapshot.sources]
    domains = sorted({urlparse(s.url).hostname or "Nguồn dẫn xuất" for s in sources})
    names = {"vira.org.vn": "VIRA", "vietnambiz.vn": "VietnamBiz", "news.google.com": "Google News",
             "query1.finance.yahoo.com": "Yahoo Finance", "sbv.gov.vn": "NHNN",
             "www.mbbank.com.vn": "MBBank", "teams.microsoft.com": "firm ALM", "example.com": "Kiểm thử"}
    dates = sorted({s.published_at.strftime("%d/%m") for s in sources})
    labels = ", ".join(names.get(domain, domain.removeprefix("www.")) for domain in domains)
    if show_source:
        paragraph(cell, "Nguồn: " + labels + " · " + ", ".join(dates), color="666666")
    fc_text = forecast if forecast is not None else "Dự kiến:"
    p_fc = paragraph(cell, fc_text, bold=True, color="C00000")
    p_fc.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def chart(path, series, ylabel, categorical=False, height=1.55):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.dates import AutoDateLocator, DateFormatter
    from matplotlib.ticker import MaxNLocator

    plt.rcParams.update({"font.family": FONT, "font.size": SIZE})
    fig, ax = plt.subplots(figsize=(3.45, height), dpi=200)
    colors = ["#245794", "#C00000", "#008000"]
    for index, (label, xs, ys) in enumerate(series):
        if categorical:
            ax.plot(xs, ys, marker="o", linewidth=1.6, markersize=3, label=label, color=colors[index % 3])
        else:
            ax.plot(xs, ys, linewidth=1.6, label=label, color=colors[index % 3])
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=SIZE)
    ax.tick_params(labelsize=SIZE)
    ax.yaxis.set_major_locator(MaxNLocator(3))
    ax.grid(alpha=.18)
    ax.spines[["top", "right"]].set_visible(False)
    if not categorical:
        ax.xaxis.set_major_formatter(DateFormatter("%d/%m"))
        ax.xaxis.set_major_locator(AutoDateLocator(minticks=2, maxticks=4))
    if len(series) > 1:
        ax.legend(fontsize=SIZE, frameon=False, loc="upper left", ncol=min(3, len(series)))
    if not series:
        ax.set_axis_off()
        ax.text(.5, .5, "Chưa có dữ liệu\nđã kiểm chứng", ha="center", va="center", transform=ax.transAxes, fontsize=SIZE)
    fig.tight_layout(pad=.6)
    fig.savefig(path)
    plt.close(fig)


def plot_cell(cell, title, snapshot, keys, directory, name, height=1.55, category=False):
    heading(cell, title)
    data = []
    if category:
        for prefix, label in keys:
            observations = [snapshot.observations.get(prefix + t) for t in ["ON", "1W", "2W", "1M", "3M", "6M"]]
            observations = [o for o in observations if o]
            if observations:
                data.append((label, [o.tenor for o in observations], [float(o.value) for o in observations]))
        unit = "%/năm"
    else:
        unit = ""
        for key in keys:
            obs = snapshot.observations.get(key)
            if obs and obs.series:
                unit = obs.unit
                data.append((obs.label, [p.at for p in obs.series], [float(p.value) for p in obs.series]))
    path = directory / f"{name}.png"
    chart(path, data, unit, categorical=category, height=height)
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_picture(str(path), width=Inches(3.45), height=Inches(height))


def plot_candlestick_cell(cell, title, snapshot, key, directory, name, chart_type="currency", tv_cache=None):
    from .candlestick import (
        get_symbol_dataframe,
        render_commodity_chart,
        render_currency_chart,
    )

    path = directory / f"{name}.png"
    df, source_label = get_symbol_dataframe(key, snapshot, tv_cache)

    from .tv_charts import render_tv_chart

    width_cm, height_cm = (8.83, 6.8) if chart_type == "currency" else (9.0, 6.58)
    # Ưu tiên biểu đồ kiểu TradingView (Lightweight Charts); lỗi thì dùng mplfinance như cũ
    success = render_tv_chart(df, path, width_cm, height_cm, chart_type)
    if not success:
        if chart_type == "currency":
            success = render_currency_chart(df, path, title, source_label)
        else:
            success = render_commodity_chart(df, path, title, source_label)

    if not success or not path.is_file():
        # Fallback to standard line chart if candlestick rendering failed
        plot_cell(cell, title, snapshot, [key], directory, name, height=1.7)
        return

    heading(cell, title)
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_picture(str(path), width=Cm(width_cm), height=Cm(height_cm))


def render(snapshot, content, template: Path, output: Path, is_draft: bool = False):
    if not template.is_file():
        raise ValueError("Required template.docx is missing")
    doc = Document(template)
    output.parent.mkdir(parents=True, exist_ok=True)

    tv_cache = {}
    if getattr(snapshot, "purpose", None) == "live":
        try:
            from .candlestick import fetch_tradingview_candles
            tv_cache = fetch_tradingview_candles()
        except Exception:
            pass
    if not doc.tables or len(doc.tables[0].rows) != 14:
        raise ValueError("Template layout changed: expected a 14-row master table")
    table = doc.tables[0]
    if any(len(cells(table.rows[i])) != 3 for i in [3, 4, 5, 7, 8, 11, 12]):
        raise ValueError("Template columns changed")
    # The legacy grid extends beyond the physical page and contains inconsistent
    # column spans. Normalize it while preserving the branded cells and merges.
    for s in doc.sections:
        s.top_margin = Pt(4.3)
        s.bottom_margin = Pt(0)
        s.left_margin = Pt(4.3)
        s.right_margin = Pt(0)
        s.header_distance = Pt(0)
        s.footer_distance = Pt(0)
    total = 15877 * 635
    for child in list(table._tbl.tblGrid):
        table._tbl.tblGrid.remove(child)
    for _ in range(6):
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(round(15877 / 6)))
        table._tbl.tblGrid.append(col)
    tbl_w = table._tbl.tblPr.find(qn("w:tblW"))
    if tbl_w is not None:
        tbl_w.set(qn("w:w"), "15877")
        tbl_w.set(qn("w:type"), "dxa")
    tbl_ind = table._tbl.tblPr.find(qn("w:tblInd"))
    if tbl_ind is not None:
        tbl_ind.set(qn("w:w"), "-34")
        tbl_ind.set(qn("w:type"), "dxa")
    else:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_ind.set(qn("w:w"), "-34")
        tbl_ind.set(qn("w:type"), "dxa")
        table._tbl.tblPr.append(tbl_ind)
    for index, row in enumerate(table.rows):
        if index != 13:
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for node in list(row._tr.get_or_add_trPr()):
            if node.tag in {qn("w:gridBefore"), qn("w:gridAfter"), qn("w:wBefore"), qn("w:wAfter")}:
                row._tr.trPr.remove(node)
        if index in (6, 10):
            trPr = row._tr.get_or_add_trPr()
            for th in list(trPr.findall(qn("w:trHeight"))):
                trPr.remove(th)
            trHeight = OxmlElement("w:trHeight")
            trHeight.set(qn("w:val"), "400")
            trHeight.set(qn("w:hRule"), "exact")
            trPr.append(trHeight)
        spans = {0: [3, 3], 1: [6], 2: [4, 2], 9: [2, 4], 13: [6]}.get(index, [2, 2, 2])
        if len(row._tr.tc_lst) != len(spans):
            raise ValueError("Unexpected template cell spans")
        for tc, span in zip(row._tr.tc_lst, spans):
            tc.grid_span = span
            tc.width = int(total * span / 6)
    # Clear every dynamic cell, including merged cells, drawings, nested tables,
    # calendars and forecast values. No stale template data survives.
    cleared = set()
    for row in list(table.rows)[3:13]:
        for c in cells(row):
            if c._tc not in cleared:
                clear(c)
                cleared.add(c._tc)
    header = cells(table.rows[2])
    clear(header[1])
    WEEKDAYS_VI = {
        0: "Thứ Hai",
        1: "Thứ Ba",
        2: "Thứ Tư",
        3: "Thứ Năm",
        4: "Thứ Sáu",
        5: "Thứ Bảy",
        6: "Chủ Nhật",
    }
    weekday_str = WEEKDAYS_VI.get(snapshot.as_of.weekday(), "Thứ Hai")
    date_text = f"{weekday_str}, ngày {snapshot.as_of.strftime('%d.%m.%Y')}"
    if snapshot.purpose == "fixture":
        date_text = "KIỂM THỬ · " + date_text
    elif is_draft:
        date_text = "BẢN NHÁP · " + date_text
    p_date = header[1].paragraphs[0]
    p_date.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_date.paragraph_format.space_before = Pt(0)
    p_date.paragraph_format.space_after = Pt(0)
    p_date.paragraph_format.line_spacing = 1
    r_date = p_date.add_run(date_text)
    style_run(r_date, font="Verdana", size=12)
    r_date.font.italic = True
    r_date.font.bold = True
    r_date.font.color.rgb = RGBColor.from_string(NAVY)
    r3, r4, r5, r7, r8, r9, r11, r12 = [cells(table.rows[i]) for i in [3, 4, 5, 7, 8, 9, 11, 12]]

    # Render exactly the content that passed the editorial checks.
    heading(r3[0], "Tin tức nổi bật:")
    for idx, section_content in enumerate(content.highlights):
        txt = resolve(" ".join(section_content.paragraphs), snapshot, safe=True).strip()
        prefix = f"{idx + 1}. " if not re.match(r"^\d+[.)]\s", txt) else ""
        paragraph(r3[0], prefix + txt)

    narrative(r3[1], "Thị trường tiền tệ liên ngân hàng", content.interbank, snapshot, show_source=False, forecast=build_interbank_forecast(snapshot, content))
    # Domestic charts use this issue's snapshot, never the undated legacy JSON.
    heading(r3[2], "Diễn biến lãi suất trên thị trường liên ngân hàng")
    chart1_path = output.parent / "interbank.png"
    if render_interbank_chart(chart1_path):
        p1 = r3[2].add_paragraph()
        p1.paragraph_format.space_before = Pt(0)
        p1.paragraph_format.space_after = Pt(0)
        p1.add_run().add_picture(str(chart1_path), width=Cm(10.0), height=Cm(4.2))
    else:
        plot_cell(r3[2], "Lãi suất liên ngân hàng theo kỳ hạn", snapshot,
                  [("VND_", "VND"), ("USD_", "USD")], output.parent,
                  "vnibor", category=True, height=1.5)

    render_sbv_block(r4[0], snapshot)
    narrative(r4[1], "Thị trường ngoại hối USD-VND", content.usd_vnd, snapshot, show_source=False, forecast=build_usdvnd_forecast(snapshot))
    heading(r4[2], "Diễn biến tỷ giá USD-VND thị trường liên ngân hàng")
    chart2_path = output.parent / "usdvnd_domestic.png"
    if render_usdvnd_chart(chart2_path):
        p2 = r4[2].add_paragraph()
        p2.paragraph_format.space_before = Pt(0)
        p2.paragraph_format.space_after = Pt(0)
        p2.add_run().add_picture(str(chart2_path), width=Cm(10.0), height=Cm(3.8))
    else:
        plot_trader_fx(r4[2], snapshot, output.parent)

    render_swap_table(r5[0], snapshot)

    # Row 6 Col 2 (r5[1]): Ý tưởng sản phẩm
    heading(r5[1], "Ý tưởng sản phẩm")
    mb_buy = snapshot.observations.get("MB_BUY")
    mb_sell = snapshot.observations.get("MB_SELL")
    min_str, max_str = "25.700", "26.200"
    if mb_buy and mb_sell and mb_buy.value and mb_sell.value:
        try:
            b_val = float(mb_buy.value)
            s_val = float(mb_sell.value)
            min_val = round(b_val - 100, -1)
            max_val = round(s_val + 100, -1)
            min_str = f"{int(min_val):,}".replace(",", ".")
            max_str = f"{int(max_val):,}".replace(",", ".")
        except Exception:
            pass

    paragraph(r5[1], f"Nhiều khả năng tỷ giá USD-VND có thể biến động trong khu vực từ {min_str}-{max_str}.", bold=False, color=BLUE)
    paragraph(r5[1], "- Sử dụng sản phẩm vay VND lãi suất ưu đãi kết hợp sản phẩm AIRS để giúp khách hàng có thể vay VND với lãi suất cạnh tranh hơn so với phương án vay VND thông thường.", bold=True, color="C00000")
    paragraph(r5[1], "- Sử dụng sản phẩm mua ngoại tệ kỳ hạn FX FWD kỳ hạn dưới 1 tháng để tận dụng điểm kỳ hạn đang ở mức hấp dẫn và tỷ giá điều chỉnh về vùng phù hợp.", bold=True, color="C00000")

    # Row 6 Col 3 (r5[2]): Chart 3 Lãi suất trái phiếu
    heading(r5[2], "Diễn biến lãi suất trái phiếu thị trường liên ngân hàng")
    chart3_path = output.parent / "bond_yield.png"
    if render_bond_yield_chart(chart3_path):
        p3 = r5[2].add_paragraph()
        p3.paragraph_format.space_before = Pt(0)
        p3.paragraph_format.space_after = Pt(0)
        p3.add_run().add_picture(str(chart3_path), width=Cm(10.0), height=Cm(3.8))
    else:
        plot_bonds(r5[2], snapshot, output.parent)
    render_calendar_events_table(r7[0])
    narrative(r7[1], "Thị trường ngoại hối EU", content.eur_usd, snapshot, show_source=False, forecast=build_eurusd_forecast(snapshot, content))
    heading(r7[2], "Thị trường ngoại hối Châu Á")
    for section in [content.japan, content.china]:
        for p in section.paragraphs:
            paragraph(r7[2], resolve(p, snapshot, safe=True))
    p_asia = paragraph(r7[2], build_asia_forecast(snapshot), bold=True, color="C00000")
    p_asia.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    render_international_money_market(r8[0])
    plot_candlestick_cell(r8[1], "Tỷ giá EUR-USD", snapshot, "EURUSD", output.parent, "eurusd", chart_type="currency", tv_cache=tv_cache)
    plot_candlestick_cell(r8[2], "Tỷ giá USD-JPY", snapshot, "USDJPY", output.parent, "usdjpy", chart_type="currency", tv_cache=tv_cache)

    render_commodities_table(r11[0], snapshot)
    render_contact_info(r11[0])
    narrative(r11[1], "Thị trường năng lượng & kim loại", content.energy_metals, snapshot, show_source=False, forecast=build_energy_metals_forecast(snapshot))
    narrative(r11[2], "Thị trường cà phê", content.coffee, snapshot, show_source=False, forecast=build_coffee_forecast(snapshot))
    plot_candlestick_cell(r12[1], "Giá dầu thô Brent", snapshot, "BRENT", output.parent, "brent", chart_type="commodity", tv_cache=tv_cache)
    plot_candlestick_cell(r12[2], "Giá cà phê Arabica", snapshot, "ARABICA", output.parent, "arabica", chart_type="commodity", tv_cache=tv_cache)
    for c in cells(table.rows[13]):
        for p in c.paragraphs:
            for r in p.runs:
                style_run(r, size=10)

    normalize_typography(doc)
    split_pages(doc, table)
    # Remove orphan chart/image relationships now that all source charts were replaced.
    used = {value for node in doc.element.iter() for key, value in node.attrib.items()
            if key in {qn("r:id"), qn("r:embed"), qn("r:link")}}
    for rid, rel in list(doc.part.rels.items()):
        if rel.reltype.endswith(("/image", "/chart")) and rid not in used:
            doc.part.drop_rel(rid)
    # A trailing paragraph after the full-page table used to create a blank page.
    for p in doc.paragraphs:
        if not p.text:
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(1)
            p.paragraph_format.page_break_before = False
            for r in p.runs:
                r.font.size = Pt(1)
    for s in doc.sections:
        for ref in list(s._sectPr):
            if ref.tag in {qn("w:headerReference"), qn("w:footerReference")}:
                s._sectPr.remove(ref)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)


def validate_docx(docx_path: Path):
    """Check Word structure and explicit typography without creating a PDF.

    This does not claim to validate pagination in the recipient's Word app.
    """
    from matplotlib.font_manager import findfont

    findfont(FONT, fallback_to_default=False)
    doc = Document(docx_path)
    if len(doc.tables) != 3:
        raise ValueError("Word report must contain the three configured page tables")
    sections = [
        ["Tỷ giá USD-VND của NHNN"],
        ["Thị trường tiền tệ quốc tế", "Sự kiện quan trọng", "LIBOR - SOFR", "LIBOR – SOFR", "VNIBOR và SOFR"],
        ["Bảng giá hàng hóa"],
    ]
    for table, heading_options in zip(doc.tables, sections):
        text = " ".join(node.text or "" for node in table._tbl.iter(qn("w:t")))
        if not any(h in text for h in heading_options) or "{{" in text or "}}" in text:
            raise ValueError("Word report has a missing section or unresolved placeholder")
    parts = [doc.part]
    for part in parts:
        for run in part.element.iter(qn("w:r")):
            text = "".join(node.text or "" for node in run.iter(qn("w:t")))
            if not text.strip():
                continue
            if "{{" in text or "}}" in text:
                raise ValueError("Word report has unresolved placeholders")
            properties = run.find(qn("w:rPr"))
            fonts = properties.find(qn("w:rFonts")) if properties is not None else None
            size = properties.find(qn("w:sz")) if properties is not None else None
            if fonts is None or any(fonts.get(qn("w:" + name)) not in (FONT, "Verdana") for name in ("ascii", "hAnsi", "eastAsia", "cs")):
                raise ValueError("Word text must use Times New Roman or Verdana")
            allowed_sizes = (str(SIZE * 2), "16", "20", "24", "36", "18", "19", "2")
            if size is None or size.get(qn("w:val")) not in allowed_sizes:
                raise ValueError(f"Word text must use 11 pt or allowed layout size (got {size.get(qn('w:val')) if size is not None else 'None'})")


def validate_pdf_layout(pdf_path: Path):
    import pymupdf

    required = [
        ["Tỷ giá USD-VND của NHNN"],
        ["Thị trường tiền tệ quốc tế", "Sự kiện quan trọng", "LIBOR - SOFR", "LIBOR – SOFR", "VNIBOR và SOFR"],
        ["Bảng giá hàng hóa"],
    ]
    with pymupdf.open(pdf_path) as pdf:
        if len(pdf) != 3:
            raise ValueError(f"Expected 3 PDF pages, got {len(pdf)}")
        for index, page in enumerate(pdf):
            text = page.get_text()
            if len(text.strip()) < 100 or "{{" in text:
                raise ValueError("Blank page or unresolved placeholder in PDF")
            if not any(h in text for h in required[index]):
                raise ValueError(f"Section moved from expected page {index + 1}")
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if not span["text"].strip():
                            continue
                        font_clean = span["font"].replace(" ", "").replace("-", "")
                        if "TimesNewRoman" not in font_clean and "Verdana" not in font_clean:
                            raise ValueError("PDF substituted Times New Roman; install the required font")
                        allowed_pt = (SIZE, 8, 10, 12, 18, 9, 8.5)
                        if not any(abs(span["size"] - pt) < .3 for pt in allowed_pt):
                            raise ValueError(f"PDF contains visible text outside allowed sizes: {span['size']}")
                        x0, y0, x1, y1 = span["bbox"]
                        if x0 < 0 or y0 < 0 or x1 > page.rect.width or y1 > page.rect.height:
                            raise ValueError("PDF text extends outside the page")


def convert_and_validate(docx_path: Path) -> Path:

    executable = os.getenv("LIBREOFFICE_PATH") or shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        raise RuntimeError("LibreOffice is required for PDF validation; no email sent")
    pdf_path = docx_path.with_suffix(".pdf")
    if pdf_path.exists():
        raise ValueError("Refusing to accept an existing PDF as a new conversion")
    with tempfile.TemporaryDirectory(prefix="bttn-lo-") as profile:
        subprocess.run([executable, f"-env:UserInstallation={Path(profile).as_uri()}",
            "--headless", "--convert-to", "pdf", "--outdir", str(docx_path.parent), str(docx_path)],
            check=True, timeout=120, capture_output=True)
    if not pdf_path.is_file():
        raise RuntimeError("LibreOffice did not create a PDF")
    validate_pdf_layout(pdf_path)
    return pdf_path

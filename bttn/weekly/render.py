"""Điền nội dung vào file mẫu bản tin tuần, giữ nguyên định dạng của mẫu.

Nguyên tắc: không tự tạo kiểu chữ. Mỗi đoạn mới được nhân bản từ đoạn tương ứng trong mẫu
(cùng căn lề, giãn dòng, font, cỡ, màu); chỉ thay chữ. Biểu đồ Word gốc chỉ thay dữ liệu,
ảnh chỉ thay nội dung trong đúng khung ảnh của mẫu.
"""
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from . import layout
from .content import WeeklyContent

C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
FORECAST_COLOR = "C00000"


# ---------- tiện ích XML ----------

def tc_at(table, pos):
    r, c = pos
    return table.rows[r]._tr.tc_lst[c]


def paragraphs(tc):
    return tc.findall(qn("w:p"))


def text_of(p, br="\n"):
    out = []
    for node in p.iter(qn("w:t"), qn("w:br"), qn("w:tab")):
        if node.tag == qn("w:t"):
            out.append(node.text or "")
        elif node.tag == qn("w:br"):
            out.append(br)
        else:
            out.append("\t")
    return "".join(out)


def _first_text_run(p):
    """Run mang định dạng chủ đạo của đoạn: run có nhiều chữ nhất (không phải ảnh/biểu đồ).

    Không lấy run đầu tiên vì mẫu hay có run ngắn khác kiểu ở đầu (ví dụ '- ' không có font).
    """
    best, best_len = None, -1
    for r in p.iter(qn("w:r")):
        if r.find(qn("w:t")) is None or r.find(".//" + qn("w:drawing")) is not None:
            continue
        n = len("".join(t.text or "" for t in r.findall(qn("w:t"))).strip())
        if n > best_len:
            best, best_len = r, n
    return best


def run_color(p):
    r = _first_text_run(p)
    rpr = r.find(qn("w:rPr")) if r is not None else None
    color = rpr.find(qn("w:color")) if rpr is not None else None
    return color.get(qn("w:val")) if color is not None else None


def set_text(p, text, rpr=None, bold=None):
    """Thay toàn bộ chữ của đoạn, giữ định dạng run đầu tiên (hoặc rPr truyền vào)."""
    ref = _first_text_run(p)
    if rpr is None and ref is not None and ref.find(qn("w:rPr")) is not None:
        rpr = ref.find(qn("w:rPr"))
    for child in list(p):
        if child.tag in (qn("w:r"), qn("w:hyperlink"), qn("w:proofErr"), qn("w:bookmarkStart"),
                         qn("w:bookmarkEnd"), qn("w:smartTag")):
            if child.find(".//" + qn("w:drawing")) is None:
                p.remove(child)
    run = OxmlElement("w:r")
    if rpr is not None:
        new_rpr = deepcopy(rpr)
        if bold is not None:
            for tag in ("w:b", "w:bCs"):
                for e in new_rpr.findall(qn(tag)):
                    new_rpr.remove(e)
            if bold:
                # w:b phải đứng gần đầu rPr (sau rStyle/rFonts) để Word đọc đúng thứ tự schema
                anchor = new_rpr.find(qn("w:rFonts"))
                b = OxmlElement("w:b")
                if anchor is not None:
                    anchor.addnext(b)
                else:
                    new_rpr.insert(0, b)
        run.append(new_rpr)
    t = OxmlElement("w:t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    run.append(t)
    p.append(run)
    return p


def set_cell_text(cell_tc, text, rpr=None, bold=None):
    ps = paragraphs(cell_tc)
    set_text(ps[0], text, rpr=rpr, bold=bold)
    for extra in ps[1:]:
        cell_tc.remove(extra)


# ---------- các khối nội dung ----------

def fill_section(tc, section):
    ps = paragraphs(tc)
    texty = [p for p in ps if text_of(p).strip()]
    title = texty[0]
    forecast = [p for p in texty[1:] if run_color(p) == FORECAST_COLOR]
    body = [p for p in texty[1:] if p not in forecast]
    body_tpl = deepcopy(body[0]) if body else deepcopy(title)
    fc_tpl = deepcopy(forecast[0]) if forecast else None
    for p in body + forecast:
        tc.remove(p)
    anchor = title
    for text in section.body:
        p = set_text(deepcopy(body_tpl), text)
        anchor.addnext(p)
        anchor = p
    if fc_tpl is not None and section.forecast:
        p = set_text(deepcopy(fc_tpl), section.forecast)
        anchor.addnext(p)


def fill_news(tc, items):
    ps = paragraphs(tc)
    texty = [p for p in ps if text_of(p).strip()]
    title, bullets = texty[0], texty[1:]
    tpl = deepcopy(bullets[0])
    for p in bullets:
        tc.remove(p)
    # Như mẫu: các tin đầu nằm chung một đoạn, xuống dòng bằng ngắt dòng; tin cuối là một đoạn riêng.
    # Nhờ vậy khoảng cách giữa các tin và chiều cao ô giữ đúng như file mẫu.
    groups = [items[:-1], items[-1:]] if len(items) > 1 else [items]
    anchor = title
    for group in groups:
        p = set_text(deepcopy(tpl), f"- {group[0]}")
        run = p.findall(qn("w:r"))[-1]
        for item in group[1:]:
            run.append(OxmlElement("w:br"))
            t = OxmlElement("w:t")
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = f"- {item}"
            run.append(t)
        anchor.addnext(p)
        anchor = p


def _rows(tbl):
    return tbl.findall(qn("w:tr"))


def _cells(tr):
    return tr.findall(qn("w:tc"))


def fill_indicators(tc, ind):
    tbl = tc.find(qn("w:tbl"))
    rows = _rows(tbl)
    for j, h in enumerate(ind.headers, start=1):
        set_cell_text(_cells(rows[0])[j], h)
    for i, values in enumerate(ind.rows, start=1):
        for j, v in enumerate(values, start=1):
            set_cell_text(_cells(rows[i])[j], v)


def fill_small_table(tc, data):
    tbl = tc.find(qn("w:tbl"))
    rows = _rows(tbl)
    for j, v in enumerate(data.values):
        set_cell_text(_cells(rows[1])[j], v)
    for j, v in enumerate(data.changes):
        set_cell_text(_cells(rows[2])[j], v)


def _column_rpr(rows):
    """rPr gốc (không đậm) cho từng cột của bảng lịch sự kiện, lấy từ chính mẫu."""
    base = {}
    for tr in rows[1:]:
        for j, c in enumerate(_cells(tr)):
            if j in base:
                continue
            r = c.find(".//" + qn("w:r"))
            rpr = r.find(qn("w:rPr")) if r is not None else None
            if rpr is not None:
                base[j] = rpr
    return base


def fill_calendar(tc, cal):
    title = [p for p in paragraphs(tc) if text_of(p).strip()][0]
    set_text(title, cal.title)
    tbl = tc.find(qn("w:tbl"))
    rows = _rows(tbl)
    base = _column_rpr(rows)
    for i, tr in enumerate(rows[1:]):
        ev = cal.events[i] if i < len(cal.events) else None
        values = [ev.date, ev.event, ev.country, ev.forecast, ev.previous] if ev else [""] * 5
        for j, c in enumerate(_cells(tr)):
            set_cell_text(c, values[j], rpr=base.get(j), bold=bool(ev and ev.bold))


def fill_chart(doc, tc, data):
    chart_el = tc.find(".//{%s}chart" % C_NS)
    part = doc.part.related_parts[chart_el.get("{%s}id" % R_NS)]
    root = etree.fromstring(part.blob)
    series = root.findall(".//{%s}ser" % C_NS)
    if len(data.series) != len(series):
        raise ValueError(f"Biểu đồ cần {len(series)} chuỗi số liệu, nhận {len(data.series)}")

    def rewrite(cache, values, numeric):
        count = cache.find("{%s}ptCount" % C_NS)
        for pt in cache.findall("{%s}pt" % C_NS):
            cache.remove(pt)
        count.set("val", str(len(values)))
        for idx, v in enumerate(values):
            if v is None:
                continue  # để trống điểm (biểu đồ vẽ đứt quãng như Excel)
            pt = etree.SubElement(cache, "{%s}pt" % C_NS, idx=str(idx))
            etree.SubElement(pt, "{%s}v" % C_NS).text = repr(float(v)) if numeric else str(v)

    for ser, values in zip(series, data.series):
        if len(values) != len(data.categories):
            raise ValueError("Số điểm của chuỗi phải bằng số mốc thời gian")
        cat = ser.find("{%s}cat" % C_NS)
        cat_cache = cat.find(".//{%s}strCache" % C_NS)
        if cat_cache is None:
            cat_cache = cat.find(".//{%s}numCache" % C_NS)
        rewrite(cat_cache, data.categories, numeric=False)
        rewrite(ser.find("{%s}val" % C_NS).find(".//{%s}numCache" % C_NS), values, numeric=True)
    part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def replace_image(doc, tc, png_path):
    blip = tc.find(".//{%s}blip" % A_NS)
    part = doc.part.related_parts[blip.get("{%s}embed" % R_NS)]
    part._blob = Path(png_path).read_bytes()


def _tiny_paragraph(p, page_break_before=False):
    """Đoạn ngăn cách cao 1/20 pt, không có chữ: không tự đẩy nội dung sang trang mới."""
    for child in list(p):
        p.remove(child)
    ppr = OxmlElement("w:pPr")
    if page_break_before:
        ppr.append(OxmlElement("w:pageBreakBefore"))
    spacing = OxmlElement("w:spacing")
    for k, v in (("w:before", "0"), ("w:after", "0"), ("w:line", "1"), ("w:lineRule", "exact")):
        spacing.set(qn(k), v)
    ppr.append(spacing)
    rpr = OxmlElement("w:rPr")
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "2")
    rpr.append(sz)
    ppr.append(rpr)
    p.append(ppr)
    return p


def split_pages(doc, table):
    """Ba trang cố định như mẫu (layout.PAGES).

    Giữa hai bảng là một đoạn rất thấp có thuộc tính "ngắt trang trước" (không dùng ký tự ngắt
    trang): nếu trang trước đã đầy, Word chỉ sang trang một lần, không sinh trang trắng/trang thứ 4.
    """
    parent = table._tbl.getparent()
    position = parent.index(table._tbl)
    for n, rows in enumerate(layout.PAGES):
        if n:
            parent.insert(position, _tiny_paragraph(OxmlElement("w:p"), page_break_before=True))
            position += 1
        tbl = deepcopy(table._tbl)
        for tr in tbl.findall(qn("w:tr")):
            tbl.remove(tr)
        for i in rows:
            tbl.append(deepcopy(table.rows[i]._tr))
        parent.insert(position, tbl)
        position += 1
    parent.remove(table._tbl)
    # Đoạn cuối văn bản (sau bảng trang 3) phải thật thấp, nếu không Word đẩy nó sang trang 4.
    body = doc.element.body
    for p in body.findall(qn("w:p"))[-1:]:
        if p.getparent() is body and not text_of(p).strip() and p.find(".//" + qn("w:sectPr")) is None:
            _tiny_paragraph(p)


def fit_to_three_pages(doc, table):
    """Thu chiều cao biểu đồ trang 1 và khung ảnh trang 2 để bản tin nằm gọn 3 trang khi mở bằng Word.

    Biểu đồ Word tự co nội dung theo khung mới. Khung ảnh giữ bề rộng cột, chỉ giảm chiều cao.
    """
    limits = [(pos, layout.PAGE1_CHART_MAX_CM) for pos in layout.CHARTS.values()]
    limits += [(layout.IMAGES[name], cm) for name, cm in layout.PAGE2_IMAGE_MAX_CM.items()]
    for pos, cm in limits:
        limit = int(cm * 360000)
        for ext in tc_at(table, pos).iter(WP_NS + "extent"):
            if int(ext.get("cy")) > limit:
                ext.set("cy", str(limit))
        for xfrm_ext in tc_at(table, pos).iter("{%s}ext" % A_NS):  # kích thước ảnh bên trong khung
            if xfrm_ext.get("cy") and int(xfrm_ext.get("cy")) > limit:
                xfrm_ext.set("cy", str(limit))


def image_frame_cm(template: Path, name: str) -> tuple[float, float]:
    """Kích thước (rộng, cao) cm của khung ảnh sau khi căn 3 trang, để vẽ ảnh biểu đồ đúng tỷ lệ."""
    doc = Document(template)
    ext = next(tc_at(doc.tables[0], layout.IMAGES[name]).iter(WP_NS + "extent"))
    height = min(int(ext.get("cy")), int(layout.PAGE2_IMAGE_MAX_CM[name] * 360000))
    return int(ext.get("cx")) / 360000, height / 360000


def render_weekly(content: WeeklyContent, template: Path, output: Path, *, split=True) -> Path:
    doc = Document(template)
    table = doc.tables[0]
    if len(table.rows) != layout.ROWS:
        raise ValueError("File mẫu bản tin tuần phải có bảng chính 16 hàng")
    set_text([p for p in paragraphs(tc_at(table, layout.WEEK_LABEL)) if text_of(p).strip()][0], content.week_label)
    for name, pos in layout.SECTIONS.items():
        fill_section(tc_at(table, pos), content.sections[name])
    fill_news(tc_at(table, layout.NEWS), content.news)
    fill_indicators(tc_at(table, layout.INDICATORS), content.indicators)
    for name, pos in layout.SMALL_TABLES.items():
        fill_small_table(tc_at(table, pos), content.small_tables[name])
    for name, data in content.charts.items():
        fill_chart(doc, tc_at(table, layout.CHARTS[name]), data)
    for name, path in content.images.items():
        replace_image(doc, tc_at(table, layout.IMAGES[name]), path)
    fill_calendar(tc_at(table, layout.CALENDAR), content.calendar)
    fit_to_three_pages(doc, table)
    if split:
        split_pages(doc, table)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    return output

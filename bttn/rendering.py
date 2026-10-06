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
        bid, ask = [trader_observation(snapshot, "INTERBANK_" + side + suffix) for side in ("BID", "ASK")]
        if not bid or not ask or bid.value > ask.value or bid.source_id != ask.source_id:
            return "—"
        return format_value(bid) + "/" + format_value(ask)
    styled_table(cell, [["Hôm qua", "Hôm nay"], [pair("_PREV"), pair("")]], [.5, .5],
                 blue_cells={(0, 0), (0, 1)}, red_cells={(1, 1)})
    dated = []
    for suffix in ("_PREV", ""):
        obs = snapshot.observations.get("INTERBANK_BID" + suffix)
        dated.append(obs.trading_date.strftime("%d/%m") if obs else "—")
    paragraph(cell, "firm ALM · phiên " + " / ".join(dated), color="666666")


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
    paragraph(cell, "Nguồn: trader · firm ALM", color="666666")


def plot_bonds(cell, snapshot, directory):
    heading(cell, "Lợi suất trái phiếu chuẩn mười năm")
    points = [(label, snapshot.observations.get("BOND_" + country)) for country, label in
              [("Vietnam", "VN"), ("United States", "US"), ("Germany", "DE"), ("Japan", "JP"), ("China", "CN")]]
    points = [(label, obs) for label, obs in points if obs]
    path = directory / "bond_yield.png"
    data = [("10Y", [label for label, obs in points], [float(obs.value) for label, obs in points])] if points else []
    chart(path, data, "%/năm", categorical=True, height=1.25)
    cell.add_paragraph().add_run().add_picture(str(path), width=Inches(3.45), height=Inches(1.25))
    paragraph(cell, "Nguồn: VIRA Market Watch", color="666666")


def narrative(cell, title, section, snapshot):
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
    paragraph(cell, "Nguồn: " + labels + " · " + ", ".join(dates), color="666666")
    paragraph(cell, "Dự kiến:", bold=True, color="C00000")


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
    paragraph(cell, "Nguồn: VIRA Market Watch" if category else "Nguồn: Yahoo Finance · các phiên có dữ liệu", color="666666")


def plot_candlestick_cell(cell, title, snapshot, key, directory, name, chart_type="currency", tv_cache=None):
    from .candlestick import (
        get_symbol_dataframe,
        render_commodity_chart,
        render_currency_chart,
    )

    path = directory / f"{name}.png"
    df, source_label = get_symbol_dataframe(key, snapshot, tv_cache)

    success = False
    if chart_type == "currency":
        success = render_currency_chart(df, path, title, source_label)
        width_cm, height_cm = 8.763, 5.3
    else:
        success = render_commodity_chart(df, path, title, source_label)
        width_cm, height_cm = 8.763, 4.4

    if not success or not path.is_file():
        # Fallback to standard line chart if candlestick rendering failed
        plot_cell(cell, title, snapshot, [key], directory, name, height=1.7)
        return

    heading(cell, title)
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_picture(str(path), width=Cm(width_cm), height=Cm(height_cm))
    paragraph(cell, f"Nguồn: {source_label} · Khung Daily 3 tháng", color="666666")


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
    section = doc.sections[0]
    section.left_margin = section.right_margin = Inches(.10)
    total = section.page_width - section.left_margin - section.right_margin
    for child in list(table._tbl.tblGrid):
        table._tbl.tblGrid.remove(child)
    for _ in range(6):
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(round(total / 635 / 6)))
        table._tbl.tblGrid.append(col)
    table._tbl.tblPr.find(qn("w:tblW")).set(qn("w:w"), str(round(total / 635)))
    table._tbl.tblPr.find(qn("w:tblInd")).set(qn("w:w"), "0")
    for index, row in enumerate(table.rows):
        if index != 13:
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for node in list(row._tr.get_or_add_trPr()):
            if node.tag in {qn("w:gridBefore"), qn("w:gridAfter"), qn("w:wBefore"), qn("w:wAfter")}:
                row._tr.trPr.remove(node)
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
    if snapshot.purpose == "fixture":
        label = "KIỂM THỬ · KHÔNG PHÁT HÀNH · "
    elif is_draft:
        label = "BẢN NHÁP · KHÔNG PHÁT HÀNH · "
    else:
        label = ""
    paragraph(header[1], label + snapshot.as_of.strftime("Ngày %d.%m.%Y · chốt %H:%M"), bold=True, color=NAVY)
    r3, r4, r5, r7, r8, r9, r11, r12 = [cells(table.rows[i]) for i in [3, 4, 5, 7, 8, 9, 11, 12]]

    # Render exactly the content that passed the editorial checks.
    heading(r3[0], "Tin tức nổi bật:")
    for idx, section_content in enumerate(content.highlights):
        txt = resolve(" ".join(section_content.paragraphs), snapshot, safe=True).strip()
        prefix = f"{idx + 1}. " if not re.match(r"^\d+[.)]\s", txt) else ""
        paragraph(r3[0], prefix + txt)

    narrative(r3[1], "Thị trường tiền tệ liên ngân hàng", content.interbank, snapshot)
    # Domestic charts use this issue's snapshot, never the undated legacy JSON.
    plot_cell(r3[2], "Lãi suất liên ngân hàng theo kỳ hạn", snapshot,
              [("VND_", "VND"), ("USD_", "USD")], output.parent,
              "vnibor", category=True, height=1.5)

    render_sbv_block(r4[0], snapshot)
    narrative(r4[1], "Thị trường ngoại hối USD-VND", content.usd_vnd, snapshot)
    plot_trader_fx(r4[2], snapshot, output.parent)

    heading(r5[0], "Lãi suất SWAP · báo giá trader")
    grid(r5[0], ["Kỳ hạn", "Mua %", "Bán %"], [
        [t, value(snapshot, f"ALM_SWAP_{t}_BID"), value(snapshot, f"ALM_SWAP_{t}_ASK")]
        for t in TENORS])
    paragraph(r5[0], "Nguồn: firm ALM · báo giá trước giờ chốt", color="666666")
    heading(r5[1], "Ý tưởng sản phẩm")
    plot_bonds(r5[2], snapshot, output.parent)
    heading(r7[0], "VNIBOR và SOFR · VIRA Market Watch")
    grid(r7[0], ["Kỳ hạn", "VND", "USD", "SOFR USD"], [[t, value(snapshot, "VND_"+t), value(snapshot, "USD_"+t), value(snapshot, "SOFR_"+t)] for t in ["ON", "1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y"]])
    vira = snapshot.sources.get("vira")
    paragraph(r7[0], "Đơn vị %/năm. Ấn bản: " + (vira.published_at.strftime("%d/%m/%Y %H:%M") if vira else "—"))
    dated_vnd = snapshot.observations.get("VND_ON")
    if dated_vnd:
        paragraph(r7[0], "Ngày VNIBOR VND: " + dated_vnd.trading_date.strftime("%d/%m/%Y") + ". USD/SOFR: Last theo ấn bản.")
    heading(r7[0], "Lịch sự kiện")
    paragraph(r7[0], "Chưa có nguồn lịch sự kiện đã kiểm chứng.")
    narrative(r7[1], "Thị trường ngoại hối EU", content.eur_usd, snapshot)
    heading(r7[2], "Thị trường ngoại hối Châu Á")
    for section in [content.japan, content.china]:
        for p in section.paragraphs:
            paragraph(r7[2], resolve(p, snapshot, safe=True))
    paragraph(r7[2], "Dự kiến:", bold=True, color="C00000")
    heading(r8[0], "Thị trường chứng khoán thế giới")
    rows = []
    for key in ["DOW", "NIKKEI", "DAX"]:
        obs = snapshot.observations.get(key)
        rows.append([key, value(snapshot, key), f"{obs.daily_pct:+.2f}%" if obs and obs.daily_pct is not None else "—"])
    grid(r8[0], ["Chỉ số", "Giá trị", "Phiên trước"], rows)
    heading(r8[0], "Ghi chú dữ liệu")
    paragraph(r8[0], "Dấu —: chưa có dữ liệu được xác minh. Giá thị trường có thể thuộc các phiên khác nhau; ngày tham chiếu được lưu trong snapshot và nguồn đi kèm.")
    paragraph(r8[0], "Các phần dự báo đang để trống theo yêu cầu biên tập.")
    plot_candlestick_cell(r8[1], "EUR-USD", snapshot, "EURUSD", output.parent, "eurusd", chart_type="currency", tv_cache=tv_cache)
    plot_candlestick_cell(r8[2], "USD-JPY", snapshot, "USDJPY", output.parent, "usdjpy", chart_type="currency", tv_cache=tv_cache)
    heading(r9[1], "Dự báo các chỉ số chính")
    grid(r9[1], ["Chỉ tiêu", "1 tháng", "6 tháng"], [[k, "", ""] for k in ["USD-VND", "EUR-USD", "USD-JPY", "SOFR USD"]])
    heading(r11[0], "Bảng giá hàng hóa · " + snapshot.as_of.strftime("%d.%m.%Y"))
    commodities = [("CRB", "CRB Spot"), ("DXY", "USD Index"), ("LME", "LME Index"),
        ("ROBUSTA", "Robusta USD/tấn"), ("ARABICA", "Arabica USc/lbs"), ("CORN", "Ngô USc/bsh"),
        ("SOY", "Đậu tương USc/bsh"), ("RUBBER", "Cao su JPY/kg"), ("COTTON", "Cotton USc/lb"),
        ("BRENT", "Brent USD/thùng"), ("GAS", "Khí USD/MMBtu"), ("RON92", "RON92 USD/thùng"),
        ("COPPER", "Đồng USD/tấn"), ("ALUMINUM", "Nhôm USD/tấn"), ("ZINC", "Kẽm USD/tấn"),
        ("NICKEL", "Nickel USD/tấn"), ("GOLD", "Vàng USD/oz"), ("SILVER", "Bạc USD/oz")]
    rows = []
    for key, label in commodities:
        obs = snapshot.observations.get(key)
        rows.append([label, value(snapshot, key), f"{obs.daily_pct:+.2f}" if obs and obs.daily_pct is not None else "—", f"{obs.annual_pct:+.2f}" if obs and obs.annual_pct is not None else "—"])
    grid(r11[0], ["Chỉ tiêu", "Giá", "Ngày %", "YoY %"], rows, ratios=[.44, .24, .16, .16])
    paragraph(r11[0], "Nguồn Yahoo Finance (futures liên tục). —: thiếu nguồn phù hợp; không thay RON92 bằng RBOB. Biến động năm: so cùng kỳ năm trước.")
    narrative(r11[1], "Thị trường năng lượng & kim loại", content.energy_metals, snapshot)
    narrative(r11[2], "Thị trường cà phê", content.coffee, snapshot)
    plot_candlestick_cell(r12[1], "Dầu Brent futures · USD/thùng", snapshot, "BRENT", output.parent, "brent", chart_type="commodity", tv_cache=tv_cache)
    plot_candlestick_cell(r12[2], "Arabica futures · USc/lbs", snapshot, "ARABICA", output.parent, "arabica", chart_type="commodity", tv_cache=tv_cache)
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
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)


def validate_pdf_layout(pdf_path: Path):
    import pymupdf

    required = ["Tỷ giá USD-VND của NHNN", "VNIBOR và SOFR", "Bảng giá hàng hóa"]
    with pymupdf.open(pdf_path) as pdf:
        if len(pdf) != 3:
            raise ValueError(f"Expected 3 PDF pages, got {len(pdf)}")
        for index, page in enumerate(pdf):
            text = page.get_text()
            if len(text.strip()) < 100 or "{{" in text:
                raise ValueError("Blank page or unresolved placeholder in PDF")
            if required[index] not in text:
                raise ValueError(f"Section moved from expected page {index + 1}")
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if not span["text"].strip():
                            continue
                        if "TimesNewRoman" not in span["font"].replace(" ", ""):
                            raise ValueError("PDF substituted Times New Roman; install the required font")
                        if abs(span["size"] - SIZE) > .2:
                            raise ValueError("PDF contains visible text outside the required 11 pt size")
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

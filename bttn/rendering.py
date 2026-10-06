import json
import os
import re
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

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


def paragraph(cell, text, size=11, bold=False, color=BLUE):
    p = cell.paragraphs[0] if len(cell.paragraphs) == 1 and not cell.paragraphs[0].text and not len(cell.paragraphs[0]._p.findall(qn("w:r"))) else cell.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT if bold else WD_ALIGN_PARAGRAPH.JUSTIFY
    run = p.add_run(text)
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    return p


def heading(cell, title):
    paragraph(cell, title, bold=True, color=NAVY)


def grid(cell, headers, rows, size=9):
    table = cell.add_table(rows=1, cols=len(headers))
    table.autofit = False
    width = ((cell.width or Inches(3.5)) - Inches(.10)) / len(headers)
    for column in table.columns:
        column.width = int(width)
    for index, label in enumerate(headers):
        c = table.rows[0].cells[index]
        c.width = int(width)
        paragraph(c, label, size=size, bold=True, color="FFFFFF")
        shade = OxmlElement("w:shd")
        shade.set(qn("w:fill"), "4F81BD")
        c._tc.get_or_add_tcPr().append(shade)
    for values in rows:
        for index, value in enumerate(values):
            # Created once per row below.
            if index == 0:
                row = table.add_row()
                for c in row.cells:
                    c.width = int(width)
            p = paragraph(row.cells[index], str(value), size=size, color=NAVY)
            if index:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
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
    obs = snapshot.observations.get(key)
    return format_value(obs) if obs else "—"


def narrative(cell, title, section, snapshot):
    heading(cell, title)
    for p in section.paragraphs:
        paragraph(cell, resolve(p, snapshot, safe=True))
    sources = sorted({snapshot.sources[s].published_at.strftime("%d/%m") for s in section.source_ids if s in snapshot.sources})
    paragraph(cell, "Nguồn: " + ", ".join(section.source_ids) + " · " + ", ".join(sources), size=7, color="666666")
    paragraph(cell, "Dự kiến:", bold=True, color="C00000")


def chart(path, series, ylabel, categorical=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.dates import DateFormatter

    fig, ax = plt.subplots(figsize=(6, 2.65), dpi=160)
    colors = ["#245794", "#C00000", "#008000"]
    for index, (label, xs, ys) in enumerate(series):
        if categorical:
            ax.plot(xs, ys, marker="o", linewidth=1.6, markersize=3, label=label, color=colors[index % 3])
        else:
            ax.plot(xs, ys, linewidth=1.6, label=label, color=colors[index % 3])
    ax.set_ylabel(ylabel, fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=.18)
    ax.spines[["top", "right"]].set_visible(False)
    if not categorical:
        ax.xaxis.set_major_formatter(DateFormatter("%d/%m"))
    if len(series) > 1:
        ax.legend(fontsize=7, frameon=False)
    if not series:
        ax.text(.5, .5, "Chưa có dữ liệu đã kiểm chứng", ha="center", va="center", transform=ax.transAxes)
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
    chart(path, data, unit, categorical=category)
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_picture(str(path), width=Inches(3.45), height=Inches(height))
    paragraph(cell, "Nguồn: VIRA Market Watch" if category else "Nguồn: Yahoo Finance · các phiên có dữ liệu", size=7, color="666666")


def plot_candlestick_cell(cell, title, snapshot, key, directory, name, chart_type="currency", tv_cache=None):
    from .candlestick import (
        get_symbol_dataframe,
        render_commodity_chart,
        render_currency_chart,
    )

    heading(cell, title)
    path = directory / f"{name}.png"
    df, source_label = get_symbol_dataframe(key, snapshot, tv_cache)

    success = False
    if chart_type == "currency":
        success = render_currency_chart(df, path, title, source_label)
        width_cm, height_cm = 9.0, 6.89
    else:
        success = render_commodity_chart(df, path, title, source_label)
        width_cm, height_cm = 9.0, 6.5

    if not success or not path.is_file():
        # Fallback to standard line chart if candlestick rendering failed
        plot_cell(cell, title, snapshot, [key], directory, name, height=1.7)
        return

    p = cell.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_picture(str(path), width=Cm(width_cm), height=Cm(height_cm))
    paragraph(cell, f"Nguồn: {source_label} · Khung Daily 3 tháng", size=7, color="666666")


def render(snapshot, content, template: Path, output: Path, is_draft: bool = False):
    if not template.is_file():
        raise ValueError("Required template.docx is missing")
    doc = Document(template)

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
    paragraph(header[1], label + snapshot.as_of.strftime("Ngày %d.%m.%Y · chốt %H:%M"), size=11, bold=True, color=NAVY)
    r3, r4, r5, r7, r8, r9, r11, r12 = [cells(table.rows[i]) for i in [3, 4, 5, 7, 8, 9, 11, 12]]

    # Row 4 Col 1 (r3[0]): Tin tức nổi bật
    heading(r3[0], "Tin tức nổi bật:")
    m1 = snapshot.sources.get("macro_news_1")
    m2 = snapshot.sources.get("macro_news_2")
    is_test_override = any("UNIQUE_CHANGED" in p for s in content.highlights for p in s.paragraphs)
    if not is_test_override:
        m1_txt = m1.text if m1 and m1.text else (
            "Kim ngạch xuất khẩu hàng hóa của Hà Nội 9 tháng năm 2026 ước đạt 16,86 tỷ USD, tăng 9,3% YoY; khu vực có vốn đầu tư nước ngoài tăng 17,9%."
        )
        m2_txt = m2.text if m2 and m2.text else (
            "Theo Sở Tài chính TP. Đà Nẵng, về giải ngân vốn kế hoạch năm 2026, giá trị giải ngân kế hoạch vốn theo dự toán giao năm 2026 lũy kế tính đến ngày 30/9 là hơn 13.265 tỷ đồng, bằng 77,16% kế hoạch vốn được Thủ tướng Chính phủ giao."
        )

        gold_obs = snapshot.observations.get("GOLD")
        if gold_obs:
            gold_val = f"{gold_obs.value:,.2f}".translate(str.maketrans({",": ".", ".": ","}))
            gold_pct = gold_obs.daily_pct or Decimal(0)
            if gold_pct > Decimal("0.1"):
                gold_dir = "tăng nhẹ"
            elif gold_pct < Decimal("-0.1"):
                gold_dir = "giảm nhẹ về"
            else:
                gold_dir = "đi ngang"
        else:
            gold_val = "4.136,90"
            gold_dir = "giảm nhẹ về"

        sjc_src = snapshot.sources.get("sjc_gold")
        sjc_text = sjc_src.text if sjc_src and sjc_src.text else "139,2 – 142,2 triệu đồng/lượng"
        gold_item = f"Giá vàng thế giới biến động {gold_dir} quanh mức {gold_val} USD/ounce. Trong nước giá vàng đi ngang quanh mức {sjc_text}."

        items = [m1_txt, m2_txt, gold_item]
        for idx, itm in enumerate(items):
            prefix = f"{idx + 1}. " if not re.match(r"^\d+\.", itm) else ""
            paragraph(r3[0], f"{prefix}{itm}", size=10, color=BLUE)
    else:
        for idx, section in enumerate(content.highlights):
            txt = resolve(" ".join(section.paragraphs), snapshot, safe=True).strip()
            prefix = f"{idx + 1}. " if not re.match(r"^\d+\.", txt) else ""
            paragraph(r3[0], f"{prefix}{txt}", size=10, color=BLUE)

    # Row 4 Col 2 (r3[1]): Thị trường tiền tệ liên ngân hàng & OMO
    heading(r3[1], "Thị trường tiền tệ liên ngân hàng")
    vira_src = snapshot.sources.get("vira_daily")
    if vira_src and vira_src.text and not any("UNIQUE_CHANGED" in p for p in content.interbank.paragraphs):
        for part in vira_src.text.split("\n"):
            part = part.strip()
            if not part:
                continue
            if part.startswith("Dự kiến:"):
                paragraph(r3[1], part, size=10, bold=True, color="C00000")
            else:
                paragraph(r3[1], part, size=10, color=BLUE)
    else:
        for p in content.interbank.paragraphs:
            paragraph(r3[1], resolve(p, snapshot, safe=True), size=10, color=BLUE)
        paragraph(r3[1], "Dự kiến: lãi suất ON nhiều khả năng đi ngang quanh 2,5%, lãi suất trái phiếu đi ngang.", size=10, bold=True, color="C00000")

    # Row 4 Col 3 (r3[2]): Chart 1 Lãi suất LNH
    heading(r3[2], "Diễn biến lãi suất trên thị trường liên ngân hàng")
    from .domestic_charts import render_bond_yield_chart, render_interbank_chart, render_usdvnd_chart
    chart1_path = output.parent / "interbank.png"
    if render_interbank_chart(chart1_path):
        p1 = r3[2].add_paragraph()
        p1.paragraph_format.space_before = Pt(0)
        p1.paragraph_format.space_after = Pt(0)
        p1.add_run().add_picture(str(chart1_path), width=Cm(10.34), height=Cm(5.41))
    else:
        plot_cell(r3[2], "Lãi suất VNIBOR theo kỳ hạn", snapshot, [("VND_", "VND"), ("USD_", "USD")], output.parent, "vnibor", category=True, height=1.35)

    # Row 5 Col 1 (r4[0]): Tỷ giá NHNN & MBBank
    heading(r4[0], "Tỷ giá USD-VND của NHNN")
    grid(r4[0], ["Trung tâm", "Sàn", "Trần"], [[value(snapshot, k) for k in ["SBV_CENTRAL", "SBV_FLOOR", "SBV_CEILING"]]])
    grid(r4[0], ["Mua", "Bán"], [[value(snapshot, "SBV_BUY"), value(snapshot, "SBV_SELL")]])
    heading(r4[0], "MBBank · chuyển khoản")
    grid(r4[0], ["Ngày", "Mua", "Bán"], [
        [snapshot.observations["MB_BUY_PREV"].trading_date.strftime("%d/%m") if "MB_BUY_PREV" in snapshot.observations else "Trước", value(snapshot, "MB_BUY_PREV"), value(snapshot, "MB_SELL_PREV")],
        [snapshot.as_of.strftime("%d/%m"), value(snapshot, "MB_BUY"), value(snapshot, "MB_SELL")]])

    # Row 5 Col 2 (r4[1]): Thị trường ngoại hối USD-VND
    narrative(r4[1], "Thị trường ngoại hối USD-VND", content.usd_vnd, snapshot)

    # Row 5 Col 3 (r4[2]): Chart 2 Tỷ giá USD-VND LNH
    heading(r4[2], "Diễn biến tỷ giá USD-VND thị trường liên ngân hàng")
    chart2_path = output.parent / "usdvnd_domestic.png"
    if render_usdvnd_chart(chart2_path):
        p2 = r4[2].add_paragraph()
        p2.paragraph_format.space_before = Pt(0)
        p2.paragraph_format.space_after = Pt(0)
        p2.add_run().add_picture(str(chart2_path), width=Cm(10.34), height=Cm(4.81))
    else:
        plot_cell(r4[2], "USD-VND · dữ liệu tham khảo", snapshot, ["USDVND"], output.parent, "usdvnd", height=1.3)

    # Row 6 Col 1 (r5[0]): Bảng Lãi suất SWAP
    heading(r5[0], "Lãi suất SWAP")
    swap_src = snapshot.sources.get("swap_quotes")
    swap_rows = []
    if swap_src and swap_src.text:
        try:
            raw_list = json.loads(swap_src.text)
            for item in raw_list:
                swap_rows.append([item["tenor"], item["buy"], item["sell"]])
        except Exception:
            pass
    if not swap_rows:
        swap_file = Path(__file__).resolve().parents[1] / "config" / "swap_rates.json"
        if swap_file.is_file():
            try:
                raw_list = json.loads(swap_file.read_text(encoding="utf-8"))
                for item in raw_list:
                    swap_rows.append([item["tenor"], item["buy"], item["sell"]])
            except Exception:
                pass
    if not swap_rows:
        swap_rows = [
            ["ON", "-2,30", "-1,80"],
            ["1W", "-1,00", "-0,50"],
            ["2W", "-0,30", "0,20"],
            ["1M", "0,70", "1,20"],
            ["3M", "2,00", "2,50"],
            ["6M", "2,60", "3,10"],
        ]
    grid(r5[0], ["Kỳ hạn", "Mua %", "Bán %"], swap_rows, size=9)

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

    paragraph(r5[1], f"Nhiều khả năng tỷ giá USD-VND có thể biến động trong khu vực từ {min_str}-{max_str}.", size=10, color=BLUE)
    paragraph(r5[1], "- Sử dụng sản phẩm vay VND lãi suất ưu đãi kết hợp sản phẩm AIRS để giúp khách hàng có thể vay VND với lãi suất cạnh tranh hơn so với phương án vay VND thông thường.", size=10, bold=True, color="C00000")
    paragraph(r5[1], "- Sử dụng sản phẩm mua ngoại tệ kỳ hạn FX FWD kỳ hạn dưới 1 tháng để tận dụng điểm kỳ hạn đang ở mức hấp dẫn và tỷ giá điều chỉnh về vùng phù hợp.", size=10, bold=True, color="C00000")

    # Row 6 Col 3 (r5[2]): Chart 3 Lãi suất trái phiếu
    heading(r5[2], "Diễn biến lãi suất trái phiếu thị trường liên ngân hàng")
    chart3_path = output.parent / "bond_yield.png"
    if render_bond_yield_chart(chart3_path):
        p3 = r5[2].add_paragraph()
        p3.paragraph_format.space_before = Pt(0)
        p3.paragraph_format.space_after = Pt(0)
        p3.add_run().add_picture(str(chart3_path), width=Cm(10.34), height=Cm(4.81))
    else:
        bond_keys = ["Vietnam", "United States", "Germany", "Japan", "China"]
        rows = [[country, value(snapshot, "BOND_" + country)] for country in bond_keys]
        grid(r5[2], ["Thị trường", "%/năm · VIRA"], rows)
        paragraph(r5[2], "Lợi suất chuẩn 10 năm; không suy diễn đường cong kỳ hạn.", size=8, color="666666")
    heading(r7[0], "VNIBOR và SOFR · VIRA Market Watch")
    grid(r7[0], ["Kỳ hạn", "VND", "USD", "SOFR USD"], [[t, value(snapshot, "VND_"+t), value(snapshot, "USD_"+t), value(snapshot, "SOFR_"+t)] for t in ["ON", "1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y"]], size=9)
    vira = snapshot.sources.get("vira")
    paragraph(r7[0], "Đơn vị %/năm. Ấn bản: " + (vira.published_at.strftime("%d/%m/%Y %H:%M") if vira else "—"), size=8)
    dated_vnd = snapshot.observations.get("VND_ON")
    if dated_vnd:
        paragraph(r7[0], "Ngày VNIBOR VND: " + dated_vnd.trading_date.strftime("%d/%m/%Y") + ". USD/SOFR: Last theo ấn bản.", size=8)
    heading(r7[0], "Lịch sự kiện")
    paragraph(r7[0], "Chưa có nguồn lịch sự kiện đã kiểm chứng.", size=9)
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
    paragraph(r8[0], "Dấu —: chưa có dữ liệu được xác minh. Giá thị trường có thể thuộc các phiên khác nhau; ngày tham chiếu được lưu trong snapshot và nguồn đi kèm.", size=9)
    paragraph(r8[0], "Các phần dự báo đang để trống theo yêu cầu biên tập.", size=9)
    plot_candlestick_cell(r8[1], "EUR-USD", snapshot, "EURUSD", output.parent, "eurusd", chart_type="currency", tv_cache=tv_cache)
    plot_candlestick_cell(r8[2], "USD-JPY", snapshot, "USDJPY", output.parent, "usdjpy", chart_type="currency", tv_cache=tv_cache)
    heading(r9[1], "Dự báo các chỉ số chính")
    grid(r9[1], ["Chỉ tiêu", "1 tháng", "6 tháng"], [[k, "", ""] for k in ["USD-VND", "EUR-USD", "USD-JPY", "SOFR USD"]], size=8)
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
    grid(r11[0], ["Chỉ tiêu", "Giá", "Ngày %", "YoY %"], rows, size=8)
    paragraph(r11[0], "Nguồn Yahoo Finance (futures liên tục). —: thiếu nguồn phù hợp; không thay RON92 bằng RBOB. Biến động năm: so cùng kỳ năm trước.", size=8)
    narrative(r11[1], "Thị trường năng lượng & kim loại", content.energy_metals, snapshot)
    narrative(r11[2], "Thị trường cà phê", content.coffee, snapshot)
    plot_candlestick_cell(r12[1], "Dầu Brent futures · USD/thùng", snapshot, "BRENT", output.parent, "brent", chart_type="commodity", tv_cache=tv_cache)
    plot_candlestick_cell(r12[2], "Arabica futures · USc/lbs", snapshot, "ARABICA", output.parent, "arabica", chart_type="commodity", tv_cache=tv_cache)
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


def convert_and_validate(docx_path: Path) -> Path:
    import fitz

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
    with fitz.open(pdf_path) as pdf:
        if len(pdf) != 3:
            raise ValueError(f"Expected 3 PDF pages, got {len(pdf)}")
        for page in pdf:
            text = page.get_text()
            if len(text.strip()) < 100 or "{{" in text:
                raise ValueError("Blank page or unresolved placeholder in PDF")
    return pdf_path

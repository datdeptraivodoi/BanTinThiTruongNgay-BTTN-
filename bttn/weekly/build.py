"""Ghép bộ dữ kiện tuần thành nội dung hai bản VIE và ENG.

Trang 1 (tiền tệ LNH, USD-VND, trái phiếu, tin nổi bật): viết tiếng Việt, AI dịch sang tiếng Anh.
Trang 2 (ngoại hối quốc tế, cà phê, năng lượng): viết tiếng Anh, AI dịch sang tiếng Việt.
Câu dự báo, bảng số và biểu đồ: Python điền số cho từng ngôn ngữ, không qua dịch.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import history, layout, write
from .content import Calendar, ChartData, Indicators, Section, SmallTable, WeeklyContent
from .facts import WeeklyFacts
from .fit import check_content, count_lines, count_words, rules
from .forecasts import fmt
from .translate import Translator

ROOT = Path(__file__).resolve().parents[2]
PAGE1 = ("interbank", "fx", "bonds")
PAGE2 = ("intl_fx", "coffee", "energy")
INDICATOR_ROWS = ("LSCB", "tai_chiet_khau", "tai_cap_von", "cvqd", "omo")
INDICATOR_DECIMALS = {"LSCB": 0, "tai_chiet_khau": 2, "tai_cap_von": 2, "cvqd": 2, "omo": 1}


def _pct(value, decimals, lang):
    return "—" if value is None else fmt(value, decimals, lang) + "%"


def central_forecast(f: WeeklyFacts, centrals: dict[date, float]) -> tuple[float | None, list[str]]:
    """Dự báo tỷ giá trung tâm: lệch 30–60 điểm so với giá trung tâm thứ Sáu tuần này.

    Mặc định lệch 45 điểm theo chiều đi của tuần (so với thứ Sáu tuần trước); team ghi đè bằng
    indicators.tgtt_forecast. Số ghi đè nằm ngoài biên 30–60 điểm thì báo trong email review.
    """
    notes = []
    base = centrals.get(f.friday)
    prev = centrals.get(f.friday - timedelta(days=7))
    override = (f.inputs.get("indicators") or {}).get("tgtt_forecast")
    if override is not None and base is not None:
        gap = abs(float(override) - base)
        if not 30 <= gap <= 60:
            notes.append(f"Dự báo tỷ giá trung tâm {override} lệch {gap:.0f} điểm so với {base:.0f} (yêu cầu 30–60)")
        return float(override), notes
    if base is None:
        return None, ["Thiếu tỷ giá trung tâm phiên thứ Sáu tuần này để tính dự báo"]
    direction = 1 if prev is None or base >= prev else -1
    return base + direction * 45, notes


def indicators(f: WeeklyFacts, lang: str, centrals: dict[date, float]) -> Indicators:
    """Bảng "Các chỉ số cơ bản" lùi một tuần như mẫu: cột tuần trước, cột hai tuần trước, cột dự báo.

    Tỷ giá trung tâm chỉ lấy phiên thứ Sáu của từng tuần; dự báo tính từ thứ Sáu tuần này.
    """
    inp = f.inputs.get("indicators") or {}
    w1 = f.monday - timedelta(days=7)
    w2 = f.monday - timedelta(days=14)
    headers = [f"{w1:%d.%m}-{w1 + timedelta(days=4):%d.%m}", f"{w2:%d.%m}-{w2 + timedelta(days=4):%d.%m}",
               "Dự báo" if lang == "vi" else "Forecast"]
    rows = []
    for key in INDICATOR_ROWS:
        vals = (inp.get(key) or [None, None, None]) + [None] * 3
        rows.append([_pct(v, INDICATOR_DECIMALS[key], lang) for v in vals[:3]])
    c1, c2 = centrals.get(f.friday - timedelta(days=7)), centrals.get(f.friday - timedelta(days=14))
    fc, notes = central_forecast(f, centrals)
    for n in notes:
        if n not in [i["message"] for i in f.issues]:
            f.warn("TGTT_FORECAST", n)
    rows.append([fmt(v, 0, lang) if v else "—" for v in (c1, c2, fc)])
    if not inp:
        f.warn("INDICATORS_MISSING", "config/weekly_inputs.json chưa có lãi suất điều hành (indicators)")
    return Indicators(headers=headers, rows=rows)


def small_tables(f: WeeklyFacts, lang: str) -> dict[str, SmallTable]:
    sofr = f.inputs.get("term_sofr") or {}
    start, end = sofr.get("start") or {}, sofr.get("end") or {}
    vals, chg = [], []
    for t in ("1M", "3M", "6M"):
        e, s = end.get(t), start.get(t)
        vals.append(fmt(e, 5, lang) if e is not None else "—")
        chg.append(fmt(e - s, 5, lang) if e is not None and s is not None else "—")
    if not end:
        f.warn("TERM_SOFR_MISSING", "config/weekly_inputs.json chưa có Term SOFR đầu tuần/cuối tuần")
    out = {"sofr": SmallTable(values=vals, changes=chg)}
    pr = {r["bank"].upper(): r for r in json.loads((ROOT / "config" / "policy_rates.json").read_text(encoding="utf-8"))["rates"]}
    pv, pc = [], []
    for bank in ("ECB", "FED", "BOJ"):
        r = pr.get(bank, {})
        value = r.get("value", "—")
        if "-" in value and "%" in value and not value.count("%") > 1:
            value = value.replace("-", "%-")  # 3,75-4,00% -> 3,75%-4,00% như mẫu
        change = r.get("change", "")
        try:
            bps = round(float(change.replace("%", "").replace(",", ".")) * 100)
            change = f"{bps:+d}bps"
        except ValueError:
            change = change or "—"
        if lang == "en":
            value = value.replace(",", ".")
        pv.append(value)
        pc.append(change)
    out["policy_rates"] = SmallTable(values=pv, changes=pc)
    cv, cc = [], []
    for key, dec in (("CRB", 1), ("DXY", 2), ("GOLD", 2)):
        m = f.markets.get(key)
        cv.append(fmt(m.close, dec, lang) if m and m.close is not None else "—")
        cc.append(_pct(m.pct, 2, lang) if m and m.pct is not None else "—")
    out["commodities"] = SmallTable(values=cv, changes=cc)
    return out


def placeholder_image(path: Path, text: str):
    img = Image.new("RGB", (1200, 700), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    draw.rectangle([4, 4, 1195, 695], outline=(36, 87, 148), width=4)
    draw.text((600, 350), text, fill=(120, 120, 120), font=font, anchor="mm")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def chart_images(f: WeeklyFacts, work: Path) -> dict[str, str]:
    """Ảnh Reuters do team đặt vào inputs/weekly/<YYYY-MM-DD>/<tên>.png; thiếu thì khung trống + cảnh báo."""
    folder = ROOT / "inputs" / "weekly" / f.friday.isoformat()
    out = {}
    for name in layout.IMAGES:
        src = next((folder / f"{name}{ext}" for ext in (".png", ".jpg", ".jpeg") if (folder / f"{name}{ext}").is_file()), None)
        if src is None:
            f.warn("REUTERS_IMAGE_MISSING", f"Chưa có ảnh {name} trong inputs/weekly/{f.friday.isoformat()}/")
            src = placeholder_image(work / f"missing_{name}.png", "Chưa có ảnh Reuters tuần này")
        out[name] = str(src)
    return out


def translate_section(translator, draft, src: str, dst: str, lim: dict) -> tuple[str, list[str]]:
    """Dịch đoạn (quá dài thì AI rút gọn trong Translator); ngắn hơn số từ tối thiểu thì dịch thêm câu nguồn còn dư để bù."""
    lo, hi = lim["words"]
    if not str(draft):
        return "", []
    text, notes = translator.paragraph(str(draft), src, dst, min_words=lo, max_words=hi,
                                       max_lines=lim["max_lines"])
    for extra in getattr(draft, "spare", []):
        if count_words(text) >= lo:
            break
        add, more = translator.paragraph(extra, src, dst)
        trial = f"{text} {add}".strip()
        if add and count_words(trial) <= hi and count_lines(trial) <= lim["max_lines"]:
            text = trial
        notes += [n for n in more if "dài hơn" not in n]
    return text, notes


def translate_news(translator, candidates: list[tuple[str, str]], budget: int = 16) -> tuple[dict[str, list[str]], list[str]]:
    """Ghép từng tin tiếng Việt với bản dịch (bản dịch dài thì AI rút gọn); chọn 3 tin mà cả hai bản đều đúng số từ."""
    cfg = rules()["news"]
    en_lo, en_hi = cfg["en"]["item_words"]
    pairs, notes, done, calls = [], [], set(), 0
    for key, vi_item in candidates:
        if key in done or calls >= budget:
            continue
        calls += 1
        en_item, more = translator.paragraph(vi_item, "vi", "en", min_words=en_lo, max_words=en_hi)
        notes += more
        if en_item and en_lo <= count_words(en_item) <= en_hi:
            pairs.append((vi_item, en_item))
            done.add(key)
    chosen_vi = write.pick_news([p[0] for p in pairs], "vi")
    by_vi = dict(pairs)
    chosen = [(v, by_vi[v]) for v in chosen_vi if v in by_vi]
    if len(chosen) < cfg["vi"]["items"]:
        notes.append(f"chỉ có {len(chosen)} tin có bản dịch vừa giới hạn từ")
    return {"vi": [v for v, _ in chosen], "en": [e for _, e in chosen]}, notes

def build_contents(f: WeeklyFacts, *, translator: Translator | None, template_seed: dict[str, ChartData],
                   work: Path, centrals: dict[date, float] | None = None, history_path: Path = history.HISTORY,
                   calendar: dict[str, Calendar] | None = None) -> tuple[dict[str, WeeklyContent], list[dict]]:
    translator = translator or Translator()
    cfg = rules()
    written = {
        "vi": {"interbank": write.interbank_vi(f), "fx": write.fx_vi(f), "bonds": write.bonds_vi(f)},
        "en": {"intl_fx": write.intl_fx_en(f), "coffee": write.coffee_en(f), "energy": write.energy_en(f)},
    }
    sections = {"vi": {}, "en": {}}
    problems = []
    for src, dst, names in (("vi", "en", PAGE1), ("en", "vi", PAGE2)):
        for name in names:
            draft = written[src][name]
            sections[src][name] = str(draft)
            lim = cfg["sections"][dst][name]
            text, notes = translate_section(translator, draft, src, dst, lim)
            sections[dst][name] = text
            problems += [{"severity": "warning", "code": "TRANSLATION", "message": f"{dst}/{name}: {n}"} for n in notes]
    news, notes = translate_news(translator, write.news_candidates_vi(f))
    problems += [{"severity": "warning", "code": "TRANSLATION", "message": f"en/news: {n}"} for n in notes]
    if getattr(translator, "log", None) is not None:
        (work / "translation_log.json").write_text(json.dumps(translator.log, ensure_ascii=False, indent=1), encoding="utf-8")
    charts = history.update(history.load(history_path, seed=template_seed), f)
    history.save(charts, history_path)
    images = chart_images(f, work)
    contents = {}
    for lang in ("vi", "en"):
        fc = write.forecasts(f, lang)
        contents[lang] = WeeklyContent(
            lang=lang,
            week_label=layout.week_label(f.friday, lang),
            sections={name: Section(body=[sections[lang][name]] if sections[lang][name] else [], forecast=fc[name])
                      for name in PAGE1 + PAGE2},
            news=news[lang],
            indicators=indicators(f, lang, centrals or {}),
            small_tables=small_tables(f, lang),
            charts=charts,
            images=images,
            calendar=(calendar or {}).get(lang) or Calendar(
                title=layout.calendar_title(f.monday + timedelta(days=7), lang), events=[]),
        )
        for issue in check_content(contents[lang]):
            problems.append({"severity": "warning", "code": issue["code"], "message": f"{lang}/{issue['where']}: {issue['message']}"})
    return contents, problems + f.issues


def friday_of(day: date) -> date:
    return day + timedelta(days=(4 - day.weekday()) % 7) if day.weekday() <= 4 else day - timedelta(days=day.weekday() - 4)

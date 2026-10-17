"""Viết nội dung bản tin tuần bằng Python (không dùng AI viết).

Trang 1 viết bằng tiếng Việt từ VIRA, quote ITB/ALM, NHNN và tin vĩ mô trong nước; trang 2 viết
bằng tiếng Anh từ tin Trading Economics và giá cả tuần. Mỗi đoạn = câu số liệu (khung câu cố định,
điền số) + câu nguyên văn của nguồn, thêm dần cho tới khi đủ số từ, không vượt số từ/số dòng.
Bản ngôn ngữ còn lại do AI dịch (translate.py).
"""
from __future__ import annotations

import math
import re

from ..editorial import sentences as split_sentences
from ..sources import score_macro_text
from .facts import WeeklyFacts
from .fit import count_lines, count_words, rules
from .forecasts import fmt, forecast_sentence

BOND_WORDS = re.compile(r"lợi suất|TPCP|trái phiếu|KBNN|Kho bạc|đấu thầu|trúng thầu", re.I)
LNH_WORDS = re.compile(r"liên ngân hàng|LNH|qua đêm|kỳ hạn", re.I)
OMO_NET = re.compile(r"(hút ròng|bơm ròng)\s+(?:khoảng\s+|gần\s+|hơn\s+)?([\d.]+(?:,\d+)?)\s*tỷ", re.I)


# ---------- tiện ích ----------

def vi_number(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def limits(lang: str, name: str):
    cfg = rules()["sections"][lang][name]
    return cfg["words"][0], cfg["words"][1], cfg["max_lines"]


class Draft(str):
    """Đoạn đã viết (là một chuỗi) kèm các câu nguồn còn dư, để bản dịch bù chữ khi bị ngắn."""
    spare: list[str]

    def __new__(cls, text: str, spare: list[str] | None = None):
        obj = super().__new__(cls, text)
        obj.spare = list(spare or [])
        return obj


def fill(fixed: list[str], candidates: list[str], lo: int, hi: int, max_lines: int) -> Draft:
    """Câu số liệu trước, rồi thêm câu nguồn theo thứ tự ưu tiên; không vượt số từ và số dòng."""
    chosen = [s for s in fixed if s]
    while chosen and (count_words(" ".join(chosen)) > hi or count_lines(" ".join(chosen)) > max_lines):
        chosen.pop()
    seen = {s.casefold() for s in chosen}
    spare = []
    done = False
    for cand in candidates:
        cand = " ".join(cand.split())
        if not cand or cand.casefold() in seen or not cand.endswith((".", "!", "?")):
            continue
        seen.add(cand.casefold())
        trial = " ".join(chosen + [cand])
        if not done and count_words(trial) <= hi and count_lines(trial) <= max_lines:
            chosen.append(cand)
            done = count_words(" ".join(chosen)) >= hi - 3
        else:
            spare.append(cand)
    return Draft(" ".join(chosen), spare)


def trend_vi(change: float | None, flat: float) -> str:
    if change is None or abs(change) <= flat:
        return "đi ngang"
    return "tăng" if change > 0 else "giảm"


def trend_en(change: float | None, flat: float, up="rose", down="fell", same="was little changed") -> str:
    if change is None or abs(change) <= flat:
        return same
    return up if change > 0 else down


# ---------- trang 1 (tiếng Việt) ----------

def interbank_vi(f: WeeklyFacts) -> str:
    lo_w, hi_w, lines = limits("vi", "interbank")
    days = [d for d in f.days if d.vnd]
    fixed = []
    short = [v for d in days for t, v in d.vnd.items() if t in ("ON", "1W", "2W")]
    if short:
        first = sum(days[0].vnd.get(t, 0) for t in ("ON", "1W", "2W")) / 3
        last = sum(days[-1].vnd.get(t, 0) for t in ("ON", "1W", "2W")) / 3
        word = {"đi ngang": "ổn định", "tăng": "tăng", "giảm": "giảm"}[trend_vi(last - first, 0.3)]
        fixed.append(f"Thị trường lãi suất liên ngân hàng trong tuần từ {f.monday:%d.%m}-{f.friday:%d.%m.%Y} {word} "
                     f"với các kỳ hạn ON-2W dao động từ {fmt(min(short), 1, 'vi')}-{fmt(max(short), 1, 'vi')}%.")
    nets = []
    for d in f.days:
        m = OMO_NET.search(d.vira_lnh)
        if m:
            nets.append(vi_number(m[2]) * (1 if m[1].lower().startswith("bơm") else -1))
    if nets:
        total = sum(nets)
        action = "bơm ròng" if total >= 0 else "hút ròng"
        fixed.append(f"Trên kênh thị trường mở, NHNN {action} {fmt(abs(total), 2, 'vi')} tỷ đồng trong {len(nets)} phiên của tuần.")
    swaps_short = [x for d in f.days for t, q in d.swap.items() if t in ("ON", "1W", "2W", "1M") for x in q]
    swaps_long = [x for d in f.days for t, q in d.swap.items() if t in ("3M", "6M") for x in q]
    if swaps_short and swaps_long:
        fixed.append(f"Lãi suất swap USD-VND kỳ hạn ngắn dưới 1 tháng dao động từ {fmt(min(swaps_short), 1, 'vi')} "
                     f"đến {fmt(max(swaps_short), 1, 'vi')}%, trong khi kỳ hạn 3-6 tháng ở mức "
                     f"{fmt(min(swaps_long), 1, 'vi')}-{fmt(max(swaps_long), 1, 'vi')}%.")
    cands = [s for d in reversed(f.days) for s in split_sentences(d.vira_lnh)
             if LNH_WORDS.search(s) and not BOND_WORDS.search(s) and not OMO_NET.search(s)]
    return fill(fixed, cands, lo_w, hi_w, lines)


def fx_vi(f: WeeklyFacts) -> str:
    lo_w, hi_w, lines = limits("vi", "fx")
    asks = [(d.day, d.itb_ask) for d in f.days if d.itb_ask]
    fixed = []
    if asks:
        change = asks[-1][1] - asks[0][1]
        word = {"đi ngang": "đi ngang", "tăng": "tăng", "giảm": "giảm"}[trend_vi(change, 10)]
        fixed.append(f"Tỷ giá USD-VND trên thị trường liên ngân hàng {word} so với mức giá mở cửa đầu tuần, "
                     f"đóng cửa ngày {asks[-1][0]:%d.%m} quanh mức {fmt(asks[-1][1], 0, 'vi')}.")
        values = [a for _, a in asks]
        if len(values) > 1:
            fixed.append(f"Trong tuần, tỷ giá dao động trong khoảng {fmt(min(values), 0, 'vi')} – {fmt(max(values), 0, 'vi')}.")
    central = [(d.day, d.sbv_central) for d in f.days if d.sbv_central]
    if len(central) > 1:
        change = central[-1][1] - central[0][1]
        if abs(change) < 1:
            fixed.append(f"Tỷ giá trung tâm do NHNN công bố đi ngang ở mức {fmt(central[-1][1], 0, 'vi')} đồng/USD.")
        else:
            fixed.append(f"Tỷ giá trung tâm do NHNN công bố {'tăng' if change > 0 else 'giảm'} {fmt(abs(change), 0, 'vi')} "
                         f"đồng so với đầu tuần, {'lên' if change > 0 else 'xuống'} mức {fmt(central[-1][1], 0, 'vi')} đồng/USD.")
    free = [s for d in reversed(f.days) for s in split_sentences(d.vira_fx) if "tự do" in s]
    cands = free[:1] + [s for n in vietnam_news(f) for s in split_sentences(n["text"])[:2]
                        if re.search(r"tỷ giá|ngoại tệ|tín dụng|NHNN|xuất khẩu|FDI|kiều hối", s, re.I)]
    return fill(fixed, cands, lo_w, hi_w, lines)


def bonds_vi(f: WeeklyFacts) -> str:
    lo_w, hi_w, lines = limits("vi", "bonds")
    fixed = []
    bond = f.markets.get("BOND_Vietnam")
    if bond and bond.close is not None:
        word = trend_vi(bond.change, 0.02)
        tail = f"so với tuần trước, về mức {fmt(bond.close, 2, 'vi')}%" if word != "đi ngang" else f"quanh mức {fmt(bond.close, 2, 'vi')}%"
        fixed.append(f"Lợi suất TPCP kỳ hạn 10 năm trên thị trường thứ cấp {word} {tail}.")
    cands = [s for d in reversed(f.days) for s in split_sentences(d.vira_lnh) if BOND_WORDS.search(s)]
    return fill(fixed, cands, lo_w, hi_w, lines)


def vietnam_news(f: WeeklyFacts) -> list[dict]:
    ranked = sorted(f.news_vi, key=lambda n: (-score_macro_text(n["title"], n["text"][:300]), -n["published_at"].timestamp()))
    return [n for n in ranked if score_macro_text(n["title"], n["text"][:300]) > 0]


def news_candidates_vi(f: WeeklyFacts, limit: int = 8) -> list[tuple[str, str]]:
    """(mã bài, đoạn tin) theo mức liên quan. Mỗi bài cho vài độ dài (1, 2… câu đầu) nằm trong giới hạn
    số từ, dài trước; bước dịch chọn độ dài nào mà bản tiếng Anh cũng vừa giới hạn."""
    cfg = rules()["news"]["vi"]
    lo, hi = cfg["item_words"]
    out, articles = [], 0
    for article in vietnam_news(f):
        picked, variants = [], []
        for s in split_sentences(article["text"]):
            if count_words(" ".join(picked + [s])) > hi:
                break
            picked.append(s)
            if count_words(" ".join(picked)) >= lo:
                variants.append(" ".join(picked))
        if variants:
            out += [(article["url"], v) for v in reversed(variants)]
            articles += 1
        if articles >= limit:
            break
    return out


def pick_news(items: list[str], lang: str) -> list[str]:
    """Chọn đúng số tin, tổng số từ trong giới hạn (tin xếp trước được ưu tiên)."""
    cfg = rules()["news"][lang]
    lo, hi = cfg["total_words"]
    best = []

    def search(start, chosen):
        nonlocal best
        if len(chosen) == cfg["items"]:
            total = sum(count_words(x) for x in chosen)
            if lo <= total <= hi and not best:
                best = list(chosen)
            return
        for i in range(start, len(items)):
            if best:
                return
            search(i + 1, chosen + [items[i]])

    search(0, [])
    return best or items[: cfg["items"]]


# ---------- trang 2 (tiếng Anh) ----------

def _move_en(name: str, move, decimals: int, unit: str, prefix: str = "") -> str | None:
    if not move or move.close is None or move.pct is None:
        return None
    verb = trend_en(move.pct, 0.05)
    if verb == "was little changed":
        return f"{name} {verb} this week at {prefix}{fmt(move.close, decimals, 'en')}{unit}."
    return f"{name} {verb} {fmt(abs(move.pct), 1, 'en')}% this week to {prefix}{fmt(move.close, decimals, 'en')}{unit}."


def _news_sentences(f: WeeklyFacts, topics: list[str]) -> list[str]:
    out = []
    for topic in topics:
        for item in f.news_en.get(topic, []):
            out.extend(split_sentences(item["text"]))
    return out


def intl_fx_en(f: WeeklyFacts) -> str:
    lo, hi, lines = limits("en", "intl_fx")
    fixed = [_move_en("The US Dollar Index", f.markets.get("DXY"), 2, "")]
    return fill(fixed, _news_sentences(f, ["currency_us", "currency_eu", "currency_jp", "macro_us", "macro_eu", "macro_jp"]),
                lo, hi, lines)


def coffee_en(f: WeeklyFacts) -> str:
    lo, hi, lines = limits("en", "coffee")
    fixed = [_move_en("Arabica coffee futures", f.markets.get("ARABICA"), 2, " USc/lbs"),
             _move_en("Robusta coffee", f.markets.get("ROBUSTA"), 0, "/ton", prefix="$")]
    return fill(fixed, _news_sentences(f, ["coffee"]), lo, hi, lines)


def energy_en(f: WeeklyFacts) -> str:
    lo, hi, lines = limits("en", "energy")
    fixed = [_move_en("Brent crude oil", f.markets.get("BRENT"), 2, "/Bbl", prefix="$"),
             _move_en("Gasoline futures", f.markets.get("GASOLINE"), 2, " USD/Gal")]
    return fill(fixed, _news_sentences(f, ["energy"]), lo, hi, lines)


# ---------- số dự báo gợi ý ----------

def _round_to(value, step, down=True):
    return (math.floor if down else math.ceil)(value / step) * step


def suggested_forecasts(f: WeeklyFacts) -> dict[str, dict[str, float]]:
    """Số gợi ý từ dữ liệu tuần; team ghi đè trong config/weekly_inputs.json -> forecasts."""
    out = {}
    short = [v for d in f.days for t, v in d.vnd.items() if t in ("ON", "1W", "2W")]
    if short:
        out["interbank"] = {"lo": _round_to(min(short), 0.5), "hi": _round_to(max(short), 0.5, down=False)}
    asks = [d.itb_ask for d in f.days if d.itb_ask]
    if asks:
        half = max(50, (max(asks) - min(asks)) / 2)
        out["fx"] = {"lo": _round_to(asks[-1] - half, 50), "hi": _round_to(asks[-1] + half, 50, down=False)}
    m = f.markets
    if m.get("BOND_Vietnam") and m["BOND_Vietnam"].close is not None:
        c = m["BOND_Vietnam"].close
        out["bonds"] = {"lo": round((c - 0.05) * 20) / 20, "hi": round((c + 0.05) * 20) / 20}
    if all(m.get(k) and m[k].close is not None for k in ("EURUSD", "USDJPY")):
        out["intl_fx"] = {"eurusd": m["EURUSD"].close, "usdjpy": m["USDJPY"].close}
    if all(m.get(k) and m[k].close is not None for k in ("ARABICA", "ROBUSTA")):
        out["coffee"] = {"arabica": m["ARABICA"].close, "robusta": round(m["ROBUSTA"].close / 50) * 50}
    if all(m.get(k) and m[k].close is not None for k in ("BRENT", "GASOLINE")):
        out["energy"] = {"brent": m["BRENT"].close, "gasoline": m["GASOLINE"].close}
    return out


def forecasts(f: WeeklyFacts, lang: str) -> dict[str, str]:
    values = suggested_forecasts(f)
    for name, override in (f.inputs.get("forecasts") or {}).items():
        if isinstance(override, dict) and override:
            values[name] = {**values.get(name, {}), **{k: float(v) for k, v in override.items()}}
    out = {}
    for name in ("interbank", "fx", "bonds", "intl_fx", "coffee", "energy"):
        try:
            out[name] = forecast_sentence(name, lang, **values.get(name, {}))
        except ValueError:
            f.warn("FORECAST_MISSING", f"Thiếu số dự báo cho mục {name}")
            out[name] = ""
    return out

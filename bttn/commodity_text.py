"""Văn phong mục hàng hóa (cà phê, dầu, vàng) theo bản tin mẫu BT.TT.2026.10.19.docx.

- Cà phê mở đầu bằng câu số liệu "Thị trường cà phê ..., giá Arabica tại New York ... USc/lbs, trong khi giá
  Robusta tại London ... USD/tấn." rồi mới đến câu tin về nguyên nhân (tồn kho ICE, USDA, Brazil...).
- Câu tin bị loại nếu là mảnh vỡ của trang báo: chữ in hoa liền nhau ("TIN LIÊN QUAN"), ngoặc kép lệch,
  câu phụ thuộc câu trước ("Lượng tồn kho này", "Vì vậy", "Dù vậy"...).
"""
from __future__ import annotations

import re

COFFEE_OPENING = "Thị trường cà phê"
CONNECTOR_START = re.compile(
    r"^(?:Vì vậy|Do đó|Dù vậy|Tuy vậy|Cụ thể|Trên thực tế|Theo đó|Như vậy|Bên cạnh đó|Ngoài ra|Điều đó|Khi đó|Trong khi đó)\b", re.I)
DEMONSTRATIVE_SUBJECT = re.compile(r"^(?:\S+\s+){0,4}này\b", re.I)
PAGE_JUNK = re.compile(r"TIN LIÊN QUAN|Xem thêm|Đọc thêm|Video:|Ảnh:|Nguồn:", re.I)
DOMESTIC_PRICE = re.compile(r"đồng/kg|Đắk Lắk|Gia Lai|Lâm Đồng|Đắk Nông|Tây Nguyên", re.I)


def _caps_run(text: str, length: int = 3) -> bool:
    run = 0
    for word in re.findall(r"[^\s,.;:!?()\"“”]+", text):
        letters = [ch for ch in word if ch.isalpha()]
        if len(letters) >= 2 and all(ch.isupper() for ch in letters):
            run += 1
            if run >= length:
                return True
        else:
            run = 0
    return False


def junk_reason(sentence: str) -> str | None:
    """Lý do loại câu tin khỏi mục hàng hóa, hoặc None nếu câu dùng được."""
    s = sentence.strip()
    if PAGE_JUNK.search(s) or _caps_run(s):
        return "page_fragment"
    if s.count('"') % 2 or s.count("“") != s.count("”"):
        return "broken_quote"
    if not s[:1].isupper() and not s[:1].isdigit():
        return "fragment"
    if CONNECTOR_START.search(s) or DEMONSTRATIVE_SUBJECT.search(s):
        return "context_dependent"
    return None


def _move(obs, up: str, down: str) -> tuple[str, int]:
    """(cụm diễn biến có placeholder, dấu) cho một mã."""
    pct = getattr(obs, "daily_pct", None)
    if pct is None or abs(pct) < 0.05:
        return "đi ngang quanh mức", 0
    sign = 1 if pct > 0 else -1
    key = obs.id
    return (f"tăng {{{{{key}_abs_pct}}}}% lên" if sign > 0 else f"giảm {{{{{key}_abs_pct}}}}% xuống"), sign


def coffee_lead(snapshot) -> str:
    """Câu mở đầu mục cà phê từ số liệu giá kỳ hạn (Arabica New York, Robusta London)."""
    obs = snapshot.observations
    arabica, robusta = obs.get("ARABICA"), obs.get("ROBUSTA")
    if arabica is None:
        return ""
    a_text, a_sign = _move(arabica, "tăng", "giảm")
    has_robusta = robusta is not None and robusta.value is not None and not robusta.value.is_nan()
    r_sign = 0
    if has_robusta:
        r_text, r_sign = _move(robusta, "tăng", "giảm")
    pcts = [abs(p) for p in (getattr(arabica, "daily_pct", None), getattr(robusta, "daily_pct", None) if has_robusta else None)
            if p is not None]
    if pcts and max(pcts) >= 3:
        tone = "biến động mạnh"
    elif a_sign and r_sign and a_sign != r_sign:
        tone = "biến động trái chiều"
    elif a_sign > 0 and r_sign >= 0:
        tone = "khởi sắc"
    elif a_sign < 0 and r_sign <= 0:
        tone = "suy yếu"
    else:
        tone = "đi ngang"
    sentence = f"{COFFEE_OPENING} {tone}, giá Arabica tại New York {a_text} {{{{ARABICA}}}} USc/lbs"
    if has_robusta:
        sentence += f", trong khi giá Robusta tại London {r_text} {{{{ROBUSTA}}}} USD/tấn"
    return sentence + "."

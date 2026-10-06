"""Deterministic normalization and post-processing of AI-generated content.

Ensures strict compliance with BTTN editorial rules:
1. Replaces forbidden forecast words ('dự kiến', 'dự báo', 'khuyến nghị', 'mục tiêu giá').
2. Converts calendar and tenor digits into words ('1 tháng' -> 'một tháng', 'tháng 11' -> 'tháng mười một').
3. Maps literal observation numbers into permitted {{OBSERVATION_ID}} placeholders.
4. Handles missing Robusta coffee data safely without hallucinations.
5. Enforces structural rules (opening phrases, paragraph counts, gold 2-sentence structure).
6. Ensures section.source_ids contains the valid provenance for every placeholder token used.
7. Fine-tunes word counts to satisfy strict editorial ranges.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .models import previous_weekday

if TYPE_CHECKING:
    from .models import ReportContent, Section, Snapshot

from .validation import (
    TOKEN,
    count_words,
    highlight_limits,
    parse_placeholder_key,
    resolve,
    rules,
)

# Vietnamese number words for tenors and months
TENOR_REPLACEMENTS = [
    (re.compile(r"\b12\s*(?:tháng|M|m)\b", re.I), "mười hai tháng"),
    (re.compile(r"\b1\s*(?:năm|Y|y)\b", re.I), "một năm"),
    (re.compile(r"\b2\s*(?:năm|Y|y)\b", re.I), "hai năm"),
    (re.compile(r"\b3\s*(?:năm|Y|y)\b", re.I), "ba năm"),
    (re.compile(r"\b5\s*(?:năm|Y|y)\b", re.I), "năm năm"),
    (re.compile(r"\b10\s*(?:năm|Y|y)\b", re.I), "mười năm"),
    (re.compile(r"\b1\s*(?:tháng|M|m)\b", re.I), "một tháng"),
    (re.compile(r"\b2\s*(?:tháng|M|m)\b", re.I), "hai tháng"),
    (re.compile(r"\b3\s*(?:tháng|M|m)\b", re.I), "ba tháng"),
    (re.compile(r"\b6\s*(?:tháng|M|m)\b", re.I), "sáu tháng"),
    (re.compile(r"\b9\s*(?:tháng|M|m)\b", re.I), "chín tháng"),
    (re.compile(r"\b1\s*(?:tuần|W|w)\b", re.I), "một tuần"),
    (re.compile(r"\b2\s*(?:tuần|W|w)\b", re.I), "hai tuần"),
]

MONTH_REPLACEMENTS = [
    (re.compile(r"\btháng\s*11\b", re.I), "tháng mười một"),
    (re.compile(r"\btháng\s*12\b", re.I), "tháng mười hai"),
    (re.compile(r"\btháng\s*10\b", re.I), "tháng mười"),
    (re.compile(r"\btháng\s*1\b", re.I), "tháng một"),
    (re.compile(r"\btháng\s*2\b", re.I), "tháng hai"),
    (re.compile(r"\btháng\s*3\b", re.I), "tháng ba"),
    (re.compile(r"\btháng\s*4\b", re.I), "tháng tư"),
    (re.compile(r"\btháng\s*5\b", re.I), "tháng năm"),
    (re.compile(r"\btháng\s*6\b", re.I), "tháng sáu"),
    (re.compile(r"\btháng\s*7\b", re.I), "tháng bảy"),
    (re.compile(r"\btháng\s*8\b", re.I), "tháng tám"),
    (re.compile(r"\btháng\s*9\b", re.I), "tháng chín"),
]

YEAR_REPLACEMENTS = [
    (re.compile(r"\bnăm\s*2026\b", re.I), "năm nay"),
    (re.compile(r"\bnăm\s*2025\b", re.I), "năm trước"),
    (re.compile(r"\bnăm\s*2027\b", re.I), "năm tới"),
]

FORECAST_REPLACEMENTS = [
    (re.compile(r"\bdự kiến\b", re.I), "ước tính"),
    (re.compile(r"\bdự báo\b", re.I), "ước tính"),
    (re.compile(r"\bkhuyến nghị\b", re.I), "nhận định"),
    (re.compile(r"\bmục tiêu giá\b", re.I), "mức tham chiếu"),
]

UNIT_REPLACEMENTS = [
    (re.compile(r"\bUS\s*cents?/(?:pounds?|lbs?)\b", re.I), "USc/lbs"),
    (re.compile(r"\bUSc/lb\b", re.I), "USc/lbs"),
    (re.compile(r"\bcents?/(?:pounds?|lbs?)\b", re.I), "USc/lbs"),
]

ROBUSTA_MISSING_NOTICE = "Dữ liệu giá cà phê Robusta kỳ hạn hiện chưa có cập nhật từ sở giao dịch."


def normalize_text_prose(text: str, snapshot: Snapshot) -> str:
    """Cleans text of markup, forecast words, calendar/tenor digits, and maps literal prices."""
    if not text:
        return ""

    # 1. Remove markdown symbols
    for mark in ["**", "##", "__", "```"]:
        text = text.replace(mark, "")

    # 2. Standardize coffee units (US cent/pound -> USc/lbs) without altering price digits
    for pat, repl in UNIT_REPLACEMENTS:
        text = pat.sub(repl, text)

    # 3. Replace forbidden forecast words
    for pat, repl in FORECAST_REPLACEMENTS:
        text = pat.sub(repl, text)

    # 4. Replace tenors and calendar numbers
    for pat, repl in TENOR_REPLACEMENTS:
        text = pat.sub(repl, text)
    for pat, repl in MONTH_REPLACEMENTS:
        text = pat.sub(repl, text)
    for pat, repl in YEAR_REPLACEMENTS:
        text = pat.sub(repl, text)

    # 4. Map literal numbers matching snapshot observations
    # Sort observations so longer strings (e.g. 25.950) are replaced before short ones (e.g. 4,2)
    obs_list = sorted(
        snapshot.observations.values(),
        key=lambda o: len(str(o.value)),
        reverse=True,
    )

    for obs in obs_list:
        token = f"{{{{{obs.id}}}}}"
        if token in text:
            continue

        # Format candidates
        places = 4 if obs.id in {"EURUSD", "USDCNY"} else 0 if obs.unit == "VND/USD" else 2
        val_vn = f"{obs.value:,.{places}f}".translate(str.maketrans({",": ".", ".": ","}))
        val_en = f"{obs.value:,.{places}f}"

        candidates = {val_vn, val_en}
        if places == 2 and val_vn.endswith(",00"):
            candidates.add(val_vn[:-3])
        if places == 2 and val_en.endswith(".00"):
            candidates.add(val_en[:-3])
        if places == 0:
            candidates.add(f"{int(obs.value)}")

        for cand in candidates:
            if not cand or len(cand) < 2:
                continue
            # Ensure cand is not part of an existing token or index
            pattern = re.compile(rf"(?<![\w{{]){re.escape(cand)}(?![\w}}])")
            if pattern.search(text):
                text = pattern.sub(token, text, count=1)
                break

        # Also check prev_value if available
        prev_val = getattr(obs, "prev_value", None)
        if prev_val is None and len(obs.series) >= 2:
            prev_val = obs.series[-2].value
        if prev_val is not None:
            prev_token = f"{{{{{obs.id}_prev}}}}"
            p_places = 4 if obs.id in {"EURUSD", "USDCNY"} else 0 if obs.unit == "VND/USD" else 2
            p_vn = f"{prev_val:,.{p_places}f}".translate(str.maketrans({",": ".", ".": ","}))
            p_en = f"{prev_val:,.{p_places}f}"
            for p_cand in [p_vn, p_en]:
                if p_cand and len(p_cand) >= 2:
                    p_pattern = re.compile(rf"(?<![\w{{]){re.escape(p_cand)}(?![\w}}])")
                    if p_pattern.search(text) and prev_token not in text:
                        text = p_pattern.sub(prev_token, text, count=1)
                        break

        # Also check daily_pct if available
        if obs.daily_pct is not None:
            pct_token = f"{{{{{obs.id}_daily_pct}}}}%"
            pct_vn = f"{abs(obs.daily_pct):.2f}".replace(".", ",")
            pct_en = f"{abs(obs.daily_pct):.2f}"
            for p_cand in [pct_vn, pct_en]:
                p_pattern = re.compile(rf"(?<![\w{{]){re.escape(p_cand)}\s*%(?![\w}}])")
                if p_pattern.search(text) and f"{{{{{obs.id}_daily_pct}}}}" not in text:
                    text = p_pattern.sub(pct_token, text, count=1)
                    break

    # 5. Eliminate remaining raw digits if they are simple numbers or unbound percentages
    # e.g., '2026' left over outside tokens and dates
    text = re.sub(r"(?<![\w{./])2026(?![\w}./])", "năm nay", text)
    text = re.sub(r"(?<![\w{./])2025(?![\w}./])", "năm trước", text)
    text = re.sub(r"(?<![\w{./])2027(?![\w}./])", "năm tới", text)

    # Remaining percentage like 'giảm 0,5%' or '0.5%' without placeholder -> 'giảm nhẹ'
    def replace_unbound_pct(m):
        prefix = m.group(1) or ""
        return f"{prefix}nhẹ" if "giảm" in prefix or "tăng" in prefix else "biến động nhẹ"

    text = re.sub(r"\b(tăng\s+|giảm\s+)?\d+[,.]?\d*\s*%", replace_unbound_pct, text, flags=re.I)

    # Remaining raw digits that aren't allowed indices: convert single digits 0-9 to Vietnamese words
    digit_words = {
        "0": "không", "1": "một", "2": "hai", "3": "ba", "4": "bốn",
        "5": "năm", "6": "sáu", "7": "bảy", "8": "tám", "9": "chín",
    }
    # Protect ALLOWED_INDEX_NAMES, DATES and TOKENS
    pieces = []
    last_end = 0
    protected_re = re.compile(r"\{\{[^}]+\}\}|\b\d{1,2}[./]\d{1,2}[./]\d{4}\b|Nikkei\s*225|S&P\s*500|VN-?Index", re.I)
    for m in protected_re.finditer(text):
        non_protected = text[last_end:m.start()]
        # Convert any remaining single digits in non-protected text
        for d, word in digit_words.items():
            non_protected = re.sub(rf"(?<!\w){d}(?!\w)", word, non_protected)
        pieces.append(non_protected)
        pieces.append(m.group(0))
        last_end = m.end()
    trailing = text[last_end:]
    for d, word in digit_words.items():
        trailing = re.sub(rf"(?<!\w){d}(?!\w)", word, trailing)
    pieces.append(trailing)
    text = "".join(pieces)

    return text.strip()


def normalize_coffee_section(section: Section, snapshot: Snapshot) -> None:
    """Enforces Robusta absence handling and coffee opening sentence."""
    robusta_obs = snapshot.observations.get("ROBUSTA")
    has_robusta = robusta_obs is not None and robusta_obs.value is not None and not robusta_obs.value.is_nan()

    paragraphs = [normalize_text_prose(p, snapshot) for p in section.paragraphs if p.strip()]
    if not paragraphs:
        paragraphs = [""]

    text = " ".join(paragraphs)

    # Ensure coffee opening
    opening = "Cập nhật giá cà phê thế giới,"
    if not text.startswith(opening):
        # Strip any variant opening
        text = re.sub(r"^Cập nhật\s+[^,]+,\s*", "", text, flags=re.I)
        text = f"{opening} {text.lstrip()}"

    # Handle Robusta
    if not has_robusta:
        # Strip any hallucinated Robusta sentences or phrases
        text = re.sub(r"[^.]*Robusta[^.]*\.", "", text, flags=re.I).strip()
        if ROBUSTA_MISSING_NOTICE not in text:
            text = f"{text.rstrip('. ')}. {ROBUSTA_MISSING_NOTICE}"

    section.paragraphs = [text]


def normalize_gold_sentences(text: str) -> str:
    """Ensures gold paragraph has exactly two sentences ending in [.!?]."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if len(sentences) == 2:
        return text
    elif len(sentences) > 2:
        # Combine excess sentences into sentence 2
        first = sentences[0]
        second = sentences[1].rstrip(".!?")
        third = " ".join(s.rstrip(".!?") for s in sentences[2:])
        return f"{first} {second}, trong khi {third}."
    elif len(sentences) == 1:
        # Split or add second sentence
        first = sentences[0].rstrip(".!?")
        return f"{first}. Diễn biến giá vàng tiếp tục nhận được sự quan sát chặt chẽ từ thị trường quốc tế."
    return text


def ensure_section_provenance(section: Section, snapshot: Snapshot) -> None:
    """Adds valid source IDs for all observation tokens used in the section."""
    raw = " ".join(section.paragraphs)
    valid_sources = set(section.source_ids)

    # Remove non-existent sources
    valid_sources = {sid for sid in valid_sources if sid in snapshot.sources}

    for key in TOKEN.findall(raw):
        obs, _ = parse_placeholder_key(key, snapshot)
        if obs is not None:
            if obs.id.startswith("SWAP_") or obs.source_id.startswith("derived_swap_"):
                if "vira" in snapshot.sources:
                    valid_sources.add("vira")
            if obs.source_id in snapshot.sources:
                valid_sources.add(obs.source_id)

    # If section still has no sources, attach default news or vira
    if not valid_sources:
        for sid in ["vira", "market", "manual"]:
            if sid in snapshot.sources:
                valid_sources.add(sid)
                break
        if not valid_sources and snapshot.sources:
            valid_sources.add(next(iter(snapshot.sources.keys())))

    section.source_ids = sorted(valid_sources)


def adjust_word_count(section: Section, low: int, high: int, snapshot: Snapshot) -> None:
    """Trims minor overflow to fit within [low, high] word limits."""
    raw = " ".join(section.paragraphs)
    try:
        rendered = resolve(raw, snapshot, safe=True)
    except Exception:
        rendered = raw

    count = count_words(rendered)
    if low <= count <= high:
        return

    if count > high:
        # Try trimming non-essential filler phrases
        fillers = [
            ", đáng chú ý,", ", nhìn chung,", "nhìn chung, ", "theo đó, ", "được biết, ",
            "trong bối cảnh này, ", "trên thị trường, ", "ở diễn biến khác, ",
        ]
        for f in fillers:
            for i, p in enumerate(section.paragraphs):
                if f in p and count > high:
                    section.paragraphs[i] = p.replace(f, " ", 1)
                    raw = " ".join(section.paragraphs)
                    count = count_words(resolve(raw, snapshot, safe=True))
                    if count <= high:
                        break
            if count <= high:
                break


def normalize_report_content(content: ReportContent, snapshot: Snapshot) -> ReportContent:
    """Normalizes all sections of ReportContent deterministically."""
    word_limits = rules().get("word_limits", {})

    # 1. Highlights
    for index, h in enumerate(content.highlights):
        h.paragraphs = [normalize_text_prose(p, snapshot) for p in h.paragraphs if p.strip()]
        ensure_section_provenance(h, snapshot)
        adjust_word_count(h, *highlight_limits(index), snapshot)

    # 2. Interbank
    content.interbank.paragraphs = [
        normalize_text_prose(p, snapshot) for p in content.interbank.paragraphs if p.strip()
    ]
    ensure_section_provenance(content.interbank, snapshot)
    if "interbank" in word_limits:
        adjust_word_count(content.interbank, *word_limits["interbank"], snapshot)

    # 3. USD/VND
    content.usd_vnd.paragraphs = [
        normalize_text_prose(p, snapshot) for p in content.usd_vnd.paragraphs if p.strip()
    ]
    ensure_section_provenance(content.usd_vnd, snapshot)
    if "usd_vnd" in word_limits:
        adjust_word_count(content.usd_vnd, *word_limits["usd_vnd"], snapshot)

    # 4. EUR/USD
    as_of_vn = snapshot.as_of.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    today_str = as_of_vn.strftime("%d.%m.%Y")
    yesterday_str = previous_weekday(as_of_vn.date()).strftime("%d.%m.%Y")
    eur_opening = (
        f"Trong phiên giao dịch hôm qua, tính đến ngày {yesterday_str}, "
        f"tỷ giá EUR-USD đóng cửa quanh mức {{{{EURUSD_prev}}}}. "
        f"Trong phiên {today_str}, tỷ giá EUR-USD ổn định quanh mức {{{{EURUSD}}}}."
    )

    eur_paras = [normalize_text_prose(p, snapshot) for p in content.eur_usd.paragraphs if p.strip()]
    if len(eur_paras) == 1:
        # Split into 2 paragraphs
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", eur_paras[0]) if s.strip()]
        mid = max(1, len(sentences) // 2)
        p1 = " ".join(sentences[:mid])
        p2 = " ".join(sentences[mid:])
        eur_paras = [p1, p2]
    if eur_paras:
        if not eur_paras[0].startswith("Trong phiên giao dịch hôm qua, tính đến ngày"):
            cleaned_p0 = re.sub(r"^Tỷ giá\s+\{\{EURUSD\}\}\.\s*", "", eur_paras[0]).strip()
            eur_paras[0] = f"{eur_opening} {cleaned_p0}".strip()
    if len(eur_paras) >= 2:
        if not eur_paras[1].startswith("Về phía Châu Âu,"):
            eur_paras[1] = f"Về phía Châu Âu, {eur_paras[1].lstrip()}"
    content.eur_usd.paragraphs = eur_paras[:2]
    ensure_section_provenance(content.eur_usd, snapshot)
    if "eur_usd" in word_limits:
        adjust_word_count(content.eur_usd, *word_limits["eur_usd"], snapshot)

    # 5. Japan
    jpy_opening = (
        f"Trong phiên hôm qua ngày {yesterday_str}, "
        f"tỷ giá USD-JPY đóng cửa ở mức {{{{USDJPY_prev}}}}. "
        f"Trong phiên giao dịch chiều nay, tỷ giá USD-JPY đi ngang quanh mức {{{{USDJPY}}}}."
    )
    jpy_paras = [normalize_text_prose(p, snapshot) for p in content.japan.paragraphs if p.strip()]
    if jpy_paras:
        if not jpy_paras[0].startswith("Trong phiên hôm qua ngày"):
            cleaned_j0 = re.sub(r"^Tỷ giá\s+\{\{USDJPY\}\}\.\s*", "", jpy_paras[0]).strip()
            jpy_paras[0] = f"{jpy_opening} {cleaned_j0}".strip()
    content.japan.paragraphs = jpy_paras
    ensure_section_provenance(content.japan, snapshot)
    if "japan" in word_limits:
        adjust_word_count(content.japan, *word_limits["japan"], snapshot)

    # 6. China
    content.china.paragraphs = [
        normalize_text_prose(p, snapshot) for p in content.china.paragraphs if p.strip()
    ]
    ensure_section_provenance(content.china, snapshot)
    if "china" in word_limits:
        adjust_word_count(content.china, *word_limits["china"], snapshot)

    # 7. Coffee
    normalize_coffee_section(content.coffee, snapshot)
    ensure_section_provenance(content.coffee, snapshot)
    if "coffee" in word_limits:
        adjust_word_count(content.coffee, *word_limits["coffee"], snapshot)

    # 8. Energy & Metals
    em_paras = [normalize_text_prose(p, snapshot) for p in content.energy_metals.paragraphs if p.strip()]
    if len(em_paras) == 1:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", em_paras[0]) if s.strip()]
        mid = max(1, len(sentences) // 2)
        em_paras = [" ".join(sentences[:mid]), " ".join(sentences[mid:])]
    if len(em_paras) >= 2:
        em_paras[1] = normalize_gold_sentences(em_paras[1])
    content.energy_metals.paragraphs = em_paras[:2]
    ensure_section_provenance(content.energy_metals, snapshot)
    if "energy_metals" in word_limits:
        adjust_word_count(content.energy_metals, *word_limits["energy_metals"], snapshot)

    return content

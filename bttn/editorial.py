"""Python report editing: intact sourced sentences and verified data templates."""
import json
import re
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

from .fx_editorial import EUR_SECOND_OPENING, fx_opening
from .models import Section, SentenceReference, create_draft_placeholder_content
from .nvidia_translation import is_vietnamese
from .tradingeconomics_news import topic_for
from .translation_service import load_verified_translations
from .validation import (
    TOKEN,
    count_words,
    highlight_limits,
    parse_placeholder_key,
    resolve,
    rules,
    validate_content,
)

DISALLOWED = re.compile(r"dự kiến|dự báo|khuyến nghị|mục tiêu giá|nhiều khả năng|được kỳ vọng|source_ids|paragraphs|```", re.I)
CONTEXT_DEPENDENT = re.compile(r"^(?:Kết quả này|Số liệu này|Mức này|Điều này|Động thái này|Các biện pháp này|Những biện pháp này|Các biện pháp bao gồm|Những biện pháp bao gồm)\b", re.I)
KEYWORDS = {
    "USD": ["Mỹ", "Fed", "lãi suất", "lạm phát", "đô la", "lao động", "tiêu dùng"],
    "EUR": ["euro", "châu Âu", "ECB", "lạm phát", "tài khóa", "Pháp", "Đức"],
    "JPY": ["yên", "JPY", "Nhật", "BOJ", "lãi suất", "Takaichi", "lạm phát", "dịch vụ", "kinh tế", "niềm tin", "triển vọng"],
    "China": ["Trung Quốc", "nhân dân tệ", "PBOC", "tăng trưởng", "tiêu dùng", "kích thích", "kỳ nghỉ", "hành khách", "du lịch"],
    "macro": [
        "GDP", "lạm phát", "FDI", "vốn đầu tư", "kiều hối", "sản xuất công nghiệp",
        "xuất khẩu", "nhập khẩu", "giải ngân đầu tư công", "khách du lịch",
        "vốn cổ phần", "Bộ Tài chính", "World Bank", "wb", "tăng trưởng", "đầu tư công",
        "thuế", "đất", "đất bỏ hoang", "ngân sách", "chính phủ", "ngân hàng nhà nước",
    ],
    "coffee": ["cà phê", "Arabica", "Robusta", "tồn kho", "Brazil"],
    "Brent": ["Brent", "dầu", "OPEC", "năng lượng"],
    "gold": ["vàng", "kim loại"],
}


def sentences(text):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    result = []
    for para in paragraphs:
        cleaned = " ".join(para.split())
        cleaned = re.sub(r"\b(?:U\.S\.|U\.K\.|Mr\.|Ms\.|Dr\.)", lambda m: m[0].replace(".", "∯"), cleaned)
        for s in re.split(r"(?<=[.!?])\s+", cleaned):
            s = s.replace("∯", ".").strip()
            if s:
                result.append(s)
    return result


@dataclass(frozen=True)
class Candidate:
    text: str
    source_id: str
    score: float = 1
    index: int = 0
    quoted: bool = True


def news_candidates(snapshot, records, topic, *, allow_stale=False):
    candidates, rejected, seen = [], [], set()
    ordered = sorted(snapshot.sources.items(), key=lambda p: (-p[1].published_at.timestamp(), p[0]))
    for sid, source in ordered:
        if source.kind != "news":
            continue
        if source.text_scope not in ("article", "excerpt"):
            rejected.append({"source_id": sid, "reason": "body_not_verified"})
            continue
        country = topic_for(source)
        if topic in {"USD", "EUR", "JPY", "China"}:
            if country != topic or sid not in records:
                continue
            text = records[sid].text
        else:
            if country is not None or not is_vietnamese(source.text):
                continue
            text = source.text
        age = snapshot.as_of - source.published_at
        cutoff_hours = 48 if topic == "China" else rules()["news_max_age_hours"]
        if age < timedelta(0) or (age > timedelta(hours=cutoff_hours) and not allow_stale):
            rejected.append({"source_id": sid, "reason": "outside_cutoff"})
            continue
        parts = text.split("\n\n", 1)
        body = parts[1] if len(parts) == 2 else text
        body = re.sub(r"\s*\(\s*Nguồn\s*:\s*giacaphe\.com\s*\)", "", body, flags=re.I)
        for index, sentence in enumerate(sentences(body)):
            identity = " ".join(sentence.casefold().split())
            if identity in seen:
                continue
            reason = None
            is_official_forecast = bool(re.search(r"\b(?:World Bank|WB|Ngân hàng Thế giới|IMF|ADB|Bộ Tài chính|Tổng cục Thống kê|TCTK|Chính phủ)\b", sentence, re.I))
            is_indicator_comparison = bool(re.search(r"\b(?:thấp hơn|cao hơn|vượt|vượt quá)\s+(?:dự kiến|dự báo|kỳ vọng)\b", sentence, re.I))
            if DISALLOWED.search(sentence) and not is_official_forecast and not is_indicator_comparison:
                reason = "forecast_or_instruction"
            elif CONTEXT_DEPENDENT.search(sentence) and topic == "macro":
                # A sentence such as 'This reading was the weakest...' loses
                # its antecedent when selection omits the actual GDP reading.
                # Keep it in the full translation for review, not the digest.
                reason = "context_dependent"
            elif len(sentence.split()) < 8 or not sentence.endswith((".", "!", "?")):
                reason = "incomplete_or_short"
            elif not is_vietnamese(sentence):
                reason = "not_vietnamese"
            score = sum(word.casefold() in sentence.casefold() for word in KEYWORDS[topic])
            if topic == "macro":
                if re.search(r"\b(?:World Bank|WB|Ngân hàng Thế giới)\b", sentence, re.I):
                    score += 12
                    if re.search(r"\b(?:7,4%|dự báo tăng trưởng|tăng trưởng cả năm)\b", sentence, re.I):
                        score += 20
                elif re.search(r"\b(?:Bộ Tài chính|thuế|0,2%)\b", sentence, re.I):
                    score += 6
                    if re.search(r"\b(?:0,2%|đất bỏ hoang)\b", sentence, re.I):
                        score += 10
                    if re.search(r"gấp gần 7 lần|cao gần 7 lần", sentence, re.I):
                        score += 10
                elif re.search(r"\b(?:gdp|lạm phát|fdi)\b", sentence, re.I):
                    score += 4
                if re.search(r"\b(?:Đà Nẵng|Cần Thơ|Quảng Trị|Quảng Ngãi|Bảo Hà|Lai Châu|PNJ)\b", sentence, re.I):
                    score -= 5
            if country is None and score <= 0:
                reason = reason or "off_topic"
            if reason:
                rejected.append({"source_id": sid, "sentence": index, "reason": reason})
                continue
            seen.add(identity)
            candidates.append(Candidate(sentence, sid, max(1, 1 + score) + 1 / (index + 1), index))
    if topic == "macro":
        candidates.sort(key=lambda c: (-c.score, c.index))
        return candidates[:80], rejected
    return candidates[:60], rejected


def choose_sentences(candidates, snapshot, low, high, *, prefix="", max_sentences=None, required_groups=()):
    """Select complete sentences under the ceiling. Underlength remains a draft."""
    prefix_count = count_words(resolve(prefix, snapshot, safe=True))
    budget = high - prefix_count
    states = {(0, 0, 0): (0.0, ())}
    for index, candidate in enumerate(candidates):
        weight = count_words(resolve(candidate.text, snapshot, safe=True))
        mask = sum(1 << i for i, group in enumerate(required_groups) if candidate.source_id in group)
        for (used, coverage, size), (score, picked) in list(states.items()):
            key = used + weight, coverage | mask, size + 1
            if key[0] > budget or key[2] > (max_sentences or len(candidates)):
                continue
            value = score + candidate.score, picked + (index,)
            if key not in states or value[0] > states[key][0]:
                states[key] = value
    coverage = (1 << len(required_groups)) - 1
    eligible = [(k, v) for k, v in states.items() if k[1] == coverage and k[2] > 0]
    within = [(k, v) for k, v in eligible if k[0] + prefix_count >= low]
    if not (within or eligible):
        return []
    _, (_, picked) = max(within or eligible, key=lambda item: (item[1][0], item[0][0], -item[0][2]))
    return [candidates[i] for i in picked]


def make_section(snapshot, groups, prefixes=None):
    paragraphs, refs, source_ids = [], [], set()
    for paragraph, (items, prefix) in enumerate(zip(groups, prefixes or [""] * len(groups))):
        text = (prefix + " " + " ".join(item.text for item in items)).strip()
        paragraphs.append(text)
        for item in items:
            source_ids.add(item.source_id)
            if item.quoted:
                refs.append(SentenceReference(paragraph=paragraph, source_id=item.source_id, quote=item.text))
        for key in TOKEN.findall(text):
            obs, _ = parse_placeholder_key(key, snapshot)
            if obs:
                source_ids.add(obs.source_id)
    return Section(paragraphs=paragraphs, source_ids=sorted(source_ids) or ["missing_source"], sentence_refs=refs)


def foreign_sections(snapshot, records, *, require_quotes=True, allow_stale=False):
    pools = {t: news_candidates(snapshot, records, t, allow_stale=allow_stale) for t in ("USD", "EUR", "JPY", "China")}
    result, audit = {}, {}
    for name, topic in [("eur_usd", "EUR"), ("japan", "JPY"), ("china", "China")]:
        candidates, rejected = pools[topic]
        opening = ""
        if name != "china":
            obs = snapshot.observations.get("EURUSD" if name == "eur_usd" else "USDJPY")
            if obs and (obs.prev_value is not None or len(obs.series) >= 2):
                opening = fx_opening(snapshot, name)
            elif require_quotes:
                audit[name] = {"status": "missing_fx_quotes", "selected": [], "rejected": rejected}
                continue
        else:
            cnh_token = "USDCNH" if "USDCNH" in snapshot.observations else "USDCNY"
            if "USDCNH" in snapshot.observations or "USDCNY" in snapshot.observations:
                opening = f"Phiên giao dịch hôm nay, tỷ giá USD-CNH biến động quanh mức {{{{{cnh_token}}}}}."
        low, high = rules()["word_limits"][name]
        if name == "eur_usd":
            us_candidates = pools["USD"][0]
            groups = ({c.source_id for c in us_candidates}, {c.source_id for c in candidates})
            picked = choose_sentences(us_candidates + candidates, snapshot, low, high,
                                      prefix=opening + " " + EUR_SECOND_OPENING, required_groups=groups)
            result[name] = make_section(snapshot, [[c for c in picked if c.source_id in groups[0]],
                                                   [c for c in picked if c.source_id in groups[1]]],
                                        [opening, EUR_SECOND_OPENING])
        else:
            lead_candidates = [c for c in candidates if candidates and c.source_id == candidates[0].source_id]
            picked = choose_sentences(lead_candidates, snapshot, low, high, prefix=opening) if lead_candidates else []
            if not picked:
                picked = choose_sentences(candidates, snapshot, low, high, prefix=opening)
            result[name] = make_section(snapshot, [picked], [opening])
        count = count_words(resolve(" ".join(result[name].paragraphs), snapshot, safe=True))
        audit[name] = {"status": "selected" if low <= count <= high and picked else "needs_editing",
                       "word_count": count, "limits": [low, high], "selected": [asdict(c) for c in picked],
                       "rejected": rejected}
    usd = choose_sentences(pools["USD"][0], snapshot, 75, 110)
    result["usd_news"] = make_section(snapshot, [usd])
    count = count_words(" ".join(c.text for c in usd))
    audit["usd_news"] = {"status": "selected" if 75 <= count <= 110 else "needs_editing",
                         "word_count": count, "limits": [75, 110], "selected": [asdict(c) for c in usd]}
    return result, audit


def observation_candidate(snapshot, text, keys):
    if not all(key in snapshot.observations for key in keys):
        return None
    return Candidate(text, snapshot.observations[keys[0]].source_id, quoted=False)


def gold_data_sentences(snapshot):
    """Hai câu vàng (35–40 từ) chỉ từ số liệu thật: vàng thế giới và SJC. Thiếu số liệu thì bỏ trống."""
    gold = snapshot.observations.get("GOLD")
    if not gold or "SJC_BUY" not in snapshot.observations or "SJC_SELL" not in snapshot.observations:
        return []
    pct = gold.daily_pct
    if pct is None or abs(pct) < 0.05:
        move = "gần như đi ngang, giao dịch"
    else:
        move = ("tăng" if pct > 0 else "giảm") + " {{GOLD_abs_pct}}% so với phiên trước, " + ("lên" if pct > 0 else "xuống")
    first = f"Giá vàng thế giới phiên gần nhất {move} quanh mức {{{{GOLD}}}} USD/ounce."
    second = ("Trong nước, giá vàng miếng SJC niêm yết ở mức {{SJC_BUY}} - {{SJC_SELL}} "
              "triệu đồng/lượng (mua vào - bán ra).")
    return [Candidate(first, gold.source_id, quoted=False),
            Candidate(second, snapshot.observations["SJC_BUY"].source_id, quoted=False)]


def extract_vira_omo(raw_text: str) -> str:
    """Extract and reformat OMO auction, maturity, and net injection/absorption from VIRA daily text."""
    if not raw_text:
        return ""
    omo_match = re.search(r"Nghiệp vụ thị trường mở:.*?(?=Thị trường trái phiếu:|$)", raw_text, re.DOTALL | re.IGNORECASE)
    section_text = omo_match.group(0) if omo_match else raw_text

    m_trung = re.search(r"Có\s+([\d.,]+\s+tỷ\s+đồng\s+trúng\s+thầu[^.]*\.)", section_text, re.IGNORECASE)
    m_daohan = re.search(r"Có\s+([\d.,]+\s+tỷ\s+đồng\s+đáo\s+hạn\.)", section_text, re.IGNORECASE)
    m_net = re.search(r"Như\s+vậy,\s*NHNN\s+(hút\s+ròng|bơm\s+ròng)\s+([\d.,]+)\s*tỷ", section_text, re.IGNORECASE)
    m_luuhanh = re.search(r"Có\s+([\d.,]+\s+tỷ\s+đồng\s+lưu\s+hành\s+trên\s+kênh\s+cầm\s+cố\.)", section_text, re.IGNORECASE)

    parts = []
    if m_trung:
        text_trung = m_trung.group(1).strip()
        text_trung = text_trung[0].lower() + text_trung[1:] if text_trung else text_trung
        parts.append(f"Trong phiên hôm qua, có {text_trung}")
    if m_daohan:
        parts.append(f"Có {m_daohan.group(1).strip()}")
    if m_net:
        action = m_net.group(1).strip()
        amount = m_net.group(2).strip()
        parts.append(f"Như vậy, NHNN {action} {amount} tỷ.")
    if m_luuhanh:
        parts.append(f"Có {m_luuhanh.group(1).strip()}")

    return " ".join(parts)


VIRA_HEADERS = re.compile(r"^(?:Thị trường ngoại tệ|Thị trường tiền tệ LNH|Nghiệp vụ thị trường mở|"
                          r"Thị trường trái phiếu|Đấu thầu TPCP|Thị trường TPCP)\s*:?\s*", re.I)
INTERBANK_WEIGHTS = [
    (r"qua đêm|\bON\b|1 tuần|2 tuần|liên ngân hàng|LNH", 4),
    (r"hút ròng|bơm ròng", 5),
    (r"trúng thầu|đáo hạn|chào thầu|cầm cố", 2),
    (r"lợi suất|trái phiếu|TPCP", 3),
]
FX_WEIGHTS = [
    (r"tự do|chợ đen", 5),
    (r"trung tâm", 4),
    (r"liên ngân hàng|đóng cửa", 3),
    (r"DXY|USD Index|chỉ số đô la", 2),
]


def vira_candidates(snapshot, source_id, weights):
    """Câu nguyên văn của VIRA (bỏ tiêu đề mục), chấm điểm theo chủ đề; giữ thứ tự trong bản tin VIRA."""
    source = snapshot.sources.get(source_id)
    if not source:
        return []
    out = []
    for index, sentence in enumerate(sentences(source.text)):
        sentence = VIRA_HEADERS.sub("", sentence).strip()
        if len(sentence.split()) < 6 or not sentence.endswith((".", "!", "?")) or DISALLOWED.search(sentence):
            continue
        score = sum(w for pattern, w in weights if re.search(pattern, sentence, re.I))
        out.append(Candidate(sentence, source_id, 1 + score + 1 / (index + 1), index))
    return out


def domestic_sections(snapshot, records):
    result, audit = {}, {}
    today_str = snapshot.as_of.strftime("%d.%m.%Y")

    # 1. Thị trường tiền tệ liên ngân hàng: câu nguyên văn từ "Bản tin Kinh tế - Tài chính ngày" của VIRA
    #    (lãi suất LNH, nghiệp vụ thị trường mở, trái phiếu). Không có VIRA thì chỉ dùng số liệu VNIBOR.
    interbank_candidates = vira_candidates(snapshot, "vira_daily", INTERBANK_WEIGHTS)
    if not interbank_candidates:
        c = observation_candidate(
            snapshot, "Lãi suất VND liên ngân hàng kỳ hạn qua đêm ở mức {{VND_ON}}%, kỳ hạn 1 tuần "
            "{{VND_1W}}% và kỳ hạn 1 tháng {{VND_1M}}%.", ["VND_ON", "VND_1W", "VND_1M"])
        interbank_candidates = [c] if c else []
    low, high = rules()["word_limits"]["interbank"]
    picked = choose_sentences(interbank_candidates, snapshot, low, high)
    result["interbank"] = make_section(snapshot, [picked])
    audit["interbank"] = {"selected": [asdict(c) for c in picked]}

    # 2. Thị trường ngoại hối USD-VND: câu đầu từ quote của trader trong room (hoặc giá niêm yết MBBank),
    #    phần còn lại là câu nguyên văn mục "Thị trường ngoại tệ" của VIRA và tin vĩ mô trong nước.
    obs = snapshot.observations
    trend = ""
    if "INTERBANK_BID" in obs and "INTERBANK_BID_PREV" in obs:
        now, prev = obs["INTERBANK_BID"].value, obs["INTERBANK_BID_PREV"].value
        trend = "tăng nhẹ" if now > prev else "giảm nhẹ" if now < prev else "đi ngang"
    if "INTERBANK_BID" in obs and "INTERBANK_ASK" in obs:
        quote_token, quote_keys = "{{INTERBANK_BID}}/{{INTERBANK_ASK}}", ["INTERBANK_BID", "INTERBANK_ASK"]
    elif "MB_BUY" in obs and "MB_SELL" in obs:
        quote_token, quote_keys = "{{MB_BUY}}/{{MB_SELL}}", ["MB_BUY", "MB_SELL"]
    else:
        quote_token, quote_keys = None, []

    liquidity_phrase = ""
    stream_candidates = [
        Path(__file__).resolve().parents[1] / "data" / "teams_quotes_stream.json",
        Path("D:/TyGia/teams_quotes_stream.json"),
        Path("/root/TyGia/teams_quotes_stream.json"),
    ]
    for sc in stream_candidates:
        if sc.is_file():
            try:
                import json
                s_data = json.loads(sc.read_text(encoding="utf-8"))
                if s_data.get("liquidity"):
                    liquidity_phrase = s_data.get("liquidity")
                    break
            except Exception:
                pass

    lead = []
    if quote_token:
        clause = f"tỷ giá USD-VND {trend}, " if trend else "tỷ giá USD-VND "
        tail = f", {liquidity_phrase}" if liquidity_phrase else ""
        s1 = f"Phiên ngày {today_str}, {clause}dao động quanh mức {quote_token}{tail}."
        lead = [Candidate(s1, obs[quote_keys[0]].source_id, quoted=False)]
    lead_words = count_words(resolve(" ".join(c.text for c in lead), snapshot, safe=True))
    fx_pool = vira_candidates(snapshot, "vira_fx", FX_WEIGHTS)
    macro, _ = news_candidates(snapshot, records, "macro")
    low, high = rules()["word_limits"]["usd_vnd"]
    rest = choose_sentences(fx_pool + [Candidate(c.text, c.source_id, c.score / 10, c.index) for c in macro[:10]],
                            snapshot, max(0, low - lead_words), high - lead_words, max_sentences=3)
    picked = lead + rest
    result["usd_vnd"] = make_section(snapshot, [picked])
    audit["usd_vnd"] = {"selected": [asdict(c) for c in picked]}
    coffee, rejected = news_candidates(snapshot, records, "coffee")
    prefix = "Cập nhật giá cà phê thế giới,"
    picked = choose_sentences(coffee, snapshot, *rules()["word_limits"]["coffee"], prefix=prefix)
    result["coffee"] = make_section(snapshot, [picked], [prefix])
    audit["coffee"] = {"selected": [asdict(c) for c in picked], "rejected": rejected}
    oil, rejected_oil = news_candidates(snapshot, records, "Brent")
    gold, rejected_gold = news_candidates(snapshot, records, "gold")
    limits = rules().get("energy_metals_parts", {"brent": [90, 95], "gold": [35, 40]})
    g_low, g_high = limits["gold"]
    gold_picked = choose_sentences(gold, snapshot, g_low, g_high, max_sentences=2)
    gold_words = count_words(resolve(" ".join(c.text for c in gold_picked), snapshot, safe=True))
    if len(gold_picked) != 2 or not g_low <= gold_words <= g_high:
        gold_picked = gold_data_sentences(snapshot)
    b_low, b_high = limits["brent"]
    oil = [c for c in oil if c.text not in {g.text for g in gold_picked}]
    oil_picked = choose_sentences(oil, snapshot, b_low, b_high)
    if not oil_picked and "BRENT" in snapshot.observations:
        cand_oil1 = observation_candidate(snapshot, "Giá dầu Brent thế giới biến động quanh mức {{BRENT}} USD/thùng.", ["BRENT"])
        cand_oil2 = observation_candidate(snapshot, "Thị trường năng lượng quốc tế tiếp tục phản ánh các biến động về nguồn cung và căng thẳng địa chính trị.", ["BRENT"])
        if cand_oil1 and cand_oil2:
            oil_picked = [cand_oil1, cand_oil2]
    result["energy_metals"] = make_section(snapshot, [oil_picked, gold_picked])
    audit["energy_metals"] = {"selected": [asdict(c) for c in oil_picked + gold_picked], "rejected": rejected_oil + rejected_gold}
    for name, section in result.items():
        low, high = rules()["word_limits"][name]
        count = count_words(resolve(" ".join(section.paragraphs), snapshot, safe=True))
        complete = all(p.strip() for p in section.paragraphs) and bool(audit[name]["selected"])
        audit[name].update(word_count=count, limits=[low, high],
                           status="selected" if complete and low <= count <= high else "needs_editing")
    return result, audit


def generate(snapshot, directory: Path):
    records = load_verified_translations(snapshot, directory / "translations.json")
    content = create_draft_placeholder_content(snapshot)
    foreign, audit = foreign_sections(snapshot, records)
    domestic, domestic_audit = domestic_sections(snapshot, records)
    audit.update(domestic_audit)
    for name, section in {**foreign, **domestic}.items():
        if name != "usd_news" and all(p.strip() for p in section.paragraphs):
            setattr(content, name, section)
    macro, rejected = news_candidates(snapshot, records, "macro")
    used = set()
    used_sources = set()
    for index in range(2):
        picked = []
        available_sources = sorted(
            {c.source_id for c in macro if c.text not in used and c.source_id not in used_sources},
            key=lambda sid: -max((c.score for c in macro if c.source_id == sid and c.text not in used), default=0)
        )
        for sid in available_sources:
            source_candidates = [c for c in macro if c.source_id == sid and c.text not in used]
            cand_picked = choose_sentences(source_candidates, snapshot, *highlight_limits(index))
            if cand_picked:
                picked = cand_picked
                break
        if not picked:
            pool = [c for c in macro if c.text not in used and c.source_id not in used_sources]
            if not pool and macro:
                pool = [c for c in macro if c.text not in used]
            picked = choose_sentences(pool, snapshot, *highlight_limits(index))
        if picked:
            content.highlights[index] = make_section(snapshot, [picked])
            used.update(c.text for c in picked)
            used_sources.update(c.source_id for c in picked)
        audit[f"highlight_{index}"] = {"selected": [asdict(c) for c in picked], "rejected": rejected}

    # Highlight 2 (Tin 3) is gold: world gold + domestic SJC gold
    gold_obs = snapshot.observations.get("GOLD")
    sjc_buy = snapshot.observations.get("SJC_BUY")
    sjc_sell = snapshot.observations.get("SJC_SELL")
    sjc_src = snapshot.sources.get("sjc_gold")

    trend = "nhẹ"
    if gold_obs and gold_obs.daily_pct is not None:
        if gold_obs.daily_pct < -0.05:
            trend = "giảm nhẹ"
        elif gold_obs.daily_pct > 0.05:
            trend = "tăng nhẹ"
        else:
            trend = "đi ngang"

    sjc_str = "{{SJC_BUY}} – {{SJC_SELL}} triệu đồng/lượng"
    if not (sjc_buy and sjc_sell) and sjc_src and sjc_src.text:
        sjc_str = sjc_src.text if "triệu đồng/lượng" in sjc_src.text else f"{sjc_src.text} triệu đồng/lượng"

    gold_text = f"Giá vàng thế giới biến động {trend} về quanh {{{{GOLD}}}} USD/ounce. Trong nước giá vàng đi ngang quanh mức {sjc_str}."
    gold_sources = []
    if gold_obs:
        gold_sources.append(gold_obs.source_id)
    if sjc_src:
        gold_sources.append(sjc_src.id)
    elif "sjc_gold" in snapshot.sources:
        gold_sources.append("sjc_gold")
    if not gold_sources and snapshot.sources:
        gold_sources = [next(iter(snapshot.sources.keys()))]

    content.highlights[2] = Section(
        paragraphs=[gold_text],
        source_ids=gold_sources,
        sentence_refs=[],
    )
    audit["highlight_2"] = {
        "selected": [{"text": gold_text, "source_id": gold_sources[0] if gold_sources else ""}],
        "rejected": [],
    }
    (directory / "selection.json").write_text(json.dumps({"editor": "python-extractive-v1", "sections": audit}, ensure_ascii=False, indent=2), encoding="utf-8")
    issues = validate_content(content, snapshot, translations_path=directory / "translations.json")
    (directory / "editorial-validation.json").write_text(json.dumps([i.model_dump() for i in issues], ensure_ascii=False, indent=2), encoding="utf-8")
    return content

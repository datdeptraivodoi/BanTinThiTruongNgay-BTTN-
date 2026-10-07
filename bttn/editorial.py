"""Python report editing: intact sourced sentences and verified data templates."""
import json
import re
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

from .fx_editorial import EUR_SECOND_OPENING, fx_opening
from .models import Section, SentenceReference, create_draft_placeholder_content
from .nvidia_translation import is_vietnamese
from .trader_quotes import trader_observation
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
    "JPY": ["yên", "JPY", "Nhật", "BOJ", "lãi suất", "Takaichi", "lạm phát"],
    "China": ["Trung Quốc", "nhân dân tệ", "PBOC", "tăng trưởng", "tiêu dùng", "kích thích"],
    "macro": ["GDP", "lạm phát", "xuất khẩu", "đầu tư công", "giải ngân", "tăng trưởng"],
    "coffee": ["cà phê", "Arabica", "Robusta", "tồn kho", "Brazil"],
    "Brent": ["Brent", "dầu", "OPEC", "năng lượng"],
    "gold": ["vàng", "kim loại"],
}


def sentences(text):
    lines = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    result = []
    for line in lines:
        cleaned = " ".join(line.split())
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
        for index, sentence in enumerate(sentences(body)):
            identity = " ".join(sentence.casefold().split())
            if identity in seen:
                continue
            reason = None
            if DISALLOWED.search(sentence):
                reason = "forecast_or_instruction"
            elif CONTEXT_DEPENDENT.search(sentence):
                # A sentence such as 'This reading was the weakest...' loses
                # its antecedent when selection omits the actual GDP reading.
                # Keep it in the full translation for review, not the digest.
                reason = "context_dependent"
            elif len(sentence.split()) < 8 or not sentence.endswith((".", "!", "?")):
                reason = "incomplete_or_short"
            elif not is_vietnamese(sentence):
                reason = "not_vietnamese"
            score = sum(word.casefold() in sentence.casefold() for word in KEYWORDS[topic])
            if country is None and score == 0:
                reason = reason or "off_topic"
            if reason:
                rejected.append({"source_id": sid, "sentence": index, "reason": reason})
                continue
            seen.add(identity)
            candidates.append(Candidate(sentence, sid, 1 + score + 1 / (index + 1), index))
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


def domestic_sections(snapshot, records):
    result, audit, rates = {}, {}, []
    for currency in ("VND", "USD"):
        for tenor, label in [("ON", "qua đêm"), ("1W", "một tuần"), ("1M", "một tháng"), ("3M", "ba tháng"), ("6M", "sáu tháng")]:
            key = f"{currency}_{tenor}"
            item = observation_candidate(snapshot, f"Lãi suất {currency} kỳ hạn {label} được ghi nhận ở mức {{{{{key}}}}}%/năm.", [key])
            if item:
                rates.append(item)
    fx_options = [
        ("NHNN công bố tỷ giá trung tâm USD-VND ở mức {{SBV_CENTRAL}} VND/USD.", ["SBV_CENTRAL"]),
        ("Mức sàn và trần tính từ tỷ giá trung tâm lần lượt là {{SBV_FLOOR}} và {{SBV_CEILING}} VND/USD.", ["SBV_FLOOR", "SBV_CEILING"]),
        ("Giá mua chuyển khoản và bán USD niêm yết tại MBBank lần lượt là {{MB_BUY}} và {{MB_SELL}} VND/USD.", ["MB_BUY", "MB_SELL"]),
    ]
    for suffix, label in [("", "hiện tại"), ("_PREV", "phiên trước")]:
        keys = ["INTERBANK_BID" + suffix, "INTERBANK_ASK" + suffix]
        if all(trader_observation(snapshot, k) for k in keys):
            fx_options.append((f"Báo giá tham khảo USD-VND {label} từ trader room firm ALM ghi nhận {{{{{keys[0]}}}}}/{{{{{keys[1]}}}}} VND/USD.", keys))
    fx = [item for text, keys in fx_options if (item := observation_candidate(snapshot, text, keys))]
    for name, candidates in [("interbank", rates), ("usd_vnd", fx)]:
        picked = choose_sentences(candidates, snapshot, *rules()["word_limits"][name])
        result[name] = make_section(snapshot, [picked])
        audit[name] = {"selected": [asdict(c) for c in picked]}
    coffee, rejected = news_candidates(snapshot, records, "coffee")
    prefix = "Cập nhật giá cà phê thế giới,"
    picked = choose_sentences(coffee, snapshot, *rules()["word_limits"]["coffee"], prefix=prefix)
    result["coffee"] = make_section(snapshot, [picked], [prefix])
    audit["coffee"] = {"selected": [asdict(c) for c in picked], "rejected": rejected}
    oil, rejected_oil = news_candidates(snapshot, records, "Brent")
    gold, rejected_gold = news_candidates(snapshot, records, "gold")
    gold_picked = choose_sentences(gold, snapshot, 20, 65, max_sentences=2)
    if len(gold_picked) != 2:
        gold_picked = []
    gold_count = count_words(" ".join(c.text for c in gold_picked))
    low, high = rules()["word_limits"]["energy_metals"]
    oil = [c for c in oil if c.text not in {g.text for g in gold_picked}]
    oil_picked = choose_sentences(oil, snapshot, max(0, low - gold_count), high - gold_count)
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
    for index in range(3):
        pool = [c for c in macro if c.text not in used]
        if index == 2:
            pool += news_candidates(snapshot, records, "USD")[0]
        picked = choose_sentences(pool, snapshot, *highlight_limits(index))
        if picked:
            content.highlights[index] = make_section(snapshot, [picked])
            used.update(c.text for c in picked)
        audit[f"highlight_{index}"] = {"selected": [asdict(c) for c in picked], "rejected": rejected}
    (directory / "selection.json").write_text(json.dumps({"editor": "python-extractive-v1", "sections": audit}, ensure_ascii=False, indent=2), encoding="utf-8")
    issues = validate_content(content, snapshot, translations_path=directory / "translations.json")
    (directory / "editorial-validation.json").write_text(json.dumps([i.model_dump() for i in issues], ensure_ascii=False, indent=2), encoding="utf-8")
    return content

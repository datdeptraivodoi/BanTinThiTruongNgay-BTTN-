import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Issue, ReportContent, Snapshot, business_days_between, previous_weekday
from .trader_quotes import validate_trader_observations

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"\{\{([A-Za-z0-9_ ]+)\}\}")


ALLOWED_INDEX_NAMES = re.compile(
    r"\b(?:"
    r"Nikkei\s*225|"
    r"S&P\s*(?:500|100|400|600)|"
    r"CSI\s*300|"
    r"FTSE\s*(?:100|250)|"
    r"CAC\s*40|"
    r"DAX\s*(?:40|30)|"
    r"(?:Euro\s*)?Stoxx\s*(?:50|600)|"
    r"Russell\s*(?:2000|1000|3000)|"
    r"Topix\s*(?:100)?|"
    r"Nifty\s*50|"
    r"Kospi\s*200|"
    r"ASX\s*200|"
    r"VN-?Index(?:\s*(?:30|100))?|"
    r"VN\s*(?:30|100)|"
    r"HNX\s*30|"
    r"G7|G20|"
    r"T\+[0-3]"
    r")\b",
    re.I,
)

ALLOWED_DATE_FORMAT = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{4}\b")


TECHNICAL_LEAKAGE_RE = re.compile(
    r"(?:"
    r"\"?source_ids\"?\s*[:=]|"
    r"\"?paragraphs\"?\s*[:=]|"
    r"\"?highlights\"?\s*[:=]|"
    r"```|"
    r"BEGIN_UNTRUSTED|END_UNTRUSTED"
    r")",
    re.I,
)

ENGLISH_BOILERPLATE_RE = re.compile(
    r"\b(?:"
    r"the|and|in|of|to|is|was|were|reported|closed|trading|market|overall|however|meanwhile|against|according"
    r")\b",
    re.I,
)


def rules():
    return json.loads((ROOT / "config/editorial_rules.json").read_text(encoding="utf-8"))


def highlight_limits(index):
    return rules().get("highlight_word_limits", [[35, 45], [35, 45], [12, 45]])[index]


def count_words(text):
    # Numbering is presentation; amounts/dates remain one whitespace token.
    text = unicodedata.normalize("NFC", text)
    return len(re.sub(r"^\s*\d+[.)]\s+", "", text).split())


def parse_placeholder_key(key: str, snapshot: Snapshot):
    """Parses a placeholder key into (Observation, field_name).
    Supports base observation (e.g. BRENT) and percentage change fields (e.g. BRENT_daily_pct, BRENT_annual_pct).
    """
    key = key.strip()
    if key in snapshot.observations:
        return snapshot.observations[key], "value"
    for suffix, field in [
        ("_daily_pct", "daily_pct"),
        ("_annual_pct", "annual_pct"),
        ("_pct", "daily_pct"),
        ("_prev", "prev_value"),
        ("_yesterday", "prev_value"),
        ("_close", "prev_value"),
    ]:
        if key.endswith(suffix):
            base_id = key[:-len(suffix)]
            if base_id in snapshot.observations:
                return snapshot.observations[base_id], field
    return None, None


def format_value(obs, field: str = "value"):
    if field == "daily_pct":
        if obs.daily_pct is None:
            return "—"
        return f"{obs.daily_pct:.2f}".translate(str.maketrans({",": ".", ".": ","}))
    elif field == "annual_pct":
        if obs.annual_pct is None:
            return "—"
        return f"{obs.annual_pct:.2f}".translate(str.maketrans({",": ".", ".": ","}))
    elif field in ("prev_value", "prev", "yesterday"):
        val = getattr(obs, "prev_value", None)
        if val is None and len(obs.series) >= 2:
            val = obs.series[-2].value
        if val is None:
            val = obs.value
        places = 4 if obs.id in {"EURUSD", "USDCNY"} else 0 if obs.unit == "VND/USD" else 2
        return f"{val:,.{places}f}".translate(str.maketrans({",": ".", ".": ","}))
    else:
        places = 4 if obs.id in {"EURUSD", "USDCNY"} else 0 if obs.unit == "VND/USD" else 2
        return f"{obs.value:,.{places}f}".translate(str.maketrans({",": ".", ".": ","}))


def resolve(text: str, snapshot: Snapshot, safe: bool = False) -> str:
    def replacement(match):
        key = match.group(1).strip()
        obs, field = parse_placeholder_key(key, snapshot)
        if obs is None:
            if safe:
                return "[số liệu đang cập nhật]"
            raise ValueError(f"Unknown observation placeholder: {key}")
        return format_value(obs, field=field)

    result = TOKEN.sub(replacement, text)
    if not safe and ("{{" in result or "}}" in result):
        raise ValueError("Malformed unresolved placeholder")
    elif safe:
        result = re.sub(r"\{\{[^}]*\}\}", "[số liệu đang cập nhật]", result)
    return result


def expected_session_date(as_of: datetime, snapshot: Snapshot | None = None) -> date:
    day = as_of.date()
    if day.weekday() >= 5:
        return previous_weekday(day + timedelta(days=1))
    # Before morning publication hours (midday report publication / morning releases ~11:00 VN)
    if as_of.hour < 11:
        if snapshot:
            vira = snapshot.sources.get("vira")
            if vira and vira.published_at.date() == day:
                return day
        return previous_weekday(day)
    return day


def validate_snapshot(snapshot: Snapshot) -> list[Issue]:
    result = [i for i in snapshot.issues if i.severity == "error"] + validate_trader_observations(snapshot)
    cfg = rules()
    def error(code, message):
        result.append(Issue(severity="error", code=code, message=message))
    expected = expected_session_date(snapshot.as_of, snapshot)
    for key in cfg["required_observations"]:
        if key not in snapshot.observations:
            error("REQUIRED_DATA", f"Thiếu số liệu bắt buộc: {key}")
    for key, obs in snapshot.observations.items():
        if key != obs.id:
            error("OBSERVATION_ID", f"{key}: ID không khớp")
        source = snapshot.sources.get(obs.source_id)
        if not source:
            error("PROVENANCE", f"{key}: thiếu nguồn")
            continue
        if source.published_at > snapshot.as_of or obs.trading_date > snapshot.as_of.date():
            error("FUTURE_DATA", f"{key}: dữ liệu sau thời điểm chốt")
        max_b_days = cfg.get("source_max_age_business_days", 3)
        b_days = business_days_between(obs.trading_date, expected)
        if b_days > max_b_days:
            error("STALE_DATA", f"{key}: số liệu cũ ngày {obs.trading_date} ({b_days} ngày làm việc)")
        if obs.annual_pct is not None and not obs.annual_basis:
            error("ANNUAL_BASIS", f"{key}: thiếu định nghĩa biến động năm")
        if obs.annual_pct is not None and obs.annual_basis != "YoY":
            error("ANNUAL_BASIS", f"{key}: bảng yêu cầu YoY")
        if any(p.at > snapshot.as_of for p in obs.series):
            error("FUTURE_SERIES", f"{key}: biểu đồ có dữ liệu sau thời điểm chốt")
        if key.startswith("SWAP_"):
            tenor = key.removeprefix("SWAP_")
            vnd, usd = (snapshot.observations.get(prefix + tenor) for prefix in ["VND_", "USD_"])
            if not vnd or not usd or obs.value != vnd.value - usd.value or obs.unit != "điểm %":
                error("SWAP_VALUE", f"{key}: không khớp VND trừ USD")
        if obs.value.is_nan() or obs.value.is_infinite():
            error("NONFINITE", key)
        if source.kind != "derived" and not source.url.startswith("https://"):
            error("SOURCE_URL", key)
    vira = snapshot.sources.get("vira")
    if not vira or vira.published_at.date() != expected:
        error("VIRA_EDITION", f"Cần Market Watch ngày {expected}; chưa nhận được ấn bản phù hợp")
    for key in ["SBV_CENTRAL", "MB_BUY", "MB_SELL"]:
        obs = snapshot.observations.get(key)
        if obs and obs.trading_date != expected:
            error("LOCAL_FIXING_DATE", f"{key}: chưa có số liệu ngày {expected}")
    news = [s for s in snapshot.sources.values() if s.kind == "news" and
            timedelta(0) <= snapshot.as_of - s.published_at <= timedelta(hours=cfg["news_max_age_hours"])]
    if len(news) < 3:
        error("NEWS_COVERAGE", "Cần ít nhất ba bài có nguồn và ngày xuất bản hợp lệ")
    return result


def validate_content(content: ReportContent, snapshot: Snapshot) -> list[Issue]:
    result = []
    sections = [(name, getattr(content, name)) for name in rules()["word_limits"]]
    sections += [(f"highlight_{i}", s) for i, s in enumerate(content.highlights)]
    for name, section in sections:
        def error(code, message):
            result.append(Issue(severity="error", code=code, message=f"{name}: {message}"))
        if any(sid not in snapshot.sources for sid in section.source_ids):
            error("CONTENT_SOURCE", "Nguồn trích dẫn không tồn tại")
        for sid in section.source_ids:
            source = snapshot.sources.get(sid)
            if source and (source.published_at > snapshot.as_of or
                           source.kind == "news" and snapshot.as_of - source.published_at > timedelta(hours=rules()["news_max_age_hours"])):
                error("CONTENT_SOURCE_DATE", "Nguồn trích dẫn nằm ngoài thời gian hợp lệ")
        raw = " ".join(section.paragraphs)
        if re.search(r"dự kiến|dự báo|khuyến nghị|mục tiêu giá", raw, re.I):
            error("FORECAST_DISABLED", "Phần dự báo đang tạm để trống")
        if any(mark in raw for mark in ["**", "##", "__"]):
            error("MARKDOWN", "Không dùng markup trong văn bản")
        # Numeric market assertions must be inserted deterministically from the
        # snapshot; this also prevents literal unsupported prices/dates.
        # Index names (e.g. Nikkei 225, S&P 500) are recognized and excluded
        # so their digits are not falsely flagged as unbound market data.
        without_tokens = TOKEN.sub("", raw)
        without_dates = ALLOWED_DATE_FORMAT.sub("", without_tokens)
        without_indices = ALLOWED_INDEX_NAMES.sub("", without_dates)
        if re.search(r"\d", without_indices):
            error("UNBOUND_NUMBER", "Số liệu phải dùng {{OBSERVATION_ID}}")
        if TECHNICAL_LEAKAGE_RE.search(raw):
            error("TECHNICAL_LEAKAGE", "Phát hiện chuỗi kỹ thuật rò rỉ vào văn bản (ví dụ: source_ids)")
        eng_matches = ENGLISH_BOILERPLATE_RE.findall(raw)
        if len(eng_matches) >= 4:
            error("LANGUAGE_NOT_VIETNAMESE", f"Văn bản chứa nhiều từ tiếng Anh ({', '.join(eng_matches[:4])}...); yêu cầu 100% tiếng Việt")

        for key in TOKEN.findall(raw):
            obs, field = parse_placeholder_key(key, snapshot)
            if obs is None:
                error("UNKNOWN_PLACEHOLDER", f"Placeholder không xác định: {key}")
                continue
            valid_sources = {obs.source_id}
            if obs.id.startswith("SWAP_") or obs.source_id.startswith("derived_swap_"):
                valid_sources.add("vira")
            if not any(sid in section.source_ids for sid in valid_sources):
                error("NUMBER_SOURCE", f"Thiếu nguồn cho {key} (cần trích dẫn {obs.source_id} hoặc vira)")
        try:
            rendered = resolve(raw, snapshot)
        except ValueError as exc:
            error("PLACEHOLDER", str(exc))
            continue
        count = count_words(rendered)
        if name.startswith("highlight"):
            low, high = highlight_limits(int(name.split("_")[1]))
        else:
            low, high = rules()["word_limits"][name]
        if not low <= count <= high:
            error("WORD_COUNT", f"{count} từ, yêu cầu {low}–{high}")
        if name == "coffee" and not rendered.startswith("Cập nhật giá cà phê thế giới,"):
            error("OPENING", "Thiếu câu mở đầu cà phê")
        if name == "eur_usd" and (len(section.paragraphs) != 2 or not section.paragraphs[1].startswith("Về phía Châu Âu,")):
            error("EU_STRUCTURE", "Cần hai đoạn; đoạn hai bắt đầu Về phía Châu Âu,")
        if name == "energy_metals":
            if len(section.paragraphs) != 2:
                error("ENERGY_STRUCTURE", "Cần một đoạn Brent và một đoạn vàng")
            else:
                gold = resolve(section.paragraphs[1], snapshot)
                if len(re.findall(r"[.!?](?:\s|$)", gold)) != 2:
                    error("GOLD_SENTENCES", "Đoạn vàng phải có đúng hai câu")
    return result

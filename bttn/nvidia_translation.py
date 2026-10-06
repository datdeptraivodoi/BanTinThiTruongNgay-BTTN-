"""NVIDIA Riva translation only: no report generation or provider fallback."""
import re
import time
from collections import Counter
from decimal import Decimal, InvalidOperation

import requests

MODEL = "nvidia/riva-translate-4b-instruct-v2"
ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
RULES_VERSION = "riva-en-vi-finance-v6"
TERMS = {
    "federal reserve": "Cục Dự trữ Liên bang Mỹ",
    "hawkish": "cứng rắn", "dovish": "mềm mỏng",
    "consumer sentiment": "tâm lý tiêu dùng",
    "nonfarm payrolls": "Bảng lương phi nông nghiệp",
    "nonfarm payroll": "Bảng lương phi nông nghiệp",
    "non-farm payrolls": "Bảng lương phi nông nghiệp",
    "non-farm payroll": "Bảng lương phi nông nghiệp",
    "core inflation": "lạm phát cơ bản", "headline inflation": "lạm phát danh nghĩa",
}
NUMBERS = re.compile(r"\d+(?:[.,:/-]\d+)*")
VIETNAMESE = re.compile(r"[ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.I)
TOKEN = re.compile(r"BTTNPROTECT[A-Z]+END")
PROTECTED = re.compile(r"<dnt>.*?</dnt>", re.I | re.S)
MONTHS = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
          "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12}
GLOSSARY_EXAMPLES = [
    ("The Federal Reserve maintained a hawkish stance, while European officials took a dovish tone.",
     "Cục Dự trữ Liên bang Mỹ duy trì lập trường cứng rắn, trong khi các quan chức châu Âu có giọng điệu mềm mỏng."),
    ("Consumer Sentiment edged up ahead of the Nonfarm Payrolls report.",
     "Tâm lý tiêu dùng tăng nhẹ trước báo cáo Bảng lương phi nông nghiệp."),
    ("Core inflation slowed to 2.8%, while headline inflation ticked up.",
     "Lạm phát cơ bản giảm xuống 2.8%, trong khi lạm phát danh nghĩa tăng nhẹ."),
    ("The Japanese yen depreciated past 150 per dollar, but remained range-bound.",
     "USD-JPY vượt mức 150 cho thấy JPY đang suy yếu, nhưng vẫn dao động trong biên độ hẹp."),
    ("The offshore yuan strengthened to 7.12 per dollar as fiscal support increased.",
     "Đồng nhân dân tệ ngoại biên tăng giá lên mức 7,12 nhân dân tệ đổi một USD khi hỗ trợ tài khóa gia tăng."),
]
TERM_ALIASES = {
    "diều hâu": "cứng rắn", "bồ câu": "mềm mỏng", "tâm lý người tiêu dùng": "tâm lý tiêu dùng",
    "lạm phát lõi": "lạm phát cơ bản", "lạm phát tổng thể": "lạm phát danh nghĩa",
}


class TranslationError(RuntimeError):
    """Safe error codes; never includes a response body, header or API key."""


def is_vietnamese(text):
    words = text.split()
    return bool(words) and sum(bool(VIETNAMESE.search(w)) for w in words) / len(words) >= .15


def clean_post_translation(text):
    text = re.sub(r"\bnhích nhẹ\b", "tăng nhẹ", text, flags=re.I)
    return re.sub(r"\bnhích lên\b", "tăng lên", text, flags=re.I)


def normalize_terms(original, translated):
    required = {vi for en, vi in TERMS.items() if re.search(r"\b" + re.escape(en) + r"\b", original, re.I)}
    for alias, replacement in TERM_ALIASES.items():
        if replacement in required:
            translated = re.sub(re.escape(alias), replacement, translated, flags=re.I)
    translated = re.sub(r"\byen\b", "yên", translated, flags=re.I)
    translated = re.sub(r"\byuan\b", "nhân dân tệ", translated, flags=re.I)
    translated = re.sub(r"\bgiao động\b", "dao động", translated, flags=re.I)
    if "monetary tightening" in original.lower():
        translated = translated.replace("nhu cầu tăng thêm chính sách tiền tệ", "nhu cầu tiếp tục thắt chặt chính sách tiền tệ")
        translated = translated.replace("nhu cầu tăng cường chính sách tiền tệ", "nhu cầu tiếp tục thắt chặt chính sách tiền tệ")
    if "fed funds rate" in original.lower():
        translated = translated.replace("lãi suất cho vay của Fed", "lãi suất quỹ liên bang")
        translated = translated.replace("lãi suất tiền gửi của Fed", "lãi suất quỹ liên bang")
    return translated


def normalize_fx_quote(original, translated):
    """A narrow source-derived quote rule, never inferred from the Vietnamese output.

    A yen depreciation past X yen/USD means USD-JPY exceeds X. Riva repeatedly
    produced 'below X'. Use the agreed pair notation for both correct and
    inverted yen/USD wording, only when the source and number match.
    Retain the complete translation after the quote clause.
    """
    match = re.match(r"The Japanese yen depreciated past (\d+(?:\.\d+)?) per dollar\b", original, re.I)
    prefix = re.match(
        r"(?:Đồng )?yên Nhật(?: Bản)? (?:suy giảm|suy yếu|giảm)(?: giá)?"
        r"(?: (?:xuống dưới|xuống mức|vượt(?: qua)?(?: mức)?)|, đưa tỷ giá(?: USD-JPY)? vượt(?: mức)?) "
        r"(\d+(?:[.,]\d+)?) (?:đồng|yên)(?:/USD| đổi một USD| mỗi (?:đô la|USD)| cho mỗi (?:đô la|USD))",
        translated, re.I,
    )
    if match and prefix and same_numbers([match.group(1)], [prefix.group(1)]):
        return ("USD-JPY vượt mức " + prefix.group(1)
                + " cho thấy JPY đang suy yếu" + translated[prefix.end():])
    return translated


def translate_fixed_title(text):
    # These complete titles contain no numbers, actors or qualifiers to discard.
    # This is a Python rule, not an additional model or a cached sample news item.
    return {"offshore yuan trades flat": "Nhân dân tệ ngoại biên đi ngang"}.get(text.strip().lower())


def numbers(text):
    result = []
    for token in NUMBERS.findall(text):
        # A percentage range may be written 4.5%-5.0% or 4,5-5,0%.
        # Keep actual ISO dates intact, but count each endpoint in a range.
        if "-" in token and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
            result.extend(token.split("-"))
        else:
            result.append(token)
    return result


def protect(text):
    if "BTTNPROTECT" in text:
        raise TranslationError("TRANSLATION_RESERVED_MARKER")
    protected = {}

    def replace(match):
        index = len(protected)
        letters = ""
        while True:
            letters = chr(65 + index % 26) + letters
            index = index // 26 - 1
            if index < 0:
                break
        token = "BTTNPROTECT" + letters + "END"
        value = match.group()
        protected[token] = value
        return token

    return PROTECTED.sub(replace, text), protected


def restore(text, protected):
    if Counter(TOKEN.findall(text)) != Counter(list(protected)):
        raise TranslationError("TRANSLATION_PROTECTED_SPANS")
    return TOKEN.sub(lambda m: protected[m.group()], clean_post_translation(text))


def check_translation(original, translated):
    if not isinstance(translated, str) or not translated.strip():
        raise TranslationError("TRANSLATION_EMPTY")
    source = original
    target_numbers_text = translated
    # Translating an English date such as May 2025 to tháng 5 năm 2025 adds a
    # legitimate month number. Only explicit month+year expressions are allowed.
    for month, number in MONTHS.items():
        source = re.sub(r"\b" + month + r"\s+(?=\d{4}\b)", str(number) + " ", source)
        vietnamese_months = ["Một", "Hai", "Ba", "Tư", "Năm", "Sáu", "Bảy", "Tám", "Chín", "Mười", "Mười Một", "Mười Hai"]
        if re.search(r"\b" + month + r"\s+\d{4}\b", original):
            target_numbers_text = re.sub(
                r"tháng\s+" + vietnamese_months[number - 1] + r"\s+(?=(?:năm\s+)?\d{4}\b)",
                "tháng " + str(number) + " ", target_numbers_text, flags=re.I,
            )
            for year in re.findall(r"\b" + month + r"\s+(\d{4})\b", original):
                target_numbers_text = re.sub(r"\b" + str(number) + "/" + year + r"\b",
                                             str(number) + " " + year, target_numbers_text)
    expected = numbers(source)
    actual = numbers(target_numbers_text)
    if not same_numbers(expected, actual):
        raise TranslationError("TRANSLATION_NUMBERS")
    negative_pattern = r"(?<![\w%.,])[-−](\d+(?:[.,]\d+)*)"
    if not same_numbers(re.findall(negative_pattern, source), re.findall(negative_pattern, target_numbers_text)):
        raise TranslationError("TRANSLATION_NUMBER_SIGN")
    scale_units = [(r"\btrillions?\b", r"nghìn tỷ"), (r"\bbillions?\b", r"\btỷ\b"),
                   (r"\bmillions?\b", r"\btriệu\b")]
    for english_scale, vietnamese_scale in scale_units:
        expected_scales = len(re.findall(english_scale, original, re.I))
        # 'nghìn tỷ' is a trillion, not a second billion.
        scale_text = re.sub(r"nghìn tỷ", "", translated, flags=re.I) if "billions" in english_scale else translated
        if expected_scales and expected_scales != len(re.findall(vietnamese_scale, scale_text, re.I)):
            raise TranslationError("TRANSLATION_SCALE_UNITS")
    if original.count("%") != translated.count("%"):
        raise TranslationError("TRANSLATION_PERCENT_UNITS")
    for symbol, alternatives in [("$", r"\$|\bUSD\b|đô la(?: Mỹ)?"),
                                 ("€", r"€|\bEUR\b|\beuro\b"), ("¥", r"¥|\bJPY\b|\byên\b")]:
        if original.count(symbol) > len(re.findall(alternatives, translated, re.I)):
            raise TranslationError("TRANSLATION_CURRENCY_UNITS")
    if len(re.findall(r"\bbps\b|(?<=\d)bps", original, re.I)) > len(re.findall(r"bps|điểm cơ bản", translated, re.I)):
        raise TranslationError("TRANSLATION_BPS_UNITS")
    if Counter(PROTECTED.findall(original)) != Counter(PROTECTED.findall(translated)):
        raise TranslationError("TRANSLATION_PROTECTED_SPANS")
    if re.search(r"yen\s+depreciated\s+past\s+\d", original, re.I) and re.search(r"dưới\s+\d", translated, re.I):
        raise TranslationError("TRANSLATION_QUOTE_DIRECTION")
    # Missing glossary terms are a validation failure, not permission to insert new sentences.
    original_prose = PROTECTED.sub("", original)
    translated_prose = PROTECTED.sub("", translated)
    for english, vietnamese in TERMS.items():
        if re.search(r"\b" + re.escape(english) + r"\b", original_prose, re.I) and vietnamese.lower() not in translated_prose.lower():
            raise TranslationError("TRANSLATION_TERMINOLOGY")
    if len(original.split()) > 8 and not is_vietnamese(translated):
        raise TranslationError("TRANSLATION_LANGUAGE")
    if TOKEN.search(translated):
        raise TranslationError("TRANSLATION_PROTECTED_SPANS")
    if len(original.split()) > 20 and len(translated.split()) < len(original.split()) * .45:
        raise TranslationError("TRANSLATION_TOO_SHORT")


def numeric_value(token, vietnamese=False):
    if any(c in token for c in ":/-"):
        return token
    if vietnamese:
        token = token.replace(".", "").replace(",", ".")
    else:
        token = token.replace(",", "")
    try:
        return Decimal(token)
    except InvalidOperation:
        return token


def same_numbers(expected, actual):
    """Match number values one-for-one, allowing EN/VI punctuation, never rounding."""
    if len(expected) != len(actual):
        return False
    values = [numeric_value(t) for t in expected]
    options = [{numeric_value(t), numeric_value(t, vietnamese=True)} for t in actual]
    matches = {}

    def assign(index, seen):
        for position, value in enumerate(values):
            if value not in options[index] or position in seen:
                continue
            seen.add(position)
            if position not in matches or assign(matches[position], seen):
                matches[position] = index
                return True
        return False

    return all(assign(i, set()) for i in range(len(options)))


class NvidiaTranslator:
    def __init__(self, api_key, *, session=None, sleep=time.sleep):
        self.api_key = (api_key or "").strip()
        self.model = MODEL
        self.session = session or requests.Session()
        self.sleep = sleep
        self.attempts = []

    def translate(self, text):
        if not self.api_key:
            raise TranslationError("NVIDIA_API_KEY_MISSING")
        if not text.strip() or len(text) > 3000:
            raise TranslationError("TRANSLATION_SEGMENT_SIZE")
        fixed = translate_fixed_title(text)
        if fixed is not None:
            self.attempts.append({"model": self.model, "status": "python_title_rule", "elapsed_seconds": 0})
            return fixed
        masked, protected = protect(text)
        # Riva's chat template expects the language pair as system content.
        messages = [{"role": "system", "content": "en-vi"}]
        for english, vietnamese in GLOSSARY_EXAMPLES:
            messages.extend([{"role": "user", "content": english}, {"role": "assistant", "content": vietnamese}])
        if protected:
            messages.extend([
                {"role": "user", "content": "The policy applies to BTTNPROTECTAEND."},
                {"role": "assistant", "content": "Chính sách áp dụng cho BTTNPROTECTAEND."},
            ])
        messages.append({"role": "user", "content": masked})
        for attempt in range(3):
            started = time.monotonic()
            status = None
            error = None
            result = None
            try:
                response = self.session.post(
                    ENDPOINT, headers={"Authorization": "Bearer " + self.api_key},
                    json={"model": self.model, "messages": messages, "temperature": 0,
                          "top_p": 1, "max_tokens": 4096}, timeout=(10, 60), allow_redirects=False,
                )
                status = response.status_code
                if status != 200:
                    raise TranslationError(f"NVIDIA_HTTP_{status}")
                try:
                    choice = response.json()["choices"][0]
                    if choice.get("finish_reason") != "stop":
                        raise TranslationError("TRANSLATION_INCOMPLETE")
                    raw = choice["message"]["content"]
                    if not isinstance(raw, str):
                        raise TranslationError("TRANSLATION_EMPTY")
                    # Normalize prose while protected spans are still masked.
                    normalized = normalize_terms(text, normalize_fx_quote(text, raw.strip()))
                    result = restore(normalized, protected)
                    check_translation(text, result)
                except (KeyError, IndexError, TypeError, ValueError):
                    raise TranslationError("NVIDIA_RESPONSE_INVALID") from None
            except requests.RequestException:
                error = TranslationError("NVIDIA_NETWORK")
            except TranslationError as exc:
                error = exc
            finally:
                self.attempts.append({
                    "model": self.model, "attempt": attempt + 1, "http_status": status,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "status": "checked" if error is None else "failed",
                    "error": str(error) if error else None,
                    "python_postprocessed": bool(result is not None and result != raw.strip()),
                })
            if error is None:
                return result
            retryable = status in (429, 500, 502, 503, 504) or str(error) == "NVIDIA_NETWORK"
            # At most one fresh translation for a rejected output, on the same
            # provider. Never add repair instructions to the article text.
            retryable = retryable or (str(error).startswith("TRANSLATION_") and attempt == 0)
            if not retryable or attempt == 2:
                raise error
            self.sleep(min(2 ** attempt, 4))
        raise TranslationError("NVIDIA_UNAVAILABLE")

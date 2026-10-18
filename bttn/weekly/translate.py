"""Dịch chéo bản tin tuần bằng DeepSeek V4.1 Flash (cùng khóa DEEPSEEK_API_KEY với bản tin ngày BTTN).

Không có DEEPSEEK_API_KEY thì dùng NVIDIA (riva-translate + mô hình ngôn ngữ dự phòng) như trước.

Trang 1 viết tiếng Việt -> dịch sang tiếng Anh; trang 2 viết tiếng Anh -> dịch sang tiếng Việt.
Dịch từng câu để giữ đúng cấu trúc; mọi con số của câu gốc phải còn nguyên trong bản dịch
(so theo giá trị, chấp nhận đổi dấu thập phân/nghìn giữa hai ngôn ngữ). Sai thì thử lại một lần,
vẫn sai thì bỏ câu đó và báo lỗi, không bao giờ để số sai.

Bản dịch dài hơn giới hạn từ/dòng của bản đích thì nhờ mô hình ngôn ngữ (NVIDIA, NVIDIA_CONDENSE_MODEL)
rút gọn về đúng khoảng số từ, bỏ thông tin ít quan trọng. Bản rút gọn chỉ được dùng khi: đúng khoảng
số từ, không quá số dòng, và mọi con số trong đó đều có trong bản dịch đầy đủ (không bịa số mới).
Không đạt thì quay về cách cũ: bỏ bớt câu cuối.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter

import requests

from .. import deepseek_translation as deepseek
from ..editorial import sentences as split_sentences
from ..nvidia_translation import ENDPOINT, MODEL
from .fit import count_lines, count_words

NUM = re.compile(r"\d[\d.,]*\d|\d")
MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December|"
          "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
# Ngày tháng được phép đổi cách viết khi dịch (12.10 -> October 12) nên không so như số liệu.
# Tiếng Việt dùng dấu phẩy thập phân, nên "12.10" chắc chắn là ngày; tiếng Anh thì "3.1" là số,
# chỉ coi là ngày khi có năm (16.10.2026) hoặc viết bằng tên tháng.
_DOTTED = r"\d{1,2}[./]\d{1,2}"
_YEAR_DOTTED = r"\d{1,2}[./]\d{1,2}[./]\d{2,4}"
DATES = {
    "vi": [re.compile(rf"\b{_DOTTED}(?:[./]\d{{2,4}})?(?:\s*[-–]\s*{_DOTTED}(?:[./]\d{{2,4}})?)?\b"),
           re.compile(r"\bngày\s+\d{1,2}(?:\s+tháng\s+\d{1,2})?(?:\s+năm\s+\d{4})?", re.I)],
    "en": [re.compile(rf"\b(?:{_DOTTED}\s*[-–]\s*)?{_YEAR_DOTTED}\b"),
           re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
           re.compile(rf"\b(?:{MONTHS})\.?\s+\d{{1,2}}(?:\s*[-–]\s*\d{{1,2}})?(?:st|nd|rd|th)?(?:,\s*\d{{4}})?", re.I),
           re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s*(?:[-–]\s*\d{{1,2}}\s+)?(?:of\s+)?(?:{MONTHS})\b(?:,?\s*\d{{4}})?", re.I)],
}
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
CONDENSE_MODEL = "mistralai/mistral-large-2-instruct"
# NVIDIA thỉnh thoảng rút mô hình khỏi danh mục (HTTP 404/410): thử lần lượt các mô hình sau
LLM_MODELS = ("mistralai/mistral-large-2-instruct", "google/gemma-4-31b-it", "deepseek-ai/deepseek-v4.1-flash",
              "nvidia/llama-3.1-nemotron-70b-instruct", "meta/llama-3.3-70b-instruct")
GONE = ("NVIDIA_HTTP_404", "NVIDIA_HTTP_410", "NVIDIA_HTTP_400")
TRANSLATE_PROMPT = {
    "vi-en": ("Translate this sentence from a Vietnamese bank market bulletin into professional English. Keep every "
              "number in digits exactly as in the source, written in English format (1,612.28; 3.1%); keep amounts in "
              "billion (tỷ = billion, do not convert to trillion); do not add or drop information. Return only the translation."),
    "en-vi": ("Dịch câu sau trong bản tin thị trường của ngân hàng sang tiếng Việt chuyên nghiệp. Giữ mọi con số bằng "
              "chữ số đúng như bản gốc, viết theo kiểu Việt Nam (1.612,28; 3,1%); không thêm hay bỏ thông tin. "
              "Chỉ trả về bản dịch."),
}
CONDENSE_PROMPT = {
    "en": ("You edit a bank's weekly market bulletin. Shorten the paragraph to between {lo} and {hi} words. "
           "Remove the least important details first (secondary comments, repeated context). Keep every remaining "
           "number exactly as written, keep the facts and the order, do not add any new information or numbers. "
           "Return only the shortened paragraph."),
    "vi": ("Bạn biên tập bản tin thị trường tuần của ngân hàng. Rút gọn đoạn văn còn từ {lo} đến {hi} từ. "
           "Ưu tiên bỏ chi tiết ít quan trọng (bình luận phụ, bối cảnh lặp lại). Giữ nguyên cách viết mọi con số "
           "còn lại, giữ đúng sự kiện và thứ tự, không thêm thông tin hay con số mới. Chỉ trả về đoạn văn đã rút gọn."),
}


LOG = logging.getLogger("bttn.weekly.translate")
TIME_BUDGET = int(os.getenv("WEEKLY_AI_BUDGET_SECONDS", "900"))   # quá 15 phút gọi AI thì dừng gọi, dùng phần đã có


class WeeklyTranslationError(RuntimeError):
    pass


WORDS = {
    "en": {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12, "first": 1, "second": 2, "third": 3, "fourth": 4,
           "half": 2, "dozen": 12},
    "vi": {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10},
}
# Đơn vị lớn: 1.000 tỷ = 1 trillion; 13,3 nghìn tỷ = 13.3 trillion (đưa về đơn vị "tỷ/billion")
SCALE = {"en": re.compile(r"(\d[\d.,]*)\s*trillion", re.I), "vi": re.compile(r"(\d[\d.,]*)\s*nghìn\s+tỷ", re.I)}


def _value(raw: str, lang: str) -> float | None:
    if lang == "vi":
        norm = raw.replace(".", "").replace(",", ".") if re.search(r"\.\d{3}(?!\d)", raw) or "," in raw else raw
    else:
        norm = raw.replace(",", "") if re.search(r",\d{3}(?!\d)", raw) else raw.replace(",", ".")
    try:
        return round(float(norm), 6)
    except ValueError:
        return None


def _strip_dates(text: str, lang: str) -> str:
    for pattern in DATES[lang]:
        text = pattern.sub(" ", text)
    return YEAR.sub(" ", text)


def numbers(text: str, lang: str) -> list[float]:
    """Giá trị số viết bằng chữ số. vi: 25.870 = 25870, 3,1 = 3.1; en: 25,870 = 25870, 3.1 = 3.1.
    "1 trillion" / "1 nghìn tỷ" tính là 1000 (đơn vị tỷ) để khớp "1.000 tỷ"."""
    text = _strip_dates(text, lang)
    values = []

    def scaled(m):
        v = _value(m.group(1), lang)
        if v is not None:
            values.append(round(v * 1000, 6))
        return " "
    text = SCALE[lang].sub(scaled, text)
    for raw in NUM.findall(text):
        v = _value(raw, lang)
        if v is not None:
            values.append(v)
    return sorted(values)


def word_numbers(text: str, lang: str) -> list[float]:
    """Số viết bằng chữ (five sessions, nine months, third quarter, chín tháng)."""
    words = re.findall(r"[^\W\d_]+", _strip_dates(text, lang).lower())
    return [float(WORDS[lang][w]) for w in words if w in WORDS[lang]]


def same_numbers(src: str, src_lang: str, out: str, out_lang: str) -> bool:
    """Bản dịch giữ đúng các con số: không thêm số lạ; số nào không còn ở dạng chữ số thì phải có ở dạng chữ."""
    a, b = Counter(numbers(src, src_lang)), Counter(numbers(out, out_lang))
    if b - a:
        return False
    missing = a - b
    if not missing:
        return True
    words = Counter(word_numbers(out, out_lang))
    return not (missing - words) and all(v <= 12 for v in missing)


GLOSSARY_EN = [(r"\bNHNN\b", "SBV"), (r"\bTPCP\b", "government bond"), (r"\bKBNN\b", "the State Treasury"),
               (r"\breposo\b", "repo"), (r"\bthe the\b", "the"), (r"\bVND(\d)", r"VND \1")]


def glossary(text: str, lang: str) -> str:
    """Sửa vài thuật ngữ mô hình dịch hay để nguyên tiếng Việt hoặc dịch sai."""
    if lang != "en":
        return text
    for pat, rep in GLOSSARY_EN:
        text = re.sub(pat, rep, text)
    return text


class Translator:
    def __init__(self, api_key=None, *, session=None, sleep=time.sleep, deepseek_key=None):
        self.api_key = (api_key or os.getenv("NVIDIA_API_KEY") or "").strip()
        self.deepseek_key = (deepseek_key if deepseek_key is not None else os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.provider = "deepseek" if self.deepseek_key else "nvidia"
        self.session = session or requests.Session()
        self.sleep = sleep
        self.log = []
        self.started = None
        self.calls = 0

    def _call(self, text: str, pair: str) -> str:
        return self._chat(MODEL, pair, text)

    def _llm(self, system: str, text: str, temperature: float = 0) -> str:
        """DeepSeek nếu có khóa; nếu không (hoặc DeepSeek lỗi) gọi mô hình ngôn ngữ của NVIDIA,
        mô hình bị rút (404/410) thì chuyển sang mô hình kế tiếp."""
        if self.deepseek_key:
            try:
                return self._deepseek(system, text, temperature).strip('"')
            except (WeeklyTranslationError, requests.RequestException, KeyError, ValueError) as exc:
                self.log.append({"pair": "llm", "status": "deepseek_failed", "error": str(exc)})
                if not self.api_key or str(exc) == "AI_TIME_BUDGET":
                    raise WeeklyTranslationError(str(exc)) from None
        first = os.getenv("NVIDIA_CONDENSE_MODEL")
        models = getattr(self, "_models", None) or [m for m in ((first,) if first else ()) + LLM_MODELS if m]
        last = None
        while models:
            try:
                out = self._chat(models[0], system, text, temperature)
                self._models = models
                return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"')
            except WeeklyTranslationError as exc:
                last = exc
                if str(exc) not in GONE:
                    raise
                self.log.append({"pair": "llm", "status": "model_unavailable", "model": models[0], "error": str(exc)})
                models = models[1:]
        self._models = []
        raise last or WeeklyTranslationError("NVIDIA_NO_LLM")

    def _chat(self, model: str, system: str, text: str, temperature: float = 0) -> str:
        if not self.api_key:
            raise WeeklyTranslationError("NVIDIA_API_KEY_MISSING")
        self._tick()
        resp = self.session.post(ENDPOINT, headers={"Authorization": "Bearer " + self.api_key},
                                 json={"model": model, "messages": [{"role": "system", "content": system},
                                                                    {"role": "user", "content": text}],
                                       "temperature": temperature, "top_p": 1, "max_tokens": 2048},
                                 timeout=(10, 45), allow_redirects=False)
        if resp.status_code != 200:
            raise WeeklyTranslationError(f"NVIDIA_HTTP_{resp.status_code}")
        return self._content(resp)

    def _deepseek(self, system: str, text: str, temperature: float = 0) -> str:
        self._tick()
        resp = self.session.post(deepseek.ENDPOINT, headers={"Authorization": "Bearer " + self.deepseek_key},
                                 json={"model": deepseek.MODEL, "messages": [{"role": "system", "content": system},
                                                                             {"role": "user", "content": text}],
                                       "temperature": temperature, "max_tokens": 4096, "stream": False},
                                 timeout=(10, 60), allow_redirects=False)
        if resp.status_code != 200:
            raise WeeklyTranslationError(f"DEEPSEEK_HTTP_{resp.status_code}")
        return deepseek.strip_wrappers(self._content(resp))

    def _content(self, resp) -> str:
        choice = resp.json()["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise WeeklyTranslationError("TRANSLATION_INCOMPLETE")
        return re.sub(r"<think>.*?</think>", "", " ".join((choice["message"]["content"] or "").split()), flags=re.S).strip()

    def _tick(self):
        self.started = self.started or time.monotonic()
        if time.monotonic() - self.started > TIME_BUDGET:
            raise WeeklyTranslationError("AI_TIME_BUDGET")
        self.calls += 1
        if self.calls % 10 == 0:
            LOG.info("AI: %d lượt gọi, %.0f giây", self.calls, time.monotonic() - self.started)

    def sentence(self, text: str, src: str, dst: str) -> str:
        return glossary(self._sentence(text, src, dst), dst)

    def _sentence(self, text: str, src: str, dst: str) -> str:
        """Dịch một câu: mô hình dịch trước; sai số liệu thì nhờ mô hình ngôn ngữ dịch lại với yêu cầu giữ số."""
        pair = f"{src}-{dst}"
        last = None
        if self.deepseek_key:   # DeepSeek dịch trước, sai số thì thử riva-translate của NVIDIA
            attempts = (lambda: self._llm(TRANSLATE_PROMPT[pair], text),) + ((lambda: self._call(text, pair),)
                                                                            if self.api_key else ())
        else:
            attempts = (lambda: self._call(text, pair),
                        lambda: self._llm(TRANSLATE_PROMPT[pair], text))
        for attempt, run in enumerate(attempts):
            try:
                out = run()
                if not same_numbers(text, src, out, dst):
                    raise WeeklyTranslationError("TRANSLATION_NUMBERS")
                self.log.append({"pair": pair, "status": "ok", "attempt": attempt, "src": text, "out": out})
                return out
            except (WeeklyTranslationError, requests.RequestException, KeyError, ValueError) as exc:
                last = exc
                self.log.append({"pair": pair, "status": "failed", "attempt": attempt, "error": str(exc),
                                 "src": text, "out": locals().get("out")})
                self.sleep(1)
        raise WeeklyTranslationError(str(last))

    def condense(self, text: str, lang: str, lo: int, hi: int, max_lines=None) -> str | None:
        """AI rút gọn đoạn về [lo, hi] từ; trả None nếu sau 2 lần vẫn không đạt điều kiện."""
        allowed = Counter(numbers(text, lang))
        system = CONDENSE_PROMPT[lang].format(lo=lo, hi=hi)
        prompt = text
        for _ in range(2):
            try:
                out = self._llm(system, prompt, temperature=0.2)
            except (WeeklyTranslationError, requests.RequestException, KeyError, ValueError) as exc:
                self.log.append({"pair": f"condense-{lang}", "status": "failed", "error": str(exc)})
                return None
            n = count_words(out)
            new_numbers = Counter(numbers(out, lang)) - allowed - Counter(word_numbers(text, lang))
            if lo <= n <= hi and not new_numbers and (not max_lines or count_lines(out) <= max_lines):
                self.log.append({"pair": f"condense-{lang}", "status": "ok", "words": n})
                return out
            reason = f"{n} từ" if not new_numbers else f"có số không có trong bản gốc {sorted(new_numbers)}"
            self.log.append({"pair": f"condense-{lang}", "status": "rejected", "error": reason})
            prompt = (f"{text}\n\n(Your previous version had {n} words; it must have {lo}-{hi} words "
                      "and only numbers from the original.)" if lang == "en" else
                      f"{text}\n\n(Bản trước có {n} từ; bắt buộc {lo}-{hi} từ và chỉ dùng số có trong bản gốc.)")
        return None

    def paragraph(self, text: str, src: str, dst: str, *, min_words=None, max_words=None,
                  max_lines=None) -> tuple[str, list[str]]:
        """Dịch từng câu (câu dịch lỗi bị bỏ). Vượt giới hạn từ/dòng của bản đích thì AI rút gọn;
        AI không đạt thì bỏ bớt các câu cuối."""
        out, problems = [], []
        for s in split_sentences(text):
            try:
                out.append(self.sentence(s, src, dst))
            except WeeklyTranslationError as exc:
                problems.append(f"bỏ câu không dịch được ({exc}): {s[:60]}…")
        full = " ".join(out)

        def fits(t):
            return not ((max_words and count_words(t) > max_words) or (max_lines and count_lines(t) > max_lines))

        if fits(full):
            return full, problems
        if max_words:
            lo = min_words or max(1, round(max_words * 0.9))
            short = self.condense(full, dst, lo, max_words, max_lines)
            if short:
                return short, problems
            reason = next((e.get("error") for e in reversed(self.log) if e.get("pair", "").startswith("condense")), "")
        kept = []
        for t in out:
            if not fits(" ".join(kept + [t])):
                break
            kept.append(t)
        problems.append("bản dịch dài hơn giới hạn, AI rút gọn không đạt" + (f" ({reason})" if max_words and reason else "")
                        + " nên bỏ các câu cuối")
        return " ".join(kept), problems

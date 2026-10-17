"""Dịch chéo bản tin tuần bằng mô hình dịch NVIDIA (cùng mô hình BTTN đang dùng).

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

import os
import re
import time
from collections import Counter

import requests

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
CONDENSE_MODEL = "meta/llama-3.3-70b-instruct"
CONDENSE_PROMPT = {
    "en": ("You edit a bank's weekly market bulletin. Shorten the paragraph to between {lo} and {hi} words. "
           "Remove the least important details first (secondary comments, repeated context). Keep every remaining "
           "number exactly as written, keep the facts and the order, do not add any new information or numbers. "
           "Return only the shortened paragraph."),
    "vi": ("Bạn biên tập bản tin thị trường tuần của ngân hàng. Rút gọn đoạn văn còn từ {lo} đến {hi} từ. "
           "Ưu tiên bỏ chi tiết ít quan trọng (bình luận phụ, bối cảnh lặp lại). Giữ nguyên cách viết mọi con số "
           "còn lại, giữ đúng sự kiện và thứ tự, không thêm thông tin hay con số mới. Chỉ trả về đoạn văn đã rút gọn."),
}


class WeeklyTranslationError(RuntimeError):
    pass


def numbers(text: str, lang: str) -> list[float]:
    """Giá trị số trong câu. vi: 25.870 = 25870, 3,1 = 3.1; en: 25,870 = 25870, 3.1 = 3.1."""
    values = []
    for pattern in DATES[lang]:
        text = pattern.sub(" ", text)
    text = YEAR.sub(" ", text)
    for raw in NUM.findall(text):
        if lang == "vi":
            norm = raw.replace(".", "").replace(",", ".") if re.search(r"\.\d{3}(?!\d)", raw) or "," in raw else raw
        else:
            norm = raw.replace(",", "") if re.search(r",\d{3}(?!\d)", raw) else raw.replace(",", ".")
        try:
            values.append(round(float(norm), 6))
        except ValueError:
            continue
    return sorted(values)


class Translator:
    def __init__(self, api_key=None, *, session=None, sleep=time.sleep):
        self.api_key = (api_key or os.getenv("NVIDIA_API_KEY") or "").strip()
        self.session = session or requests.Session()
        self.sleep = sleep
        self.log = []

    def _call(self, text: str, pair: str) -> str:
        return self._chat(MODEL, pair, text)

    def _chat(self, model: str, system: str, text: str, temperature: float = 0) -> str:
        if not self.api_key:
            raise WeeklyTranslationError("NVIDIA_API_KEY_MISSING")
        resp = self.session.post(ENDPOINT, headers={"Authorization": "Bearer " + self.api_key},
                                 json={"model": model, "messages": [{"role": "system", "content": system},
                                                                    {"role": "user", "content": text}],
                                       "temperature": temperature, "top_p": 1, "max_tokens": 2048},
                                 timeout=(10, 90), allow_redirects=False)
        if resp.status_code != 200:
            raise WeeklyTranslationError(f"NVIDIA_HTTP_{resp.status_code}")
        choice = resp.json()["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise WeeklyTranslationError("TRANSLATION_INCOMPLETE")
        return " ".join(choice["message"]["content"].split())

    def sentence(self, text: str, src: str, dst: str) -> str:
        pair = f"{src}-{dst}"
        last = None
        for attempt in range(2):
            try:
                out = self._call(text, pair)
                if numbers(out, dst) != numbers(text, src):
                    raise WeeklyTranslationError("TRANSLATION_NUMBERS")
                self.log.append({"pair": pair, "status": "ok"})
                return out
            except (WeeklyTranslationError, requests.RequestException, KeyError, ValueError) as exc:
                last = exc
                self.log.append({"pair": pair, "status": "failed", "error": str(exc)})
                self.sleep(1)
        raise WeeklyTranslationError(str(last))

    def condense(self, text: str, lang: str, lo: int, hi: int, max_lines=None) -> str | None:
        """AI rút gọn đoạn về [lo, hi] từ; trả None nếu sau 2 lần vẫn không đạt điều kiện."""
        model = os.getenv("NVIDIA_CONDENSE_MODEL") or CONDENSE_MODEL
        allowed = Counter(numbers(text, lang))
        system = CONDENSE_PROMPT[lang].format(lo=lo, hi=hi)
        prompt = text
        for _ in range(2):
            try:
                out = self._chat(model, system, prompt, temperature=0.2).strip().strip('"')
            except (WeeklyTranslationError, requests.RequestException, KeyError, ValueError) as exc:
                self.log.append({"pair": f"condense-{lang}", "status": "failed", "error": str(exc)})
                return None
            n = count_words(out)
            new_numbers = Counter(numbers(out, lang)) - allowed
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
        kept = []
        for t in out:
            if not fits(" ".join(kept + [t])):
                break
            kept.append(t)
        problems.append("bản dịch dài hơn giới hạn, AI rút gọn không đạt nên bỏ các câu cuối")
        return " ".join(kept), problems

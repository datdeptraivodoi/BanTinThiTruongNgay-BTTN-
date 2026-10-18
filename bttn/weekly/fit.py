"""Đếm số dòng một đoạn sẽ chiếm trong cột của bản tin tuần, và kiểm tra giới hạn từ/dòng.

Word ngắt dòng theo kiểu tham lam (đủ chỗ thì giữ từ trên dòng). Ở đây đo bề rộng từng từ
bằng chính font Times New Roman 11 (hoặc Liberation Serif, cùng số đo) nên số dòng khớp
với file mẫu: 12/12 đoạn mẫu VIE/ENG cho đúng số dòng như khi mở file.
"""
import json
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

ROOT = Path(__file__).resolve().parents[2]
SCALE = 100  # đo ở cỡ lớn cho chính xác rồi chia lại

FONT_CANDIDATES = {
    False: ["/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf",
            "/usr/share/fonts/truetype/msttcorefonts/times.ttf",
            "C:/Windows/Fonts/times.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"],
    True: ["/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold.ttf",
           "/usr/share/fonts/truetype/msttcorefonts/timesbd.ttf",
           "C:/Windows/Fonts/timesbd.ttf",
           "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"],
}


@lru_cache
def rules():
    return json.loads((ROOT / "config" / "weekly_rules.json").read_text(encoding="utf-8"))


@lru_cache
def _font(bold: bool, size_pt: float):
    for path in FONT_CANDIDATES[bold]:
        if Path(path).is_file():
            return ImageFont.truetype(path, int(size_pt * SCALE))
    raise FileNotFoundError("Không thấy font Times New Roman hoặc Liberation Serif để đo dòng")


def count_words(text: str) -> int:
    return len(text.split())


def count_lines(text: str, *, bold=False, width_pt=None, size_pt=None) -> int:
    cfg = rules()
    width = width_pt or cfg["column_width_pt"]
    font = _font(bold, size_pt or cfg["font_size_pt"])
    space = font.getlength(" ") / SCALE
    lines, current = 1, 0.0
    for word in text.split():
        w = font.getlength(word) / SCALE
        if current == 0:
            current = w
        elif current + space + w <= width:
            current += space + w
        else:
            lines += 1
            current = w
    return lines


def check_content(content) -> list[dict]:
    """Lỗi giới hạn của một bản (vi hoặc en). Rỗng nghĩa là đạt."""
    cfg = rules()
    lang = content.lang
    issues = []

    def issue(code, where, msg):
        issues.append({"code": code, "where": where, "message": msg})

    for name, limit in cfg["sections"][lang].items():
        section = content.sections[name]
        body = " ".join(section.body)
        n, lines = count_words(body), count_lines(body)
        lo, hi = limit["words"]
        if lines > limit["max_lines"]:
            issue("TOO_MANY_LINES", name, f"{lines} dòng, tối đa {limit['max_lines']} (sẽ tràn trang)")
        if not lo <= n <= hi:
            issue("WORD_COUNT", name, f"{n} từ, yêu cầu {lo}–{hi}")
        if section.forecast and count_lines(section.forecast, bold=True) > cfg["forecast_max_lines"]:
            issue("TOO_MANY_LINES", name + ".forecast", f"dự báo quá {cfg['forecast_max_lines']} dòng")
    news = cfg["news"][lang]
    if len(content.news) != news["items"]:
        issue("NEWS_COUNT", "news", f"{len(content.news)} tin, yêu cầu {news['items']}")
    lo, hi = news["item_words"]
    for i, item in enumerate(content.news, 1):
        if not lo <= count_words(item) <= hi:
            issue("WORD_COUNT", f"news[{i}]", f"{count_words(item)} từ, yêu cầu {lo}–{hi}")
    total = sum(count_words(x) for x in content.news)
    lo, hi = news["total_words"]
    if not lo <= total <= hi:
        issue("WORD_COUNT", "news", f"tổng {total} từ, yêu cầu {lo}–{hi}")
    return issues


def trim_to_lines(text: str, max_lines: int) -> str:
    """Bỏ bớt câu cuối cho đến khi vừa số dòng (không cắt giữa câu)."""
    import re

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    while len(sentences) > 1 and count_lines(" ".join(sentences)) > max_lines:
        sentences.pop()
    return " ".join(sentences)

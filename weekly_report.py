"""Bản tin thị trường tuần: dựng file VIE và ENG đúng định dạng mẫu.

Ví dụ:
  python weekly_report.py --friday 2026-10-16 --content-vi vi.json --content-en en.json
  python weekly_report.py --extract-sample      # tạo JSON nội dung từ chính 2 file mẫu
Chưa gửi email: giai đoạn này chỉ dựng định dạng.
"""
import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from bttn.weekly.content import WeeklyContent
from bttn.weekly.extract import extract_weekly
from bttn.weekly.layout import calendar_title, week_bounds, week_label, weekly_filename
from bttn.weekly.render import render_weekly

ROOT = Path(__file__).resolve().parent
TEMPLATES = {"vi": ROOT / "templates/weekly/BT_TTT_template_VIE.docx",
             "en": ROOT / "templates/weekly/BT_TTT_template_ENG.docx"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--friday", default=date.today().isoformat())
    ap.add_argument("--content-vi")
    ap.add_argument("--content-en")
    ap.add_argument("--output-dir", default=str(ROOT / "output/weekly"))
    ap.add_argument("--extract-sample", action="store_true",
                    help="đọc 2 file mẫu thành JSON nội dung trong output-dir/sample")
    args = ap.parse_args(argv)
    out = Path(args.output_dir)
    if args.extract_sample:
        for lang, tpl in TEMPLATES.items():
            content = extract_weekly(tpl, lang, image_dir=out / "sample" / f"images_{lang}")
            path = out / "sample" / f"content_{lang}.json"
            path.write_text(content.model_dump_json(indent=2), encoding="utf-8")
            print("Đã tạo", path)
        return 0
    friday = date.fromisoformat(args.friday)
    for lang, src in (("vi", args.content_vi), ("en", args.content_en)):
        if not src:
            continue
        content = WeeklyContent.model_validate(json.loads(Path(src).read_text(encoding="utf-8")))
        # Nhãn tuần và tiêu đề lịch sự kiện (tuần kế tiếp) luôn tính từ ngày thứ Sáu của kỳ bản tin.
        content.week_label = week_label(friday, lang)
        content.calendar.title = calendar_title(week_bounds(friday)[0] + timedelta(days=7), lang)
        path = render_weekly(content, TEMPLATES[lang], out / weekly_filename(friday, lang))
        print("Đã tạo", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

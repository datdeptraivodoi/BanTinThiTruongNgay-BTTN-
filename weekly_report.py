"""Bản tin thị trường tuần (thứ Sáu): hai file Word VIE và ENG đúng định dạng mẫu.

Chạy thật trên VPS (thứ Sáu 11:30):
  python weekly_report.py --collect --review        # lấy dữ liệu, viết, dịch, dựng 2 file, gửi người duyệt
  python weekly_report.py --collect                 # chỉ dựng file, không gửi
Công cụ:
  python weekly_report.py --extract-sample          # đọc 2 file mẫu thành JSON nội dung
  python weekly_report.py --friday 2026-10-16 --content-vi vi.json --content-en en.json
"""
import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bttn.weekly.content import WeeklyContent
from bttn.weekly.extract import extract_weekly
from bttn.weekly.layout import calendar_title, week_bounds, week_label, weekly_filename
from bttn.weekly.render import render_weekly

ROOT = Path(__file__).resolve().parent
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
TEMPLATES = {"vi": ROOT / "templates/weekly/BT_TTT_template_VIE.docx",
             "en": ROOT / "templates/weekly/BT_TTT_template_ENG.docx"}
LOG = logging.getLogger("bttn.weekly")


def collect_and_build(friday: date, out: Path, *, review: bool) -> int:
    from dotenv import load_dotenv

    from bttn.http import Http
    from bttn.sources import collect_snapshot
    from bttn.weekly import history, te_stream
    from bttn.weekly.build import build_contents
    from bttn.weekly.calendar import build_calendar
    from bttn.weekly.deliver import send_weekly
    from bttn.weekly.facts import build_facts, load_inputs, load_week_snapshots, tradingview_move

    load_dotenv(ROOT / ".env")
    run = out / datetime.now(VN_TZ).strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    manifest = {"friday": friday.isoformat(), "status": "started"}
    try:
        now = datetime.now(VN_TZ)
        snap = collect_snapshot(Http(run / "sources"), now)
        (run / "snapshot.json").write_text(snap.model_dump_json(indent=1), encoding="utf-8")
        monday, fri = week_bounds(friday)
        snaps = load_week_snapshots(ROOT / "output", monday, fri, extra=[snap])
        te_items, te_errors = te_stream.collect_week(monday, fri)
        facts = build_facts(friday, snaps, load_inputs(), te_items)
        for e in te_errors:
            facts.warn("TE_WEEK_SOURCE", e)
        bcom = tradingview_move("CRB", os.getenv("TV_BCOM_SYMBOL", "TVC:BCOM"), monday, fri)
        if bcom is not None:
            facts.markets["CRB"] = bcom
        else:
            facts.warn("BCOM_TRADINGVIEW", "Không lấy được BCOM Index từ TradingView; dùng số Yahoo (^BCOM) nếu có")
        earlier = (load_week_snapshots(ROOT / "output", monday - timedelta(days=7), fri - timedelta(days=7))
                   + load_week_snapshots(ROOT / "output", monday - timedelta(days=14), fri - timedelta(days=14)))
        centrals = history.friday_centrals(facts, earlier)
        calendar, cal_issues = build_calendar(monday + timedelta(days=7))
        seed = extract_weekly(TEMPLATES["vi"], "vi").charts
        contents, issues = build_contents(facts, translator=None, template_seed=seed, work=run,
                                          centrals=centrals, calendar=calendar)
        issues += cal_issues
        files = []
        for lang in ("vi", "en"):
            (run / f"content_{lang}.json").write_text(contents[lang].model_dump_json(indent=1), encoding="utf-8")
            files.append(render_weekly(contents[lang], TEMPLATES[lang], run / weekly_filename(friday, lang)))
        (run / "issues.json").write_text(json.dumps(issues, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest.update(status="word_created", files=[p.name for p in files], issue_count=len(issues))
        if review:
            recipients = [r.strip() for r in (os.getenv("REVIEW_RECIPIENTS") or "datnh1@mbbank.com.vn").split(",") if r.strip()]
            send_weekly(files, f"{monday:%d.%m}-{fri:%d.%m.%Y}", recipients, review=True, issues=issues)
            manifest.update(status="review_sent", recipients=recipients)
        return 0
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        LOG.exception("Bản tin tuần lỗi")
        return 1
    finally:
        (run / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--friday", default=None, help="ngày thứ Sáu của kỳ bản tin (mặc định: thứ Sáu tuần này)")
    ap.add_argument("--collect", action="store_true", help="lấy dữ liệu, viết, dịch và dựng 2 file")
    ap.add_argument("--review", action="store_true", help="gửi 2 file cho người duyệt (REVIEW_RECIPIENTS)")
    ap.add_argument("--content-vi")
    ap.add_argument("--content-en")
    ap.add_argument("--output-dir", default=str(ROOT / "output/weekly"))
    ap.add_argument("--extract-sample", action="store_true",
                    help="đọc 2 file mẫu thành JSON nội dung trong output-dir/sample")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = Path(args.output_dir)
    today = datetime.now(VN_TZ).date()
    friday = date.fromisoformat(args.friday) if args.friday else week_bounds(today)[1]
    if args.extract_sample:
        for lang, tpl in TEMPLATES.items():
            content = extract_weekly(tpl, lang, image_dir=out / "sample" / f"images_{lang}")
            path = out / "sample" / f"content_{lang}.json"
            path.write_text(content.model_dump_json(indent=2), encoding="utf-8")
            print("Đã tạo", path)
        return 0
    if args.collect:
        return collect_and_build(friday, out, review=args.review)
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

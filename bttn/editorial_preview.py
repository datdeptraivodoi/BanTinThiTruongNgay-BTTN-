"""Inspect Python news selection from saved translations, without any network."""
import argparse
import html
import json
from datetime import timedelta
from pathlib import Path

from .editorial import foreign_sections
from .models import Snapshot
from .translation_service import load_verified_translations


def preview(snapshot, translations, directory, *, allow_stale=False):
    records = load_verified_translations(snapshot, translations, allow_review_only=allow_stale)
    sections, audit = foreign_sections(snapshot, records, require_quotes=False, allow_stale=allow_stale)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stale = [sid for sid in records if snapshot.as_of - snapshot.sources[sid].published_at > timedelta(hours=36)]
    payload = {"editor": "python-extractive-v1", "review_only": True, "historical": allow_stale,
               "as_of": snapshot.as_of.isoformat(), "stale_sources": stale,
               "sections": {name: s.model_dump() for name, s in sections.items()}, "selection": audit}
    (directory / "selection.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    escape = html.escape
    labels = {"eur_usd": "EUR và USD", "japan": "Nhật Bản / JPY", "china": "Trung Quốc", "usd_news": "Tin USD"}
    cards = []
    for name, section in sections.items():
        facts = audit[name]
        paragraphs = "".join(f"<p>{escape(p)}</p>" for p in section.paragraphs)
        refs = "".join(f"<li><a href='{escape(snapshot.sources[sid].url, quote=True)}'>{escape(sid)}</a></li>"
                       for sid in section.source_ids if sid in snapshot.sources)
        status = "Đủ giới hạn từ; cần duyệt nội dung" if facts.get("status") == "selected" else "Cần biên tập thêm"
        cards.append(f"<section><h2>{escape(labels.get(name, name))} — {facts.get('word_count', 0)} từ</h2>"
                     f"<p>{escape(status)}</p>{paragraphs}<ul>{refs}</ul></section>")
    for sid, record in records.items():
        source = snapshot.sources[sid]
        label = "BÀI CŨ — chỉ thử lịch sử" if sid in stale else "Trong cửa sổ thời gian của thử nghiệm"
        cards.append(f"<details><summary>{escape(record.title)} — {escape(label)}</summary>"
                     f"<p>{escape(source.published_at.isoformat())}; {escape(record.review_status)}; {source.text_scope}</p>"
                     f"<div class='pair'><pre>{escape(record.original_text)}</pre><pre>{escape(record.text)}</pre></div></details>")
    page = """<!doctype html><html lang='vi'><meta charset='utf-8'><title>Riva và Python — kiểm tra chọn tin</title>
<style>body{max-width:1100px;margin:35px auto;padding:0 20px;font:17px/1.6 Georgia,serif;color:#16335a;background:#f6f8fc}
section,details{background:white;padding:20px;margin:20px 0;border:1px solid #ccd7e7;border-radius:8px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}pre{white-space:pre-wrap;font:16px/1.6 Georgia,serif}
@media(max-width:700px){.pair{grid-template-columns:1fr}}</style>
<h1>Riva dịch → Python chọn câu</h1><p>Chỉ xem trước nội dung tin. Chưa kiểm chứng đầy đủ báo giá, bản dịch và bố cục phát hành.
Thiếu câu đủ giới hạn từ thì cần biên tập; không có email được gửi từ công cụ này.</p>"""
    cutoff = f"<p>Giờ chốt của dữ liệu thử: <strong>{escape(snapshot.as_of.isoformat())}</strong>.</p>"
    if allow_stale:
        cutoff += "<p><strong>THỬ LỊCH SỬ: có thể chứa bài đã cũ. Không dùng bản này để phát hành.</strong></p>"
    (directory / "review.html").write_text(page + cutoff + "".join(cards) + "</html>", encoding="utf-8")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preview sourced news selection; no API or email calls")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--translations", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--allow-stale-review", action="store_true")
    args = parser.parse_args(argv)
    snapshot = Snapshot.model_validate_json(Path(args.snapshot).read_text(encoding="utf-8"))
    preview(snapshot, args.translations, args.output_dir, allow_stale=args.allow_stale_review)
    print("News selection preview saved; no network requests or email sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

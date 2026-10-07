import argparse
import hashlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from .models import (
    Issue,
    ReportContent,
    Snapshot,
    create_draft_placeholder_content,
    parse_as_of,
    sanitize_content_for_draft_render,
)
from .summary import write_step_summary
from .validation import ROOT, validate_content, validate_snapshot

LOG = logging.getLogger("bttn")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_news_translation(args):
    """Isolated NVIDIA-only review, without prices, other models, Word or SMTP."""
    from .tradingeconomics_news import collect_tradingeconomics
    from .translation_service import translate_snapshot

    as_of = parse_as_of(args.as_of)
    directory = Path(args.output_dir) / (as_of.strftime("%Y%m%d-%H%M%S") + "-translations-" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "started", "as_of": as_of.isoformat(), "send_requested": False,
                "mode": "nvidia_translation_only"}
    try:
        if args.snapshot:
            snapshot = Snapshot.model_validate_json(Path(args.snapshot).read_text(encoding="utf-8"))
            if args.as_of and snapshot.as_of != as_of:
                raise ValueError("--as-of must match snapshot cutoff exactly")
        else:
            snapshot = Snapshot(as_of=as_of)
            collect_tradingeconomics(None, snapshot, import_file=getattr(args, "news_file", None))
        manifest["as_of"] = snapshot.as_of.isoformat()
        (directory / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        result = translate_snapshot(snapshot, directory, Path(args.state_dir) / "translations")
        manifest["translation_review_pending"] = len(result["pending_review"])
        if result["status"] == "checked":
            manifest["status"] = "translated_news_pending_review" if result["pending_review"] else "translated_news_approved"
        else:
            manifest["status"] = "blocked_translation"
        return 0 if result["status"] == "checked" else 2
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error_type"] = type(exc).__name__
        LOG.error("Translation run failed (%s); see %s", type(exc).__name__, directory)
        return 1
    finally:
        write_json(directory / "manifest.json", manifest)
        LOG.info("Translation status: %s; directory: %s; no email sent", manifest["status"], directory)


def run(args):
    if getattr(args, "translate_news_only", False):
        return run_news_translation(args)
    from .analysis import generate
    from .delivery import send_report
    from .http import Http
    from .rendering import render, validate_docx
    from .sources import collect_snapshot

    started = time.monotonic()
    as_of = parse_as_of(args.as_of)
    directory = Path(args.output_dir) / (as_of.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "status": "started",
        "as_of": as_of.isoformat(),
        "send_requested": args.send,
        "output_format": "docx",
        "pdf_generated": False,
        "translation_review_policy": "optional",
        "rules_sha256": hashlib.sha256((ROOT / "config/editorial_rules.json").read_bytes()).hexdigest(),
        "skill_sha256": hashlib.sha256((ROOT / "SKILL.md").read_bytes()).hexdigest(),
    }
    try:
        manifest["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        manifest["commit"] = "unknown"
    LOG.info("Run directory: %s", directory)

    snapshot = None
    data_issues = []
    content_issues = []
    timing_issue = None

    try:
        if args.snapshot:
            snapshot = Snapshot.model_validate_json(Path(args.snapshot).read_text(encoding="utf-8"))
            if args.as_of and snapshot.as_of != as_of:
                raise ValueError("--as-of must match snapshot cutoff exactly")
            as_of = snapshot.as_of
            manifest["as_of"] = as_of.isoformat()
        else:
            snapshot = collect_snapshot(Http(directory / "sources"), as_of)
        (directory / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")

        if args.send and snapshot.purpose != "live":
            raise ValueError("Test fixtures cannot be sent")

        data_issues = validate_snapshot(snapshot)
        write_json(directory / "validation-data.json", [i.model_dump() for i in snapshot.issues + data_issues])

        if args.collect_only:
            manifest["status"] = "collected" if not data_issues else "blocked_data"
            return 0 if not data_issues else 2

        if data_issues and args.send:
            LOG.warning("Data validation has issues (%d issues) for --send; producing draft for inspection without sending", len(data_issues))

        ai_error = None
        content = None
        translation_issues = []
        if args.content:
            supplied = Path(args.content)
            content = ReportContent.model_validate_json(supplied.read_text(encoding="utf-8"))
            stored_translations = supplied.parent / "translations.json"
            if stored_translations.is_file():
                from .translation_service import load_verified_translations

                records = load_verified_translations(snapshot, stored_translations)
                (directory / "translations.json").write_bytes(stored_translations.read_bytes())
                used = {ref.source_id for name in content.__class__.model_fields
                        for section in (getattr(content, name) if name == "highlights" else [getattr(content, name)])
                        for ref in section.sentence_refs}
                pending = [sid for sid, record in records.items() if sid in used and record.review_status != "approved"]
                manifest["translation_review_pending"] = len(pending)
        else:
            try:
                from .translation_service import translate_snapshot

                translated = translate_snapshot(snapshot, directory, Path(args.state_dir) / "translations")
                manifest["translation_status"] = translated["status"]
                manifest["editor"] = "python-extractive-v1"
                if translated.get("missing_topics") or (translated["status"] != "checked" and not translated.get("failures")):
                    translation_issues.append(Issue(severity="error", code="TRANSLATION_INCOMPLETE",
                        message="Thiếu tin hoặc bản dịch NVIDIA đạt kiểm tra; xem translations.json."))
                content = generate(snapshot, directory)
                used = {ref.source_id for name in content.__class__.model_fields
                        for section in (getattr(content, name) if name == "highlights" else [getattr(content, name)])
                        for ref in section.sentence_refs}
                pending = sorted(set(translated["pending_review"]) & used)
                manifest["translation_review_pending"] = len(pending)
            except Exception as exc:
                ai_error = str(exc)
                LOG.warning("Translation/Python editing did not complete: %s", exc)

        if content is None:
            # Produce draft placeholder content so verified tables/charts are preserved in draft
            content = create_draft_placeholder_content(snapshot, reason=ai_error or "Python editing incomplete")
            content_issues = [
                Issue(severity="error", code="AI_INCOMPLETE", message=f"Phần nhận xét chưa hoàn tất ({ai_error or 'Lỗi tạo nội dung'})")
            ]
        else:
            content_issues = validate_content(content, snapshot, translations_path=directory / "translations.json")
        content_issues.extend(translation_issues)

        write_json(directory / "validation-content.json", [i.model_dump() for i in content_issues])
        (directory / "content.json").write_text(content.model_dump_json(indent=2), encoding="utf-8")

        # Distinct draft vs official publication watermark / label
        is_draft = snapshot.purpose != "live" or bool(data_issues or content_issues or ai_error)
        output = directory / f"BTTN-{snapshot.as_of:%Y%m%d}.docx"
        render_content = sanitize_content_for_draft_render(content, snapshot, content_issues) if (content_issues or ai_error) else content
        render(snapshot, render_content, ROOT / "template.docx", output, is_draft=is_draft)
        validate_docx(output)
        manifest["document_validation"] = "docx_structure_times_new_roman_11"
        manifest["artifacts"] = {output.name: hashlib.sha256(output.read_bytes()).hexdigest()}

        # If there are data or content issues:
        # In --send mode: Block delivery and return 1 (artifacts preserved for review)
        # In --dry-run mode: Return 0 with draft_with_issues status
        if data_issues:
            manifest["status"] = "blocked_data" if args.send else "draft_with_issues"
            if args.send:
                LOG.error("Sending blocked due to data issues; draft saved to %s", output)
                return 1
            return 0

        if content_issues or ai_error:
            manifest["status"] = "blocked_content" if args.send else "draft_with_issues"
            if ai_error and not manifest.get("error_message"):
                manifest["error_message"] = ai_error
                manifest["error_type"] = "RuntimeError"
            if args.send:
                LOG.error("Sending blocked due to content/AI issues; draft saved to %s", output)
                return 1
            return 0

        manifest["status"] = "word_created"

        if args.send:
            recipients = [r.strip() for r in os.getenv("RECIPIENTS", "datnh1@mbbank.com.vn,trungnt@mbbank.com.vn,research.treasury@mbbank.com.vn").split(",") if r.strip()]
            manifest["recipients"] = recipients
            send_report(snapshot, [output], Path(args.state_dir), recipients)
            manifest["status"] = "sent"

        LOG.info("Completed: %s", manifest["status"])
        return 0
    except Exception as exc:
        if manifest["status"] in ("started", "word_created"):
            manifest["status"] = "failed"
        manifest["error_type"] = type(exc).__name__
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        manifest["error_message"] = message
        LOG.error("%s. Run directory: %s", message, directory)
        return 1
    finally:
        manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(directory / "manifest.json", manifest)
        write_step_summary(manifest, snapshot=snapshot, data_issues=data_issues,
                           content_issues=content_issues, timing_issue=timing_issue,
                           directory=directory)


def main(argv=None):
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Source-grounded BTTN generator; preview by default")
    parser.add_argument("--type", choices=["midday"], default="midday")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--send", action="store_true", help="Send only after all validation gates pass")
    modes.add_argument("--create-only", "--dry-run", dest="dry_run", action="store_true", help="Create Word without sending email (default); no PDF")
    modes.add_argument("--translate-news-only", action="store_true", help="NVIDIA translation review only; no other models or email")
    parser.add_argument("--news-file", help="Imported Trading Economics article JSON for translation review")
    parser.add_argument("--as-of", help="ISO timestamp including UTC offset")
    parser.add_argument("--snapshot", help="Replay stored snapshot JSON, without fetching sources")
    parser.add_argument("--content", help="Use existing structured content JSON, without calling AI")
    parser.add_argument("--collect-only", action="store_true", help="Collect/validate sources only; no AI, documents or email")
    parser.add_argument("--output-dir", default=str(ROOT / "output"))
    parser.add_argument("--state-dir", default=str(ROOT / ".state"))
    args = parser.parse_args(argv)
    if args.translate_news_only and (args.collect_only or args.content):
        parser.error("--translate-news-only cannot be combined with content or collection options")
    if args.news_file and not args.translate_news_only:
        parser.error("--news-file requires --translate-news-only")
    if args.send and args.collect_only:
        parser.error("--send cannot be combined with --collect-only")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return run(args)

import argparse
import hashlib
import json
import logging
import os
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from .models import Issue, ReportContent, Snapshot, create_draft_placeholder_content, parse_as_of
from .summary import write_step_summary
from .validation import ROOT, validate_content, validate_snapshot

LOG = logging.getLogger("bttn")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(args):
    from .analysis import generate
    from .delivery import send_report
    from .http import Http
    from .rendering import convert_and_validate, render
    from .sources import collect_snapshot

    started = time.monotonic()
    as_of = parse_as_of(args.as_of)
    directory = Path(args.output_dir) / (as_of.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"status": "started", "as_of": as_of.isoformat(), "send_requested": args.send,
                "rules_sha256": hashlib.sha256((ROOT / "config/editorial_rules.json").read_bytes()).hexdigest(),
                "skill_sha256": hashlib.sha256((ROOT / "SKILL.md").read_bytes()).hexdigest()}
    try:
        manifest["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        manifest["commit"] = "unknown"
    LOG.info("Run directory: %s", directory)

    snapshot = None
    data_issues = []
    content_issues = []
    timing_issue = None

    try:
        # Early delivery window check: avoids costly generation if schedule delay already missed midday window
        if args.send and not args.snapshot:
            now = parse_as_of(None)
            if (now.date() != as_of.date() or now.weekday() >= 5
                    or not 12 <= now.hour < 15 or not timedelta(0) <= now - as_of <= timedelta(hours=3)):
                timing_issue = (
                    f"Lịch chạy ngoài khung giờ phát hành 12:00–15:00 VN hôm nay "
                    f"(hiện tại: {now.strftime('%H:%M %d/%m/%Y')}). "
                    f"Dừng sớm để tránh gửi bản tin trưa vào buổi tối."
                )
                LOG.warning(timing_issue)
                manifest["status"] = "blocked_timing"
                manifest["timing_issue"] = timing_issue
                return 1

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
        if args.content:
            content = ReportContent.model_validate_json(Path(args.content).read_text(encoding="utf-8"))
        else:
            try:
                content = generate(snapshot, directory)
            except Exception as exc:
                ai_error = str(exc)
                LOG.warning("AI generation did not produce validated content: %s", exc)

        if content is None:
            # Produce draft placeholder content so verified tables/charts are preserved in draft
            content = create_draft_placeholder_content(snapshot, reason=ai_error or "AI generation incomplete")
            content_issues = [
                Issue(severity="error", code="AI_INCOMPLETE", message=f"Phần nhận xét chưa hoàn tất ({ai_error or 'Lỗi tạo nội dung'})")
            ]
        else:
            content_issues = validate_content(content, snapshot)

        write_json(directory / "validation-content.json", [i.model_dump() for i in content_issues])
        (directory / "content.json").write_text(content.model_dump_json(indent=2), encoding="utf-8")

        # Distinct draft vs official publication watermark / label
        is_draft = not args.send or bool(data_issues or content_issues or ai_error)
        output = directory / f"BTTN-{snapshot.as_of:%Y%m%d}.docx"
        render(snapshot, content, ROOT / "template.docx", output, is_draft=is_draft)
        pdf = convert_and_validate(output)
        manifest["artifacts"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [output, pdf]}

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

        manifest["status"] = "validated_draft"

        if args.send:
            now = parse_as_of(None)
            if (now.date() != snapshot.as_of.date() or now.weekday() >= 5
                    or not 12 <= now.hour < 15 or not timedelta(0) <= now - snapshot.as_of <= timedelta(hours=3)):
                manifest["status"] = "blocked_timing"
                raise ValueError("Only today's weekday midday edition may be sent between 12:00 and 15:00 VN")
            recipients = [r.strip() for r in os.getenv("RECIPIENTS", "datnh1@mbbank.com.vn,trungnt@mbbank.com.vn,research.treasury@mbbank.com.vn").split(",") if r.strip()]
            send_report(snapshot, [output, pdf], Path(args.state_dir), recipients)
            manifest["status"] = "sent"

        LOG.info("Completed: %s", manifest["status"])
        return 0
    except Exception as exc:
        if manifest["status"] in ("started", "validated_draft"):
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
    modes.add_argument("--dry-run", action="store_true", help="Generate and validate without sending (default)")
    parser.add_argument("--as-of", help="ISO timestamp including UTC offset")
    parser.add_argument("--snapshot", help="Replay stored snapshot JSON, without fetching sources")
    parser.add_argument("--content", help="Use existing structured content JSON, without calling AI")
    parser.add_argument("--collect-only", action="store_true", help="Collect/validate sources only; no AI, documents or email")
    parser.add_argument("--output-dir", default=str(ROOT / "output"))
    parser.add_argument("--state-dir", default=str(ROOT / ".state"))
    args = parser.parse_args(argv)
    if args.send and args.collect_only:
        parser.error("--send cannot be combined with --collect-only")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return run(args)

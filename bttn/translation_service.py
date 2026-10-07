"""Separate, checked article translations with seven-day idle retention."""
import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field

from .models import Snapshot, StrictModel, parse_as_of
from .nvidia_translation import MODEL, RULES_VERSION, NvidiaTranslator, TranslationError, check_translation
from .tradingeconomics_news import COUNTRIES, collect_tradingeconomics, topic_for

RETENTION_DAYS = 7


class TranslationRecord(StrictModel):
    version: str = RULES_VERSION
    url: str
    title: str
    published_at: AwareDatetime
    source_sha256: str
    original_text: str
    original_segments: list[str] = Field(min_length=1)
    translated_segments: list[str] = Field(min_length=1)
    provider: str = "nvidia"
    requested_model: str = MODEL
    created_at: AwareDatetime
    modified_at: AwareDatetime
    validation_status: str = "numbers_units_markers_language_checked"
    review_status: Literal["needs_review", "approved"] = "needs_review"
    reviewed_at: AwareDatetime | None = None

    @property
    def text(self):
        return "\n\n".join(self.translated_segments)


def split_article(text, maximum=2800):
    """Translate every paragraph, without truncating the article tail."""
    segments = []
    # A title is not a sentence in the article body; Riva may drop it when
    # translating a combined document. Treat each paragraph independently.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(paragraphs) > 1:
        return [s for paragraph in paragraphs for s in split_article(paragraph, maximum)]
    remaining = text.strip()
    while remaining:
        if len(remaining) <= maximum:
            segments.append(remaining)
            break
        prefix = remaining[:maximum]
        ends = list(re.finditer(r"[.!?](?=\s)|\n\s*\n", prefix))
        end = ends[-1].end() if ends else prefix.rfind(" ")
        if end <= 0:
            raise TranslationError("TRANSLATION_UNSPLITTABLE")
        # Do not cut a protected span in half.
        for span in re.finditer(r"<dnt>.*?</dnt>", remaining, re.I | re.S):
            if span.start() < end < span.end():
                end = span.start()
                break
        if end <= 0:
            raise TranslationError("TRANSLATION_UNSPLITTABLE")
        segments.append(remaining[:end].strip())
        remaining = remaining[end:].strip()
    return segments


def check_segments(original, translated):
    if not original or len(original) != len(translated):
        raise TranslationError("TRANSLATION_SEGMENT_COUNT")
    for source, target in zip(original, translated):
        check_translation(source, target)


def cache_key(source):
    value = "\n".join([RULES_VERSION, MODEL, "en-vi", source.url, source.published_at.isoformat(), source.text])
    return hashlib.sha256(value.encode()).hexdigest()


def load_verified_translations(snapshot, path, *, allow_review_only=False):
    if path is None or not Path(path).is_file():
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("review_only") and not allow_review_only:
        raise ValueError("Historical translation tests cannot be used for report generation")
    records = {}
    for sid, item in payload.get("articles", {}).items():
        source = snapshot.sources.get(sid)
        record = TranslationRecord.model_validate(item["record"])
        if (source is None or item.get("cache_key") != cache_key(source)
                or record.original_text != source.text or record.url != source.url
                or record.version != RULES_VERSION or record.requested_model != MODEL
                or record.published_at != source.published_at
                or " ".join(record.original_segments).split() != source.text.split()
                or record.source_sha256 != hashlib.sha256(source.text.encode()).hexdigest()):
            raise ValueError("Translation does not match report source")
        check_segments(record.original_segments, record.translated_segments)
        records[sid] = record
    return records


def atomic_write(path, record):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(record.model_dump_json(indent=2))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class TranslationStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Invalid translation key")
        return self.directory / (key + ".json")

    def cleanup(self, now=None):
        cutoff = (now or datetime.now(timezone.utc)).timestamp() - RETENTION_DAYS * 86400
        removed = 0
        for path in self.directory.glob("*.json"):
            if path.is_symlink() or not re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
                continue
            try:
                if path.stat().st_mtime <= cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                pass
        return removed

    def load(self, source):
        path = self.path(cache_key(source))
        try:
            if path.is_symlink() or path.stat().st_mtime <= datetime.now(timezone.utc).timestamp() - 7 * 86400:
                return None
            record = TranslationRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if (record.version != RULES_VERSION or record.requested_model != MODEL
                    or record.url != source.url or record.original_text != source.text
                    or record.published_at != source.published_at
                    or record.source_sha256 != hashlib.sha256(source.text.encode()).hexdigest()
                    or " ".join(record.original_segments).split() != source.text.split()):
                return None
            check_segments(record.original_segments, record.translated_segments)
            # Reading does not extend the seven-day idle period.
            return record
        except (OSError, ValueError, TranslationError):
            return None

    def save(self, source, translated):
        original = split_article(source.text)
        check_segments(original, translated)
        now = datetime.now(timezone.utc)
        record = TranslationRecord(
            url=source.url, title=source.text.splitlines()[0][:300], published_at=source.published_at,
            source_sha256=hashlib.sha256(source.text.encode()).hexdigest(), original_text=source.text,
            original_segments=original, translated_segments=translated, created_at=now, modified_at=now,
        )
        atomic_write(self.path(cache_key(source)), record)
        return record

    def edit(self, key, translated):
        record = TranslationRecord.model_validate_json(self.path(key).read_text(encoding="utf-8"))
        check_segments(record.original_segments, translated)
        record.translated_segments = translated
        record.modified_at = datetime.now(timezone.utc)
        record.provider = "human_edit"
        record.review_status = "needs_review"
        record.reviewed_at = None
        atomic_write(self.path(key), record)
        return record

    def approve(self, key):
        record = TranslationRecord.model_validate_json(self.path(key).read_text(encoding="utf-8"))
        check_segments(record.original_segments, record.translated_segments)
        record.review_status = "approved"
        record.reviewed_at = record.modified_at = datetime.now(timezone.utc)
        atomic_write(self.path(key), record)
        return record


def translate_snapshot(snapshot, directory, cache_dir, *, translator=None, allow_stale=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    store = TranslationStore(cache_dir)
    store.cleanup()
    translator = translator or NvidiaTranslator(os.getenv("NVIDIA_API_KEY"))
    result = {"provider": "nvidia", "model": MODEL, "review_only": allow_stale, "articles": {}, "failures": {}}
    for sid, source in snapshot.sources.items():
        topic = topic_for(source)
        if source.kind != "news" or topic is None:
            continue
        age = (snapshot.as_of - source.published_at).total_seconds()
        cutoff_hours = 48 if topic == "China" else 36
        if age < 0 or (age > cutoff_hours * 3600 and not allow_stale):
            result["failures"][sid] = "TE_NEWS_DATE"
            continue
        try:
            record = store.load(source)
            status = "cached" if record else "translated"
            if record is None:
                record = store.save(source, [translator.translate(s) for s in split_article(source.text)])
            result["articles"][sid] = {"topic": topic, "cache_key": cache_key(source), "status": status,
                                       "record": record.model_dump(mode="json")}
        except TranslationError as exc:
            result["failures"][sid] = str(exc)
    present = {a["topic"] for a in result["articles"].values()}
    result["missing_topics"] = [t for t in COUNTRIES.values() if t not in present]
    result["pending_review"] = [sid for sid, a in result["articles"].items()
                                if a["record"]["review_status"] != "approved"]
    result["status"] = "checked" if not result["missing_topics"] and not result["failures"] else "incomplete"
    for name, data in [("translations.json", result), ("translation-attempts.json", translator.attempts)]:
        (directory / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv=None):
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description="Translate news with NVIDIA only; never send email")
    parser.add_argument("action", choices=["translate", "cleanup", "edit", "approve"])
    parser.add_argument("--cache-dir", default=".state/translations")
    parser.add_argument("--output-dir", default="output/translation-review")
    parser.add_argument("--news-file", help="TE API JSON or imported article records")
    parser.add_argument("--snapshot", help="Replay existing snapshot; no source network requests")
    parser.add_argument("--as-of")
    parser.add_argument("--allow-stale-review", action="store_true", help="Historical translation test only")
    parser.add_argument("--key")
    parser.add_argument("--text-file", help="UTF-8 JSON containing translated_segments")
    args = parser.parse_args(argv)
    store = TranslationStore(args.cache_dir)
    if args.action == "cleanup":
        print(f"Deleted {store.cleanup()} translations idle for seven days")
        return 0
    if args.action == "edit":
        if not args.key or not args.text_file:
            parser.error("edit requires --key and --text-file")
        translated = json.loads(Path(args.text_file).read_text(encoding="utf-8"))["translated_segments"]
        store.edit(args.key, translated)
        print("Translation saved; seven-day idle retention reset")
        return 0
    if args.action == "approve":
        if not args.key:
            parser.error("approve requires --key after human review")
        store.approve(args.key)
        print("Translation approved; no email sent")
        return 0
    if args.snapshot and args.news_file:
        parser.error("choose --snapshot or --news-file")
    if args.snapshot:
        snapshot = Snapshot.model_validate_json(Path(args.snapshot).read_text(encoding="utf-8"))
        if args.as_of and parse_as_of(args.as_of) != snapshot.as_of:
            parser.error("--as-of must match snapshot cutoff")
    else:
        snapshot = Snapshot(as_of=parse_as_of(args.as_of))
        collect_tradingeconomics(None, snapshot, import_file=args.news_file, allow_stale=args.allow_stale_review)
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    (Path(args.output_dir) / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    result = translate_snapshot(snapshot, args.output_dir, args.cache_dir, allow_stale=args.allow_stale_review)
    print(f"NVIDIA translations: {len(result['articles'])}; technical checks: {result['status']}; "
          f"pending review: {len(result['pending_review'])}; no email sent")
    return 0 if result["status"] == "checked" else 2


if __name__ == "__main__":
    raise SystemExit(main())

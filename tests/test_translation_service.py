import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bttn.editorial import generate_sections, get_section
from bttn.models import ReportContent, Snapshot, Source
from bttn.translation_service import (
    TranslationStore,
    cache_key,
    check_segments,
    split_article,
    translate_article,
)

ORIGINAL = "Japan and China: gold and euro rose 2% to USD 100."
TRANSLATED = "Nhật Bản và Trung Quốc: vàng và euro tăng 2% lên USD 100."


def source(text=ORIGINAL):
    now = datetime.now(timezone.utc)
    return Source(id="article", url="https://example.com/article", text=text,
                  published_at=now-timedelta(hours=1), retrieved_at=now, kind="news")


def test_translation_cache_reused_without_extending_retention(tmp_path):
    store = TranslationStore(tmp_path)
    calls = []
    def model(prompt, *args):
        calls.append(prompt)
        return json.dumps({"segments": [TRANSLATED]}), {}
    providers = [("fake", "model", "fake", model, 2)]
    record, status = translate_article(source(), store, providers, set(), [])
    assert status == "translated"
    assert record.original_text == ORIGINAL
    assert record.text == TRANSLATED
    path = store.path(cache_key(source()))
    old = datetime.now(timezone.utc)-timedelta(days=6)
    os.utime(path, (old.timestamp(), old.timestamp()))
    reused, status = translate_article(source(), store, providers, set(), [])
    assert status == "cached"
    assert reused == record
    assert len(calls) == 1
    assert path.stat().st_mtime == pytest.approx(old.timestamp())
    assert cache_key(source(ORIGINAL + " Updated.")) != cache_key(source())


@pytest.mark.parametrize("target,error", [
    (TRANSLATED.replace("100", "101"), "TRANSLATION_NUMBERS"),
    (TRANSLATED.replace("%", ""), "TRANSLATION_UNITS"),
    (ORIGINAL, "TRANSLATION_LANGUAGE"),
])
def test_invalid_translation_is_rejected(target, error):
    with pytest.raises(ValueError, match=error):
        check_segments([ORIGINAL], [target])


def test_native_vietnamese_needs_no_api(tmp_path):
    store = TranslationStore(tmp_path)
    original = source(TRANSLATED)
    record, status = translate_article(original, store, [], set(), [])
    assert status == "native_vi"
    assert record.text == TRANSLATED
    assert store.load(original) == record


def test_full_article_split_keeps_tail():
    text = "First paragraph. " * 800 + "The important final paragraph has USD 12."
    chunks = split_article(text)
    assert len(chunks) > 4
    assert all(len(part) <= 3000 for part in chunks)
    assert " ".join(chunks).split() == text.split()
    assert chunks[-1].endswith("USD 12.")


def test_seven_day_cleanup_uses_last_edit_and_only_translation_files(tmp_path):
    store = TranslationStore(tmp_path / "translations")
    store.save(source(), [TRANSLATED], "fake", "model")
    path = store.path(cache_key(source()))
    now = datetime.now(timezone.utc)
    old = now-timedelta(days=7)
    os.utime(path, (old.timestamp(), old.timestamp()))
    ledger = tmp_path / "ledger.json"
    ledger.write_text("do not delete", encoding="utf-8")
    unrelated = store.directory / "settings.json"
    unrelated.write_text("do not delete", encoding="utf-8")
    os.utime(unrelated, (old.timestamp(), old.timestamp()))
    assert store.cleanup(now) == 1
    assert not path.exists()
    assert ledger.exists() and unrelated.exists()
    store.save(source(), [TRANSLATED], "fake", "model")
    os.utime(path, (old.timestamp(), old.timestamp()))
    store.edit(cache_key(source()), [TRANSLATED.replace("vàng", "giá vàng")])
    assert store.cleanup(now) == 0
    edited = store.load(source())
    assert edited.provider == "human_edit"
    assert "giá vàng" in edited.text
    assert path.stat().st_mtime > old.timestamp()


def test_failed_translation_never_enters_cache(tmp_path):
    store = TranslationStore(tmp_path)
    def model(*args):
        return json.dumps({"segments": [TRANSLATED.replace("100", "500")]}), {}
    attempts = []
    with pytest.raises(RuntimeError):
        translate_article(source(), store, [("fake", "model", "fake", model, 2)], set(), attempts)
    assert not list(tmp_path.glob("*.json"))
    assert len(attempts) == 2
    assert all(record["error"] == "TRANSLATION_NUMBERS" for record in attempts)


def test_editorial_uses_one_stored_translation_across_topics_and_runs(tmp_path):
    fixtures = Path(__file__).parent / "fixtures"
    snapshot = Snapshot.model_validate_json((fixtures / "snapshot.json").read_text(encoding="utf-8"))
    content = ReportContent.model_validate_json((fixtures / "content.json").read_text(encoding="utf-8"))
    original = " ".join([ORIGINAL + " Japan yen and China yuan markets await the ECB policy decision."] * 10)
    translated = " ".join([TRANSLATED + " Thị trường đồng yên Nhật Bản và nhân dân tệ Trung Quốc chờ quyết định chính sách của ECB."] * 10)
    article = source(original).model_copy(update={"published_at": snapshot.as_of-timedelta(hours=1)})
    snapshot.sources[article.id] = article
    translation_calls, evidence_seen = [], []
    def model(prompt, schema, *args):
        if "segments" in schema["properties"]:
            translation_calls.append(prompt)
            return json.dumps({"segments": [translated]}), {}
        name = prompt.split("SECTION: ")[1].splitlines()[0]
        evidence = json.loads(prompt.split("BEGIN_UNTRUSTED_SOURCE_DATA\n")[1].split("\nEND_UNTRUSTED_SOURCE_DATA")[0])
        if "article" in evidence["sources"]:
            evidence_seen.append(evidence["sources"]["article"]["text"])
        section = get_section(content, name).model_copy(deep=True)
        section.source_ids = list(evidence["sources"])[:1]
        return section.model_dump_json(), {}
    providers = [("fake", "model", "fake", model, 2)]
    for run in ("first", "second"):
        directory = tmp_path / run
        directory.mkdir()
        generate_sections(snapshot, directory, providers, translation_dir=tmp_path / "cache")
        for diagnostic in list(directory.glob("evidence-*.json")) + list(directory.glob("prompt-*.txt")):
            assert TRANSLATED not in diagnostic.read_text(encoding="utf-8")
    assert len(translation_calls) == 1
    assert len(evidence_seen) >= 3
    assert all(text == translated for text in evidence_seen)


def test_failed_foreign_article_is_not_silently_sent_to_editor(tmp_path):
    fixtures = Path(__file__).parent / "fixtures"
    snapshot = Snapshot.model_validate_json((fixtures / "snapshot.json").read_text(encoding="utf-8"))
    content = ReportContent.model_validate_json((fixtures / "content.json").read_text(encoding="utf-8"))
    snapshot.sources["article"] = source(" ".join([ORIGINAL] * 10)).model_copy(update={"published_at": snapshot.as_of-timedelta(hours=1)})
    def model(prompt, schema, *args):
        if "segments" in schema["properties"]:
            return "{}", {}
        assert ORIGINAL not in prompt
        name = prompt.split("SECTION: ")[1].splitlines()[0]
        evidence = json.loads(prompt.split("BEGIN_UNTRUSTED_SOURCE_DATA\n")[1].split("\nEND_UNTRUSTED_SOURCE_DATA")[0])
        section = get_section(content, name).model_copy(update={"source_ids": list(evidence["sources"])[:1]})
        return section.model_dump_json(), {}
    generate_sections(snapshot, tmp_path, [("fake", "model", "fake", model, 2)])
    index = json.loads((tmp_path / "translation-index.json").read_text(encoding="utf-8"))
    assert index["articles"]["article"]["status"] == "unavailable"

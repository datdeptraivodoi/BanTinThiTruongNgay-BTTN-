import json
import os
from datetime import timedelta
from unittest.mock import Mock

import pytest
import requests

from bttn.models import Snapshot, Source, parse_as_of
from bttn.nvidia_translation import (
    MODEL,
    NvidiaTranslator,
    TranslationError,
    check_translation,
    clean_post_translation,
    normalize_fx_quote,
    protect,
    restore,
    same_numbers,
)
from bttn.tradingeconomics_news import collect_tradingeconomics, parse_news
from bttn.translation_service import TranslationStore, cache_key, split_article, translate_snapshot

AT = parse_as_of("2026-10-06T20:00:00+07:00")
ENGLISH = "The euro rose 0.3% to $1.12 as investors awaited inflation data."
VIETNAMESE = "Đồng euro tăng 0,3% lên 1,12 USD khi nhà đầu tư chờ dữ liệu lạm phát."


def source(text=ENGLISH):
    return Source(id="news_te_eur", url="https://tradingeconomics.com/euro-area/currency/news/123",
                  published_at=AT - timedelta(hours=1), retrieved_at=AT, text=text, kind="news")


def response(text=VIETNAMESE, status=200, finish="stop"):
    result = Mock(status_code=status)
    result.json.return_value = {"choices": [{"finish_reason": finish, "message": {"content": text}}]}
    return result


def test_nvidia_contract_without_other_provider_or_json_schema():
    session = Mock()
    session.post.return_value = response()
    client = NvidiaTranslator("test-secret-do-not-log", session=session)
    assert client.translate(ENGLISH) == VIETNAMESE
    args, kwargs = session.post.call_args
    assert args == ("https://integrate.api.nvidia.com/v1/chat/completions",)
    assert kwargs["json"]["model"] == MODEL
    assert kwargs["json"]["messages"][0] == {"role": "system", "content": "en-vi"}
    assert "response_format" not in kwargs["json"]
    assert kwargs["allow_redirects"] is False
    assert "test-secret-do-not-log" not in json.dumps(client.attempts)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_and_access_errors_are_not_retried_or_logged(status):
    session = Mock()
    session.post.return_value = response(status=status)
    client = NvidiaTranslator("private", session=session, sleep=Mock())
    with pytest.raises(TranslationError, match=f"NVIDIA_HTTP_{status}"):
        client.translate(ENGLISH)
    assert session.post.call_count == 1
    assert "private" not in json.dumps(client.attempts)


def test_bounded_service_retries_and_no_fallback():
    session, sleep = Mock(), Mock()
    session.post.side_effect = [response(status=429), response(status=503), response()]
    client = NvidiaTranslator("private", session=session, sleep=sleep)
    assert client.translate(ENGLISH) == VIETNAMESE
    assert session.post.call_count == 3
    assert sleep.call_count == 2
    session.post.side_effect = requests.Timeout("credential must not appear")
    with pytest.raises(TranslationError, match="NVIDIA_NETWORK"):
        client.translate(ENGLISH)
    assert "credential must not appear" not in json.dumps(client.attempts)


@pytest.mark.parametrize("text,finish,code", [
    ("", "stop", "TRANSLATION_EMPTY"),
    (VIETNAMESE, "length", "TRANSLATION_INCOMPLETE"),
    (VIETNAMESE.replace("1,12", "1,21"), "stop", "TRANSLATION_NUMBERS"),
    (ENGLISH, "stop", "TRANSLATION_LANGUAGE"),
    (VIETNAMESE.replace("%", ""), "stop", "TRANSLATION_PERCENT_UNITS"),
    (VIETNAMESE.replace("USD", "EUR"), "stop", "TRANSLATION_CURRENCY_UNITS"),
])
def test_invalid_output_rejected(text, finish, code):
    session = Mock()
    session.post.return_value = response(text=text, finish=finish)
    with pytest.raises(TranslationError, match=code):
        NvidiaTranslator("private", session=session).translate(ENGLISH)


@pytest.mark.parametrize("expected,actual,equal", [
    (["1.12", "25"], ["1,12", "25"], True),
    (["29,000", "4.2"], ["29.000", "4,2"], True),
    (["6.70", "4.5", "5.0"], ["6,70", "4,5", "5,0"], True),
    (["4.5", "5.0"], ["4,5"], False),
    (["1.12"], ["1,2"], False),
    (["1.12"], ["1.12", "1.12"], False),
    (["2026-10-06"], ["06-10-2026"], False),
])
def test_numeric_values_without_rounding(expected, actual, equal):
    assert same_numbers(expected, actual) == equal


def test_translated_month_is_allowed_but_extra_figures_are_not():
    original = "The euro fell to $1.12, its weakest level since May 2025, as concerns grew."
    target = "Đồng euro giảm xuống 1,12 USD, mức yếu nhất kể từ tháng 5 năm 2025, khi lo ngại gia tăng."
    check_translation(original, target)
    check_translation(original, target.replace("tháng 5", "tháng Năm"))
    check_translation(original, target.replace("tháng 5 năm 2025", "tháng 5/2025"))
    with pytest.raises(TranslationError, match="TRANSLATION_NUMBERS"):
        check_translation(original, target + " Thêm 100 tỷ đồng.")


def test_growth_range_allows_local_decimal_notation_but_not_lost_percent():
    check_translation("The growth target is 4.5%-5.0% this year.",
                      "Mục tiêu tăng trưởng năm nay là 4,5%-5,0%.")
    with pytest.raises(TranslationError):
        check_translation("The growth target is 4.5%-5.0% this year.",
                          "Mục tiêu tăng trưởng năm nay là 4,5-5,0%.")


def test_negative_value_cannot_lose_its_sign():
    with pytest.raises(TranslationError, match="TRANSLATION_NUMBER_SIGN"):
        check_translation("Inflation was -0.3%.", "Lạm phát ở mức 0,3%.")
    check_translation("Inflation was -0.3%.", "Lạm phát ở mức -0,3%.")


def test_billion_cannot_become_million():
    with pytest.raises(TranslationError, match="TRANSLATION_SCALE_UNITS"):
        check_translation("Spending fell by 25 billion euros.", "Chi tiêu giảm 25 triệu euro.")
    check_translation("Spending fell by 25 billion euros.", "Chi tiêu giảm 25 tỷ euro.")


def test_glossary_and_dnt_are_checked():
    with pytest.raises(TranslationError, match="TRANSLATION_TERMINOLOGY"):
        check_translation("Core inflation rose.", "Lạm phát toàn phần tăng.")
    original = "The hawkish policy applies to <dnt>USD-Fed 2026</dnt>."
    masked, spans = protect(original)
    assert "USD-Fed 2026" not in masked
    translated = restore("Chính sách cứng rắn áp dụng cho BTTNPROTECTAEND.", spans)
    check_translation(original, translated)
    assert "<dnt>USD-Fed 2026</dnt>" in translated
    with pytest.raises(TranslationError, match="TRANSLATION_PROTECTED_SPANS"):
        restore("Nội dung bị thiếu.", spans)
    assert clean_post_translation("NHÍCH NHẸ và nhích lên") == "tăng nhẹ và tăng lên"


def test_cache_reuse_does_not_extend_idle_retention(tmp_path):
    store, news = TranslationStore(tmp_path), source()
    store.save(news, [VIETNAMESE])
    path = store.path(cache_key(news))
    previous_mtime = path.stat().st_mtime
    assert store.load(news).text == VIETNAMESE
    assert path.stat().st_mtime == previous_mtime
    old = previous_mtime - 8 * 86400
    os.utime(path, (old, old))
    assert store.load(news) is None
    unrelated = tmp_path / "report.json"
    unrelated.write_text("{}")
    os.utime(unrelated, (old, old))
    assert store.cleanup() == 1
    assert unrelated.exists()


def test_edit_refreshes_retention_and_validates_numbers(tmp_path):
    store, news = TranslationStore(tmp_path), source()
    store.save(news, [VIETNAMESE])
    key = cache_key(news)
    path = store.path(key)
    old = path.stat().st_mtime - 8 * 86400
    os.utime(path, (old, old))
    store.edit(key, [VIETNAMESE.replace("nhà đầu tư", "giới đầu tư")])
    assert store.cleanup() == 0
    assert store.load(news).provider == "human_edit"
    with pytest.raises(TranslationError):
        store.edit(key, [VIETNAMESE.replace("1,12", "9,12")])
    assert store.approve(key).review_status == "approved"
    assert store.load(news).reviewed_at is not None
    store.edit(key, [VIETNAMESE])
    assert store.load(news).review_status == "needs_review"


def test_corrupt_cache_and_revised_source_are_not_reused(tmp_path):
    store, news = TranslationStore(tmp_path), source()
    store.save(news, [VIETNAMESE])
    path = store.path(cache_key(news))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["translated_segments"] = [VIETNAMESE.replace("1,12", "9,12")]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert store.load(news) is None
    assert cache_key(news) != cache_key(source(ENGLISH + " A revision."))


def test_previous_fx_translation_rules_are_not_reused_or_considered_approved(tmp_path):
    store, news = TranslationStore(tmp_path), source()
    record = store.save(news, [VIETNAMESE])
    path = store.path(cache_key(news))
    record.version = "riva-en-vi-finance-v5"
    record.review_status = "approved"
    path.write_text(record.model_dump_json(), encoding="utf-8")
    assert store.load(news) is None


def test_split_preserves_title_and_article_tail():
    text = "News title\n\n" + "Inflation is falling. " * 300 + "Final sentence."
    chunks = split_article(text)
    assert chunks[0] == "News title"
    assert max(map(len, chunks)) <= 2800
    assert " ".join(chunks).split() == text.split()
    assert chunks[-1].endswith("Final sentence.")


def news_row(date=None, description=None):
    return {"id": "123", "title": "Euro rises on inflation data", "date": date or "2026-10-06T10:00:00",
            "description": description or ENGLISH + " Investors continue to assess monetary policy decisions and the outlook for economic growth.",
            "country": "Euro Area", "url": "/euro-area/currency/news/123"}


def test_te_api_dates_are_utc_and_newest_eligible_news_is_selected():
    older, latest = news_row("2026-10-05T21:00:00Z"), news_row()
    older["id"] = "122"
    selected, rejected = parse_news([older, latest], AT)
    assert not rejected
    assert len(selected) == 1
    assert next(iter(selected.values())).published_at.utcoffset().total_seconds() == 0
    assert next(iter(selected.values())).published_at.hour == 10


@pytest.mark.parametrize("row,code", [
    (news_row("2026-10-06T23:00:00Z"), "TE_NEWS_AFTER_CUTOFF"),
    (news_row("2026-09-30T02:55:00Z"), "TE_NEWS_STALE"),
    (news_row(description="Headline alone."), "TE_NEWS_BODY_MISSING"),
    (news_row("not-a-date"), "TE_NEWS_DATE"),
])
def test_te_missing_body_and_ineligible_dates_are_rejected(row, code):
    selected, rejected = parse_news([row], AT)
    assert not selected
    assert rejected[0]["code"] == code


def test_stale_china_is_only_accepted_for_explicit_historical_review():
    row = news_row("2026-09-30T02:55:00Z")
    row.update(country="China", url="/china/currency/news/123")
    assert not parse_news([row], AT)[0]
    assert parse_news([row], AT, allow_stale=True)[0]


def test_no_te_key_does_not_request_website_or_use_search_snippet(monkeypatch):
    monkeypatch.delenv("TRADINGECONOMICS_API_KEY", raising=False)
    monkeypatch.delenv("TE_NEWS_IMPORT_PATH", raising=False)
    get = Mock(side_effect=AssertionError("No unauthorized source request"))
    monkeypatch.setattr(requests, "get", get)
    snap = Snapshot(as_of=AT)
    collect_tradingeconomics(None, snap)
    assert not snap.sources
    assert snap.issues[0].code == "TE_NEWS_ACCESS"
    get.assert_not_called()


def test_translation_failure_is_diagnostic_and_not_saved_as_success(tmp_path):
    client = Mock(attempts=[])
    client.translate.side_effect = TranslationError("NVIDIA_HTTP_401")
    news = source()
    snap = Snapshot(as_of=AT, sources={news.id: news})
    result = translate_snapshot(snap, tmp_path / "run", tmp_path / "cache", translator=client)
    assert result["status"] == "incomplete"
    assert not result["articles"]
    assert result["failures"][news.id] == "NVIDIA_HTTP_401"
    assert not list((tmp_path / "cache").glob("*.json"))


def test_observed_jpy_quote_direction_error_is_rejected():
    with pytest.raises(TranslationError, match="TRANSLATION_QUOTE_DIRECTION"):
        check_translation("The Japanese yen depreciated past 158 per dollar on Tuesday.",
                          "Đồng yên Nhật giảm xuống dưới 158 yên/USD vào thứ Ba.")


def test_source_derived_quote_correction_preserves_number_and_tail():
    original = "The Japanese yen depreciated past 158 per dollar on Tuesday, but remained range-bound."
    translated = "Đồng yên Nhật suy giảm xuống dưới 158 đồng/USD vào thứ Ba, nhưng vẫn dao động trong biên độ hẹp."
    corrected = normalize_fx_quote(original, translated)
    assert corrected.startswith("USD-JPY vượt mức 158 cho thấy JPY đang suy yếu")
    assert corrected.endswith("vào thứ Ba, nhưng vẫn dao động trong biên độ hẹp.")
    check_translation(original, corrected)
    assert normalize_fx_quote(original.replace("158", "159"), translated) == translated
    assert normalize_fx_quote(original.replace("depreciated", "appreciated"), translated) == translated


@pytest.mark.parametrize("quote", [
    "Đồng yên Nhật suy yếu, đưa tỷ giá vượt 158 yên đổi một USD",
    "Đồng yên Nhật suy yếu, đưa tỷ giá USD-JPY vượt 158 yên đổi một USD",
    "Yên Nhật Bản giảm giá vượt mức 158 yên cho mỗi USD",
])
def test_jpy_pair_notation_preserves_source_direction_and_following_news(quote):
    original = "The Japanese yen depreciated past 158 per dollar, but remained range-bound."
    tail = ", nhưng vẫn dao động trong biên độ hẹp."
    corrected = normalize_fx_quote(original, quote + tail)
    assert corrected == "USD-JPY vượt mức 158 cho thấy JPY đang suy yếu" + tail
    assert normalize_fx_quote(original, corrected) == corrected
    check_translation(original, corrected)


def test_quote_formatter_keeps_decimal_value_and_does_not_guess_unrelated_direction():
    original = "The Japanese yen depreciated past 158.25 per dollar."
    translated = "Đồng yên Nhật giảm xuống dưới 158,25 yên đổi một USD."
    corrected = normalize_fx_quote(original, translated)
    assert corrected == "USD-JPY vượt mức 158,25 cho thấy JPY đang suy yếu."
    check_translation(original, corrected)
    for other in ["The Japanese yen appreciated to 158.25 per dollar.",
                  "The euro traded at 158.25 per dollar.",
                  "The Japanese yen traded around 158.25 per dollar."]:
        assert normalize_fx_quote(other, translated) == translated


def test_dnt_prose_is_not_modified_by_postprocessing():
    original = "The policy applies to <dnt>Yen nhích nhẹ</dnt>."
    session = Mock()
    session.post.return_value = response("Chính sách áp dụng cho BTTNPROTECTAEND.")
    result = NvidiaTranslator("private", session=session).translate(original)
    assert "<dnt>Yen nhích nhẹ</dnt>" in result


def test_prompt_uses_matching_translation_and_rejects_historical_test(tmp_path):
    from bttn.analysis import make_prompt

    store, news = TranslationStore(tmp_path / "cache"), source()
    store.save(news, [VIETNAMESE])
    snap = Snapshot(as_of=AT, sources={news.id: news})
    client = Mock(attempts=[])
    result = translate_snapshot(snap, tmp_path / "run", tmp_path / "cache", translator=client)
    path = tmp_path / "run" / "translations.json"
    prompt = make_prompt(snap, path)
    assert VIETNAMESE in prompt and ENGLISH in prompt
    assert result["pending_review"] == [news.id]
    client.translate.assert_not_called()
    result["review_only"] = True
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ValueError, match="Historical"):
        make_prompt(snap, path)


def test_translation_only_pipeline_does_not_call_editor_renderer_or_email(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bttn import analysis, delivery, pipeline, rendering

    news = source()
    snap = Snapshot(as_of=AT, sources={news.id: news})
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(snap.model_dump_json(), encoding="utf-8")
    for module, name in [(analysis, "generate"), (delivery, "send_report"), (rendering, "render")]:
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError("Translation-only boundary breached")))
    store = TranslationStore(tmp_path / "state" / "translations")
    store.save(news, [VIETNAMESE])
    args = SimpleNamespace(translate_news_only=True, as_of=AT.isoformat(), snapshot=str(snapshot_path),
                           output_dir=str(tmp_path / "runs"), state_dir=str(tmp_path / "state"), news_file=None)
    assert pipeline.run(args) == 2  # Only EUR supplied; do not disguise missing countries as success.
    directory = next((tmp_path / "runs").iterdir())
    assert json.loads((directory / "manifest.json").read_text())["status"] == "blocked_translation"
    assert json.loads((directory / "translations.json").read_text(encoding="utf-8"))["articles"]


def test_unapproved_translation_blocks_publication(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bttn import analysis, delivery, pipeline, rendering, translation_service

    fixtures = __import__("pathlib").Path(__file__).parent / "fixtures"
    snap = Snapshot.model_validate_json((fixtures / "snapshot.json").read_text(encoding="utf-8"))
    snap.purpose = "live"
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(snap.model_dump_json(), encoding="utf-8")
    from bttn.models import ReportContent
    content = ReportContent.model_validate_json((fixtures / "content.json").read_text(encoding="utf-8"))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-only")
    monkeypatch.setattr(translation_service, "translate_snapshot", Mock(return_value={"status": "checked", "pending_review": ["news"]}))
    monkeypatch.setattr(analysis, "generate", Mock(return_value=content))
    monkeypatch.setattr(rendering, "render", Mock())

    def convert(path):
        path.write_bytes(b"docx mock")
        pdf = path.with_suffix(".pdf")
        pdf.write_bytes(b"pdf mock")
        return pdf

    monkeypatch.setattr(rendering, "convert_and_validate", convert)
    send = Mock()
    monkeypatch.setattr(delivery, "send_report", send)
    args = SimpleNamespace(translate_news_only=False, test_smtp=False, as_of=None,
                           snapshot=str(snapshot_path), content=None, collect_only=False, send=True,
                           output_dir=str(tmp_path / "runs"), state_dir=str(tmp_path / "state"))
    assert pipeline.run(args) == 1
    send.assert_not_called()
    directory = next((tmp_path / "runs").iterdir())
    validation = json.loads((directory / "validation-content.json").read_text(encoding="utf-8"))
    assert any(i["code"] == "TRANSLATION_REVIEW_REQUIRED" for i in validation)

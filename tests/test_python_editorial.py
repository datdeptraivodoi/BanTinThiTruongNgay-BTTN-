import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bttn.editorial import (
    Candidate,
    choose_sentences,
    domestic_sections,
    generate,
    make_section,
    news_candidates,
    sentences,
)
from bttn.editorial_preview import preview
from bttn.models import ReportContent, Snapshot, Source
from bttn.sources import collect_sjc_gold, collect_vietnam_macro_news, collect_vira_daily
from bttn.summary import generate_markdown_summary
from bttn.tradingeconomics_news import parse_news
from bttn.validation import count_words, resolve, validate_content

AT = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
VI = "Kim ngạch xuất khẩu hàng hóa tăng 9,3% so với cùng kỳ theo số liệu công bố."


def news(text=VI, **kwargs):
    return Source(id="article", url="https://vneconomy.vn/article.htm", published_at=AT,
                  retrieved_at=AT, text=text, kind="news", text_scope="article", **kwargs)


def test_sentence_split_keeps_decimals_abbreviations_and_complete_text():
    text = "U.S. inflation rose to 2.8%. Tỷ giá USD-JPY vượt 158,25 trong phiên hôm qua."
    assert sentences(text) == ["U.S. inflation rose to 2.8%.", "Tỷ giá USD-JPY vượt 158,25 trong phiên hôm qua."]


def test_selector_preserves_intact_sentences_and_never_pads_or_truncates():
    snap = Snapshot(as_of=AT)
    candidates = [Candidate("Một hai ba bốn năm.", "a"), Candidate("Sáu bảy tám chín.", "b")]
    selected = choose_sentences(candidates, snap, 8, 9)
    assert [c.text for c in selected] == [c.text for c in candidates]
    assert choose_sentences(candidates, snap, 6, 7) == [candidates[0]]  # Below minimum: keep draft evidence.
    assert choose_sentences(candidates, snap, 1, 3) == []


def test_required_country_coverage_cannot_be_replaced_by_high_score():
    a, b, c = Candidate("Một hai ba.", "US", 100), Candidate("Bốn năm sáu.", "US", 99), Candidate("Bảy tám chín.", "EU", 1)
    chosen = choose_sentences([a, b, c], Snapshot(as_of=AT), 6, 6, required_groups=({"US"}, {"EU"}))
    assert {item.source_id for item in chosen} == {"US", "EU"}


def test_unverified_snippets_forecasts_and_old_articles_are_excluded():
    source = news(VI + " Dự báo xuất khẩu hàng hóa sẽ tiếp tục tăng mạnh trong các tháng tới.")
    snap = Snapshot(as_of=AT, sources={source.id: source})
    picked, rejected = news_candidates(snap, {}, "macro")
    assert [c.text for c in picked] == [VI]
    assert rejected[0]["reason"] == "forecast_or_instruction"
    source.text_scope = "headline"
    assert news_candidates(snap, {}, "macro")[0] == []
    source.text_scope = "article"
    source.published_at = AT - timedelta(hours=37)
    assert news_candidates(snap, {}, "macro")[0] == []
    source.published_at = AT + timedelta(minutes=1)
    assert news_candidates(snap, {}, "macro")[0] == []


def test_anaphoric_sentence_cannot_be_selected_without_its_context():
    source = news(VI + " Kết quả này đánh dấu tốc độ tăng trưởng yếu nhất trong nhiều năm qua.")
    snap = Snapshot(as_of=AT, sources={source.id: source})
    picked, rejected = news_candidates(snap, {}, "macro")
    assert [c.text for c in picked] == [VI]
    assert rejected[0]["reason"] == "context_dependent"


def test_verified_news_sentence_can_keep_figures_but_modified_figures_fail():
    fixtures = Path(__file__).parent / "fixtures"
    snap = Snapshot.model_validate_json((fixtures / "snapshot.json").read_text(encoding="utf-8"))
    content = ReportContent.model_validate_json((fixtures / "content.json").read_text(encoding="utf-8"))
    source = news()
    source.published_at = snap.as_of
    snap.sources[source.id] = source
    content.highlights[2] = make_section(snap, [[Candidate(VI, source.id)]])
    assert not [i for i in validate_content(content, snap) if i.code in ("SENTENCE_SOURCE", "UNBOUND_NUMBER")]
    content.highlights[2].paragraphs[0] = VI.replace("9,3%", "19,3%")
    assert {i.code for i in validate_content(content, snap)} >= {"SENTENCE_SOURCE", "UNBOUND_NUMBER"}
    content.highlights[2].paragraphs[0] = VI
    content.highlights[2].paragraphs[0] = "Không đúng là " + VI
    assert any(i.code == "SENTENCE_SOURCE" for i in validate_content(content, snap))
    content.highlights[2].paragraphs[0] = VI
    content.highlights[2].sentence_refs[0].quote = VI.split(" theo ")[0]
    assert any(i.code == "SENTENCE_SOURCE" for i in validate_content(content, snap))


def test_data_templates_meet_word_limits_and_use_trader_instead_of_mb_quotes(monkeypatch):
    from bttn.trader_quotes import collect_trader_quotes
    root = Path(__file__).resolve().parents[1]
    snap = Snapshot.model_validate_json((root / "tests/fixtures/snapshot.json").read_text(encoding="utf-8"))
    monkeypatch.setenv("TRADER_QUOTES_PATH", str(root / "config/trader_quotes.example.json"))
    collect_trader_quotes(None, snap)
    sections, _ = domestic_sections(snap, {})
    assert 88 <= count_words(resolve(" ".join(sections["interbank"].paragraphs), snap)) <= 95
    assert 75 <= count_words(resolve(" ".join(sections["usd_vnd"].paragraphs), snap)) <= 80
    assert "{{INTERBANK_BID}}/{{INTERBANK_ASK}}" in sections["usd_vnd"].paragraphs[0]


def test_underlength_verified_prose_is_preserved_for_draft_review():
    from bttn.models import sanitize_content_for_draft_render
    fixtures = Path(__file__).parent / "fixtures"
    snap = Snapshot.model_validate_json((fixtures / "snapshot.json").read_text(encoding="utf-8"))
    content = ReportContent.model_validate_json((fixtures / "content.json").read_text(encoding="utf-8"))
    source = news()
    source.published_at = snap.as_of
    snap.sources[source.id] = source
    content.highlights[0] = make_section(snap, [[Candidate(VI, source.id)]])
    issues = validate_content(content, snap)
    assert any(i.code == "WORD_COUNT" for i in issues)
    draft = sanitize_content_for_draft_render(content, snap, issues)
    assert draft.highlights[0].paragraphs == [VI]


def test_python_editor_and_preview_do_not_call_models_or_sources(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", Mock(side_effect=AssertionError("Unexpected API")))
    monkeypatch.setattr(requests, "get", Mock(side_effect=AssertionError("Unexpected source fetch")))
    source = news()
    snap = Snapshot(as_of=AT, sources={source.id: source})
    content = generate(snap, tmp_path)
    assert content.highlights[0].sentence_refs[0].quote == VI
    preview(snap, None, tmp_path / "preview")
    assert (tmp_path / "preview/review.html").is_file()
    assert json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))["editor"] == "python-extractive-v1"


@pytest.mark.parametrize("collector", [collect_vietnam_macro_news, collect_vira_daily, collect_sjc_gold])
def test_failed_collectors_never_fabricate_news_or_prices(collector):
    http = Mock()
    http.get.side_effect = RuntimeError("Unavailable")
    snap = Snapshot(as_of=AT)
    collector(http, snap)
    assert not snap.sources and snap.issues


def test_macro_requires_actual_article_time_and_body():
    listing = '<article><h3><a href="/a.htm">Xuất khẩu tăng 9,3%</a></h3></article>'
    http = Mock()
    http.get.return_value = SimpleNamespace(text=listing)
    snap = Snapshot(as_of=AT)
    collect_vietnam_macro_news(http, snap)
    assert not snap.sources


def test_te_articles_are_bounded_deduplicated_and_country_checked():
    records = [{"id": str(i), "country": "Japan", "date": AT.isoformat(), "title": f"News {i}",
                "url": f"https://tradingeconomics.com/japan/currency/news/{i}",
                "description": "The Japanese yen weakened against the dollar as investors monitored the economy and central bank decisions during the current trading session."}
               for i in range(5)]
    accepted, rejected = parse_news(records, AT)
    assert len(accepted) == 3 and not rejected
    records[0]["country"] = "China"
    assert any(r["code"] == "TE_NEWS_COUNTRY_URL_MISMATCH" for r in parse_news(records, AT)[1])


def test_recovered_translation_attempt_does_not_change_success_to_failure(tmp_path):
    attempts = [{"model": "nvidia/riva-translate-4b-instruct-v2", "status": "failed", "error": "NVIDIA_HTTP_503"},
                {"model": "nvidia/riva-translate-4b-instruct-v2", "status": "checked", "error": None}]
    (tmp_path / "translation-attempts.json").write_text(json.dumps(attempts), encoding="utf-8")
    summary = generate_markdown_summary({"status": "word_created", "artifacts": {"report.docx": "abcdef"}}, directory=tmp_path)
    assert "ĐÃ TẠO FILE WORD" in summary and "NVIDIA_HTTP_503" in summary
    assert "Lý do chặn" not in summary and "Chính thức (Official)" not in summary


def test_foreign_numbers_require_translation_bound_to_exact_article(tmp_path):
    from bttn.translation_service import TranslationStore, translate_snapshot
    original = "The euro rose 0.3% to $1.12 as investors awaited inflation data."
    translated = "Đồng euro tăng 0,3% lên 1,12 USD khi nhà đầu tư chờ dữ liệu lạm phát."
    source = Source(id="eur", url="https://tradingeconomics.com/euro-area/currency/news/123",
                    published_at=AT, retrieved_at=AT, text=original, kind="news", text_scope="article")
    store = TranslationStore(tmp_path / "cache")
    store.save(source, [translated])
    snap = Snapshot(as_of=AT, sources={source.id: source})
    translate_snapshot(snap, tmp_path / "run", tmp_path / "cache", translator=Mock(attempts=[]))
    from bttn.models import create_draft_placeholder_content
    content = create_draft_placeholder_content(snap)
    content.highlights[2] = make_section(snap, [[Candidate(translated, source.id)]])
    path = tmp_path / "run/translations.json"
    issues = validate_content(content, snap, translations_path=path)
    assert not [i for i in issues if i.code in ("UNBOUND_NUMBER", "SENTENCE_SOURCE", "TRANSLATION_SOURCE") and i.message.startswith("highlight_2")]
    assert any(i.code == "SENTENCE_SOURCE" for i in validate_content(content, snap))
    source.text = original.replace("0.3%", "0.4%")
    assert any(i.code == "TRANSLATION_SOURCE" for i in validate_content(content, snap, translations_path=path))


def test_china_sources_recognize_topic_and_allow_48h_cutoff():
    from bttn.tradingeconomics_news import topic_for

    scmp = Source(id="news_china_1", url="https://www.scmp.com/economy/china-economy/article/1",
                  published_at=AT - timedelta(hours=47), retrieved_at=AT,
                  text="Title\n\nBody text with more than twenty words about the China economy and PBOC monetary policy.",
                  kind="news", text_scope="article")
    nikkei = Source(id="news_china_2", url="https://asia.nikkei.com/economy/china-factory-activity-expands",
                    published_at=AT - timedelta(hours=40), retrieved_at=AT,
                    text="Title\n\nBody text with more than twenty words about China manufacturing growth and investment.",
                    kind="news", text_scope="article")
    stale = Source(id="news_china_3", url="https://www.scmp.com/economy/article/old",
                   published_at=AT - timedelta(hours=49), retrieved_at=AT,
                   text="Title\n\nStale news.", kind="news", text_scope="article")

    assert topic_for(scmp) == "China"
    assert topic_for(nikkei) == "China"
    assert topic_for(stale) == "China"

    snap = Snapshot(as_of=AT, sources={scmp.id: scmp, nikkei.id: nikkei, stale.id: stale})
    rec = {
        scmp.id: SimpleNamespace(text="Ngân hàng Nhân dân Trung Quốc PBOC duy trì chính sách tiền tệ hỗ trợ tăng trưởng kinh tế."),
        nikkei.id: SimpleNamespace(text="Sản xuất công nghiệp của Trung Quốc ghi nhận mức tăng trưởng ổn định trong tháng qua."),
        stale.id: SimpleNamespace(text="Tin tức cũ về kinh tế Trung Quốc.")
    }
    candidates, rejected = news_candidates(snap, rec, "China")
    source_ids = {c.source_id for c in candidates}
    assert scmp.id in source_ids
    assert nikkei.id in source_ids
    assert stale.id not in source_ids
    assert any(r["source_id"] == stale.id and r["reason"] == "outside_cutoff" for r in rejected)


import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
import requests

from bttn.deepseek_translation import ENDPOINT, MODEL, DeepSeekTranslator, TranslationError, strip_wrappers
from bttn.models import Snapshot, Source, parse_as_of
from bttn.translation_service import translate_snapshot

AT = parse_as_of("2026-10-06T20:00:00+07:00")
ENGLISH = "The euro rose 0.3% to $1.12 as investors awaited inflation data."
VIETNAMESE = "Đồng euro tăng 0,3% lên 1,12 USD khi nhà đầu tư chờ dữ liệu lạm phát."


def response(text=VIETNAMESE, status=200, finish="stop"):
    result = Mock(status_code=status)
    result.json.return_value = {"choices": [{"finish_reason": finish, "message": {"content": text}}]}
    return result


def test_deepseek_contract_and_key_not_logged():
    session = Mock()
    session.post.return_value = response()
    client = DeepSeekTranslator("test-secret-do-not-log", session=session)
    assert client.translate(ENGLISH) == VIETNAMESE
    args, kwargs = session.post.call_args
    assert args == (ENDPOINT,)
    assert ENDPOINT.endswith("/chat/completions")
    body = kwargs["json"]
    assert body["model"] == MODEL and body["stream"] is False
    assert body["messages"][0]["role"] == "system"
    assert "BTTNPROTECT" in body["messages"][0]["content"]
    assert body["messages"][-1] == {"role": "user", "content": ENGLISH}
    assert "response_format" not in body
    assert kwargs["allow_redirects"] is False
    assert "test-secret-do-not-log" not in json.dumps(client.attempts)


def test_missing_key_fails_without_request():
    session = Mock()
    with pytest.raises(TranslationError, match="DEEPSEEK_API_KEY_MISSING"):
        DeepSeekTranslator("", session=session).translate(ENGLISH)
    session.post.assert_not_called()


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_auth_balance_errors_not_retried(status):
    session = Mock()
    session.post.return_value = response(status=status)
    client = DeepSeekTranslator("private", session=session, sleep=Mock())
    with pytest.raises(TranslationError, match=f"DEEPSEEK_HTTP_{status}"):
        client.translate(ENGLISH)
    assert session.post.call_count == 1
    assert "private" not in json.dumps(client.attempts)


def test_bounded_retries_and_network_error_is_safe():
    session, sleep = Mock(), Mock()
    session.post.side_effect = [response(status=429), response(status=503), response()]
    client = DeepSeekTranslator("private", session=session, sleep=sleep)
    assert client.translate(ENGLISH) == VIETNAMESE
    assert session.post.call_count == 3 and sleep.call_count == 2
    session.post.side_effect = requests.Timeout("credential must not appear")
    with pytest.raises(TranslationError, match="DEEPSEEK_NETWORK"):
        client.translate(ENGLISH)
    assert "credential must not appear" not in json.dumps(client.attempts)


@pytest.mark.parametrize("text,finish,code", [
    ("", "stop", "TRANSLATION_EMPTY"),
    (VIETNAMESE, "length", "TRANSLATION_INCOMPLETE"),
    (VIETNAMESE.replace("1,12", "1,21"), "stop", "TRANSLATION_NUMBERS"),
    (ENGLISH, "stop", "TRANSLATION_LANGUAGE"),
])
def test_same_technical_checks_reject_bad_output(text, finish, code):
    session = Mock()
    session.post.return_value = response(text=text, finish=finish)
    with pytest.raises(TranslationError, match=code):
        DeepSeekTranslator("private", session=session, sleep=Mock()).translate(ENGLISH)


def test_wrappers_are_stripped_but_prose_kept():
    assert strip_wrappers("```\n" + VIETNAMESE + "\n```") == VIETNAMESE
    assert strip_wrappers("Bản dịch: " + VIETNAMESE) == VIETNAMESE


def test_service_records_deepseek_provider(tmp_path):
    snapshot = Snapshot(as_of=AT)
    snapshot.sources["news_te_eur"] = Source(
        id="news_te_eur", url="https://tradingeconomics.com/euro-area/currency/news/123",
        published_at=AT - timedelta(hours=1), retrieved_at=AT, text=ENGLISH, kind="news")
    translator = Mock(attempts=[])
    translator.translate.return_value = VIETNAMESE
    result = translate_snapshot(snapshot, tmp_path / "out", tmp_path / "cache", translator=translator)
    assert result["provider"] == "deepseek" and result["model"] == MODEL
    assert result["articles"]["news_te_eur"]["record"]["provider"] == "deepseek"

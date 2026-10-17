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


# Real deepseek-flash outputs from the 2026-10-18 check, before the style rules
# (weekday names reach the model masked as BTTNPROTECT...END markers).
STYLE_CASES = [
    ("The Federal Reserve kept rates unchanged at 4.25%, while the Japanese yen depreciated past 150 per dollar on Tuesday.",
     "Cục Dự trữ Liên bang Mỹ giữ nguyên lãi suất ở mức 4.25%, trong khi đồng yên Nhật suy yếu vượt mức 150 yên đổi một USD vào BTTNPROTECTAEND.",
     "Cục Dự trữ Liên bang Mỹ giữ nguyên lãi suất ở mức 4,25%, trong khi USD-JPY vượt mức 150 cho thấy JPY đang suy yếu vào thứ Ba."),
    ("Core inflation slowed to 2.8% in September, while headline inflation ticked up and Nonfarm Payrolls rose by 150,000.",
     "Lạm phát cơ bản giảm xuống 2.8% trong tháng 9, trong khi lạm phát danh nghĩa tăng nhẹ và Bảng lương phi nông nghiệp tăng thêm 150,000.",
     "Lạm phát cơ bản giảm xuống 2,8% trong tháng 9, trong khi lạm phát danh nghĩa tăng nhẹ và Bảng lương phi nông nghiệp tăng thêm 150.000."),
    ("The offshore yuan strengthened to 7.12 per dollar as China announced $500 billion in expansionary fiscal policies.",
     "Đồng nhân dân tệ offshore tăng lên 7.12 mỗi đô la khi Trung Quốc công bố 500 tỷ $ chính sách tài khóa mở rộng.",
     "Đồng nhân dân tệ ngoại biên tăng lên 7,12 nhân dân tệ đổi một USD khi Trung Quốc công bố 500 tỷ USD chính sách tài khóa mở rộng."),
    ("ECB Chief Economist Philip Lane said further tightening may be needed in the first quarter, sending the euro up 0.4% to $1.1650.",
     "Kinh tế trưởng ECB Philip Lane cho biết có thể cần thắt chặt trong quý 1, khiến đồng euro tăng 0,4% lên $1.1650.",
     "Kinh tế trưởng ECB Philip Lane cho biết có thể cần thắt chặt thêm trong quý 1, khiến đồng euro tăng 0,4% lên 1,1650 USD."),
]


@pytest.mark.parametrize("source,raw,expected", STYLE_CASES)
def test_style_rules_fix_observed_deepseek_output(source, raw, expected):
    session = Mock()
    session.post.return_value = response(text=raw)
    client = DeepSeekTranslator("private", session=session, sleep=Mock())
    assert client.translate(source) == expected
    assert session.post.call_count == 1


def test_style_never_changes_a_number_value():
    from bttn.deepseek_translation import normalize_style
    # A different yen level must not be rewritten into the pair notation.
    out = normalize_style("The Japanese yen depreciated past 150 per dollar.",
                          "Đồng yên Nhật suy yếu vượt mức 151 yên đổi một USD.")
    assert "USD-JPY" not in out and "151" in out
    # A Vietnamese-formatted number is left as is.
    assert normalize_style("Growth was 0.4%.", "Tăng trưởng đạt 0,4%.") == "Tăng trưởng đạt 0,4%."


def test_missing_further_tightening_is_retried_then_rejected():
    source = "Officials said further tightening and further tightening of credit may follow."
    raw = "Các quan chức cho biết có thể thắt chặt và thắt chặt tín dụng."
    session = Mock()
    session.post.return_value = response(text=raw)
    with pytest.raises(TranslationError, match="TRANSLATION_TERMINOLOGY"):
        DeepSeekTranslator("private", session=session, sleep=Mock()).translate(source)
    assert session.post.call_count == 2


def test_prompt_states_the_five_style_rules():
    from bttn.deepseek_translation import system_prompt
    prompt = system_prompt()
    for phrase in ["4,25%", "500 tỷ USD", "nhân dân tệ ngoại biên", "thắt chặt thêm", "USD-JPY vượt mức X"]:
        assert phrase in prompt

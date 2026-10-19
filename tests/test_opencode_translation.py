"""Dịch bằng DeepSeek V4.1 Flash miễn phí qua OpenCode trước, rồi DeepSeek trả phí, GLM miễn phí, NVIDIA Riva."""
from bttn import deepseek_translation as ds
from bttn.nvidia_translation import TranslationError
from bttn.translation_service import ChainTranslator, default_translator


class Fake:
    def __init__(self, name, *, fail=None, key="k"):
        self.name, self.fail, self.api_key, self.attempts, self.calls = name, fail, key, [], 0

    def translate(self, text):
        self.calls += 1
        self.attempts.append({"model": self.name, "status": "failed" if self.fail else "checked"})
        if self.fail:
            raise TranslationError(self.fail)
        return f"{self.name}:{text}"


def _chain(free, paid, glm, riva):
    return ChainTranslator([("opencode", free, True), ("deepseek", paid, True),
                            ("opencode_glm", glm, False), ("nvidia_riva", riva, False)])


def test_free_deepseek_first_and_cached():
    t = _chain(Fake("free"), Fake("paid"), Fake("glm"), Fake("riva"))
    assert t.translate("a") == "free:a" and not t.used_backup and t.used == {"opencode"}


def test_free_quota_exhausted_uses_paid_deepseek_for_rest_of_run():
    free = Fake("free", fail="OPENCODE_HTTP_402")
    t = _chain(free, Fake("paid"), Fake("glm"), Fake("riva"))
    assert t.translate("a") == "paid:a" and t.translate("b") == "paid:b"
    assert free.calls == 1 and t.down == {"opencode": "OPENCODE_HTTP_402"} and not t.used_backup


def test_rate_limit_moves_on_without_disabling():
    free = Fake("free", fail="OPENCODE_HTTP_429")
    t = _chain(free, Fake("paid"), Fake("glm"), Fake("riva"))
    assert t.translate("a") == "paid:a" and t.translate("b") == "paid:b"
    assert free.calls == 2 and "opencode" not in t.down


def test_both_deepseek_down_uses_glm_not_cached_then_riva():
    t = _chain(Fake("free", fail="OPENCODE_HTTP_402"), Fake("paid", fail="DEEPSEEK_HTTP_402"),
               Fake("glm", fail="OPENCODE_HTTP_500"), Fake("riva"))
    assert t.translate("a") == "riva:a" and t.used_backup
    assert t.provider_label(t.used) == "nvidia_riva"


def test_missing_keys_skip_steps():
    t = _chain(Fake("free", key=""), Fake("paid"), Fake("glm", key=""), Fake("riva"))
    assert t.translate("a") == "paid:a" and t.down["opencode"] == "OPENCODE_API_KEY_MISSING"


def test_default_chain_uses_opencode_endpoint_and_free_models(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "oc")
    t = default_translator()
    names = [n for n, _, _ in t.steps]
    assert names == ["opencode", "deepseek", "opencode_glm", "nvidia_riva"]
    free, glm = t.steps[0][1], t.steps[2][1]
    assert free.endpoint == "https://opencode.ai/inference/openai/v1/chat/completions"
    assert free.model == "deepseek-v4.1-flash-free" and glm.model == "glm-5.3-flash-free"
    assert free.prefix == "OPENCODE"


def test_opencode_http_error_code_uses_opencode_prefix(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-secret-oc-123")

    class Resp:
        status_code = 402

    class Session:
        def post(self, *a, **k):
            return Resp()

    client = ds.opencode_translator(session=Session(), sleep=lambda s: None)
    try:
        client.translate("The Fed held rates steady.")
    except TranslationError as exc:
        assert str(exc) == "OPENCODE_HTTP_402"
    else:
        raise AssertionError("expected error")
    assert all("sk-secret" not in str(a) for a in client.attempts)   # khóa không bị ghi vào nhật ký


def test_weekly_uses_free_opencode_before_paid_deepseek():
    from bttn.weekly.translate import Translator

    sent = []

    class R:
        status_code = 200

        def json(self):
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": "The SBV net withdrew VND 1,612.28 billion over five sessions."}}]}

    class Session:
        def post(self, url, headers, json, timeout, allow_redirects):
            sent.append((url, json["model"]))
            return R()

    t = Translator(api_key="nv", deepseek_key="ds", opencode_key="oc", session=Session(), sleep=lambda s: None)
    assert t.provider == "opencode"
    t.sentence("NHNN hút ròng 1.612,28 tỷ đồng trong 5 phiên.", "vi", "en")
    assert sent == [("https://opencode.ai/inference/openai/v1/chat/completions", "deepseek-v4.1-flash-free")]


def test_weekly_free_quota_exhausted_falls_to_paid():
    from bttn.weekly.translate import Translator

    sent = []

    class R:
        def __init__(self, code):
            self.status_code = code

        def json(self):
            return {"choices": [{"finish_reason": "stop",
                                 "message": {"content": "The SBV net withdrew VND 1,612.28 billion."}}]}

    class Session:
        def post(self, url, headers, json, timeout, allow_redirects):
            sent.append(json["model"])
            return R(402 if "opencode" in url else 200)

    t = Translator(api_key="nv", deepseek_key="ds", opencode_key="oc", session=Session(), sleep=lambda s: None)
    t.sentence("NHNN hút ròng 1.612,28 tỷ đồng.", "vi", "en")
    t.sentence("NHNN hút ròng 1.612,28 tỷ đồng.", "vi", "en")
    assert sent == ["deepseek-v4.1-flash-free", ds.MODEL, ds.MODEL]

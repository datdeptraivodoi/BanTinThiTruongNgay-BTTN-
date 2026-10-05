import pytest

from bttn import analysis


def test_zenmux_uses_own_endpoint_and_final_content(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}], "usage": {"total_tokens": 12}}

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(analysis.requests, "post", post)
    assert analysis.zenmux("prompt", {}, "google/gemini-3.8-flash", "test-key") == ("{}", {"total_tokens": 12})
    assert captured["url"] == "https://zenmux.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "google/gemini-3.8-flash"


def test_zenmux_rejects_truncated_response(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}

    monkeypatch.setattr(analysis.requests, "post", lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match="Incomplete ZenMux"):
        analysis.zenmux("prompt", {}, "model", "test-key")

"""BTTN ngày: DeepSeek lỗi (hết số dư, thiếu khóa…) thì NVIDIA Riva dịch, bản tin vẫn ra."""
import pytest

from bttn.nvidia_translation import TranslationError
from bttn.translation_service import FallbackTranslator


class Fake:
    def __init__(self, name, *, fail=None, key="k"):
        self.name, self.fail, self.api_key, self.attempts, self.calls = name, fail, key, [], 0

    def translate(self, text):
        self.calls += 1
        self.attempts.append({"model": self.name, "status": "failed" if self.fail else "checked"})
        if self.fail:
            raise TranslationError(self.fail)
        return f"{self.name}:{text}"


def test_deepseek_first():
    t = FallbackTranslator(Fake("ds"), Fake("riva"))
    assert t.translate("a") == "ds:a" and not t.used_backup


def test_out_of_balance_switches_to_riva_for_rest_of_run():
    ds, riva = Fake("ds", fail="DEEPSEEK_HTTP_402"), Fake("riva")
    t = FallbackTranslator(ds, riva)
    assert t.translate("a") == "riva:a" and t.translate("b") == "riva:b"
    assert ds.calls == 1 and t.primary_down == "DEEPSEEK_HTTP_402" and t.used_backup
    assert [a["provider"] for a in t.attempts] == ["deepseek", "nvidia_riva", "nvidia_riva"]


def test_one_bad_segment_uses_riva_but_keeps_deepseek():
    ds = Fake("ds", fail="TRANSLATION_NUMBERS")
    t = FallbackTranslator(ds, Fake("riva"))
    assert t.translate("a") == "riva:a"
    ds.fail = None
    assert t.translate("b") == "ds:b"


def test_no_riva_key_keeps_original_error():
    t = FallbackTranslator(Fake("ds", fail="DEEPSEEK_HTTP_402"), Fake("riva", key=""))
    with pytest.raises(TranslationError, match="DEEPSEEK_HTTP_402"):
        t.translate("a")

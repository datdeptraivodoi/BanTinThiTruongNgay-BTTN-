"""Sửa sau lần chạy thử 17/10/2026 trên VPS: mô hình NVIDIA bị rút (410), Trading Economics 403,
giá tuần thiếu (cà phê, xăng, BCOM), câu sai mục ở trang 1, thuật ngữ dịch."""
from datetime import date, datetime, timezone

from bttn.weekly import te_stream
from bttn.weekly.facts import WeeklyFacts, yahoo_move
from bttn.weekly.translate import Translator, WeeklyTranslationError, glossary
from bttn.weekly.write import BOND_STRICT, NOT_BOND, clean_sentences


def test_llm_skips_retired_models():
    t = Translator(api_key="x", sleep=lambda s: None)
    tried = []

    def chat(model, system, text, temperature=0):
        tried.append(model)
        if len(tried) < 3:
            raise WeeklyTranslationError("NVIDIA_HTTP_410")
        return "<think>x</think> ok"
    t._chat = chat
    assert t._llm("sys", "text") == "ok"
    assert len(set(tried)) == 3
    t._llm("sys", "again")
    assert tried[-1] == tried[2]                       # nhớ mô hình đang chạy được


def test_glossary_fixes_vietnamese_terms():
    assert glossary("The NHNN sold TPCP on the reposo channel for VND1,000 billion", "en") == \
        "The SBV sold government bond on the repo channel for VND 1,000 billion"


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def rec(day, n):
    body = f"EUR rose {n}. " + "The euro gained against the dollar as investors weighed the outlook for rates. " * 2
    return {"id": n, "title": f"t{n}", "description": body, "url": "/euro-area/currency",
            "date": f"2026-10-{day:02d}T08:00:00"}


def test_te_week_pages_until_monday_and_sends_browser_headers():
    calls = []

    class Session:
        def get(self, url, params, headers, timeout):
            calls.append((params["start"], headers.get("Accept-Language")))
            if "i" in params:
                return FakeResp(403, None)
            day = 15 if params["start"] == 0 else 9
            return FakeResp(200, [rec(day, params["start"] + i) for i in range(30)])

    found, errors = te_stream.collect_week(date(2026, 10, 12), date(2026, 10, 16), session=Session())
    assert all(lang == "en-US,en;q=0.9" for _, lang in calls)
    assert {s for s, _ in calls} == {0, 30}             # trang 2 đã lùi quá thứ Hai thì dừng
    assert len(found["currency_eu"]) == 30 and errors == ["commodity: HTTP 403"]


def test_te_fallback_from_daily_snapshots():
    from bttn.models import Snapshot, Source
    now = datetime(2026, 10, 14, 4, tzinfo=timezone.utc)
    snap = Snapshot.model_construct(as_of=now, sources={"te1": Source(
        id="te1", url="https://tradingeconomics.com/united-states/currency", published_at=now, retrieved_at=now,
        text="Dollar slips\n\nThe dollar index fell to 101.9.", kind="news", text_scope="article")}, observations={})
    items = te_stream.from_snapshots([snap])
    assert items["currency_us"][0]["text"] == "The dollar index fell to 101.9."


def test_yahoo_week_move():
    ts = [int(datetime(2026, 10, d, 14, tzinfo=timezone.utc).timestamp()) for d in (9, 12, 13, 16)]
    data = {"chart": {"result": [{"meta": {"gmtoffset": -14400}, "timestamp": ts,
                                  "indicators": {"quote": [{"close": [300.0, 302.0, None, 305.6],
                                                            "high": [301, 303, None, 306.0], "low": [299, 298.5, None, 304]}]}}]}}
    m = yahoo_move("ARABICA", date(2026, 10, 12), date(2026, 10, 16), fetch=lambda url: data)
    assert (m.prev_close, m.open, m.close, m.high, m.low) == (300.0, 302.0, 305.6, 306.0, 298.5)


def test_page1_filters():
    assert NOT_BOND.search("Có 125,72 tỷ đồng trúng thầu ở kỳ hạn 91 ngày.")
    assert not BOND_STRICT.search("Có 564,85 tỷ đồng trúng thầu ở cả 4 kỳ hạn.")
    assert BOND_STRICT.search("Kỳ hạn 15Y 1.000 tỷ đồng, 30Y 500 tỷ đồng.") and not NOT_BOND.search("Kỳ hạn 15Y 1.000 tỷ đồng.")
    text = ('Bắc Ninh ... "điểm đến lớn” của dòng vốn quốc tế. GDP quý 3/2026 tăng 9,95% so với cùng kỳ năm trước. '
            "Trong ảnh: Sản xuất tại một doanh nghiệp. Ảnh: Lê Toàn")
    assert clean_sentences(text) == ["GDP quý 3/2026 tăng 9,95% so với cùng kỳ năm trước."]


def test_fx_range_skipped_when_quote_flat():
    from bttn.weekly.facts import Day
    from bttn.weekly.write import fx_vi
    days = [Day(day=date(2026, 10, d)) for d in (12, 13, 14, 15)]
    for d, v in zip(days, (25877, 25878, 25878, 25878)):
        d.itb_ask, d.itb_bid = v, v - 5
    f = WeeklyFacts(date(2026, 10, 12), date(2026, 10, 16), days, {}, [], {}, {})
    assert "dao động trong khoảng" not in str(fx_vi(f))


class _Resp:
    def __init__(self, status, content):
        self.status_code, self._content = status, content

    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {"content": self._content}}]}


def test_weekly_translation_uses_deepseek_first():
    sent = []

    class Session:
        def post(self, url, headers, json, timeout, allow_redirects):
            sent.append((url, json["model"]))
            if "deepseek" in url:
                return _Resp(200, "The SBV net withdrew VND 1,612.28 billion over five sessions.")
            return _Resp(200, "wrong 9,999")

    t = Translator(api_key="nv", deepseek_key="ds", session=Session(), sleep=lambda s: None)
    out = t.sentence("NHNN hút ròng 1.612,28 tỷ đồng trong 5 phiên.", "vi", "en")
    assert out.startswith("The SBV net withdrew VND 1,612.28 billion")
    assert len(sent) == 1 and "deepseek" in sent[0][0]


def test_weekly_translation_falls_back_to_riva_when_deepseek_changes_numbers():
    class Session:
        def post(self, url, headers, json, timeout, allow_redirects):
            if "deepseek" in url:
                return _Resp(200, "The SBV net withdrew VND 1,600 billion.")
            return _Resp(200, "The SBV net withdrew VND 1,612.28 billion in 5 sessions.")

    t = Translator(api_key="nv", deepseek_key="ds", session=Session(), sleep=lambda s: None)
    assert "1,612.28" in t.sentence("NHNN hút ròng 1.612,28 tỷ đồng trong 5 phiên.", "vi", "en")


def test_weekly_translation_without_deepseek_key_uses_nvidia():
    t = Translator(api_key="nv", deepseek_key="")
    assert t.provider == "nvidia"

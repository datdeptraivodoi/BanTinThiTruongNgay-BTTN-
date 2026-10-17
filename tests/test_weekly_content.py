"""Bản tin tuần: từ dữ kiện cả tuần ra hai file VIE/ENG đúng giới hạn, không gọi mạng."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from docx import Document

from bttn.models import Observation, Point, Snapshot, Source
from bttn.weekly import write
from bttn.weekly.build import build_contents
from bttn.weekly.calendar import to_calendars
from bttn.weekly.extract import extract_weekly
from bttn.weekly.facts import build_facts
from bttn.weekly.fit import count_lines, rules
from bttn.weekly.render import render_weekly
from bttn.weekly.te_stream import parse_stream, topic_of
from bttn.weekly.translate import numbers

VN = ZoneInfo("Asia/Ho_Chi_Minh")
ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = {"vi": ROOT / "templates/weekly/BT_TTT_template_VIE.docx",
             "en": ROOT / "templates/weekly/BT_TTT_template_ENG.docx"}
FRIDAY = date(2026, 10, 16)

VIRA_LNH = ("Ngày {d}, lãi suất VND liên ngân hàng biến động trái chiều ở các kỳ hạn ngắn so với phiên trước đó. "
            "Lãi suất kỳ hạn qua đêm và 1 tuần dao động quanh vùng 3,5-4,7% trong phiên giao dịch hôm qua.\n\n"
            "NHNN chào thầu 1.000 tỷ đồng ở mỗi kỳ hạn 7, 14 và 91 ngày với lãi suất 4,5%. "
            "Như vậy, NHNN hút ròng 764,91 tỷ đồng khỏi thị trường qua kênh thị trường mở.\n\n"
            "Lợi suất TPCP trên thị trường thứ cấp biến động trái chiều, kỳ hạn 10 năm đóng cửa ở mức 4,47%. "
            "KBNN đấu thầu thành công 11.452/18.000 tỷ đồng TPCP với lãi suất trúng thầu kỳ hạn 10 năm ở mức 4,43%. "
            "Lãi suất trúng thầu kỳ hạn 5 năm ở mức 4,27%, không đổi so với phiên đấu thầu trước đó của KBNN. "
            "Kỳ hạn 15 năm và 30 năm không có khối lượng trúng thầu trong phiên đấu thầu của KBNN tuần này.")
VIRA_FX = "Tỷ giá trên thị trường tự do giao dịch ở mức 26.020 đồng/USD chiều mua vào và 26.120 đồng/USD chiều bán ra."


def obs(key, value, series=(), unit="x"):
    return Observation(id=key, label=key, value=Decimal(str(value)), unit=unit, source_id="market",
                       trading_date=FRIDAY, basis="test",
                       series=[Point(at=datetime.combine(d, datetime.min.time(), VN) + timedelta(hours=12),
                                     value=Decimal(str(v))) for d, v in series])


def snapshots():
    snaps = []
    monday = FRIDAY - timedelta(days=4)
    for i in range(5):
        day = monday + timedelta(days=i)
        at = datetime.combine(day, datetime.min.time(), VN) + timedelta(hours=11, minutes=30)
        snap = Snapshot(as_of=at)
        snap.sources["market"] = Source(id="market", url="https://example.test", published_at=at, retrieved_at=at)
        for t, base in (("ON", 3.1), ("1W", 4.2), ("2W", 4.8)):
            snap.observations[f"VND_{t}"] = obs(f"VND_{t}", round(base + 0.1 * i, 2))
        for t, (b, a) in (("ON", (-0.5, 0.2)), ("1W", (0.1, 0.8)), ("1M", (0.9, 1.8)), ("3M", (2.0, 2.6)), ("6M", (2.4, 2.9))):
            snap.observations[f"ALM_SWAP_{t}_BID"] = obs(f"ALM_SWAP_{t}_BID", b)
            snap.observations[f"ALM_SWAP_{t}_ASK"] = obs(f"ALM_SWAP_{t}_ASK", a)
        snap.observations["INTERBANK_BID"] = obs("INTERBANK_BID", 25860 + 5 * i)
        snap.observations["INTERBANK_ASK"] = obs("INTERBANK_ASK", 25870 + 5 * i)
        snap.observations["SBV_CENTRAL"] = obs("SBV_CENTRAL", 25630 + 2 * i)
        snap.sources["vira_daily"] = Source(id="vira_daily", url="https://vira.org.vn/x", published_at=at, retrieved_at=at,
                                            text=VIRA_LNH.format(d=f"{day:%d/%m}"), kind="news", text_scope="excerpt")
        snap.sources["vira_fx"] = Source(id="vira_fx", url="https://vira.org.vn/x", published_at=at, retrieved_at=at,
                                         text=VIRA_FX, kind="news", text_scope="excerpt")
        snaps.append(snap)
    last = snaps[-1]
    days = [FRIDAY - timedelta(days=k) for k in range(11, -1, -1) if (FRIDAY - timedelta(days=k)).weekday() < 5]
    for key, start, step in (("EURUSD", 1.11, 0.002), ("USDJPY", 158.3, -0.1), ("DXY", 101.9, -0.03), ("GOLD", 4190, -1),
                             ("BRENT", 104.0, 0.2), ("GASOLINE", 3.05, 0.01), ("ARABICA", 298.0, -0.5),
                             ("ROBUSTA", 3400, -5), ("CRB", 143.0, 0.1), ("BOND_Vietnam", 4.50, 0.005)):
        series = [(d, round(start + step * n, 4)) for n, d in enumerate(days)]
        last.observations[key] = obs(key, series[-1][1], series)
    macro = ("Theo Cục Thống kê, vốn đầu tư toàn xã hội 9 tháng năm 2026 tăng 11,2% so với cùng kỳ, "
             "trong đó vốn FDI thực hiện đạt 18,8 tỷ USD, mức cao nhất 5 năm. "
             "Xuất khẩu hàng hóa tiếp tục tăng trưởng hai con số nhờ nhóm hàng điện tử và máy tính.")
    for k in range(3):
        last.sources[f"news_vn_{k}"] = Source(id=f"news_vn_{k}", url=f"https://baodautu.vn/a{k}", published_at=last.as_of,
                                               retrieved_at=last.as_of, text=f"Tiêu đề GDP FDI {k}\n\n" + macro.replace("11,2", f"1{k},2"),
                                               kind="news", text_scope="article", sha256=f"h{k}")
    return snaps


def te_items():
    at = datetime.combine(FRIDAY, datetime.min.time(), VN) + timedelta(hours=9)
    def item(topic, text):
        return {"id": topic, "title": topic, "text": text, "url": "https://tradingeconomics.com/x", "published_at": at, "topic": topic}
    return {
        "currency_us": [item("currency_us", "The dollar index retreated from an 18-month peak this week as investors looked past strong US inflation data. "
                                            "Data showed that US consumer prices rose sharply in September as gasoline costs surged. "
                                            "Traders are pricing in a 72% chance of the Fed standing pat later this month, up from 53% a month earlier. "
                                            "US producer prices rose by the most in 4.5 years as goods and services costs increased.")],
        "currency_eu": [item("currency_eu", "The euro recovered from a 17-month low as markets reassessed the ECB policy outlook for the coming months.")],
        "coffee": [item("coffee", "Arabica coffee futures settled lower after hitting a one-week high on Wednesday as supply worries eased. "
                                  "Uganda's coffee exports fell 13.7% in August as a prolonged drought reduced the harvest in some areas. "
                                  "ICE certified stocks fell for the last three days to reach only 238,976 bags. "
                                  "The harvest in top grower Vietnam is getting underway and industry participants expect output to be steady.")],
        "energy": [item("energy", "Oil prices edged lower on Friday, driven by persistent Middle East supply concerns and a sharp draw in US crude inventories. "
                                  "Attacks and regional tensions have disrupted shipments through the vital Strait of Hormuz, reducing tanker flows. "
                                  "The International Energy Agency agreed to release over 20 million barrels of emergency diesel. "
                                  "Markets continue to price in significant geopolitical risk premiums until tensions ease.")],
    }


class EchoTranslator:
    """Thay AI trong test: trả nguyên câu (chỉ kiểm phần ghép, không kiểm chất lượng dịch)."""
    def paragraph(self, text, src, dst, min_words=None, max_words=None, max_lines=None):
        from bttn.weekly.translate import Translator, WeeklyTranslationError
        t = Translator(api_key="x")

        def no_ai(*a, **k):
            raise WeeklyTranslationError("offline")
        t._chat = no_ai
        # vi->en ngắn hơn khoảng 20% như bản dịch thật; en->vi dài hơn khoảng 30%
        def fake(s, a, b):
            words = s.rstrip(".").split()
            if a == "vi" and b == "en":
                return " ".join(words[: max(1, int(len(words) * 0.8))]) + "."
            return " ".join(words + words[: int(len(words) * 0.3)]) + "."
        t.sentence = fake
        return t.paragraph(text, src, dst, min_words=min_words, max_words=max_words, max_lines=max_lines)


def calendar_pack():
    events = [{"localDate": "2026-10-19", "localTime": "09:00", "countryCode": "VN", "countryVi": "Việt Nam", "countryEn": "Vietnam",
               "nameVi": "Cục Hải quan công bố số liệu xuất nhập khẩu hàng hóa", "nameEn": "Vietnam Customs releases trade figures",
               "impact": "medium", "priorityScore": 50}]
    for i in range(20):
        events.append({"localDate": f"2026-10-{19 + i % 5}", "localTime": f"{8 + i % 10:02d}:30", "countryCode": "US",
                       "countryVi": "Mỹ", "countryEn": "United States", "nameVi": f"Mỹ: Sự kiện {i}", "nameEn": f"United States: Event {i}",
                       "forecast": "4.4%", "previous": "4.3%", "impact": "high" if i < 5 else "medium", "priorityScore": 90 - i})
    return {"week": {"startDate": "2026-10-19"}, "events": events}


@pytest.fixture
def built(tmp_path):
    facts = build_facts(FRIDAY, snapshots(), inputs={
        "indicators": {"LSCB": [9, 9, 9], "tai_chiet_khau": [3, 3, 3], "tai_cap_von": [4.5, 4.5, 4.5],
                       "cvqd": [5, 5, 5], "omo": [4.5, 4.5, 4.5], "tgtt_forecast": None},
        "term_sofr": {"start": {"1M": 3.92465, "3M": 4.04249, "6M": 4.2014}, "end": {"1M": 3.97183, "3M": 4.11624, "6M": 4.29534}},
        "forecasts": {"fx": {"lo": 25800, "hi": 25950}}}, te_items=te_items())
    seed = extract_weekly(TEMPLATES["vi"], "vi").charts
    contents, issues = build_contents(facts, translator=EchoTranslator(), template_seed=seed, work=tmp_path,
                                      centrals={date(2026, 10, 2): 25629, date(2026, 10, 9): 25637, FRIDAY: 25638},
                                      history_path=tmp_path / "hist.json",
                                      calendar=to_calendars(calendar_pack(), date(2026, 10, 19)))
    return facts, contents, issues


def test_written_sections_respect_line_limits(built):
    _, contents, issues = built
    assert not [i for i in issues if i["code"] == "TOO_MANY_LINES"]
    for name in ("interbank", "fx", "bonds"):
        text = contents["vi"].sections[name].body[0]
        assert count_lines(text) <= rules()["sections"]["vi"][name]["max_lines"]
    vi = contents["vi"].sections
    assert "ON-2W dao động từ 3,1-5,2%" in vi["interbank"].body[0]
    assert "hút ròng 3.824,55 tỷ đồng trong 5 phiên" in vi["interbank"].body[0]
    assert "đóng cửa ngày 16.10 quanh mức 25.890" in vi["fx"].body[0]
    assert vi["fx"].forecast.endswith("quanh khu vực 25.800 – 25.950.")      # số team ghi đè
    assert contents["en"].sections["intl_fx"].body[0].startswith("The US Dollar Index fell")
    assert len(contents["vi"].news) == 3


def test_tables_and_calendar(built):
    _, contents, _ = built
    vi, en = contents["vi"], contents["en"]
    assert vi.small_tables["sofr"].values == ["3,97183", "4,11624", "4,29534"]
    assert en.small_tables["sofr"].changes == ["0.04718", "0.07375", "0.09394"]
    assert vi.small_tables["policy_rates"].changes[1] == "+25bps"
    # Bảng lùi một tuần: tuần 05–09.10, tuần 28.09–02.10; dự báo = trung tâm thứ Sáu 16.10 ± 45 điểm theo chiều tuần
    assert vi.indicators.headers == ["05.10-09.10", "28.09-02.10", "Dự báo"]
    assert vi.indicators.rows[5] == ["25.637", "25.629", "25.683"]
    assert len(vi.calendar.events) == 15
    vn = [e for e in vi.calendar.events if e.country == "Việt Nam"]
    assert len(vn) == 1 and vn[0].bold                        # dòng Việt Nam luôn giữ
    assert vi.calendar.events[0].date == "19.10.2026" and vi.calendar.events[1].date == ""
    assert en.calendar.events[0].forecast == "4.4%" and vi.calendar.events[0].forecast == "4,4%"


def test_renders_three_pages_both_languages(built, tmp_path):
    _, contents, _ = built
    for lang in ("vi", "en"):
        out = render_weekly(contents[lang], TEMPLATES[lang], tmp_path / f"{lang}.docx")
        assert [len(t.rows) for t in Document(out).tables] == [6, 6, 4]


def test_history_seeded_from_template_then_appended(built, tmp_path):
    _, contents, _ = built
    chart = contents["vi"].charts["interbank"]
    assert chart.categories[-1] == "16/10/2026" and len(chart.categories) == 22
    assert chart.series[0][-1] == pytest.approx(3.5)


def test_number_check_accepts_format_change_only():
    assert numbers("Tỷ giá 25.870, lãi suất 3,1% ngày 12.10", "vi") == numbers("Rate 25,870, yield 3.1% on October 12", "en")
    assert numbers("Tỷ giá 25.870", "vi") != numbers("Rate 25.870", "en")


def test_te_stream_parsing_and_topics():
    assert topic_of("https://tradingeconomics.com/united-states/currency") == "currency_us"
    assert topic_of("https://tradingeconomics.com/commodity/coffee") == "coffee"
    assert topic_of("https://tradingeconomics.com/commodity/brent-crude-oil") == "energy"
    assert topic_of("https://tradingeconomics.com/commodity/wheat") is None
    body = " ".join(["word"] * 25)
    recs = [{"id": 1, "title": "Dollar Set for Weekly Loss", "description": body, "url": "/united-states/currency", "date": "2026-10-16T08:00:00"},
            {"id": 2, "title": "Energy Commodities Updates: Gas", "description": body, "url": "/commodity/gasoline", "date": "2026-10-16T08:00:00"},
            {"id": 3, "title": "Old", "description": body, "url": "/commodity/coffee", "date": "2026-10-09T08:00:00"}]
    items = parse_stream(recs, date(2026, 10, 12), FRIDAY)
    assert [i["topic"] for i in items] == ["currency_us"]


def test_suggested_forecasts_from_week(built):
    facts, _, _ = built
    s = write.suggested_forecasts(facts)
    assert s["interbank"] == {"lo": 3.0, "hi": 5.5}
    assert s["intl_fx"]["eurusd"] == pytest.approx(1.128)


def _central_facts(override=None):
    return build_facts(FRIDAY, snapshots(), inputs={"indicators": {"tgtt_forecast": override}}, te_items=[])


def test_central_forecast_band_and_direction():
    from bttn.weekly.build import central_forecast
    up = {date(2026, 10, 9): 25637, FRIDAY: 25650}
    down = {date(2026, 10, 9): 25660, FRIDAY: 25650}
    assert central_forecast(_central_facts(), up) == (25695, [])
    assert central_forecast(_central_facts(), down) == (25605, [])
    value, notes = central_forecast(_central_facts(25690), up)       # lệch 40 điểm: hợp lệ
    assert value == 25690 and not notes
    value, notes = central_forecast(_central_facts(25620), up)       # lệch 30 điểm: biên dưới vẫn hợp lệ
    assert not notes
    value, notes = central_forecast(_central_facts(25640), up)       # lệch 10 điểm: báo trong email review
    assert value == 25640 and "30–60" in notes[0]
    assert central_forecast(_central_facts(), {})[0] is None


def test_tradingview_move_from_candles():
    from datetime import datetime as dt

    from bttn.weekly.facts import VN_TZ, tradingview_move

    def ts(d):
        return int(dt(2026, 10, d, 12, tzinfo=VN_TZ).timestamp())

    candles = [{"time": ts(9), "close": 100.0}] + [{"time": ts(d), "close": 100.0 + d - 11} for d in range(12, 17)]
    m = tradingview_move("CRB", "TVC:BCOM", date(2026, 10, 12), FRIDAY, runner=lambda k, s: {k: {"candles": candles}})
    assert (m.prev_close, m.open, m.close, m.high, m.low) == (100.0, 101.0, 105.0, 105.0, 101.0)
    assert round(m.pct, 2) == 5.0
    assert tradingview_move("CRB", "X", date(2026, 10, 12), FRIDAY, runner=lambda k, s: {}) is None


def _condense_translator(reply):
    from bttn.weekly.translate import Translator
    t = Translator(api_key="x", sleep=lambda s: None)
    t.sentence = lambda s, a, b: s
    calls = []

    def chat(model, system, text, temperature=0):
        calls.append(system)
        return reply(len(calls))
    t._chat = chat
    return t, calls


LONG_EN = ("The dollar index fell 0.8% to 98.4 this week. Euro rose to 1.1650 after strong German data. "
           "Analysts in London and New York said positioning was light ahead of the holiday. "
           "Yen gained to 149.2 as Japanese officials repeated warnings about speculative moves.")


def test_condense_used_when_translation_too_long():
    short = "The dollar index fell 0.8% to 98.4. Euro rose to 1.1650. Yen gained to 149.2 on official warnings."
    t, calls = _condense_translator(lambda n: short)
    text, notes = t.paragraph(LONG_EN, "vi", "en", min_words=15, max_words=25)
    assert text == short and not notes and "15 and 25 words" in calls[0]


def test_condense_rejects_invented_numbers_then_drops_sentences():
    bad = "The dollar index fell 0.9% to 98.4. Euro rose to 1.1650. Yen gained to 149.2 on official warnings."
    t, calls = _condense_translator(lambda n: bad)
    text, notes = t.paragraph(LONG_EN, "vi", "en", min_words=15, max_words=25)
    assert len(calls) == 2                                  # thử lại một lần
    assert text == "The dollar index fell 0.8% to 98.4 this week. Euro rose to 1.1650 after strong German data."
    assert "AI rút gọn không đạt" in notes[0]


def test_condense_retries_when_word_count_off():
    good = "The dollar index fell 0.8% to 98.4. Euro rose to 1.1650. Yen gained to 149.2 on official warnings."
    t, calls = _condense_translator(lambda n: "Dollar fell." if n == 1 else good)
    text, _ = t.paragraph(LONG_EN, "vi", "en", min_words=15, max_words=25)
    assert text == good and len(calls) == 2

"""Góp ý 22.10: mục liên ngân hàng mở đầu bằng "Phiên ngày ..., thị trường interbank tăng/giảm/đi ngang quanh vùng".
Khi bảng ảnh Market Watch không đọc được, lấy lãi suất LNH từ đoạn chữ bản tin ngày VIRA."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bttn import editorial, vira
from bttn.models import VN_TZ, Observation, Snapshot, Source
from bttn.sources import vira_daily_interbank
from bttn.validation import resolve

ROOT = Path(__file__).resolve().parents[1]


def _snap():
    return Snapshot.model_validate_json((ROOT / "tests/fixtures/snapshot.json").read_text(encoding="utf-8"))


def _set(s, on, w1, w2, day=date(2026, 10, 21)):
    for t, v in (("ON", on), ("1W", w1), ("2W", w2)):
        key = f"VND_{t}"
        src = s.observations[key].source_id if key in s.observations else "vira_0"
        s.observations[key] = Observation(id=key, label=key, value=Decimal(v), unit="%/năm", source_id=src,
                                          trading_date=day, tenor=t, basis="test")


def test_lead_with_trend_from_history(tmp_path):
    s = _snap()
    _set(s, "4.10", "4.30", "4.50")
    prev = ("2026-10-20", {"ON": Decimal("4.30"), "1W": Decimal("4.45"), "2W": Decimal("4.60")})
    lead = editorial.interbank_lead(s, previous=prev)
    text = resolve(lead[0].text, s)
    assert text.startswith("Phiên ngày 21.10.2026, thị trường interbank giảm quanh vùng 4,1")
    assert "ON-2W" in text
    flat = ("2026-10-20", {"ON": Decimal("4.11"), "1W": Decimal("4.30"), "2W": Decimal("4.49")})
    assert "đi ngang" in resolve(editorial.interbank_lead(s, previous=flat)[0].text, s)


def test_history_roundtrip(tmp_path):
    s = _snap()
    _set(s, "4.10", "4.30", "4.50")
    path = tmp_path / "h.json"
    vira.record_vnibor_history(s, path)
    _set(s, "4.20", "4.40", "4.60", day=date(2026, 10, 22))
    vira.record_vnibor_history(s, path)
    day, rows = vira.previous_vnibor(date(2026, 10, 22), path)
    assert day == "2026-10-21" and rows["ON"] == Decimal("4.10")
    assert vira.previous_vnibor(date(2026, 10, 21), path) is None


def test_fallback_from_vira_daily_text():
    s = _snap()
    for k in [k for k in s.observations if k.startswith(("VND_", "USD_"))]:
        s.observations.pop(k)
    body = ("Lãi suất bình quân LNH VND giảm mạnh 0,30-0,50 đpt ở hầu hết các kỳ hạn từ 1M trở xuống, giao dịch tại "
            "ON 8,50%; 1W 9,00%; 2W 9,00% và 1M 9,20%. Lãi suất bình quân LNH USD biến động nhẹ, giao dịch tại "
            "ON 3,62%; 1W 3,68%; 2W 3,71%; 1M 3,75%. Lợi suất TPCP thứ cấp tăng nhẹ: 3Y 3,20%; 10Y 4,11%.")
    published = datetime(2026, 10, 22, 8, 0, tzinfo=VN_TZ)
    s.sources["vira_daily"] = Source(id="vira_daily", url="https://vira.org.vn/tin/Ban-tin-Kinh-te-Tai-chinh-ngay/Ban-tin-x.html",
                                     published_at=published, retrieved_at=published + timedelta(hours=1), text=body,
                                     kind="news", text_scope="excerpt")
    filled = vira_daily_interbank(s)
    assert {"VND_ON", "VND_1W", "VND_2W", "VND_1M", "USD_ON", "USD_1M"} <= set(filled)
    assert s.observations["VND_ON"].value == Decimal("8.50")
    assert s.observations["USD_1M"].value == Decimal("3.75")
    assert s.observations["VND_ON"].trading_date == date(2026, 10, 21)

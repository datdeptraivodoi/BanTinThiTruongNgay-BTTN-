"""Góp ý bản tin 19.10.2026: mục USD-VND không lặp tin nổi bật, tập trung cán cân thương mại, XNK,
kiều hối, FDI, chênh lệch vàng và tỷ giá tự do."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bttn import editorial
from bttn.calculations import derive_gold_spread
from bttn.models import Observation, Snapshot, Source
from bttn.validation import resolve

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def snap():
    s = Snapshot.model_validate_json((ROOT / "tests/fixtures/snapshot.json").read_text(encoding="utf-8"))
    at = s.as_of
    s.sources["sjc_gold"] = Source(id="sjc_gold", url="https://webgia.com/gia-vang/sjc/", published_at=at,
                                   retrieved_at=at, text="140,1 – 143,1 triệu đồng/lượng", kind="market")
    for side, v in (("BUY", "140.1"), ("SELL", "143.1")):
        s.observations[f"SJC_{side}"] = Observation(id=f"SJC_{side}", label="SJC", value=Decimal(v),
                                                    unit="triệu đồng/lượng", source_id="sjc_gold",
                                                    trading_date=at.date(), basis="test")
    body = ("Kinh tế\n\nCán cân thương mại nửa đầu tháng 10 thặng dư 1,82 tỷ USD, ghi nhận hai kỳ liên tiếp xuất siêu. "
            "Kim ngạch xuất khẩu 9 tháng tăng 15,6% so với cùng kỳ năm trước. "
            "Lượng kiều hối về TP.HCM 9 tháng đạt 8,2 tỷ USD, tăng 12% so với cùng kỳ. "
            "Thủ tướng dự lễ khánh thành nhà máy mới tại tỉnh Bắc Ninh với tổng vốn 200 triệu USD.")
    s.sources["news_vn_trade"] = Source(id="news_vn_trade", url="https://vneconomy.vn/a.htm",
                                        published_at=at - timedelta(hours=2), retrieved_at=at, text=body,
                                        kind="news", text_scope="article")
    return s


def test_gold_spread_uses_sjc_and_world_price_converted(snap):
    derive_gold_spread(snap)
    world = snap.observations["GOLD"].value * snap.observations["MB_SELL"].value * Decimal("1.20565") / 1_000_000
    assert snap.observations["GOLD_SPREAD"].value == round(Decimal("143.1") - world, 2)
    assert "triệu đồng/lượng" in resolve("{{GOLD_SPREAD}} triệu đồng/lượng", snap)


def test_usd_vnd_focuses_on_fx_fundamentals_and_skips_highlight_news(snap):
    derive_gold_spread(snap)
    section, _ = editorial.usd_vnd_section(snap, {})
    text = resolve(" ".join(section.paragraphs), snap, safe=True)
    assert "Cán cân thương mại nửa đầu tháng 10" in text and "kiều hối" in text
    assert "Thủ tướng dự lễ khánh thành" not in text           # không phải tin cung – cầu ngoại tệ
    assert "Giá vàng miếng SJC đang cao hơn giá vàng thế giới quy đổi" in text
    assert text.index("Cán cân thương mại") < text.index("kiều hối") < text.index("Giá vàng miếng SJC")
    # Tin đã lên "Tin tức nổi bật" thì mục USD-VND bỏ qua
    section, _ = editorial.usd_vnd_section(snap, {}, exclude_sources={"news_vn_trade"})
    assert "Cán cân thương mại nửa đầu" not in " ".join(section.paragraphs)


BAD_COFFEE_1910 = [   # câu thật trong mục cà phê bản BTTN-20261019 (bị góp ý viết hoa lung tung, câu cụt)
    "Lượng tồn kho này cần được bổ sung để giá cà phê ổn định hơn.",
    "Vì vậy, so với mức ngoài, Brazil vẫn có một lượng lớn cà phê chất lượng tuyệt vời\".",
    "Dù vậy, nông dân Brazil không có nhiều lý do để vội vàng bán hàng.",
    "TIN LIÊN QUAN EU và Mỹ tăng mua mạnh, vì sao giá cà phê vẫn giảm?",
]
GOOD_COFFEE = "Tồn kho cà phê Arabica được chứng nhận trên sàn ICE giảm xuống mức thấp nhất kể từ năm 1999."


def test_coffee_junk_sentences_are_rejected():
    from bttn.commodity_text import junk_reason
    assert all(junk_reason(s) for s in BAD_COFFEE_1910)
    assert junk_reason(GOOD_COFFEE) is None


def test_coffee_opening_sentence_from_prices(snap):
    from bttn.commodity_text import coffee_lead
    snap.observations["ARABICA"].daily_pct = Decimal("5.6")
    snap.observations["ROBUSTA"].daily_pct = Decimal("1.2")
    lead = resolve(coffee_lead(snap), snap)
    assert lead.startswith("Thị trường cà phê biến động mạnh, giá Arabica tại New York tăng 5,60% lên ")
    assert "trong khi giá Robusta tại London tăng 1,20% lên" in lead and lead.endswith("USD/tấn.")

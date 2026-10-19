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


WEEKLY_1216 = (  # VIRA "Tổng hợp Kinh tế - Tài chính tuần 12/10 - 16/10/2026", đăng 08:23 thứ Hai 19/10
    "Tóm lược thị trường trong nước từ 12/10 - 16/10/2026\n"
    "Thị trường ngoại tệ: Trong tuần từ 12/10 - 16/10, tỷ giá trung tâm được NHNN điều chỉnh tăng – giảm đan xen qua các phiên. "
    "Tỷ giá trên thị trường tự do tuần qua ít biến động. Chốt phiên 16/10, tỷ giá tự do giảm 40 đồng ở chiều mua vào và 50 đồng "
    "ở chiều bán ra so với phiên cuối tuần trước đó, giao dịch tại 26.020 VND/USD và 26.120 VND/USD.\n"
    "Thị trường tiền tệ LNH: Tuần từ 12/10 - 16/10, lãi suất VND LNH biến động tăng – giảm mạnh qua các phiên ở tất cả các kỳ hạn "
    "từ 1M trở xuống. Chốt ngày 16/10, lãi suất VND LNH giao dịch ở mức: ON 4,50% (không thay đổi); 1W 4,60% (-0,55 đpt); "
    "2W 4,70% (+0,70 đpt); 1M 5,50% (+0,30 đpt).\n"
    "Thị trường mở: Trên thị trường mở tuần qua từ 12/10 - 16/10, ở kênh cầm cố, NHNN chào thầu 34.000 tỷ đồng với các kỳ hạn "
    "7 ngày, 14 ngày, 35 ngày và 91 ngày, lãi suất duy trì ở mức 4,5%. Kết quả, có 17.281,85 tỷ đồng trúng thầu. "
    "Trong tuần có 9.093,22 tỷ đồng đáo hạn trên kênh cầm cố.\nNHNN không chào thầu tín phiếu NHNN trong tuần từ 12/10 - 16/10.\n"
    "Như vậy, tuần qua, NHNN bơm ròng 8.188,63 tỷ đồng ra thị trường qua kênh thị trường mở. "
    "Có 104.932,98 tỷ đồng lưu hành trên kênh cầm cố.\n"
    "Thị trường trái phiếu: Lợi suất TPCP chốt tuần qua ít biến động, chốt tuần gần như không thay đổi so với phiên cuối tuần "
    "trước đó ở tất cả các kỳ hạn.\nThị trường chứng khoán: Tuần từ 12/10 - 16/10, thị trường chứng khoán khá tiêu cực."
)


def test_monday_uses_vira_weekly_summary(snap):
    from bttn.sources import parse_vira_sections
    sections = parse_vira_sections(WEEKLY_1216)
    assert "NHNN chào thầu 34.000 tỷ đồng" in sections["lnh"] and "tự do" in sections["fx"]
    at = snap.as_of
    for sid, key in (("vira_daily", "lnh"), ("vira_fx", "fx")):
        snap.sources[sid] = Source(id=sid, url="https://vira.org.vn/tin/Tong-hop-tin-kinh-te-tai-chinh-tuan/Tong-hop-12-10-16-10-2026-.html",
                                   published_at=at - timedelta(hours=3), retrieved_at=at, text=sections[key],
                                   kind="news", text_scope="excerpt")
    result, audit = editorial.domestic_sections(snap, {})
    text = resolve(" ".join(result["interbank"].paragraphs), snap)
    assert text.startswith("Phiên ") and "lãi suất VND liên ngân hàng các kỳ hạn ngắn ON-2W" in text
    assert "NHNN chào thầu 34.000 tỷ đồng" in text and "bơm ròng 8.188,63 tỷ đồng" in text
    assert "Chốt ngày 16/10, lãi suất VND LNH" not in text
    low, high = editorial.rules()["word_limits"]["interbank"]
    assert low <= audit["interbank"]["word_count"] <= high


def _add_bonds(snap, *, liquidity=True):
    at = snap.as_of
    url = "https://vira.org.vn/tin/Ban-tin-Kinh-te-Tai-chinh-ngay/Ban-tin-Kinh-te-Tai-chinh-ngay-16-10-2026-.html"
    snap.sources["vira_bonds"] = Source(id="vira_bonds", url=url, published_at=at - timedelta(days=3), retrieved_at=at,
                                        text="Lợi suất TPCP ... chốt phiên: 7Y 4,34%; 10Y 4,46%", kind="market")
    for key, v, prev in (("BOND_VN_7Y", "4.34", "4.33"), ("BOND_VN_10Y", "4.46", "4.45")):
        snap.observations[key] = Observation(id=key, label=key, value=Decimal(v), unit="%", source_id="vira_bonds",
                                             trading_date=at.date() - timedelta(days=4), basis="test",
                                             prev_value=Decimal(prev))
    if liquidity:
        snap.sources["vbma_bonds"] = Source(
            id="vbma_bonds", url="https://vbma.org.vn/storage/reports/October2026/x.pdf",
            published_at=at - timedelta(days=10), retrieved_at=at, kind="market",
            text="GTGD outright trung bình ngày tuần 05/10 - 09/10/2026: 14475 tỷ đồng (-10.6% so với tuần trước)")


def test_parse_vbma_outright_and_liquidity_label():
    from bttn.bonds import liquidity_label, parse_outright
    text = ("GTGD outright trung bình ngày là 14,475 tỷ đồng (giảm 10.6% so với kỳ trước) và GTGD repo "
            "trung bình ngày là 5,551 tỷ đồng (giảm 33.2% so với kỳ trước).")
    assert parse_outright(text) == (Decimal("14475"), Decimal("-10.6"))
    assert liquidity_label(Decimal("14475")) == "vừa"
    assert liquidity_label(Decimal("8000")) == "thấp" and liquidity_label(Decimal("25000")) == "cao"


def test_bond_sentence_like_sample(snap):
    _add_bonds(snap)
    text = resolve(editorial.bond_sentence(snap)[0].text, snap)
    assert text == "Trên thị trường trái phiếu, lợi tức kỳ hạn 7 & 10 năm đi ngang, quanh mức 4,34%-4,46%, thanh khoản vừa."
    del snap.sources["vbma_bonds"]
    assert "thanh khoản" not in editorial.bond_sentence(snap)[0].text


def test_interbank_ends_with_bond_sentence_within_limits(snap):
    from bttn.sources import parse_vira_sections
    _add_bonds(snap)
    sections = parse_vira_sections(WEEKLY_1216)
    at = snap.as_of
    for sid, key in (("vira_daily", "lnh"), ("vira_fx", "fx")):
        snap.sources[sid] = Source(id=sid, url="https://vira.org.vn/tin/Tong-hop-tin-kinh-te-tai-chinh-tuan/Tong-hop-12-10-16-10-2026-.html",
                                   published_at=at - timedelta(hours=3), retrieved_at=at, text=sections[key],
                                   kind="news", text_scope="excerpt")
    from bttn.sources import vira_weekly_omo
    assert vira_weekly_omo(snap, sections["lnh"], at.date() - timedelta(days=3)) == 6
    result, audit = editorial.domestic_sections(snap, {})
    text = resolve(" ".join(result["interbank"].paragraphs), snap)
    assert text.endswith("quanh mức 4,34%-4,46%, thanh khoản vừa.")
    assert "Tuần qua, NHNN chào thầu 34.000 tỷ đồng với lãi suất duy trì ở mức 4,5%." in text
    assert "NHNN bơm ròng 8.188,63 tỷ đồng." in text and text.count("trái phiếu") == 1
    low, high = editorial.rules()["word_limits"]["interbank"]
    assert low <= audit["interbank"]["word_count"] <= high, text


def test_collect_bond_market_from_vira_and_vbma(monkeypatch, snap):
    from types import SimpleNamespace

    from bttn import bonds

    def page(day, y7, y10):
        return (f'<html><head><meta property="article:published_time" content="2026-09-{day}T08:00:00+07:00"></head>'
                f'<body><div id="abody">Thị trường tiền tệ LNH: Lợi suất TPCP trên thị trường thứ cấp biến động trái chiều, '
                f'chốt phiên: 3Y 3,72%; 5Y 4,29%; 7Y {y7}%; 10Y {y10}%; 15Y 4,57%.</div></body></html>')
    base = "https://vira.org.vn/tin/Ban-tin-Kinh-te-Tai-chinh-ngay/"
    pages = {
        bonds.VIRA_DAILY_LIST: f'<a href="{base}Ban-tin-24.html">a</a><a href="{base}Ban-tin-23.html">b</a>',
        base + "Ban-tin-24.html": page(24, "4,34", "4,46"),
        base + "Ban-tin-23.html": page(23, "4,20", "4,30"),
        bonds.VBMA_LIST: '<a href="/storage/reports/September2026/14092026 - 18092026 BAO CAO TUAN TTTP.pdf">r</a>',
    }

    class FakeHttp:
        def get(self, url, **kw):
            return SimpleNamespace(text=pages.get(url, ""), content=b"%PDF")

    monkeypatch.setattr(bonds, "pdf_text", lambda data: "GTGD outright trung bình ngày là 25,100 tỷ đồng (tăng 12.5% so với kỳ trước)")
    bonds.collect_bond_market(FakeHttp(), snap)
    assert snap.observations["BOND_VN_7Y"].value == Decimal("4.34")
    assert snap.observations["BOND_VN_10Y"].prev_value == Decimal("4.30")
    assert str(snap.observations["BOND_VN_7Y"].trading_date) == "2026-09-23"
    text = resolve(editorial.bond_sentence(snap)[0].text, snap)
    assert text == "Trên thị trường trái phiếu, lợi tức kỳ hạn 7 & 10 năm tăng, quanh mức 4,34%-4,46%, thanh khoản cao."

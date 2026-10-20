"""Góp ý bản tin 20.10: câu vô nghĩa ("Petersburg vào Chủ Nhật..."), câu lặp, câu lạc đề trong mục hàng hóa,
đoạn EU lệch về tin Mỹ; giới hạn từ theo bản đã duyệt để trang 2–3 không nhảy trang."""
from datetime import timedelta
from pathlib import Path

from bttn import editorial
from bttn.models import Snapshot, Source
from bttn.validation import resolve

ROOT = Path(__file__).resolve().parents[1]


def _snap():
    return Snapshot.model_validate_json((ROOT / "tests/fixtures/snapshot.json").read_text(encoding="utf-8"))


def _news(s, sid, title, body, hours=2):
    s.sources[sid] = Source(id=sid, url=f"https://vneconomy.vn/{sid}.htm", published_at=s.as_of - timedelta(hours=hours),
                            retrieved_at=s.as_of, text=title + "\n\n" + body, kind="news", text_scope="article")


def test_st_petersburg_is_not_cut_into_a_fragment():
    text = ("Nga tấn công cơ sở năng lượng gần St. Petersburg vào Chủ Nhật, làm gia tăng bất ổn địa chính trị. "
            "Đồng euro giảm nhẹ.")
    assert editorial.sentences(text)[0].startswith("Nga tấn công") and len(editorial.sentences(text)) == 2


def test_foreign_context_dependent_and_pronoun_sentences_rejected():
    for bad in ("Những phát biểu của ông đã giúp củng cố kỳ vọng của thị trường rằng Fed sẽ giữ nguyên lãi suất.",
                "Bà nhấn mạnh sự cần thiết phải cân bằng giữa tăng trưởng và tài khóa.",
                "Điều này khiến đồng euro suy yếu trong phiên."):
        assert editorial.FOREIGN_CONTEXT.search(bad) or editorial.DEMONSTRATIVE_SUBJECT.search(bad), bad
    ok = "Thống đốc BOJ Kazuo Ueda cho biết lạm phát cơ bản đã gần mục tiêu 2%."
    assert not editorial.FOREIGN_CONTEXT.search(ok)


def test_near_duplicates_dropped():
    a = ("Những người tham gia thị trường cũng đang chờ đợi chỉ số giá Chi tiêu Tiêu dùng Cá nhân mới nhất, "
         "thước đo lạm phát được Fed ưa chuộng.")
    b = ("Những người tham gia thị trường cũng đang chờ đợi báo cáo chỉ số giá PCE mới nhất, "
         "thước đo lạm phát ưa thích của Fed.")
    assert editorial.near_duplicate(a, b)


def test_coffee_drops_trade_sentences_and_photo_captions():
    s = _snap()
    _news(s, "news_cf", "Giá cà phê hôm nay",
          "Giá cà phê arabica tăng mạnh do tồn kho ICE giảm xuống mức thấp nhất 20 năm. "
          "Trong đó xuất khẩu 29,25 tỷ USD, nhập khẩu 27,43 tỷ USD. "
          "Giá cà phê robusta trên sàn London trong phiên giao dịch ngày 19/10.")
    cands, rejected = editorial.news_candidates(s, {}, "coffee")
    texts = [c.text for c in cands if c.source_id == "news_cf"]
    assert texts == ["Giá cà phê arabica tăng mạnh do tồn kho ICE giảm xuống mức thấp nhất 20 năm."]


def test_gold_ignores_digital_gold_and_oil_keeps_one_price_quote():
    s = _snap()
    _news(s, "news_oil1", "Giá dầu hôm nay",
          "Tại thời điểm 7h15 (giờ Việt Nam), giá dầu Brent giảm 0,27% xuống 105,78 USD/thùng. "
          "Giá dầu giảm khi OPEC+ cân nhắc tăng sản lượng trong tháng tới để bù đắp thiếu hụt nguồn cung. "
          "Shabwa là một tỉnh sản xuất năng lượng của Yemen.", hours=1)
    _news(s, "news_oil2", "Giá dầu tuần qua",
          "Tại thời điểm 7h15 (giờ Việt Nam), giá dầu Brent giảm 1,64% xuống 108,06 USD/thùng và WTI giảm.",
          hours=20)
    _news(s, "news_gold", "Vàng số",
          "Thị trường vàng kỹ thuật số tăng gấp ba lần, đạt tới 55 tỷ USD trong năm nay.")
    oil, _ = editorial.news_candidates(s, {}, "Brent")
    texts = [c.text for c in oil]
    assert not any(t.startswith("Shabwa") for t in texts)
    quotes = [t for t in texts if editorial.OIL_QUOTE.search(t)]
    assert 1 <= len(quotes) <= 2     # câu gần trùng đã bị loại; còn lại lọc ở bước dựng đoạn
    gold, _ = editorial.news_candidates(s, {}, "gold")
    assert not any("kỹ thuật số" in c.text for c in gold)
    result, audit = editorial.domestic_sections(s, {})
    brent = resolve(result["energy_metals"].paragraphs[0], s)
    assert brent.count("Tại thời điểm 7h15") <= 1


def test_us_paragraph_lowercases_common_first_word():
    assert editorial.lower_initial("Thị trường hiện đặt 83% khả năng Fed giữ nguyên lãi suất.").startswith("thị trường")
    assert editorial.lower_initial("Fed giữ nguyên lãi suất.").startswith("Fed")


def test_word_limits_follow_approved_2010_bulletin():
    limits = editorial.rules()["word_limits"]
    assert limits["eur_usd"] == [185, 195] and limits["coffee"] == [105, 115]
    assert limits["japan"] == [110, 130] and limits["china"] == [50, 62]
    assert limits["energy_metals"] == [105, 125]
    assert editorial.rules()["eur_usd_parts"] == {"europe": [115, 125], "us": [28, 40]}


def test_highlights_prefer_domestic_news():
    s = _snap()
    _news(s, "macro_news_vn", "Giải ngân đầu tư công",
          "Đến ngày 15/10, Quảng Ngãi có nhiều chủ đầu tư đã giải ngân 100% kế hoạch vốn, nhưng vẫn còn 23 đơn vị "
          "dưới mức bình quân cả nước về giải ngân vốn đầu tư công.")
    _news(s, "macro_news_us", "Lệnh trừng phạt",
          "Hôm thứ Sáu, ông Trump đã ký một sắc lệnh hành pháp miễn áp dụng các lệnh trừng phạt và thuế quan "
          "chủ chốt theo đạo luật được gọi là Đạo luật Graham về tăng trưởng.")
    cands, _ = editorial.news_candidates(s, {}, "macro")
    order = [c.source_id for c in cands]
    assert "macro_news_vn" in order
    assert "macro_news_us" not in order or order.index("macro_news_vn") < order.index("macro_news_us")

from datetime import datetime, timezone
from decimal import Decimal

from bttn.forecasts import (
    build_asia_forecast,
    build_coffee_forecast,
    build_energy_metals_forecast,
    build_eurusd_forecast,
    build_interbank_forecast,
    build_usdvnd_forecast,
)
from bttn.models import Observation, ReportContent, Section, Snapshot


def make_dummy_snapshot():
    now = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)
    s = Snapshot(as_of=now)
    s.observations["MB_BUY"] = Observation(
        id="MB_BUY", label="MB Mua", value=Decimal("25730.0"), unit="VND",
        source_id="mbbank", trading_date=now.date(), basis="Niêm yết"
    )
    s.observations["MB_SELL"] = Observation(
        id="MB_SELL", label="MB Bán", value=Decimal("26080.0"), unit="VND",
        source_id="mbbank", trading_date=now.date(), basis="Niêm yết"
    )
    s.observations["EURUSD"] = Observation(
        id="EURUSD", label="EUR/USD", value=Decimal("1.1184"), unit="USD",
        source_id="fx", trading_date=now.date(), basis="Spot"
    )
    s.observations["USDJPY"] = Observation(
        id="USDJPY", label="USD/JPY", value=Decimal("158.15"), unit="JPY",
        source_id="fx", trading_date=now.date(), basis="Spot"
    )
    s.observations["USDCNY"] = Observation(
        id="USDCNY", label="USD/CNH", value=Decimal("6.7120"), unit="CNY",
        source_id="fx", trading_date=now.date(), basis="Spot"
    )
    s.observations["BRENT"] = Observation(
        id="BRENT", label="Dầu Brent", value=Decimal("104.0"), unit="USD/thùng",
        source_id="com", trading_date=now.date(), basis="Futures"
    )
    s.observations["GOLD"] = Observation(
        id="GOLD", label="Vàng", value=Decimal("4175.0"), unit="USD/oz",
        source_id="com", trading_date=now.date(), basis="Futures"
    )
    s.observations["ARABICA"] = Observation(
        id="ARABICA", label="Arabica", value=Decimal("295.2"), unit="USc/lbs",
        source_id="com", trading_date=now.date(), basis="Futures"
    )
    s.observations["ROBUSTA"] = Observation(
        id="ROBUSTA", label="Robusta", value=Decimal("3470.0"), unit="USD/T",
        source_id="com", trading_date=now.date(), basis="Futures"
    )
    return s


def test_usdvnd_forecast_words_and_formula():
    s = make_dummy_snapshot()
    text = build_usdvnd_forecast(s)
    # Buy 25730 - 30 = 25700; Sell 26080 + 70 = 26150
    assert text == "Dự kiến: tỷ giá USD-VND có thể diễn biến dao động quanh ngưỡng 25.700-26.150."
    # Must be exactly 14 words
    assert len(text.split()) == 14


def test_eurusd_forecast_range_and_sentiment():
    s = make_dummy_snapshot()
    # Neutral
    text_neutral = build_eurusd_forecast(s)
    assert "Dự kiến: Tỷ giá EUR-USD có thể dao động đi ngang giá trong biên độ vừa quanh khu vực" in text_neutral
    assert "1,1124-1,1244" in text_neutral  # 1.1184 +- 0.0060

    # Bullish content
    content = ReportContent(
        highlights=[
            Section(paragraphs=["Tin vĩ mô 1."], source_ids=["s1"]),
            Section(paragraphs=["Tin vĩ mô 2."], source_ids=["s1"]),
            Section(paragraphs=["Tin giá vàng."], source_ids=["s1"]),
        ],
        interbank=Section(paragraphs=["Interbank text."], source_ids=["s1"]),
        usd_vnd=Section(paragraphs=["USD-VND text."], source_ids=["s1"]),
        eur_usd=Section(paragraphs=["Đồng EUR tăng trưởng mạnh mẽ và bứt phá."], source_ids=["s1"]),
        japan=Section(paragraphs=["Japan text."], source_ids=["s1"]),
        china=Section(paragraphs=["China text."], source_ids=["s1"]),
        coffee=Section(paragraphs=["Coffee text."], source_ids=["s1"]),
        energy_metals=Section(paragraphs=["Metals text."], source_ids=["s1"]),
    )
    text_bull = build_eurusd_forecast(s, content)
    # Spot 1.1184: low = 1.1144, high = 1.1264
    assert "1,1144-1,1264" in text_bull

    # Bearish content
    content.eur_usd.paragraphs = ["Đồng EUR giảm mạnh do suy yếu và chịu áp lực tiêu cực."]
    text_bear = build_eurusd_forecast(s, content)
    # Spot 1.1184: low = 1.1104, high = 1.1224
    assert "1,1104-1,1224" in text_bear


def test_asia_forecast_formula():
    s = make_dummy_snapshot()
    text = build_asia_forecast(s)
    # JPY: 158.15 + 0.40 = 158.55; CNH: 6.7120 + 0.0030 = 6.7150
    assert text == "Dự kiến: Tỷ giá USD-JPY dao động quanh 158,55; tỷ giá USD-CNH có thể đi ngang quanh 6,7150."


def test_energy_metals_forecast_formula():
    s = make_dummy_snapshot()
    text = build_energy_metals_forecast(s)
    # Brent: 104 +- 5 -> $99-$109; Gold: 4175 +- 100 -> $4.075-$4.275
    assert text == "Dự kiến: Giá dầu Brent dao động từ $99-$109/thùng. Giá vàng biến động từ $4.075-$4.275/ounce."


def test_coffee_forecast_formula():
    s = make_dummy_snapshot()
    text = build_coffee_forecast(s)
    assert text == "Dự kiến: Giá Arabica giao dịch quanh 295,2 USc/lbs, giá Robusta dao động quanh ngưỡng 3.470 USD/T."


def test_interbank_forecast():
    s = make_dummy_snapshot()
    # Case 1: VND_ON observation is 0.8% -> closest to 1.5%
    s.observations["VND_ON"] = Observation(
        id="VND_ON", label="VND ON", value=Decimal("0.80"), unit="%",
        source_id="vira", trading_date=s.as_of.date(), basis="Niêm yết"
    )
    text = build_interbank_forecast(s)
    assert text == "Dự kiến: lãi suất ON nhiều khả năng đi ngang quanh 1,5%, lãi suất trái phiếu đi ngang."

    # Case 2: From content paragraph containing 0,60% -> closest to 1.5%
    content = ReportContent(
        highlights=[
            Section(paragraphs=["Tin 1"], source_ids=["s1"]),
            Section(paragraphs=["Tin 2"], source_ids=["s1"]),
            Section(paragraphs=["Tin 3"], source_ids=["s1"]),
        ],
        interbank=Section(
            paragraphs=["Lãi suất VND kỳ hạn qua đêm được ghi nhận ở mức 0,60%/năm. Lãi suất kỳ hạn 1 tuần là 2,30%."],
            source_ids=["s1"]
        ),
        usd_vnd=Section(paragraphs=["USD text"], source_ids=["s1"]),
        eur_usd=Section(paragraphs=["EUR text"], source_ids=["s1"]),
        japan=Section(paragraphs=["Japan text"], source_ids=["s1"]),
        china=Section(paragraphs=["China text"], source_ids=["s1"]),
        coffee=Section(paragraphs=["Coffee text"], source_ids=["s1"]),
        energy_metals=Section(paragraphs=["Metals text"], source_ids=["s1"]),
    )
    text_content = build_interbank_forecast(s, content)
    assert text_content == "Dự kiến: lãi suất ON nhiều khả năng đi ngang quanh 1,5%, lãi suất trái phiếu đi ngang."

    # Case 3: Rate is 4.0% -> randomly 3.5% or 4.5%
    s.observations["VND_ON"].value = Decimal("4.00")
    results = {build_interbank_forecast(s) for _ in range(30)}
    expected_35 = "Dự kiến: lãi suất ON nhiều khả năng đi ngang quanh 3,5%, lãi suất trái phiếu đi ngang."
    expected_45 = "Dự kiến: lãi suất ON nhiều khả năng đi ngang quanh 4,5%, lãi suất trái phiếu đi ngang."
    assert results.issubset({expected_35, expected_45})
    # Both 3.5% and 4.5% should be valid outputs
    assert len(results) >= 1


"""Vị trí các ô nội dung trong file mẫu bản tin tuần (BT_TTT, bản VIE và ENG cùng bố cục).

Bảng chính 16 hàng. Như mẫu: trang 1 là hàng 0–5; trang 2 hàng 6–11 (mở đầu bằng dải xanh,
rồi ba bảng nhỏ và các biểu đồ giá); trang 3 hàng 12–15 (dải xanh, lịch sự kiện, miễn trừ, liên hệ).
Toạ độ là (hàng, ô) theo thứ tự ô trong hàng (đã gộp cột).
"""
from datetime import date, timedelta

ROWS = 16
PAGES = [range(0, 6), range(6, 12), range(12, 16)]

WEEK_LABEL = (2, 1)

# Ô có tiêu đề + đoạn nội dung (màu 0070C0) + đoạn dự báo (đậm, màu C00000)
SECTIONS = {
    "interbank": (3, 0),
    "fx": (4, 0),
    "bonds": (5, 0),
    "intl_fx": (9, 0),
    "coffee": (10, 0),
    "energy": (11, 0),
}

NEWS = (3, 2)            # "Tin tức nổi bật:" / "Breaking news:" + các gạch đầu dòng
INDICATORS = (4, 2)      # bảng "Các chỉ số cơ bản" 7 x 4

# Biểu đồ Word gốc (dữ liệu nằm trong chart, không phải ảnh)
CHARTS = {
    "interbank": (3, 1),  # ON / 1W / 2W
    "usdvnd": (4, 1),     # Ask / Midpoint / Ask-Bid
    "bonds": (5, 1),      # 1y / 2y / 3y / 5y / 7y
    "credit": (5, 2),     # tăng trưởng huy động / tín dụng
}

# Chiều cao tối đa (cm) của 4 biểu đồ Word ở trang 1. Mẫu để 5,1–5,4 cm làm trang 1 tràn khi mở
# bằng Word. Biểu đồ trái phiếu cao hơn một chút để dòng ngày tháng (nhãn xoay) không bị che;
# hàng trái phiếu vẫn do cột chữ quyết định chiều cao nên không làm trang 1 dài thêm.
PAGE1_CHART_MAX_CM = {"interbank": 4.6, "usdvnd": 4.6, "bonds": 5.2, "credit": 4.6}

# Chiều cao tối đa (cm) của khung ảnh biểu đồ giá ở trang 2 (mẫu: 5,75 / 5,36 / 6,03 cm).
# Giữ nguyên bề rộng cột; ảnh biểu đồ do code vẽ sẽ được vẽ đúng tỷ lệ khung này.
PAGE2_IMAGE_MAX_CM = {"eurusd": 5.3, "usdjpy": 5.3, "arabica": 5.0, "robusta": 5.0, "brent": 5.5, "gasoline": 5.5}

# Ba bảng nhỏ đầu trang 2: hàng 1 = giá trị, hàng 2 = thay đổi
SMALL_TABLES = {
    "sofr": (7, 0),
    "policy_rates": (7, 1),
    "commodities": (7, 2),
}

# Ảnh biểu đồ giá (giữ nguyên khung ảnh của mẫu, chỉ thay nội dung ảnh)
IMAGES = {
    "eurusd": (9, 1),
    "usdjpy": (9, 2),
    "arabica": (10, 1),
    "robusta": (10, 2),
    "brent": (11, 1),
    "gasoline": (11, 2),
}

CALENDAR = (13, 0)

LANG_SUFFIX = {"vi": "VIE", "en": "ENG"}


def week_bounds(friday: date) -> tuple[date, date]:
    monday = friday - timedelta(days=friday.weekday())
    return monday, monday + timedelta(days=4)


def weekly_filename(friday: date, lang: str) -> str:
    """Tên file như mẫu: BT_TTT2026.12.10-2026.16.10_VIE.docx (năm.ngày.tháng)."""
    monday, fri = week_bounds(friday)
    return (f"BT_TTT{monday:%Y.%d.%m}-{fri:%Y.%d.%m}_{LANG_SUFFIX[lang]}.docx")


def week_label(friday: date, lang: str) -> str:
    monday, fri = week_bounds(friday)
    if lang == "vi":
        return f"Tuần từ {monday:%d.%m} – {fri:%d.%m.%Y}"
    return f"Week {monday.isocalendar().week}: {monday:%d.%m} - {fri:%d.%m.%Y}"


def calendar_title(next_monday: date, lang: str) -> str:
    fri = next_monday + timedelta(days=4)
    if lang == "vi":
        return f"Lịch sự kiện quan trọng (Tuần từ {next_monday:%d.%m} – {fri:%d.%m.%Y})"
    return f"Economic calendar (Week {next_monday.isocalendar().week}: {next_monday:%d.%m.%Y} – {fri:%d.%m.%Y})"

"""Câu dự báo của bản tin tuần: giữ nguyên câu mẫu, chỉ thay số (như file mẫu 12–16.10.2026)."""

TEMPLATES = {
    "vi": {
        "interbank": "Dự kiến: tuần tới, lãi suất ON-2W nhiều khả năng dao động đi ngang quanh mức {lo}%-{hi}%.",
        "fx": "Dự kiến: tỷ giá USD-VND liên ngân hàng khả năng sẽ biến động quanh khu vực {lo} – {hi}.",
        "bonds": "Dự báo: Lợi tức TPCP khả năng dao động quanh {lo}% - {hi}% kỳ hạn 10Y.",
        "intl_fx": "Dự báo: Trong tuần tới, EUR-USD dao động quanh {eurusd}; USD-JPY có thể giao dịch quanh {usdjpy}.",
        "coffee": "Dự báo: Arabica có thể giao dịch quanh {arabica} US cent/lbs; Robusta giao dịch gần mức {robusta} USD/T.",
        "energy": "Dự báo: Giá dầu Brent dao động quanh mức {brent} USD/thùng, giá xăng đi ngang vùng {gasoline} USD/Gal.",
    },
    "en": {
        "interbank": "Forecast: ON-2W interest rates are highly likely to fluctuate to around {lo}%-{hi}%.",
        "fx": "Forecast: Exchange rate is projected to fluctuate within the region of {lo} – {hi}.",
        "bonds": "Forecast: Bond yield would fluctuate around {lo} – {hi}% (10-year government bond).",
        "intl_fx": "Forecast: EUR-USD could trade around {eurusd}, USD-JPY could trade around the {usdjpy} area.",
        "coffee": "Forecasts: Arabica might trade around {arabica} USc/lbs, and Robusta is traded near ${robusta}/ton.",
        "energy": "Forecast: Brent Crude Oil might trade around {brent} USD/Bbl, Gasoline around the {gasoline} (USD/Gal) area.",
    },
}

# Số chữ số thập phân của từng con số trong câu dự báo (theo file mẫu)
DECIMALS = {
    "interbank": {"lo": 1, "hi": 1},
    "fx": {"lo": 0, "hi": 0},
    "bonds": {"lo": 2, "hi": 2},
    "intl_fx": {"eurusd": 4, "usdjpy": 2},
    "coffee": {"arabica": 2, "robusta": 0},
    "energy": {"brent": 1, "gasoline": 2},
}


def fmt(value: float, decimals: int, lang: str) -> str:
    """vi: 25.800 / 3,0 ; en: 25,800 / 3.0"""
    text = f"{value:,.{decimals}f}"
    if lang == "vi":
        text = text.translate(str.maketrans({",": ".", ".": ","}))
    return text


def forecast_sentence(section: str, lang: str, **values: float) -> str:
    spec = DECIMALS[section]
    missing = set(spec) - set(values)
    if missing:
        raise ValueError(f"Thiếu số dự báo {sorted(missing)} cho mục {section}")
    return TEMPLATES[lang][section].format(**{k: fmt(values[k], spec[k], lang) for k in spec})

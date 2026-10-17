"""Giới hạn từ/dòng và câu dự báo mẫu của bản tin tuần."""
from pathlib import Path

import pytest

from bttn.weekly.extract import extract_weekly
from bttn.weekly.fit import check_content, count_lines, rules, trim_to_lines
from bttn.weekly.forecasts import forecast_sentence

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = {"vi": ROOT / "templates/weekly/BT_TTT_template_VIE.docx",
             "en": ROOT / "templates/weekly/BT_TTT_template_ENG.docx"}
# Số dòng thực tế của từng đoạn trong file mẫu (đếm trên bản in)
SAMPLE_LINES = {
    "vi": {"interbank": 9, "fx": 9, "bonds": 10, "intl_fx": 11, "coffee": 10, "energy": 11},
    "en": {"interbank": 9, "fx": 10, "bonds": 9, "intl_fx": 10, "coffee": 10, "energy": 11},
}


@pytest.mark.parametrize("lang", ["vi", "en"])
def test_line_counter_matches_sample_layout(lang):
    content = extract_weekly(TEMPLATES[lang], lang)
    for name, section in content.sections.items():
        assert count_lines(section.body[0]) == SAMPLE_LINES[lang][name], name
        assert count_lines(section.forecast, bold=True) == 2, name
        # Giới hạn dòng trong cấu hình đúng bằng số dòng của mẫu (mẫu vừa khít 3 trang)
        assert rules()["sections"][lang][name]["max_lines"] == SAMPLE_LINES[lang][name]


def test_sample_never_breaks_line_limits():
    for lang in ("vi", "en"):
        issues = check_content(extract_weekly(TEMPLATES[lang], lang))
        assert not [i for i in issues if i["code"] == "TOO_MANY_LINES"]


def test_overlong_paragraph_is_flagged_and_trimmed():
    content = extract_weekly(TEMPLATES["vi"], "vi")
    body = content.sections["interbank"].body[0]
    content.sections["interbank"].body = [body + " Câu thêm vào để vượt số dòng cho phép của đoạn này."]
    codes = {(i["code"], i["where"]) for i in check_content(content)}
    assert ("TOO_MANY_LINES", "interbank") in codes
    assert count_lines(trim_to_lines(content.sections["interbank"].body[0], 9)) <= 9


def test_forecast_sentences_reproduce_sample_wording():
    vi = extract_weekly(TEMPLATES["vi"], "vi").sections
    assert forecast_sentence("interbank", "vi", lo=3.0, hi=5.5) == vi["interbank"].forecast
    assert forecast_sentence("fx", "vi", lo=25800, hi=25950) == vi["fx"].forecast
    assert forecast_sentence("bonds", "vi", lo=4.55, hi=4.65) == vi["bonds"].forecast
    assert forecast_sentence("intl_fx", "vi", eurusd=1.1291, usdjpy=157.65) == vi["intl_fx"].forecast
    assert forecast_sentence("coffee", "vi", arabica=280.45, robusta=3350) == vi["coffee"].forecast
    assert forecast_sentence("energy", "vi", brent=102.8, gasoline=3.11) == vi["energy"].forecast
    en = extract_weekly(TEMPLATES["en"], "en").sections
    assert forecast_sentence("fx", "en", lo=25800, hi=25950) == en["fx"].forecast
    assert forecast_sentence("coffee", "en", arabica=280.45, robusta=3350) == en["coffee"].forecast
    assert forecast_sentence("energy", "en", brent=102.8, gasoline=3.11) == en["energy"].forecast
    with pytest.raises(ValueError):
        forecast_sentence("fx", "vi", lo=25800)

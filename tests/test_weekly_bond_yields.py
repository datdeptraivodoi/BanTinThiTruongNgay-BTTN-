"""Lợi suất TPCP: ghép VBMA (thứ Sáu) với VIRA (thứ Hai–thứ Năm) và câu trong đoạn trái phiếu như mẫu.

Số liệu thật tháng 10/2026 (VIRA bản tin ngày, VBMA báo cáo tuần) được dùng làm dữ liệu kiểm thử.
"""
from datetime import date

from bttn.weekly import bond_yields as by
from bttn.weekly.history import bonds_chart
from bttn.weekly.write import bond_sentence_vi

VBMA = {date(2026, 10, 2): {"1N": 3.53, "2N": 3.64, "3N": 3.71, "5N": 4.29, "7N": 4.34, "10N": 4.47, "15N": 4.58},
        date(2026, 10, 9): {"1N": 3.54, "2N": 3.64, "3N": 3.72, "5N": 4.29, "7N": 4.34, "10N": 4.46, "15N": 4.58}}
VIRA_TEXT = {  # ngày đăng: câu trong "Bản tin Kinh tế - Tài chính ngày"
    date(2026, 10, 16): "Lợi suất TPCP trên thị trường thứ cấp biến động trái chiều, chốt phiên: 3Y 3,72%; 5Y 4,29%; 7Y 4,34%; 10Y 4,46%; 15Y 4,57%.",
    date(2026, 10, 15): "Lợi suất TPCP trên thị trường thứ cấp biến động trái chiều, chốt phiên ở mức: 3Y 3,72%; 5Y 4,28%; 7Y 4,34%; 10Y 4,47%; 15Y 4,57%.",
    date(2026, 10, 14): "Lợi suất TPCP trên thị trường thứ cấp biến động trái chiều, chốt phiên ở mức: 3Y 3,72%; 5Y 4,29%; 7Y 4,34%; 10Y 4,47%; 15Y 4,57%.",
    date(2026, 10, 13): "Lợi suất TPCP trên thị trường thứ cấp biến động trái chiều, chốt phiên ở mức: 3Y 3,72%; 5Y 4,28%; 7Y 4,34%; 10Y 4,46%; 15Y 4,57%.",
    date(2026, 10, 9): ("Lợi suất TPCP trên thị trường thứ cấp không thay đổi ở các kỳ hạn 3Y và 7Y trong khi giảm ở các kỳ hạn còn lại, "
                        "chốt phiên ở mức: 3Y 3,72%; 5Y 4,28%; 7Y 4,34%; 10Y 4,45%; 15Y 4,57%."),
}


def test_parse_vira_sentence_variants():
    assert by.parse_vira_yields(VIRA_TEXT[date(2026, 10, 16)]) == {"3N": 3.72, "5N": 4.29, "7N": 4.34, "10N": 4.46, "15N": 4.57}
    assert by.parse_vira_yields(VIRA_TEXT[date(2026, 10, 9)])["10N"] == 4.45
    assert by.parse_vira_yields("Thị trường trái phiếu: KBNN gọi thầu 18.000 tỷ đồng.") == {}


def test_bulletin_reports_previous_session():
    assert by.previous_workday(date(2026, 10, 13)) == date(2026, 10, 12)   # thứ Ba -> phiên thứ Hai
    assert by.previous_workday(date(2026, 10, 12)) == date(2026, 10, 9)    # thứ Hai -> phiên thứ Sáu


def vira_rows():
    return {by.previous_workday(d): by.parse_vira_yields(t) for d, t in VIRA_TEXT.items()}


def test_vira_agrees_with_vbma_within_one_bp():
    # Phiên 8/10 (VIRA) và 9/10 (VBMA) chênh tối đa 1 điểm cơ bản ở mọi kỳ hạn chung
    v, b = vira_rows()[date(2026, 10, 8)], VBMA[date(2026, 10, 9)]
    assert max(abs(v[t] - b[t]) for t in v) <= 0.0100001


def test_combine_prefers_vbma_and_estimates_short_end():
    rows, notes = by.combine(VBMA, vira_rows())
    assert rows[date(2026, 10, 9)] == VBMA[date(2026, 10, 9)]                      # ngày có VBMA dùng VBMA
    thu = rows[date(2026, 10, 15)]
    assert thu["3N"] == 3.72 and thu["10N"] == 4.46                                # phiên VIRA mới nhất
    assert thu["1N"] == 3.54 and thu["2N"] == 3.64                                 # 1Y/2Y: VBMA 9/10 + thay đổi 3Y (0)
    assert notes == []


def test_combine_flags_big_gap():
    bad = {date(2026, 10, 9): {"3N": 3.90, "5N": 4.29}}
    _, notes = by.combine(VBMA, bad)
    assert "lệch VBMA 18" in notes[0]


def test_week_view_latest_session_vs_last_week():
    rows, _ = by.combine(VBMA, vira_rows())
    last, row, prev = by.week_view(rows, date(2026, 10, 12), date(2026, 10, 16))
    assert last == date(2026, 10, 15) and row["5N"] == 4.29 and prev == VBMA[date(2026, 10, 9)]


def test_bond_sentence_matches_template_style():
    same = {"3N": 3.85, "5N": 4.38, "7N": 4.46, "10N": 4.55, "15N": 4.72}
    assert bond_sentence_vi(same, same) == ("Lợi suất TPCP trên thị trường thứ cấp duy trì không đổi ở các kỳ hạn, chốt phiên: "
                                            "3Y 3,85%; 5Y 4,38%; 7Y 4,46%; 10Y 4,55%; 15Y 4,72% không đổi so với tuần trước đó.")
    rows, _ = by.combine(VBMA, vira_rows())
    s = bond_sentence_vi(rows[date(2026, 10, 15)], VBMA[date(2026, 10, 9)])
    # Tuần 12–16/10/2026: phiên 15/10 so với 9/10, chỉ kỳ hạn 15 năm giảm 1 điểm cơ bản
    assert s == ("Lợi suất TPCP trên thị trường thứ cấp đi ngang ở hầu hết các kỳ hạn so với tuần trước, riêng kỳ hạn 15Y "
                 "giảm nhẹ, chốt phiên: 3Y 3,72%; 5Y 4,29%; 7Y 4,34%; 10Y 4,46%; 15Y 4,57%.")
    up = {k: v + 0.03 for k, v in same.items()}
    up["3N"], up["5N"] = same["3N"], same["5N"]
    assert "đi ngang ở kỳ hạn 3Y, 5Y trong khi tăng nhẹ" in bond_sentence_vi(up, same)
    mixed = dict(same, **{"3N": 3.80, "10N": 4.60})
    assert "biến động trái chiều" in bond_sentence_vi(mixed, same)
    assert "giảm ở các kỳ hạn" in bond_sentence_vi({k: v - 0.10 for k, v in same.items()}, same)
    assert "VBMA" not in s and "VIRA" not in s


def test_chart_uses_vira_sessions_after_last_vbma_friday():
    rows, _ = by.combine(VBMA, vira_rows())
    chart = bonds_chart(rows, date(2026, 10, 16))
    i = chart.categories.index("13/10/2026")
    assert [s[i] for s in chart.series] == [3.54, 3.64, 3.72, 4.29, 4.34]
    assert [s[-1] for s in chart.series] == [s[chart.categories.index("15/10/2026")] for s in chart.series]  # thứ Sáu giữ số thứ Năm


LIST = """<div class="story__title"><a href="/tin/Ban-tin-Kinh-te-Tai-chinh-ngay/Ban-tin-Kinh-te-Tai-chinh-ngay-16-10-2026-.html">a</a></div>
<div class="story__title"><a href="/tin/Ban-tin-Kinh-te-Tai-chinh-ngay/Ban-tin-Kinh-te-Tai-chinh-ngay-15-10-2026-.html">b</a></div>
<div class="story__title"><a href="/tin/Ban-tin-Kinh-te-Tai-chinh-ngay/Ban-tin-Kinh-te-Tai-chinh-ngay-02-10-2026-.html">c</a></div>"""


def article(d):
    return (f'<html><head><meta property="article:published_time" content="{d.isoformat()}T08:10:00+07:00"></head>'
            f'<body><div id="abody"><p>Thị trường trái phiếu:</p><p>{VIRA_TEXT.get(d, "")}</p></div></body></html>')


def test_fetch_vira_week():
    def fetch(url, http=None):
        if url == by.VIRA_LIST:
            return LIST.encode()
        day = int(url.split("-ngay-")[1][:2])
        return article(date(2026, 10, day)).encode()

    rows, notes = by.fetch_vira(date(2026, 10, 12), date(2026, 10, 16), fetch=fetch)
    assert sorted(rows) == [date(2026, 10, 14), date(2026, 10, 15)] and not notes

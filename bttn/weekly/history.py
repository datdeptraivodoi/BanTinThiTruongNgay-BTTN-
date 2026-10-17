"""Lịch sử số liệu cho 4 biểu đồ Word của bản tin tuần, lưu trên VPS (data/weekly/chart_history.json).

Lần đầu lấy đúng số liệu đang có trong biểu đồ của file mẫu (đến 16/10/2026), sau đó mỗi tuần
thêm các phiên mới: lãi suất LNH ON/1W/2W (VIRA), USD-VND (quote ITB và tỷ giá trung tâm),
đường cong lợi suất và tín dụng – huy động (team nhập trong config/weekly_inputs.json).
"""
from __future__ import annotations

import json
from pathlib import Path

from .content import ChartData
from .facts import WeeklyFacts

ROOT = Path(__file__).resolve().parents[2]
HISTORY = ROOT / "data" / "weekly" / "chart_history.json"
KEEP = {"interbank": 22, "usdvnd": 22, "bonds": 243, "credit": 13}


def load(path: Path = HISTORY, seed: dict[str, ChartData] | None = None) -> dict[str, ChartData]:
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: ChartData.model_validate(v) for k, v in raw.items()}
    return {k: v.model_copy(deep=True) for k, v in (seed or {}).items()}


def save(charts: dict[str, ChartData], path: Path = HISTORY):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v.model_dump() for k, v in charts.items()}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def upsert(chart: ChartData, label: str, values: list[float | None], keep: int):
    if label in chart.categories:
        i = chart.categories.index(label)
        for s, v in zip(chart.series, values):
            if v is not None:
                s[i] = v
    else:
        chart.categories.append(label)
        for s, v in zip(chart.series, values):
            s.append(v)
    if len(chart.categories) > keep:
        cut = len(chart.categories) - keep
        chart.categories = chart.categories[cut:]
        chart.series = [s[cut:] for s in chart.series]


def update(charts: dict[str, ChartData], facts: WeeklyFacts) -> dict[str, ChartData]:
    for d in facts.days:
        label = d.day.strftime("%d/%m/%Y")
        if "interbank" in charts and d.vnd:
            upsert(charts["interbank"], label, [d.vnd.get("ON"), d.vnd.get("1W"), d.vnd.get("2W")], KEEP["interbank"])
        if "usdvnd" in charts and (d.itb_ask or d.sbv_central):
            spread = d.itb_ask - d.itb_bid if d.itb_ask and d.itb_bid else None
            upsert(charts["usdvnd"], label, [d.itb_ask, d.sbv_central, spread], KEEP["usdvnd"])
    if "bonds" in charts and facts.vbma:
        update_bonds_vbma(charts["bonds"], facts.vbma, facts.friday)
    for label, values in sorted(((facts.inputs.get("bond_curve") or {}).get("rows") or {}).items(),
                                key=lambda kv: tuple(reversed([int(x) for x in kv[0].split("/")]))):
        if "bonds" in charts:
            upsert(charts["bonds"], label, [None if v is None else float(v) for v in values], KEEP["bonds"])
    for label, values in ((facts.inputs.get("credit_deposit") or {}).get("rows") or {}).items():
        if "credit" in charts:
            upsert(charts["credit"], label, [None if v is None else float(v) for v in values], KEEP["credit"])
    return charts


def update_bonds_vbma(chart: ChartData, yields: dict, friday) -> ChartData:
    """Nối lợi suất 1/2/3/5/7 năm theo VBMA vào biểu đồ ngày.

    Mỗi ngày làm việc từ sau phiên cuối đã có đến thứ Sáu kỳ bản tin lấy số VBMA mới nhất tính tới ngày
    đó (lợi suất TPCP ít biến động; VBMA chỉ công bố số thứ Sáu). Phiên đã có số VBMA thì cập nhật đúng số.
    """
    from datetime import timedelta

    from .vbma import CHART_TENORS, chart_label, parse_label

    known = sorted(yields)
    if not known:
        return chart
    dates = [d for d in (parse_label(c) for c in chart.categories) if d]
    day = (max(dates) + timedelta(days=1)) if dates else known[0]
    for d in known:                       # sửa đúng phiên thứ Sáu đã có trong biểu đồ
        if chart_label(d) in chart.categories:
            upsert(chart, chart_label(d), [yields[d].get(t) for t in CHART_TENORS], KEEP["bonds"])
    while day <= friday:
        base = [d for d in known if d <= day]
        if day.weekday() < 5 and base:
            row = yields[base[-1]]
            upsert(chart, chart_label(day), [row.get(t) for t in CHART_TENORS], KEEP["bonds"])
        day += timedelta(days=1)
    return chart


CENTRALS = ROOT / "data" / "weekly" / "central_rates.json"
# Tỷ giá trung tâm phiên thứ Sáu đã có trong file mẫu (để tuần đầu chạy có đủ hai cột lùi tuần).
CENTRAL_SEED = {"2026-10-02": 25629.0, "2026-10-09": 25637.0}


def friday_centrals(facts: WeeklyFacts, earlier_snapshots=(), path: Path = CENTRALS) -> dict:
    """Tỷ giá trung tâm phiên thứ Sáu theo tuần: lưu trên VPS, thêm thứ Sáu tuần này nếu có."""
    from datetime import date

    from ..models import VN_TZ

    store = dict(CENTRAL_SEED)
    if path.is_file():
        store.update(json.loads(path.read_text(encoding="utf-8")))
    for snap in earlier_snapshots:
        day = snap.as_of.astimezone(VN_TZ).date()
        if day.weekday() == 4 and "SBV_CENTRAL" in snap.observations:
            store.setdefault(day.isoformat(), float(snap.observations["SBV_CENTRAL"].value))
    friday = next((d for d in facts.days if d.day == facts.friday and d.sbv_central), None)
    if friday:
        store[facts.friday.isoformat()] = friday.sbv_central
    else:
        facts.warn("TGTT_FRIDAY_MISSING", "Chưa có tỷ giá trung tâm phiên thứ Sáu tuần này")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(store.items())), indent=1), encoding="utf-8")
    return {date.fromisoformat(k): float(v) for k, v in store.items()}

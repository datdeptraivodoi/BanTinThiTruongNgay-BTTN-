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
    for label, values in sorted(((facts.inputs.get("bond_curve") or {}).get("rows") or {}).items(),
                                key=lambda kv: tuple(reversed([int(x) for x in kv[0].split("/")]))):
        if "bonds" in charts:
            upsert(charts["bonds"], label, [None if v is None else float(v) for v in values], KEEP["bonds"])
    for label, values in ((facts.inputs.get("credit_deposit") or {}).get("rows") or {}).items():
        if "credit" in charts:
            upsert(charts["credit"], label, [None if v is None else float(v) for v in values], KEEP["credit"])
    return charts

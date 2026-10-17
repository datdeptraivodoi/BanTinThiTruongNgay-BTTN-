"""Bộ dữ kiện của một tuần (thứ Hai – thứ Sáu) cho bản tin tuần.

Nguồn:
* Snapshot của các lần chạy BTTN trong tuần (output/<lần chạy>/snapshot.json, thứ Hai – thứ Năm)
  và snapshot lấy lúc chạy bản tin tuần (thứ Sáu): VIRA, NHNN, quote ITB/ALM trong Teams, tin tức.
* Chuỗi giá ngày của Yahoo trong snapshot mới nhất: thay đổi cả tuần của tỷ giá, hàng hóa, vàng.
* config/weekly_inputs.json: số liệu team nhập (Term SOFR, tín dụng – huy động, lãi suất điều hành,
  ghi đè số dự báo).
Không có số thì để None; phần viết và bảng sẽ hiện "—" và báo lỗi, không tự điền.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from ..models import Snapshot

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
ROOT = Path(__file__).resolve().parents[2]
TENORS = ("ON", "1W", "2W", "1M", "3M", "6M")
MARKETS = ("EURUSD", "USDJPY", "DXY", "GOLD", "BRENT", "GASOLINE", "ARABICA", "ROBUSTA", "CRB", "BOND_Vietnam")


@dataclass
class Day:
    day: date
    vnd: dict[str, float] = field(default_factory=dict)        # VNIBOR VND theo kỳ hạn (VIRA)
    swap: dict[str, tuple[float, float]] = field(default_factory=dict)  # quote swap ALM (bid, ask)
    itb_bid: float | None = None
    itb_ask: float | None = None
    sbv_central: float | None = None
    vira_lnh: str = ""   # đoạn "Thị trường tiền tệ LNH" (gồm OMO, trái phiếu) của bản tin VIRA ngày
    vira_fx: str = ""    # đoạn "Thị trường ngoại tệ" của bản tin VIRA ngày


@dataclass
class Move:
    """Giá trong tuần: so với giá đóng cửa tuần trước."""
    prev_close: float | None
    open: float | None
    close: float | None
    high: float | None
    low: float | None
    close_date: date | None = None

    @property
    def change(self):
        return None if self.close is None or self.prev_close is None else self.close - self.prev_close

    @property
    def pct(self):
        if self.close is None or not self.prev_close:
            return None
        return (self.close / self.prev_close - 1) * 100


@dataclass
class WeeklyFacts:
    monday: date
    friday: date
    days: list[Day]
    markets: dict[str, Move]
    news_vi: list[dict]        # tin vĩ mô VN trong tuần: {title, text, url, published_at, score}
    news_en: dict[str, list[dict]]  # tin Trading Economics cả tuần theo nhóm (te_stream.topic_of)
    inputs: dict
    issues: list[dict] = field(default_factory=list)
    bond_yields: dict = field(default_factory=dict)   # lợi suất TPCP theo ngày (VBMA + VIRA): {ngày: {"1N": 3.54, ...}}

    def warn(self, code, message):
        self.issues.append({"severity": "warning", "code": code, "message": message})


def week_of(friday: date) -> tuple[date, date]:
    monday = friday - timedelta(days=friday.weekday())
    return monday, monday + timedelta(days=4)


def load_week_snapshots(output_dir: Path, monday: date, friday: date, extra: list[Snapshot] = ()) -> list[Snapshot]:
    """Snapshot mới nhất của mỗi ngày làm việc trong tuần."""
    found = {}
    for path in sorted(output_dir.glob("*/snapshot.json")):
        try:
            snap = Snapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        found.setdefault(snap.as_of.astimezone(VN_TZ).date(), []).append(snap)
    for snap in extra:
        found.setdefault(snap.as_of.astimezone(VN_TZ).date(), []).append(snap)
    days = [monday + timedelta(days=i) for i in range((friday - monday).days + 1)]
    return [max(found[d], key=lambda s: s.as_of) for d in days if d in found]


def _f(value) -> float | None:
    return None if value is None else float(value)


def day_record(snap: Snapshot) -> Day:
    obs = snap.observations
    rec = Day(day=snap.as_of.astimezone(VN_TZ).date())
    for t in TENORS:
        if f"VND_{t}" in obs:
            rec.vnd[t] = float(obs[f"VND_{t}"].value)
        bid, ask = obs.get(f"ALM_SWAP_{t}_BID"), obs.get(f"ALM_SWAP_{t}_ASK")
        if bid and ask:
            rec.swap[t] = (float(bid.value), float(ask.value))
    rec.itb_bid = _f(obs["INTERBANK_BID"].value) if "INTERBANK_BID" in obs else None
    rec.itb_ask = _f(obs["INTERBANK_ASK"].value) if "INTERBANK_ASK" in obs else None
    rec.sbv_central = _f(obs["SBV_CENTRAL"].value) if "SBV_CENTRAL" in obs else None
    if "vira_daily" in snap.sources:
        rec.vira_lnh = snap.sources["vira_daily"].text
    if "vira_fx" in snap.sources:
        rec.vira_fx = snap.sources["vira_fx"].text
    return rec


def market_move(snap: Snapshot, key: str, monday: date, friday: date) -> Move | None:
    obs = snap.observations.get(key)
    if obs is None:
        return None
    points = sorted(((p.at.astimezone(VN_TZ).date(), float(p.value)) for p in obs.series), key=lambda x: x[0])
    if not points:
        return Move(None, None, float(obs.value), None, None, obs.trading_date)
    before = [v for d, v in points if d < monday]
    week = [(d, v) for d, v in points if monday <= d <= friday]
    if not week:
        return Move(before[-1] if before else None, None, None, None, None)
    values = [v for _, v in week]
    return Move(before[-1] if before else None, values[0], values[-1], max(values), min(values), week[-1][0])


def load_inputs(path: Path | None = None) -> dict:
    path = path or ROOT / "config" / "weekly_inputs.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _news_items(snapshots: list[Snapshot], predicate) -> list[dict]:
    seen, items = set(), []
    for snap in snapshots:
        for sid, src in snap.sources.items():
            if src.kind != "news" or src.text_scope not in ("article", "excerpt") or not predicate(sid, src):
                continue
            key = src.sha256 or src.url
            if key in seen:
                continue
            seen.add(key)
            title, _, body = src.text.partition("\n\n")
            items.append({"id": sid, "title": title.strip(), "text": (body or src.text).strip(), "url": src.url,
                          "published_at": src.published_at})
    return sorted(items, key=lambda x: x["published_at"], reverse=True)


def build_facts(friday: date, snapshots: list[Snapshot], inputs: dict | None = None,
                te_items: dict[str, list[dict]] | None = None) -> WeeklyFacts:
    monday, friday = week_of(friday)
    snaps = [s for s in snapshots if monday <= s.as_of.astimezone(VN_TZ).date() <= friday]
    latest = max(snaps, key=lambda s: s.as_of) if snaps else None
    facts = WeeklyFacts(monday=monday, friday=friday, days=[day_record(s) for s in snaps], markets={},
                        news_vi=[], news_en=te_items or {}, inputs=inputs if inputs is not None else load_inputs())
    if latest is None:
        facts.warn("WEEK_NO_DATA", "Không có snapshot nào trong tuần")
        return facts
    for key in MARKETS:
        move = market_move(latest, key, monday, friday)
        if move is None or move.close is None:
            facts.warn("WEEK_MARKET_MISSING", f"{key}: thiếu giá trong tuần")
        if move is not None:
            facts.markets[key] = move
    start = datetime.combine(monday - timedelta(days=3), datetime.min.time(), VN_TZ)
    facts.news_vi = [n for n in _news_items(snaps, lambda sid, s: "tradingeconomics.com" not in s.url
                                            and not sid.startswith("news_china_") and sid not in ("vira_daily", "vira_fx"))
                     if n["published_at"] >= start]
    if not any(d.vira_lnh for d in facts.days):
        facts.warn("WEEK_VIRA_MISSING", "Không có bản tin VIRA nào trong tuần")
    return facts


def decimal_str(value: float | Decimal | None) -> str | None:
    return None if value is None else format(Decimal(str(value)).normalize(), "f")


def tradingview_move(key: str, tv_symbol: str, monday: date, friday: date, runner=None) -> Move | None:
    """Giá cả tuần lấy từ TradingView (cầu nối Node scripts/fetch_tradingview.js), ví dụ BCOM Index."""
    import subprocess

    try:
        if runner is None:
            proc = subprocess.run(["node", str(ROOT / "scripts" / "fetch_tradingview.js"), json.dumps({key: tv_symbol})],
                                  cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=True)
            payload = json.loads(proc.stdout)
        else:
            payload = runner(key, tv_symbol)
        candles = payload.get(key, {}).get("candles") or []
    except Exception:  # noqa: BLE001 - nguồn lỗi thì trả None để báo trong email review
        return None
    points = sorted((datetime.fromtimestamp(int(c["time"]), VN_TZ).date(), float(c["close"])) for c in candles)
    before = [v for d, v in points if d < monday]
    week = [v for d, v in points if monday <= d <= friday]
    if not week:
        return None
    last_day = max(d for d, _ in points if d <= friday)
    return Move(before[-1] if before else None, week[0], week[-1], max(week), min(week), last_day)


WEEK_MARKETS = ("DXY", "EURUSD", "USDJPY", "GOLD", "BRENT", "GASOLINE", "ARABICA", "ROBUSTA", "CRB")


def yahoo_move(key: str, monday: date, friday: date, fetch=None) -> Move | None:
    """Giá cả tuần của một mã (nến ngày Yahoo Finance, tính theo ngày giao dịch của sàn)."""
    from urllib.parse import quote

    import requests

    from ..sources import INSTRUMENTS

    symbol = INSTRUMENTS.get(key, (None,))[0]
    if not symbol:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?interval=1d&range=1mo"
    try:
        if fetch is None:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=(10, 20))
            resp.raise_for_status()
            data = resp.json()
        else:
            data = fetch(url)
        result = data["chart"]["result"][0]
        offset = int(result.get("meta", {}).get("gmtoffset") or 0)
        closes = result["indicators"]["quote"][0]["close"]
        highs = result["indicators"]["quote"][0].get("high") or closes
        lows = result["indicators"]["quote"][0].get("low") or closes
    except Exception:  # noqa: BLE001 - nguồn lỗi thì để trống và báo trong email review
        return None
    rows = []
    for t, c, h, lo in zip(result.get("timestamp") or [], closes, highs, lows):
        if c is None:
            continue
        day = datetime.fromtimestamp(int(t) + offset, timezone.utc).date()
        rows.append((day, float(c), float(h if h is not None else c), float(lo if lo is not None else c)))
    before = [c for d, c, _, _ in rows if d < monday]
    week = [r for r in rows if monday <= r[0] <= friday]
    if not week:
        return None
    return Move(before[-1] if before else None, week[0][1], week[-1][1], max(r[2] for r in week),
                min(r[3] for r in week), week[-1][0])


def fill_markets(facts: "WeeklyFacts", keys=WEEK_MARKETS, fetch=None) -> list[str]:
    """Mã nào snapshot ngày không có đủ giá cả tuần (ví dụ cà phê lấy từ VietnamBiz) thì lấy nến Yahoo."""
    filled = []
    for key in keys:
        m = facts.markets.get(key)
        if m is not None and m.close is not None and m.pct is not None:
            continue
        move = yahoo_move(key, facts.monday, facts.friday, fetch=fetch)
        if move is not None:
            facts.markets[key] = move
            filled.append(key)
    facts.issues = [i for i in facts.issues if not (i["code"] == "WEEK_MARKET_MISSING"
                                                     and i["message"].split(":")[0] in filled)]
    return filled

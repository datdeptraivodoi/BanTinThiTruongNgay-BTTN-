"""Weekly economic calendar from ForexFactory plus Vietnam releases (Rules CAL-03).

Scope, as decided by the project owner on 25/09/2026:

* International events come from the ForexFactory weekly calendar export
  (the "Export" JSON that forexfactory.com/calendar links to), or from the
  calendar page when the export has no data for that week (next week).
  **High and Medium** impact events are kept (owner, 25/09/2026), for the 7 economies of the News filter besides Vietnam:
  United States, Euro Area, China, Japan, United Kingdom, Canada, Australia.
* Vietnam is not on ForexFactory. It adds at most one row for the NSO monthly
  socio-economic report and one row per day on which Vietnam Customs publishes
  trade figures. Those dates come from official sources (NSO release metadata,
  the curated Customs schedule in the seed file).
* A trading week is Monday-Friday in Vietnam time, with **at most 20 rows**
  in total. Vietnam rows are always kept; the remaining slots are filled with
  international events: reserve daily coverage (High or one Medium when no
  High is available) and US weekly initial claims, then fill High before Medium.

The export has previous/forecast but no actual values; those stay empty
rather than being guessed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

MAX_EVENTS_PER_WEEK = 20
IMPACTS = ("high", "medium")  # ForexFactory levels kept, most important first
IMPACT_RANK = {level: index for index, level in enumerate(IMPACTS)}
TE_CACHE_POLICY = 2  # Earlier cached TE pages contain High only; refresh once.
PAGE_RETRY_SECONDS = 6 * 60 * 60
INTL_CODES = {"forexfactory", "tradingeconomics"}  # eventCode of international rows
MONTH_VI = {m: f"T{i}" for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
# Trading Economics title endings: "... YoY Flash" -> base + change + release stage.
TE_CHANGE = {"MoM": " (so tháng trước)", "YoY": " (so cùng kỳ)", "QoQ": " (so quý trước)"}
TE_STAGE = {"Final": " – chính thức", "Flash": " – sơ bộ", "Prel": " – sơ bộ", "Adv": " – sơ bộ lần đầu",
            "2nd Est": " – ước tính lần 2", "3rd Est": " – ước tính lần 3"}
VN_TZ = timezone(timedelta(hours=7))  # Vietnam has no daylight saving time.

FEEDS = {
    "thisweek": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "nextweek": "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
}
FF_CALENDAR_URL = "https://www.forexfactory.com/calendar"
# The export is refreshed about hourly and blocks clients that poll too often.
MIN_FETCH_INTERVAL_SECONDS = 60 * 60

# Currency on ForexFactory -> economy shown in Master Data, in ranking order.
ECONOMIES: dict[str, dict[str, str]] = {
    "USD": {"code": "US", "vi": "Mỹ", "en": "United States", "region": "North America"},
    "EUR": {"code": "EA", "vi": "Khu vực đồng euro", "en": "Euro Area", "region": "Europe"},
    "CNY": {"code": "CN", "vi": "Trung Quốc", "en": "China", "region": "Asia"},
    "JPY": {"code": "JP", "vi": "Nhật Bản", "en": "Japan", "region": "Asia"},
    "GBP": {"code": "GB", "vi": "Vương quốc Anh", "en": "United Kingdom", "region": "Europe"},
    "CAD": {"code": "CA", "vi": "Canada", "en": "Canada", "region": "North America"},
    "AUD": {"code": "AU", "vi": "Australia", "en": "Australia", "region": "Oceania"},
}
ECONOMY_RANK = {currency: index for index, currency in enumerate(ECONOMIES)}

CUSTOMS_URL = (
    "https://www.customs.gov.vn/index.jsp?pageId=449&group=C%C3%94NG%20B%E1%BB%90%20V%C3%80"
    "%20TH%C3%94NG%20TIN&category=L%E1%BB%8Bch%20c%C3%B4ng%20b%E1%BB%91%20th%C3%B4ng%20tin"
)
NSO_URL = "https://www.nso.gov.vn/bao-cao-tinh-hinh-kinh-te-xa-hoi-hang-thang/"
NSO_DEFAULT_DAY = 3  # Owner: NSO publishes on the 3rd of each month.

SUFFIXES = (
    (" m/m", " (so tháng trước)", " (m/m)"),
    (" q/q", " (so quý trước)", " (q/q)"),
    (" y/y", " (so cùng kỳ)", " (y/y)"),
)


# --------------------------------------------------------------------------- fetch


def fetch_feeds(cache_dir: Path, *, force: bool = False, session: Any = None) -> dict[str, str]:
    """Download the weekly exports into cache_dir, at most once an hour each.

    Every successful download is also archived under cache_dir/archive with a
    timestamp and SHA-256, so the source of each published row stays traceable.
    Returns {feed: status} where status is FETCHED, CACHED, NOT_FOUND or an error.
    """
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "archive").mkdir(exist_ok=True)
    http = session or requests.Session()
    statuses: dict[str, str] = {}
    for name, url in FEEDS.items():
        latest = cache_dir / f"ff_calendar_{name}.json"
        if not force and latest.is_file() and time.time() - latest.stat().st_mtime < MIN_FETCH_INTERVAL_SECONDS:
            statuses[name] = "CACHED"
            continue
        try:
            response = http.get(url, timeout=30, headers={"User-Agent": "MB-Treasury-Research/2.0 (internal calendar)"})
        except Exception as exc:  # network errors keep the previous cache
            statuses[name] = f"ERROR: {exc.__class__.__name__}"
            continue
        if response.status_code == 404:
            statuses[name] = "NOT_FOUND"
            continue
        if response.status_code != 200:
            statuses[name] = f"HTTP {response.status_code}"
            continue
        try:
            payload = response.json()
        except ValueError:
            # A rate-limit page is HTML; never overwrite a good cache with it.
            statuses[name] = "ERROR: not JSON (rate limited?)"
            continue
        if not isinstance(payload, list):
            statuses[name] = "ERROR: unexpected payload"
            continue
        body = json.dumps(payload, ensure_ascii=False, indent=1)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (cache_dir / "archive" / f"ff_calendar_{name}_{stamp}_{digest[:10]}.json").write_text(body, encoding="utf-8")
        temporary = latest.with_suffix(".tmp")
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(latest)
        statuses[name] = "FETCHED"
    return statuses


def load_cached_items(cache_dir: Path) -> list[dict[str, Any]]:
    """All events from the cached exports (this week + next week), de-duplicated."""
    items: dict[tuple[str, str, str], dict[str, Any]] = {}
    for name in FEEDS:
        path = cache_dir / f"ff_calendar_{name}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for item in payload if isinstance(payload, list) else []:
            if isinstance(item, dict):
                key = (str(item.get("title")), str(item.get("country")), str(item.get("date")))
                items[key] = item
    return list(items.values())


# --------------------------------------------------------------------------- helpers


def week_bounds(value: str | date) -> tuple[date, date]:
    day = value if isinstance(value, date) else date.fromisoformat(str(value))
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=4)


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _unit(*values: str | None) -> str:
    for value in values:
        if not value:
            continue
        if value.endswith("%"):
            return "%"
        if value[-1:] in {"K", "M", "B", "T"}:
            return {"K": "nghìn", "M": "triệu", "B": "tỷ", "T": "nghìn tỷ"}[value[-1]]
    return ""


def is_initial_claims(title: str) -> bool:
    return bool(re.fullmatch(r"(?:Initial (?:Jobless|Unemployment) Claims|Unemployment Claims|Jobless Claims)",
                             title.strip(), re.IGNORECASE))


def _source_gaps(items: list[dict[str, Any]], monday: date) -> bool:
    scoped = [item for item in items if item.get("country") in ECONOMIES
              and str(item.get("impact", "")).lower() in IMPACTS
              and (day := _vn_day(item)) and monday <= day <= monday + timedelta(days=4)]
    days = {_vn_day(item) for item in scoped}
    claims = any(item.get("country") == "USD" and is_initial_claims(str(item.get("title", ""))) for item in scoped)
    return len(days) < 5 or not claims


def _supplement_week(items: list[dict[str, Any]], extra: list[dict[str, Any]], monday: date) -> list[dict[str, Any]]:
    """Use TE to fill missing days/claims, without duplicating FF releases whose titles differ."""
    scoped = [item for item in items if item.get("country") in ECONOMIES
              and str(item.get("impact", "")).lower() in IMPACTS
              and (day := _vn_day(item)) and monday <= day <= monday + timedelta(days=4)]
    days = {_vn_day(item) for item in scoped}
    claims = any(item.get("country") == "USD" and is_initial_claims(str(item.get("title", ""))) for item in scoped)
    return [item for item in extra
            if (day := _vn_day(item)) and monday <= day <= monday + timedelta(days=4)
            and (day not in days or (not claims and item.get("country") == "USD"
                                    and is_initial_claims(str(item.get("title", "")))))]


@dataclass
class TitleBook:
    """Vietnamese names for ForexFactory titles (config/forexfactory_titles_vi.json)."""

    exact: dict[str, str]
    patterns: list[tuple[re.Pattern[str], str]]
    speakers: dict[str, str] | None = None
    prefixes: dict[str, str] | None = None

    @classmethod
    def load(cls, path: Path | None) -> "TitleBook":
        if path is None or not path.is_file():
            return cls({}, [])
        payload = json.loads(path.read_text(encoding="utf-8"))
        patterns = [(re.compile(row["pattern"]), row["vi"]) for row in payload.get("patterns", [])]
        return cls(dict(payload.get("titles", {})), patterns, dict(payload.get("speakers", {})), dict(payload.get("countryPrefixes", {})))

    def translate(self, title: str) -> str | None:
        if is_initial_claims(title):
            return "Trợ cấp thất nghiệp hàng tuần"
        # "ECB President Lagarde Speaks" -> "Chủ tịch ECB Lagarde phát biểu"
        for ending, verb in ((" Speaks", "phát biểu"), (" Testifies", "điều trần")):
            if title.endswith(ending) and self.speakers:
                who = title[: -len(ending)]
                for role in sorted(self.speakers, key=len, reverse=True):
                    if who == role or who.startswith(role + " "):
                        return f"{self.speakers[role]}{who[len(role):]} {verb}"
        # "French Flash Services PMI" -> "PMI dịch vụ sơ bộ (Pháp)"
        for prefix, country in (self.prefixes or {}).items():
            if title.startswith(prefix + " ") and title not in self.exact:
                inner = self.translate(title[len(prefix) + 1:])
                if inner:
                    return f"{inner} ({country})"
        te = re.fullmatch(r"(.+?)(?: (MoM|YoY|QoQ))?(?: (Final|Flash|Prel|Adv|2nd Est|3rd Est))?", title)
        if te and (te.group(2) or te.group(3)) and te.group(1) in self.exact:
            return self.exact[te.group(1)] + TE_CHANGE.get(te.group(2) or "", "") + TE_STAGE.get(te.group(3) or "", "")
        base, suffix_vi = title, ""
        for suffix, vi, _ in SUFFIXES:
            if title.endswith(suffix):
                base, suffix_vi = title[: -len(suffix)], vi
                break
        if title in self.exact:
            return self.exact[title]
        if base in self.exact:
            return self.exact[base] + suffix_vi
        for pattern, template in self.patterns:
            match = pattern.fullmatch(base)
            if match:
                return match.expand(template) + suffix_vi
        return None


# --------------------------------------------------------------------------- rows


def forexfactory_rows(items: Iterable[dict[str, Any]], titles: TitleBook, untranslated: set[str]) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for item in items:
        impact = str(item.get("impact", "")).strip().lower()
        if impact not in IMPACT_RANK:
            continue
        currency = str(item.get("country", "")).upper()
        economy = ECONOMIES.get(currency)
        if economy is None:
            continue
        try:
            moment = datetime.fromisoformat(str(item["date"])).astimezone(VN_TZ)
        except (KeyError, ValueError):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        vi = titles.translate(title)
        if vi is None:
            untranslated.add(title)
        previous, forecast = _clean(item.get("previous")), _clean(item.get("forecast"))
        local_date = moment.date().isoformat()
        local_time = moment.strftime("%H:%M")
        identity = (currency, "initial_claims" if currency == "USD" and is_initial_claims(title) else title.casefold(),
                    local_date, local_time)
        if identity in seen:
            continue
        seen.add(identity)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        from_te = item.get("from") == "te"
        period = str(item.get("period") or "").strip().upper()
        period = MONTH_VI.get(period, period)
        rows.append({
            "id": f"{'te' if from_te else 'ff'}-{economy['code'].lower()}-{slug}-{local_date}-{local_time.replace(':', '')}",
            "eventCode": "tradingeconomics" if from_te else "forexfactory",
            "bundleId": f"ff-{currency}-{local_date}",
            "localDate": local_date,
            "localTime": None if item.get("allDay") else local_time,
            "releaseAt": moment.isoformat(),
            "countryCode": economy["code"],
            "countryVi": economy["vi"],
            "countryEn": economy["en"],
            "currency": currency,
            "region": economy["region"],
            "category": "Macro",
            "nameVi": f"{economy['vi']}: {vi or title}",
            "nameEn": f"{economy['en']}: {title}",
            "titleSource": title,
            "period": period,
            "previous": previous,
            "forecast": forecast,
            # Filled for past weeks from the calendar page; the export has none.
            "actual": _clean(item.get("actual")),
            "previousRevised": _clean(item.get("revision")),
            "unit": _unit(forecast, previous, _clean(item.get("actual"))),
            "impact": impact,
            "priorityScore": float(100 - 10 * IMPACT_RANK[impact] - ECONOMY_RANK[currency]),
            "status": "scheduled",
            "sourceName": item.get("sourceName") or "ForexFactory",
            "sourceUrl": item.get("sourceUrl") or FF_CALENDAR_URL,
            "forecastKind": item.get("forecastKind"),
            "sourceType": "tradingeconomics_page" if from_te else ("forexfactory_page" if item.get("from") in {"embedded", "table"} else "forexfactory_export"),
            "notes": "" if vi else "Chưa có tên tiếng Việt cho tiêu đề này; đang hiện tên gốc.",
        })
    return rows


def _vn_row(kind: str, day: date, *, name_vi: str, name_en: str, period: str, url: str, source: str, notes: str) -> dict[str, Any]:
    return {
        "id": f"vn-{kind}-{day.isoformat()}",
        "eventCode": f"vn_{kind}",
        "bundleId": f"vn-{kind}-{day.isoformat()}",
        "localDate": day.isoformat(),
        "localTime": None,
        "releaseAt": f"{day.isoformat()}T00:00:00+07:00",
        "countryCode": "VN",
        "countryVi": "Việt Nam",
        "countryEn": "Vietnam",
        "currency": "VND",
        "region": "Vietnam",
        "category": "Macro" if kind == "nso" else "Trade",
        "nameVi": name_vi,
        "nameEn": name_en,
        "period": period,
        "previous": None,
        "forecast": None,
        "actual": None,
        "unit": "",
        "impact": "high",
        "priorityScore": 200.0,  # Vietnam rows are always kept.
        "status": "scheduled",
        "sourceName": source,
        "sourceUrl": url,
        "sourceType": "official_schedule",
        "notes": notes,
    }


def nso_release_dates(nso_latest: Path | None, seed_events: list[dict[str, Any]], start: date, end: date,
                      known_dates: dict[str, str] | None = None) -> list[tuple[date, str]]:
    """NSO report dates between start and end (inclusive), any weekday.

    Uses the date NSO announced ("Lần công bố sắp tới") or a seed entry for
    that month; otherwise the owner's rule of the 3rd of the month.
    """
    known: dict[str, date] = {key: date.fromisoformat(value) for key, value in (known_dates or {}).items()}
    if nso_latest and nso_latest.is_file():
        try:
            value = json.loads(nso_latest.read_text(encoding="utf-8")).get("nextPublicationDate")
            if value:
                day = date.fromisoformat(value)
                known[day.strftime("%Y-%m")] = day
        except ValueError:
            pass
    for raw in seed_events:
        if raw.get("eventCode") == "nso_socio_economic_report" and raw.get("localDate"):
            day = date.fromisoformat(str(raw["localDate"]))
            known.setdefault(day.strftime("%Y-%m"), day)
    found = []
    for month_start in sorted({start.replace(day=1), end.replace(day=1)}):
        key = month_start.strftime("%Y-%m")
        day, how = (known[key], "announced") if key in known else (month_start.replace(day=NSO_DEFAULT_DAY), "default_3rd")
        if start <= day <= end:
            found.append((day, how))
    return found


def vietnam_rows(
    start: date,
    end: date,
    *,
    nso_latest: Path | None,
    seed_events: list[dict[str, Any]],
    customs_history: list[dict[str, Any]] | None = None,
    nso_dates: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    from .customs_calendar import PAGE_URL, release_days, summarize

    rows, notes = [], []
    # Look through Sunday: a weekend NSO date is shown on the Friday before (owner, 25/09/2026).
    for day, how in nso_release_dates(nso_latest, seed_events, start, end + timedelta(days=2), nso_dates):
        shown = day - timedelta(days=day.weekday() - 4) if day.weekday() >= 5 else day
        if not start <= shown <= end:
            continue
        report = day.replace(day=1) - timedelta(days=1)
        weekday = "thứ Bảy" if day.weekday() == 5 else "Chủ nhật"
        suffix = f" (công bố {weekday} {day.strftime('%d/%m')})" if shown != day else ""
        note = "Ngày theo 'Lần công bố sắp tới' của NSO." if how == "announced" else "Chưa có ngày NSO công bố; tạm theo lệ ngày mùng 3."
        if shown != day:
            note += f" NSO công bố {weekday} {day.strftime('%d/%m/%Y')}; hiện ở thứ Sáu vì lịch chỉ tính thứ Hai–thứ Sáu."
        row = _vn_row(
            "nso", shown,
            name_vi=f"NSO công bố báo cáo tình hình kinh tế – xã hội tháng {report.month}/{report.year}{suffix}",
            name_en=f"NSO monthly socio-economic report for {report.strftime('%b %Y')}"
            + (f" (released {day.strftime('%a %d %b')})" if shown != day else ""),
            period=report.strftime("%m/%Y"), url=NSO_URL, source="Cục Thống kê (NSO)", notes=note,
        )
        row["releaseDate"] = day.isoformat()
        rows.append(row)

    days = release_days(customs_history or [], start, end)
    # Older weeks may only exist in the curated seed.
    for raw in seed_events:
        if raw.get("eventCode") == "customs_preliminary_trade" and raw.get("localDate"):
            day = date.fromisoformat(str(raw["localDate"]))
            if start <= day <= end:
                days.setdefault(day, [])
    for day, titles in sorted(days.items()):
        rows.append(_vn_row(
            "customs", day,
            name_vi=summarize(titles) if titles else "Cục Hải quan công bố số liệu xuất nhập khẩu hàng hóa",
            name_en="Vietnam Customs releases merchandise trade figures",
            period="", url=PAGE_URL, source="Cục Hải quan",
            notes=("Lịch công bố của Cục Hải quan: " + "; ".join(titles)) if titles else "Ngày theo lịch tuyển chọn cũ.",
        ))
    if not days:
        notes.append("Cục Hải quan không có lịch công bố trong tuần này.")
    return rows, notes


# --------------------------------------------------------------------------- pack


def build_week(
    week_start: str | date,
    ff_items: list[dict[str, Any]],
    *,
    titles: TitleBook,
    seed_events: list[dict[str, Any]],
    nso_latest: Path | None,
    customs_history: list[dict[str, Any]] | None = None,
    nso_dates: dict[str, str] | None = None,
    feed_status: dict[str, str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    start, end = week_bounds(week_start)
    untranslated: set[str] = set()
    ff_all = forexfactory_rows(ff_items, titles, untranslated)
    ff_week = [row for row in ff_all if start.isoformat() <= row["localDate"] <= end.isoformat()]
    vn, vn_notes = vietnam_rows(start, end, nso_latest=nso_latest, seed_events=seed_events,
                                customs_history=customs_history, nso_dates=nso_dates)
    slots = max(0, MAX_EVENTS_PER_WEEK - len(vn))
    # Within a level, releases with numbers come before speeches and press conferences.
    ranked = sorted(ff_week, key=lambda row: (
        IMPACT_RANK[row["impact"]],
        0 if (row["forecast"] is not None or row["previous"] is not None) else 1,
        ECONOMY_RANK[row["currency"]], row["releaseAt"], row["id"],
    ))
    # The global top-20 cut used to remove an entire quiet day. Reserve the
    # weekly US claims release and one real event for each available weekday.
    # Source dates win, including official holiday rescheduling; never create
    # a Thursday release or actual/forecast values from a recurrence assumption.
    chosen: list[dict[str, Any]] = []
    chosen_ids: set[str] = set()

    def reserve(row: dict[str, Any], reason: str) -> None:
        if row["id"] not in chosen_ids and len(chosen) < slots:
            chosen.append({**row, "selectionReason": reason})
            chosen_ids.add(row["id"])

    for row in ranked:
        if row["countryCode"] == "US" and is_initial_claims(row["titleSource"]):
            reserve(row, "weekly_initial_claims")
    for offset in range(5):
        day = (start + timedelta(days=offset)).isoformat()
        if any(row["localDate"] == day and row["impact"] == "high" for row in vn):
            continue
        candidates = [row for row in ranked if row["localDate"] == day]
        if not candidates:
            continue
        best = candidates[0]
        if best["impact"] == "high":
            reserve(best, "daily_high_coverage")
        elif not any(row["localDate"] == day for row in vn + chosen):
            reserve(best, "daily_medium_fallback")
    for row in ranked:
        reserve(row, "priority")
    events = sorted(vn + chosen, key=lambda row: (row["localDate"], row["localTime"] or "", -row["priorityScore"], row["id"]))
    notes = [
        "Nguồn quốc tế: ForexFactory, tin mức High và Medium của 7 nền kinh tế (High ưu tiên trước); Việt Nam từ NSO và Cục Hải quan.",
        f"Tối đa {MAX_EVENTS_PER_WEEK} dòng mỗi tuần thứ Hai–thứ Sáu; dòng Việt Nam luôn được giữ.",
        "Giữ Trợ cấp thất nghiệp hàng tuần của Mỹ theo ngày nguồn công bố; ngày không có High ưu tiên ít nhất một Medium nếu nguồn có.",
        "Số thực tế chỉ bổ sung khi tuần đã qua, lấy từ trang lịch ForexFactory của tuần đó.",
        *vn_notes,
    ]
    if not ff_week:
        notes.append("Chưa có dữ liệu ForexFactory cho tuần này (bản xuất chưa phát hành hoặc chưa tải được).")
    missing_days = [(start + timedelta(days=offset)).isoformat() for offset in range(5)
                    if not any(row["localDate"] == (start + timedelta(days=offset)).isoformat() for row in events)]
    claims_selected = any(row["countryCode"] == "US" and is_initial_claims(row.get("titleSource", "")) for row in chosen)
    if not claims_selected:
        notes.append("Nguồn chưa có lịch Trợ cấp thất nghiệp hàng tuần của Mỹ; cần đối chiếu lịch công bố, không tự tạo sự kiện.")
    return {
        "title": "Lịch kinh tế tuần",
        "asOf": end.isoformat(),
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
        "week": {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "label": f"Tuần {start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}",
            "timezone": "Asia/Ho_Chi_Minh",
        },
        "scope": {
            "maxEvents": MAX_EVENTS_PER_WEEK,
            "impact": ["High", "Medium"],
            "economies": [value["code"] for value in ECONOMIES.values()] + ["VN"],
            "forecastPolicy": "Theo ForexFactory; để trống khi nguồn không có.",
            "actualPolicy": "Chỉ có với tuần đã qua (trang lịch ForexFactory); tuần này và tuần tới không hiện số thực tế.",
        },
        "source": {
            "primary": " · ".join(
                (["ForexFactory (tin High, Medium)"] if any(r["eventCode"] == "forexfactory" for r in chosen) else [])
                + (["Trading Economics (2–3 sao)"] if any(r["eventCode"] == "tradingeconomics" for r in chosen) else [])
                + ["NSO", "Cục Hải quan"]),
            "url": FF_CALENDAR_URL,
            "feedStatus": feed_status or {},
        },
        "quality": {
            "forexfactoryHighInWeek": len(ff_week),  # High + Medium in scope (name kept for compatibility)
            "forexfactoryByImpact": {level: sum(row["impact"] == level for row in ff_week) for level in IMPACTS},
            "forexfactorySelected": len(chosen),
            "forexfactoryDroppedOverLimit": len(ff_week) - len(chosen),
            "vietnamRows": len(vn),
            "selectedCount": len(events),
            "missingDays": missing_days,
            "weeklyClaimsSelected": claims_selected,
            "mediumFallbackDays": sorted({row["localDate"] for row in chosen if row["selectionReason"] == "daily_medium_fallback"
                                          or (row["impact"] == "medium" and not any(
                                              other["localDate"] == row["localDate"] and other["impact"] == "high" for other in vn + ff_week))}),
            "actualsFilled": sum(row.get("actual") is not None for row in chosen),
            "untranslatedTitles": sorted(untranslated & {row["titleSource"] for row in chosen}),
            "notes": notes,
        },
        "events": events,
    }


# --------------------------------------------------------------------------- past weeks

ACTUAL_RETRY_SECONDS = 6 * 60 * 60
ACTUAL_GIVE_UP_DAYS = 7
BACKFILL_LOOKBACK_DAYS = 28


def _expects_actual(row: dict[str, Any]) -> bool:
    # Speeches, statements and press conferences have no number on ForexFactory.
    return row.get("eventCode") in INTL_CODES and (row.get("forecast") is not None or row.get("previous") is not None)


def actuals_complete(pack: dict[str, Any]) -> bool:
    rows = [row for row in pack.get("events", []) if _expects_actual(row)]
    return all(row.get("actual") is not None for row in rows)


def fill_actuals_from_table(pack: dict[str, Any], items: list[dict[str, Any]]) -> int:
    """Put actual/revised values from table rows into matching existing rows.

    Table dates follow the page's time zone, so a row matches by currency and
    title on the same or an adjacent day; each table row is used once.
    """
    pool = [item for item in items if item.get("actual") is not None]
    filled = 0
    for row in pack.get("events", []):
        if row.get("eventCode") != "forexfactory" or row.get("actual") is not None:
            continue
        day = date.fromisoformat(row["localDate"])
        for index, item in enumerate(pool):
            if item["country"] != row.get("currency") or item["title"] != row.get("titleSource"):
                continue
            item_day = item.get("day") or datetime.fromisoformat(item["date"]).astimezone(VN_TZ).date().isoformat()
            if abs((date.fromisoformat(item_day) - day).days) > 1:
                continue
            row["actual"] = item["actual"]
            if item.get("revision"):
                row["previousRevised"] = item["revision"]
            row["sourceType"] = "forexfactory_page"
            pool.pop(index)
            filled += 1
            break
    return filled


def backfill_week(
    pack: dict[str, Any] | None,
    monday: date,
    *,
    reader: Any,
    build: Any,
    now: datetime,
) -> tuple[dict[str, Any] | None, str]:
    """Add actual values to a finished week; returns (pack or None, status)."""
    items, how = reader(monday)
    if not items:
        if pack is not None:
            pack.setdefault("history", {})["lastActualAttempt"] = now.isoformat()
            pack["history"]["lastActualStatus"] = how
        return pack, f"FAILED ({how})"
    if any(item.get("from") in {"embedded", "te"} and item.get("date") for item in items):
        # Full events with timestamps: rebuild the week, actuals included.
        rebuilt = build(monday, items)
        if pack is not None and pack["quality"].get("forexfactoryHighInWeek") and not rebuilt["quality"]["forexfactoryHighInWeek"]:
            # Impact levels could not be read from the page: keep the rows, add the numbers.
            filled = fill_actuals_from_table(pack, items)
            pack.setdefault("history", {}).update({"lastActualAttempt": now.isoformat(), "lastActualStatus": how})
            pack["quality"]["actualsFilled"] = sum(r.get("actual") is not None for r in pack["events"] if r.get("eventCode") in INTL_CODES)
            return pack, f"FILLED {filled} ({how}, impact not readable)"
        rebuilt["history"] = {**(pack or {}).get("history", {}), "lastActualAttempt": now.isoformat(), "lastActualStatus": how}
        return rebuilt, f"REBUILT ({how})"
    if pack is None:
        return None, f"FAILED ({how}: table rows cannot create a week)"
    filled = fill_actuals_from_table(pack, items)
    pack.setdefault("history", {}).update({"lastActualAttempt": now.isoformat(), "lastActualStatus": how})
    pack["quality"]["actualsFilled"] = sum(row.get("actual") is not None for row in pack["events"] if row.get("eventCode") in INTL_CODES)
    return pack, f"FILLED {filled} ({how})"


# --------------------------------------------------------------------------- files


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _remember_nso_date(path: Path, nso_latest: Path) -> dict[str, str]:
    dates = _read_json(path) or {}
    latest = _read_json(nso_latest) or {}
    value = latest.get("nextPublicationDate") if isinstance(latest, dict) else None
    if value:
        dates[value[:7]] = value
        _write_json(path, dates)
    return dates


def _vn_day(item: dict[str, Any]) -> date | None:
    try:
        return datetime.fromisoformat(str(item["date"])).astimezone(VN_TZ).date()
    except (KeyError, ValueError):
        return None


def _source_week(cache_dir: Path, name: str, monday: date, *, reader: Any, fetch: bool, force: bool,
                 now: datetime) -> tuple[list[dict[str, Any]], str]:
    """Events of one week from a calendar web page (ForexFactory or Trading Economics).

    A good result is cached for 6 hours. A failure is remembered for 6 hours too,
    so an unreachable site (bank firewall, bot check) is not retried every hour.
    """
    path = cache_dir / f"{name}_week_{monday.isoformat()}.json"
    cached = _read_json(path) or {}
    fetched_at, failed_at = cached.get("fetchedAt"), cached.get("failedAt")
    label = "PAGE" if name == "page" else name.upper()
    policy = TE_CACHE_POLICY if name == "te" else 1
    current_policy = cached.get("policyVersion", 1) == policy

    def recent(stamp: str | None) -> bool:
        return bool(stamp) and (now - datetime.fromisoformat(stamp)).total_seconds() < PAGE_RETRY_SECONDS

    items_cached = list(cached.get("items", []))
    if not fetch:
        return items_cached, f"{label} CACHED ({len(items_cached)})" if items_cached else f"{label} OFFLINE"
    if not force and current_policy and recent(fetched_at):
        return items_cached, f"{label} CACHED ({len(items_cached)})"
    if not force and cached.get("failedPolicyVersion", 1) == policy and recent(failed_at):
        return items_cached, f"{label} SKIPPED (lỗi lúc {failed_at}: {cached.get('how')})"
    items, how = reader(monday)
    items = [item for item in items if item.get("date")]  # only timestamped events can create rows
    if items:
        _write_json(path, {"fetchedAt": now.isoformat(), "how": how, "items": items, "policyVersion": policy})
        return items, f"{label} {how} ({len(items)})"
    _write_json(path, {**cached, "failedAt": now.isoformat(), "how": how, "failedPolicyVersion": policy})
    if items_cached:
        return items_cached, f"{label} FAILED ({how}); dùng bản {fetched_at}"
    return [], f"{label} FAILED ({how})"


def write_calendar_packs(
    root: Path,
    *,
    today: date | None = None,
    fetch: bool = True,
    force: bool = False,
    backfill: bool = True,
    backfill_weeks: list[date] | None = None,
    use_browser: bool = True,
    now: datetime | None = None,
    week_reader: Any = None,
    te_reader: Any = None,
    customs_fetcher: Any = None,
) -> dict[str, Any]:
    """Build this week and next week, archive them, add actuals to past weeks.

    Returns {"packs": [this week, next week], "backfill": {week: status}, "customs": status}.
    """
    from . import customs_calendar, te_calendar
    from .forexfactory_history import read_week

    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone(VN_TZ).date()
    cache_dir = root / "data" / "market" / "forexfactory"
    archive_dir = root / "data" / "market" / "economic_calendar" / "weeks"
    status = fetch_feeds(cache_dir, force=force) if fetch else {"thisweek": "OFFLINE", "nextweek": "OFFLINE"}
    items = load_cached_items(cache_dir)
    reader = week_reader or (lambda monday: read_week(monday, use_browser=use_browser))
    te = te_reader or te_calendar.read_week
    this_monday = week_bounds(today)[0]
    for monday, feed in ((this_monday, "thisweek"), (this_monday + timedelta(days=7), "nextweek")):
        # The export has no "next week" file: read that week's calendar page instead.
        in_export = any(_vn_day(item) and monday <= _vn_day(item) <= monday + timedelta(days=4) for item in items)
        if in_export and not _source_gaps(items, monday):
            continue
        page_items, page_status = _source_week(cache_dir, "page", monday, reader=reader, fetch=fetch, force=force, now=now)
        status[f"{feed}Page"] = page_status
        if page_items:
            items += page_items
            if not _source_gaps(items, monday):
                continue
        # Also supplement partial exports/pages, not only fully unavailable weeks.
        # TE Medium rows can cover quiet days and the weekly US initial claims.
        te_items, te_status = _source_week(cache_dir, "te", monday, reader=te, fetch=fetch, force=force, now=now)
        status[f"{feed}TE"] = te_status
        items += _supplement_week(items, te_items, monday)
    titles = TitleBook.load(root / "config" / "forexfactory_titles_vi.json")
    seed = _read_json(root / "config" / "economic_calendar_seed.json") or {}
    seed_events = seed.get("events", []) if isinstance(seed, dict) else seed
    nso_latest = root / "data" / "mega_data" / "nso" / "latest_release.json"
    nso_dates = _remember_nso_date(root / "data" / "market" / "economic_calendar" / "nso_dates.json", nso_latest)

    customs_path = root / "data" / "market" / "customs_calendar" / "releases.json"
    customs_status = "OFFLINE"
    if fetch:
        try:
            fresh = force or not customs_path.is_file() or time.time() - customs_path.stat().st_mtime > ACTUAL_RETRY_SECONDS
            if fresh:
                rows = (customs_fetcher or customs_calendar.fetch_schedule)()
                customs_calendar.merge_history(customs_path, rows)
                customs_status = f"FETCHED {len(rows)}"
            else:
                customs_status = "CACHED"
        except Exception as exc:  # keep the history already saved
            customs_status = f"ERROR: {exc.__class__.__name__}"
    customs_history = customs_calendar.load_history(customs_path)
    status = {**status, "customs": customs_status}

    def build(monday: date, source_items: list[dict[str, Any]]) -> dict[str, Any]:
        return build_week(monday, source_items, titles=titles, seed_events=seed_events, nso_latest=nso_latest,
                          customs_history=customs_history, nso_dates=nso_dates, feed_status=status)

    start, _ = week_bounds(today)
    packs = []
    for path, monday in (
        (root / "frontend" / "public" / "economic-calendar.json", start),
        (root / "frontend" / "public" / "economic-calendar-next-week.json", start + timedelta(days=7)),
    ):
        pack = build(monday, items)
        previous = _read_json(archive_dir / f"{monday.isoformat()}.json")
        if previous and not pack["quality"]["forexfactoryHighInWeek"] and previous.get("quality", {}).get("forexfactoryHighInWeek"):
            # The export can drop a week before it ends; keep the last good version.
            pack = previous
        _write_json(path, pack)
        _write_json(archive_dir / f"{monday.isoformat()}.json", pack)
        packs.append(pack)

    def past_reader(monday: date) -> tuple[list[dict[str, Any]], str]:
        # Actual values: ForexFactory page first, Trading Economics when it cannot be reached.
        items_ff, how_ff = [], "ForexFactory skipped (lỗi gần đây)"
        blocked = any((_read_json(p) or {}).get("failedAt") and not (_read_json(p) or {}).get("fetchedAt")
                      for p in cache_dir.glob("page_week_*.json"))
        if not blocked:
            items_ff, how_ff = reader(monday)
        if items_ff:
            return items_ff, how_ff
        items_te, how_te = _source_week(cache_dir, "te", monday, reader=te,
                                        fetch=True, force=force, now=now)
        return items_te, f"{how_ff}; {how_te}"

    results: dict[str, str] = {}
    candidates = list(backfill_weeks or [])
    if backfill and not candidates:
        for path in sorted(archive_dir.glob("*.json"), reverse=True):
            try:
                monday = date.fromisoformat(path.stem)
            except ValueError:
                continue
            _, friday = week_bounds(monday)
            if friday >= today or (today - friday).days > BACKFILL_LOOKBACK_DAYS:
                continue
            pack = _read_json(path) or {}
            history = pack.get("history", {})
            if history.get("actualsComplete"):
                continue
            if (today - friday).days > ACTUAL_GIVE_UP_DAYS:
                history["actualsComplete"] = True
                history["lastActualStatus"] = history.get("lastActualStatus", "") + " · dừng thử sau 7 ngày"
                pack["history"] = history
                _write_json(path, pack)
                continue
            last = history.get("lastActualAttempt")
            if last and (now - datetime.fromisoformat(last)).total_seconds() < ACTUAL_RETRY_SECONDS:
                continue
            candidates.append(monday)
            break  # one page per run: the browser is slow and the site rate-limits
    for monday in candidates:
        monday, _ = week_bounds(monday)
        path = archive_dir / f"{monday.isoformat()}.json"
        pack, result = backfill_week(_read_json(path), monday, reader=past_reader, build=build, now=now)
        if pack is not None:
            pack.setdefault("history", {})["actualsComplete"] = actuals_complete(pack)
            _write_json(path, pack)
        results[monday.isoformat()] = result
    return {"packs": packs, "backfill": results, "customs": customs_status}

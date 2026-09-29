from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(StrictModel):
    id: str
    url: str
    published_at: AwareDatetime
    retrieved_at: AwareDatetime
    text: str = ""
    sha256: str = ""
    kind: Literal["news", "market", "derived"] = "market"


class Point(StrictModel):
    at: AwareDatetime
    value: Decimal


class Observation(StrictModel):
    id: str
    label: str
    value: Decimal
    unit: str
    source_id: str
    trading_date: date
    basis: str
    tenor: str | None = None
    daily_pct: Decimal | None = None
    annual_pct: Decimal | None = None
    annual_basis: Literal["YoY", "YTD"] | None = None
    series: list[Point] = Field(default_factory=list)


class Issue(StrictModel):
    severity: Literal["error", "warning"]
    code: str
    message: str


class Snapshot(StrictModel):
    version: Literal[1] = 1
    purpose: Literal["live", "fixture"] = "live"
    as_of: AwareDatetime
    sources: dict[str, Source] = Field(default_factory=dict)
    observations: dict[str, Observation] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)

    def add_issue(self, code: str, message: str, severity="warning"):
        self.issues.append(Issue(code=code, message=message, severity=severity))


class Section(StrictModel):
    paragraphs: list[str] = Field(min_length=1, max_length=3)
    source_ids: list[str] = Field(min_length=1)


class ReportContent(StrictModel):
    highlights: list[Section] = Field(min_length=3, max_length=3)
    interbank: Section
    usd_vnd: Section
    eur_usd: Section
    japan: Section
    china: Section
    coffee: Section
    energy_metals: Section
    # Forecasts deliberately absent: user suspended forecasts on 2026-09-27.


def previous_weekday(day: date) -> date:
    from datetime import timedelta

    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def business_days_between(start: date, end: date) -> int:
    from datetime import timedelta

    if start >= end:
        return 0
    cur = start + timedelta(days=1)
    count = 0
    while cur <= end:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


def parse_as_of(value: str | None) -> datetime:
    from zoneinfo import ZoneInfo

    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    if not value:
        return datetime.now(vn)
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("--as-of requires a UTC offset, e.g. 2026-09-24T12:00:00+07:00")
    return result.astimezone(vn)


def create_draft_placeholder_content(snapshot: Snapshot, reason: str = "") -> ReportContent:
    """Creates a draft placeholder content when AI generation is incomplete or encounters service errors.
    
    Preserves all verified market data, tables, and charts while clearly marking commentary as pending.
    """
    ref_src = ["vira"] if "vira" in snapshot.sources else (list(snapshot.sources.keys())[:1] if snapshot.sources else ["manual"])
    return ReportContent(
        highlights=[
            Section(paragraphs=["[Bản nháp kỹ thuật: Nhận định thị trường nổi bật đang chờ cập nhật.]"], source_ids=ref_src),
            Section(paragraphs=["[Dữ liệu vĩ mô và lãi suất thị trường được đối soát tự động từ nguồn VIRA và các sở giao dịch.]"], source_ids=ref_src),
            Section(paragraphs=["[Bản tin đang ở chế độ bản nháp; phần nhận xét phân tích sẽ được hoàn thiện trong phiên tiếp theo.]"], source_ids=ref_src),
        ],
        interbank=Section(
            paragraphs=["[Phần nhận xét thị trường tiền tệ liên ngân hàng chưa hoàn tất. Vui lòng tham khảo bảng lãi suất VNIBOR và tỷ giá đã được cập nhật đầy đủ.]"],
            source_ids=ref_src,
        ),
        usd_vnd=Section(
            paragraphs=["[Phần nhận xét tỷ giá USD-VND chưa hoàn tất. Vui lòng tham khảo bảng tỷ giá trung tâm SBV và giá niêm yết MBBank.]"],
            source_ids=ref_src,
        ),
        eur_usd=Section(
            paragraphs=[
                "[Phần nhận xét thị trường ngoại hối EUR-USD chưa hoàn tất.]",
                "Về phía Châu Âu, [Phần nhận định kinh tế Châu Âu đang chờ cập nhật.]",
            ],
            source_ids=ref_src,
        ),
        japan=Section(
            paragraphs=["[Phần nhận xét thị trường Nhật Bản chưa hoàn tất. Vui lòng tham khảo diễn biến tỷ giá USD-JPY và chỉ số Nikkei 225 trên bảng số liệu.]"],
            source_ids=ref_src,
        ),
        china=Section(
            paragraphs=["[Phần nhận xét thị trường Trung Quốc chưa hoàn tất. Vui lòng tham khảo tỷ giá USD-CNY trên bảng số liệu.]"],
            source_ids=ref_src,
        ),
        coffee=Section(
            paragraphs=["Cập nhật giá cà phê thế giới, [Phần nhận xét thị trường cà phê chưa hoàn tất. Vui lòng tham khảo bảng giá hàng hóa nông sản.]"],
            source_ids=ref_src,
        ),
        energy_metals=Section(
            paragraphs=[
                "[Phần nhận xét thị trường năng lượng dầu Brent chưa hoàn tất.]",
                "[Phần nhận định thị trường vàng chưa hoàn tất. Vui lòng tham khảo bảng giá kim loại quý.]",
            ],
            source_ids=ref_src,
        ),
    )


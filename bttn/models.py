from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


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
    text_scope: Literal["article", "excerpt", "headline", "unknown"] = "unknown"


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
    prev_value: Decimal | None = None
    prev_trading_date: date | None = None
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


class SentenceReference(StrictModel):
    paragraph: int = Field(ge=0)
    source_id: str
    quote: str = Field(min_length=1)


class Section(StrictModel):
    paragraphs: list[str] = Field(min_length=1, max_length=3)
    source_ids: list[str] = Field(min_length=1)
    sentence_refs: list[SentenceReference] = Field(default_factory=list)


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
    if not value:
        return datetime.now(VN_TZ)
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("--as-of requires a UTC offset, e.g. 2026-09-24T12:00:00+07:00")
    return result.astimezone(VN_TZ)


def create_draft_placeholder_content(snapshot: Snapshot, reason: str = "") -> ReportContent:
    """Creates pending notices when translation or Python editing is incomplete.
    
    Preserves all verified market data, tables, and charts while clearly marking commentary as pending.
    """
    from .fx_editorial import fx_opening

    ref_src = ["vira"] if "vira" in snapshot.sources else (list(snapshot.sources.keys())[:1] if snapshot.sources else ["manual"])
    src_eur = [s for s in ["yf_EURUSD", "market", "vira"] if s in snapshot.sources] or ref_src
    src_jpy = [s for s in ["yf_USDJPY", "market", "vira"] if s in snapshot.sources] or ref_src

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
                f"{fx_opening(snapshot, 'eur_usd')} [Phần nhận xét thị trường ngoại hối EUR-USD đang chờ cập nhật.]",
                "Về phía Châu Âu, [Phần nhận định kinh tế Châu Âu đang chờ cập nhật.]",
            ],
            source_ids=src_eur,
        ),
        japan=Section(
            paragraphs=[f"{fx_opening(snapshot, 'japan')} [Phần nhận xét thị trường Nhật Bản đang chờ cập nhật.]"],
            source_ids=src_jpy,
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


def sanitize_content_for_draft_render(
    content: ReportContent,
    snapshot: Snapshot,
    content_issues: list[Issue] | None = None,
) -> ReportContent:
    """Sanitizes content for Word/PDF rendering when content has validation issues or unknown placeholders.
    Sections with placeholder, language or technical leakage errors are replaced by clean draft pending notices,
    preventing renderer crashes while preserving all verified tables and charts in the draft document.
    """
    from .fx_editorial import fx_opening
    from .validation import count_words, highlight_limits, resolve, rules

    ref_src = ["vira"] if "vira" in snapshot.sources else (list(snapshot.sources.keys())[:1] if snapshot.sources else ["manual"])
    cloned = content.model_copy(deep=True)
    issue_messages = []
    for issue in content_issues or []:
        name = issue.message.split(":", 1)[0]
        if issue.code == "WORD_COUNT":
            section = content.highlights[int(name.split("_")[-1])] if name.startswith("highlight_") else getattr(content, name)
            high = highlight_limits(int(name.split("_")[-1]))[1] if name.startswith("highlight_") else rules()["word_limits"][name][1]
            if count_words(resolve(" ".join(section.paragraphs), snapshot, safe=True)) <= high:
                continue  # Preserve verified underlength prose for draft review.
        issue_messages.append(issue.message)

    def is_safe_text(t: str) -> bool:
        try:
            res = resolve(t, snapshot, safe=False)
            return bool(res)
        except Exception:
            return False

    for sec_name in ["interbank", "usd_vnd", "eur_usd", "japan", "china", "coffee", "energy_metals"]:
        sec = getattr(cloned, sec_name)
        has_issue = any(msg.startswith(sec_name) for msg in issue_messages)
        if not has_issue:
            for p in sec.paragraphs:
                if not is_safe_text(p):
                    has_issue = True
                    break
        if has_issue:
            label = sec_name.replace("_", "-").upper()
            if sec_name == "coffee":
                sec.paragraphs = [
                    "Cập nhật giá cà phê thế giới, [Phần nhận xét thị trường cà phê chưa hoàn tất do lỗi kiểm định nội dung. Bảng số liệu đã được đối soát đầy đủ.]"
                ]
            elif sec_name == "eur_usd":
                sec.paragraphs = [
                    f"{fx_opening(snapshot, 'eur_usd')} [Phần nhận xét chi tiết chưa hoàn tất do lỗi kiểm định nội dung.]",
                    "Về phía Châu Âu, [Phần nhận định kinh tế Châu Âu đang chờ cập nhật.]",
                ]
            elif sec_name == "japan":
                sec.paragraphs = [
                    f"{fx_opening(snapshot, 'japan')} [Phần nhận xét chi tiết chưa hoàn tất do lỗi kiểm định nội dung.]"
                ]
            elif sec_name == "energy_metals":
                sec.paragraphs = [
                    "[Phần nhận xét năng lượng dầu Brent chưa hoàn tất do lỗi kiểm định nội dung.]",
                    "[Phần nhận định thị trường vàng đang chờ cập nhật. Vui lòng tham khảo bảng giá kim loại quý.]",
                ]
            else:
                sec.paragraphs = [
                    f"[Phần nhận xét {label} chưa hoàn tất do lỗi kiểm định nội dung. Vui lòng tham khảo bảng số liệu đã được đối soát chuẩn xác.]"
                ]

    # Highlights
    new_highlights = []
    for idx, h in enumerate(cloned.highlights):
        has_issue = any(msg.startswith(f"highlight_{idx}") for msg in issue_messages)
        if not has_issue:
            for p in h.paragraphs:
                if not is_safe_text(p):
                    has_issue = True
                    break
        if has_issue:
            new_highlights.append(
                Section(
                    paragraphs=["[Bản nháp: Tiêu điểm thị trường đang chờ hoàn tất biên tập.]"],
                    source_ids=ref_src,
                )
            )
        else:
            new_highlights.append(h)
    cloned.highlights = new_highlights
    return cloned


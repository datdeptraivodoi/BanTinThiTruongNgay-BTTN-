"""Nội dung một bản tin tuần (một ngôn ngữ). Định dạng do file mẫu quyết định, không nằm ở đây."""
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Section(Strict):
    body: list[str] = Field(default_factory=list)   # mỗi phần tử là một đoạn
    forecast: str = ""                               # đoạn đỏ đậm "Dự báo:/Forecast:"


class Indicators(Strict):
    headers: list[str] = Field(min_length=3, max_length=3)       # 2 kỳ gần nhất + cột dự báo
    rows: list[list[str]] = Field(min_length=6, max_length=6)    # 6 dòng x 3 giá trị


class SmallTable(Strict):
    values: list[str] = Field(min_length=3, max_length=3)
    changes: list[str] = Field(min_length=3, max_length=3)


class ChartData(Strict):
    categories: list[str]
    series: list[list[float | None]]   # theo đúng thứ tự series trong biểu đồ mẫu


class CalendarEvent(Strict):
    date: str = ""          # để trống nếu cùng ngày với dòng trên
    event: str = ""
    country: str = ""
    forecast: str = ""
    previous: str = ""
    bold: bool = False


class Calendar(Strict):
    title: str
    events: list[CalendarEvent] = Field(max_length=15)


class WeeklyContent(Strict):
    lang: str
    week_label: str
    sections: dict[str, Section]
    news: list[str]
    indicators: Indicators
    small_tables: dict[str, SmallTable]
    charts: dict[str, ChartData] = Field(default_factory=dict)
    images: dict[str, str] = Field(default_factory=dict)   # tên ô -> đường dẫn ảnh PNG
    calendar: Calendar

"""Câu thị trường trái phiếu cuối mục liên ngân hàng (góp ý 19.10), theo bản tin mẫu:

    "Trên thị trường trái phiếu, lợi tức kỳ hạn 7 & 10 năm đi ngang, quanh mức 4,2%-4,75%, thanh khoản vừa."

- Lợi suất TPCP 7 và 10 năm: câu "Lợi suất TPCP trên thị trường thứ cấp …, chốt phiên: 3Y …; 7Y 4,34%; 10Y 4,46%"
  trong "Bản tin Kinh tế - Tài chính ngày" của VIRA (đăng thứ Ba – thứ Sáu cho phiên liền trước). Thứ Hai VIRA chỉ
  đăng bản tổng hợp tuần (không có số), nên dùng bản tin ngày mới nhất (thứ Sáu, phiên thứ Năm). Diễn biến so với
  bản tin ngày liền trước đó.
- Thanh khoản: giá trị giao dịch outright bình quân ngày trong báo cáo tuần mới nhất của VBMA (đăng chậm khoảng
  1 tuần), xếp mức thấp / vừa / cao theo ngưỡng "bond_liquidity" trong config/editorial_rules.json. Email review
  ghi rõ số liệu và tuần báo cáo để người duyệt kiểm tra.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .models import VN_TZ, Observation, Source
from .weekly.bond_yields import VIRA_SENTENCE, parse_vira_yields, previous_workday
from .weekly.vbma import LIST_URL as VBMA_LIST
from .weekly.vbma import list_reports

LOG = logging.getLogger("bttn.bonds")
VIRA_DAILY_LIST = "https://vira.org.vn/tin/Ban-tin-Kinh-te-Tai-chinh-ngay.html"
BOND_KEYS = {"7N": "BOND_VN_7Y", "10N": "BOND_VN_10Y"}
OUTRIGHT = re.compile(r"GTGD\s+outright\s+trung\s+bình\s+ngày\s+(?:là|đạt)\s+([\d.,]+)\s*tỷ\s+đồng\s*"
                      r"\(\s*(tăng|giảm)\s+([\d.,]+)\s*%", re.I)
DEFAULT_LIQUIDITY = {"low": 10000, "high": 20000}     # tỷ đồng/ngày, GTGD outright bình quân


def _number(text: str) -> Decimal:
    """'14,475' / '14.475' (phân cách nghìn) hoặc '10.6' / '10,6' (thập phân)."""
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", text):
        return Decimal(re.sub(r"[.,]", "", text))
    return Decimal(text.replace(",", "."))


def parse_outright(text: str):
    """(GTGD outright bình quân ngày, % thay đổi so với kỳ trước có dấu) từ chữ của báo cáo tuần VBMA."""
    m = OUTRIGHT.search(" ".join(text.split()))
    if not m:
        return None
    pct = _number(m.group(3))
    return _number(m.group(1)), (-pct if m.group(2).lower() == "giảm" else pct)


def liquidity_label(value: Decimal, limits: dict | None = None) -> str:
    limits = {**DEFAULT_LIQUIDITY, **(limits or {})}
    if value < limits["low"]:
        return "thấp"
    if value > limits["high"]:
        return "cao"
    return "vừa"


def _vira_bulletins(http, as_of, count=2):
    """[(ngày đăng, url, lợi suất theo kỳ hạn, câu gốc)] của các bản tin ngày VIRA mới nhất, đăng trong 6 ngày."""
    soup = BeautifulSoup(http.get(VIRA_DAILY_LIST).text, "html.parser")
    links = []
    for a in soup.find_all("a", href=re.compile(r"/tin/Ban-tin-Kinh-te-Tai-chinh-ngay/Ban-tin.*\.html")):
        url = urljoin(VIRA_DAILY_LIST, a["href"])
        if urlparse(url).hostname == "vira.org.vn" and url not in links:
            links.append(url)
    from .sources import article_time

    out = []
    for url in links[:6]:
        detail = BeautifulSoup(http.get(url).text, "html.parser")
        at = article_time(detail)
        if at > as_of:
            continue
        if as_of - at > timedelta(days=6) and not out:
            break
        body = (detail.select_one("#abody") or detail).get_text(" ", strip=True)
        row = parse_vira_yields(body)
        if all(t in row for t in BOND_KEYS):
            m = VIRA_SENTENCE.search(" ".join(body.split()))
            out.append((at, url, row, m.group(0) if m else ""))
        if len(out) >= count:
            break
    return out


def collect_bond_market(http, snapshot):
    """Lợi suất TPCP 7/10 năm (VIRA) và thanh khoản thứ cấp (VBMA) cho câu trái phiếu mục liên ngân hàng."""
    try:
        bulletins = _vira_bulletins(http, snapshot.as_of)
    except Exception as exc:  # noqa: BLE001
        bulletins = []
        LOG.debug("VIRA bond yields failed: %s", type(exc).__name__)
    if not bulletins:
        snapshot.add_issue("BOND_YIELDS_MISSING", "Không đọc được lợi suất TPCP 7–10 năm từ bản tin ngày của VIRA; "
                                                  "mục liên ngân hàng không có câu trái phiếu.")
    else:
        at, url, row, sentence = bulletins[0]
        prev = bulletins[1] if len(bulletins) > 1 else None
        snapshot.sources["vira_bonds"] = Source(
            id="vira_bonds", url=url, published_at=at, retrieved_at=datetime.now(timezone.utc), text=sentence,
            sha256=hashlib.sha256(sentence.encode()).hexdigest(), kind="market", text_scope="excerpt")
        session = previous_workday(at.astimezone(VN_TZ).date())
        for tenor, key in BOND_KEYS.items():
            snapshot.observations[key] = Observation(
                id=key, label=f"Lợi suất TPCP {tenor.removesuffix('N')} năm", value=Decimal(str(row[tenor])),
                unit="%", source_id="vira_bonds", trading_date=session, tenor=tenor.replace("N", "Y"),
                basis="Lợi suất TPCP thứ cấp chốt phiên theo bản tin ngày VIRA",
                prev_value=Decimal(str(prev[2][tenor])) if prev else None,
                prev_trading_date=previous_workday(prev[0].astimezone(VN_TZ).date()) if prev else None)
    try:
        _collect_vbma_liquidity(http, snapshot)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("VBMA liquidity failed: %s", type(exc).__name__)
    if "vbma_bonds" not in snapshot.sources:
        snapshot.add_issue("BOND_LIQUIDITY_MISSING", "Không đọc được giá trị giao dịch TPCP từ báo cáo tuần VBMA; "
                                                     "câu trái phiếu không nêu thanh khoản.")


def pdf_text(data: bytes) -> str:
    import pymupdf

    with pymupdf.open(stream=io.BytesIO(data), filetype="pdf") as doc:
        return " ".join(page.get_text() for page in doc)


def _collect_vbma_liquidity(http, snapshot):
    reports = [r for r in list_reports(http.get(VBMA_LIST).text) if r.end <= snapshot.as_of.astimezone(VN_TZ).date()]
    for rep in reports[:2]:
        parsed = parse_outright(pdf_text(http.get(rep.url).content))
        if not parsed:
            continue
        value, pct = parsed
        body = (f"GTGD outright trung bình ngày tuần {rep.start:%d/%m} - {rep.end:%d/%m/%Y}: {value} tỷ đồng "
                f"({'+' if pct >= 0 else ''}{pct}% so với tuần trước)")
        published = datetime.combine(rep.end, time(17), VN_TZ)
        snapshot.sources["vbma_bonds"] = Source(
            id="vbma_bonds", url=rep.url, published_at=min(published, snapshot.as_of),
            retrieved_at=datetime.now(timezone.utc), text=body, sha256=hashlib.sha256(body.encode()).hexdigest(),
            kind="market", text_scope="excerpt")
        return


def outright_from_source(snapshot):
    src = snapshot.sources.get("vbma_bonds")
    if not src:
        return None
    m = re.search(r":\s*([\d.]+) tỷ đồng \(([+-]?[\d.]+)%", src.text)
    return (Decimal(m.group(1)), Decimal(m.group(2))) if m else None

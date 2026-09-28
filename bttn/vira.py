"""Read dated VIRA editions and extract their published image tables locally.

No LLM invents or repairs market numbers. Two OCR passes must agree on each
used rate/yield cell. Unreadable cells are missing data, not guessed values.
"""
import hashlib
import io
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .models import Observation, Snapshot, Source

INDEX = "https://vira.org.vn/tin/Market-Watch.html"
VN = ZoneInfo("Asia/Ho_Chi_Minh")
TENORS = {"ON", "1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y", "SW"}


def editions(html: str, as_of: datetime) -> list[tuple[datetime, str]]:
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for tag in soup.select("time"):
        raw = tag.get("datetime", "")
        try:
            published = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S GMT+7").replace(tzinfo=VN)
        except ValueError:
            continue
        parent = tag.parent
        for _ in range(4):
            if parent is None:
                break
            link = parent.select_one('a[href*="/Market-Watch/Market-Watch-"]')
            if link:
                url = urljoin(INDEX, link["href"])
                if published <= as_of and urlparse(url).hostname == "vira.org.vn":
                    found[url] = published
                break
            parent = parent.parent
    return sorted([(d, u) for u, d in found.items()], reverse=True)


def article_images(html: str) -> tuple[datetime, list[tuple[str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("meta", property="article:published_time")
    if not tag:
        raise ValueError("VIRA article has no publication timestamp")
    published = datetime.fromisoformat(tag["content"])
    body = soup.select_one("#abody")
    if body is None:
        raise ValueError("VIRA article body not found")
    section = ""
    images = []
    for item in body.find_all(["li", "img"]):
        if item.name == "li":
            section = item.get_text(" ", strip=True).upper()
        elif section in {"MONEY MARKET", "BOND"}:
            url = urljoin(INDEX, item.get("src", ""))
            if urlparse(url).hostname != "static.vira.org.vn":
                raise ValueError("Unexpected VIRA image host")
            images.append((section, url))
    if len(images) < 3:
        raise ValueError("Missing VIRA money market or bond image tables")
    return published, images


def number(text: str) -> Decimal:
    # Exact decimal punctuation is required; never silently repair OCR digits.
    text = text.strip().replace(" ", "").replace("−", "-")
    if not re.fullmatch(r"-?\d{1,2}[,.]\d{2}", text):
        raise ValueError(f"Ambiguous rate cell: {text!r}")
    return Decimal(text.replace(",", "."))


def parse_tokens(tokens: list, width: int, height: int, kind: str, published: datetime):
    boxes = []
    for box, text, confidence in tokens:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        boxes.append((sum(xs) / len(xs) / width, sum(ys) / len(ys) / height,
                      text.strip(), confidence))
    boxes.sort(key=lambda b: b[1])
    parsed = {}
    if kind == "MONEY MARKET":
        headers = [(y, "SOFR" if "SOFR" in t.upper() else "USD" if "USD" in t.upper() else "VND")
                   for x, y, t, c in boxes if x < .18 and any(k in t.upper() for k in ["SOFR", "USD", "VND"])]
        if not headers:
            raise ValueError("Unrecognized VIRA currency header")
        dates = re.findall(r"\b(\d{2})[-/](\d{2})[-/](\d{2,4})\b", " ".join(b[2] for b in boxes if b[1] < .19))
        observation_date = published.date()
        if dates:
            d, m, y = dates[0]
            observation_date = datetime(int(y) + (2000 if len(y) == 2 else 0), int(m), int(d)).date()
        for x, y, text, confidence in boxes:
            tenor = text.upper().replace(" ", "")
            if tenor not in TENORS or not .055 < x < .18 or confidence < .75:
                continue
            preceding = [h for h in headers if h[0] < y]
            if not preceding:
                continue
            group = preceding[-1][1]
            # First numeric data column, after STT and tenor. Fail on ambiguity.
            values = [(t, c) for xx, yy, t, c in boxes if .18 < xx < .26 and abs(yy - y) < .012]
            if len(values) != 1 or values[0][1] < .85:
                continue
            try:
                value = number(values[0][0])
            except ValueError:
                continue
            key = f"{group}_{tenor}"
            if key in parsed:
                raise ValueError(f"Duplicate OCR row {key}")
            parsed[key] = (value, tenor, observation_date,
                           "dated fixing" if dates else "Last as published in this VIRA edition; no explicit fixing date")
    elif kind == "BOND":
        if not any("10Y" in b[2].upper().replace(" ", "") for b in boxes):
            raise ValueError("VIRA bond table is not labelled 10Y")
        countries = ["Vietnam", "United States", "United Kingdom", "Germany", "France", "Japan",
                     "China", "India", "Australia", "Korea", "Thailand", "Indonesia", "Philippines",
                     "Malaysia", "Singapore", "Austria", "Brazil", "Mexico", "Spain", "Switzerland"]
        for x, y, text, confidence in boxes:
            match = next((c for c in countries if c.lower() == text.lower()), None)
            if not match or not .055 < x < .215 or confidence < .85:
                continue
            values = [(t, c) for xx, yy, t, c in boxes if .22 < xx < .31 and abs(yy - y) < .009]
            if len(values) != 1 or values[0][1] < .85:
                continue
            try:
                value = number(values[0][0])
            except ValueError:
                continue
            parsed[f"BOND_{match}"] = (value, "10Y", published.date(), "10Y Closed as published in VIRA edition")
    return parsed


class ImageReader:
    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR

        self.engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)

    def read(self, payload: bytes, kind: str, published: datetime, archive: Path):
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(payload)).convert("RGB")
        passes = []
        for index, (scale, crop) in enumerate([(2, False), (3, False), (3, True), (4, True)]):
            region = image.crop((0, 0, int(image.width * (.27 if kind == "MONEY MARKET" else .315)), image.height)) if crop else image
            enlarged = region.resize((region.width * scale, region.height * scale))
            tokens, _ = self.engine(np.array(enlarged))
            tokens = tokens or []
            archive.with_suffix(f".ocr{index}.json").write_text(json.dumps(tokens), encoding="utf-8")
            passes.append(parse_tokens(tokens, image.width * scale, image.height * scale, kind, published))
        accepted = {}
        for key in set().union(*(p.keys() for p in passes)):
            votes = [p[key] for p in passes if key in p]
            if len(votes) >= 2 and all(value == votes[0] for value in votes):
                accepted[key] = votes[0]
        return accepted


def collect_vira(http, snapshot: Snapshot, reader=None):
    candidates = editions(http.get(INDEX).text, snapshot.as_of)
    if not candidates:
        raise ValueError("No VIRA edition at or before report cutoff")
    _, article_url = candidates[0]
    response = http.get(article_url)
    published, images = article_images(response.text)
    if published > snapshot.as_of:
        raise ValueError("VIRA edition was published after cutoff")
    snapshot.sources["vira"] = Source(id="vira", url=article_url, published_at=published,
        retrieved_at=datetime.now(VN), text="VIRA Market Watch / MSB Research; tables are archived as source images.")
    reader = reader or ImageReader()
    for index, (kind, url) in enumerate(images):
        payload = http.get(url).content
        path = http.directory / f"vira-{index}.png"
        path.write_bytes(payload)
        sid = f"vira_{index}"
        rows = reader.read(payload, kind, published, path)
        snapshot.sources[sid] = Source(id=sid, url=url, published_at=published,
            retrieved_at=datetime.now(VN), sha256=hashlib.sha256(payload).hexdigest(),
            text="\n".join(f"{k}: {v[0]} %/năm ({v[3]})" for k, v in rows.items()))
        for key, (value, tenor, day, basis) in rows.items():
            if not Decimal("-10") <= value <= Decimal("50"):
                raise ValueError(f"Rate/yield outside permitted range: {key}")
            if key in snapshot.observations:
                raise ValueError(f"Conflicting VIRA row: {key}")
            snapshot.observations[key] = Observation(id=key, label=key.replace("_", " "), value=value,
                unit="%/năm", source_id=sid, trading_date=day, tenor=tenor, basis=basis)
    snapshot.add_issue("SWAP_REFERENCE", "Swap = VNIBOR VND − VNIBOR USD cùng kỳ hạn/cùng ấn bản VIRA; USD Last không ghi ngày fixing. Đây là chênh lệch lãi suất tham khảo, không phải giá swap mua/bán.")

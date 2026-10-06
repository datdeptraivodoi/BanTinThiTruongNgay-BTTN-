"""Country news via the official TE API or explicitly imported article records.

The website currently denies Python clients (403). Do not bypass that denial or
substitute search-result headlines for article bodies.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .models import Source

COUNTRIES = {"euro area": "EUR", "united states": "USD", "japan": "JPY", "china": "China"}
PATHS = {"euro-area": "EUR", "united-states": "USD", "japan": "JPY", "china": "China"}
NEWS_PAGES = {topic: f"https://tradingeconomics.com/{path}/news" for path, topic in PATHS.items()}
MAX_ARTICLES_PER_COUNTRY = 3


def topic_for(source):
    parsed = urlparse(source.url)
    if parsed.scheme != "https" or parsed.hostname != "tradingeconomics.com":
        return None
    return PATHS.get(parsed.path.strip("/").split("/")[0])


def parse_news(records, as_of, *, allow_stale=False):
    """API dates are UTC per TE docs. Filter future news before translation."""
    if not isinstance(records, list):
        raise ValueError("TE_NEWS_RESPONSE_INVALID")
    accepted = {}
    rejected = []
    for record in records:
        try:
            item = {k.lower(): v for k, v in record.items()}
            topic = COUNTRIES[item["country"].lower()]
            at = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            url = urljoin("https://tradingeconomics.com", item["url"])
            url_parts = urlparse(url)
            if url_parts.scheme != "https" or url_parts.hostname != "tradingeconomics.com":
                raise ValueError("TE_NEWS_URL")
            if url_parts.query or url_parts.username or url_parts.password:
                raise ValueError("TE_NEWS_URL")
            title = BeautifulSoup(item["title"], "html.parser").get_text(" ", strip=True)
            body = BeautifulSoup(item["description"], "html.parser").get_text(" ", strip=True)
            if len(body.split()) < 20 or not title:
                raise ValueError("TE_NEWS_BODY_MISSING")
            age = as_of - at
            if age < timedelta(0):
                raise ValueError("TE_NEWS_AFTER_CUTOFF")
            if age > timedelta(hours=36) and not allow_stale:
                raise ValueError("TE_NEWS_STALE")
            identity = str(item.get("id") or url)
            sid = "news_te_" + topic + "_" + hashlib.sha256(identity.encode()).hexdigest()[:12]
            scope = item.get("text_scope", "article")
            if scope not in ("article", "excerpt"):
                raise ValueError("TE_NEWS_BODY_MISSING")
            source = Source(id=sid, url=url, published_at=at, retrieved_at=datetime.now(timezone.utc),
                            text=title + "\n\n" + body, sha256=hashlib.sha256((title + "\n\n" + body).encode()).hexdigest(),
                            kind="news", text_scope=scope)
            if topic_for(source) != topic:
                raise ValueError("TE_NEWS_COUNTRY_URL_MISMATCH")
            accepted[sid] = source
        except (KeyError, TypeError, AttributeError):
            rejected.append({"code": "TE_NEWS_FIELDS"})
        except ValueError as exc:
            code = str(exc) if str(exc).startswith("TE_NEWS_") else "TE_NEWS_DATE"
            rejected.append({"code": code})
    # A few distinct articles provide currency and macro context. Never translate
    # an unbounded country archive; keep the newest eligible bodies first.
    selected = {}
    for topic in COUNTRIES.values():
        matching = [s for s in accepted.values() if topic_for(s) == topic]
        seen = set()
        for source in sorted(matching, key=lambda s: (s.published_at, s.id), reverse=True):
            if source.sha256 in seen:
                continue
            seen.add(source.sha256)
            selected[source.id] = source
            if len(seen) >= MAX_ARTICLES_PER_COUNTRY:
                break
    return selected, rejected


def collect_tradingeconomics(http, snapshot, *, import_file=None, allow_stale=False):
    """Archive only public article data, never the credential-bearing API request."""
    import_file = import_file or os.getenv("TE_NEWS_IMPORT_PATH")
    if import_file:
        records = json.loads(Path(import_file).read_text(encoding="utf-8-sig"))
    else:
        key = os.getenv("TRADINGECONOMICS_API_KEY", "").strip()
        if not key:
            snapshot.add_issue("TE_NEWS_ACCESS", "Trading Economics: chưa có API nguồn tin hoặc file nhập bài; website trả 403.")
            return
        start = snapshot.as_of - timedelta(hours=36)
        try:
            response = requests.get(
                "https://api.tradingeconomics.com/news/country/euro%20area,united%20states,japan,china",
                params={"c": key, "d1": start.date().isoformat(), "d2": snapshot.as_of.date().isoformat(),
                        "f": "json"}, timeout=(10, 25), allow_redirects=False,
            )
            if response.status_code != 200:
                raise RuntimeError(f"TE_NEWS_HTTP_{response.status_code}")
            records = response.json()
        except requests.RequestException:
            raise RuntimeError("TE_NEWS_NETWORK") from None
        except ValueError:
            raise RuntimeError("TE_NEWS_RESPONSE_INVALID") from None
    selected, rejected = parse_news(records, snapshot.as_of, allow_stale=allow_stale)
    snapshot.sources.update(selected)
    present = {topic_for(s) for s in selected.values()}
    for topic in COUNTRIES.values():
        if topic not in present:
            snapshot.add_issue("TE_NEWS_MISSING", f"{topic}: không có toàn văn tin đủ điều kiện trước giờ chốt.")
    if rejected:
        snapshot.add_issue("TE_NEWS_REJECTED", f"Trading Economics: loại {len(rejected)} bản ghi sai ngày, thiếu nội dung hoặc nguồn.")
    if http is not None:
        path = http.directory / "tradingeconomics-news.json"
        path.write_text(json.dumps({"sources": {k: s.model_dump(mode="json") for k, s in selected.items()},
                                    "rejected": rejected}, ensure_ascii=False, indent=2), encoding="utf-8")

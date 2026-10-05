"""Discover, extract and select dated publisher articles without an LLM."""
import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from functools import lru_cache
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup

from .models import Source, previous_weekday

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def config():
    return json.loads((ROOT / "config/news_rules.json").read_text(encoding="utf-8"))


def normalized(text):
    # Some publisher listing attributes double-escape Vietnamese characters.
    return " ".join(unicodedata.normalize("NFC", unescape(unescape(text))).casefold().split())


def contains(text, term):
    return bool(re.search(r"(?<!\w)" + re.escape(normalized(term)) + r"(?!\w)", normalized(text)))


def window_start(as_of):
    from .validation import rules
    local = as_of.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    preceding = previous_weekday(local.date())
    # Include the preceding completed weekday over weekends. This is a news
    # window, not a holiday-aware exchange trading calendar.
    weekday_start = datetime.combine(preceding, time(0), tzinfo=local.tzinfo)
    return min(local-timedelta(hours=rules()["news_max_age_hours"]), weekday_start)


def fresh(published, as_of):
    return window_start(as_of) <= published <= as_of


def canonical_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("INVALID_URL")
    host = parts.hostname.casefold().removeprefix("www.")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.casefold().startswith("utm_") and k.casefold() not in {"fbclid", "gclid", "_ga"}]
    return urlunsplit(("https", host, parts.path or "/", urlencode(sorted(query)), ""))


def publisher_allowed(url):
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.").casefold() in config()["allowed_publishers"]


def rss_publication_date(raw):
    # VietnamBiz writes an explicit GMT+7 suffix rather than RFC +0700. The
    # stdlib silently returns a naive date for it; preserve the stated offset.
    raw = raw.strip()
    offset = re.search(r"GMT([+-])(\d{1,2})$", raw)
    if offset:
        hours = int(offset[2])
        if hours > 23:
            raise ValueError("INVALID_RSS_OFFSET")
        raw = raw[:offset.start()] + f"{offset[1]}{hours:02d}00"
    date = parsedate_to_datetime(raw)
    if date.tzinfo is None:
        raise ValueError("PUBLICATION_TIME_NO_TIMEZONE")
    return date


def article_quality(source):
    cfg = config()
    if source.content_scope == "headline":
        return "HEADLINE_ONLY"
    if not source.text.strip():
        return "EMPTY_BODY"
    if len(source.text) < cfg["minimum_characters"] or len(source.text.split()) < cfg["minimum_words"]:
        return "SHORT_BODY"
    if len(source.text) > cfg["maximum_article_characters"]:
        return "BODY_TOO_LONG"
    return None


def topic_scores(source):
    # URLs and publisher names are deliberately excluded from topic matching.
    result = {}
    for topic, rule in config()["topics"].items():
        context = source.title + "\n" + source.text
        if rule.get("require_any") and not any(contains(context, term) for term in rule["require_any"]):
            continue
        title = sum(contains(source.title, term) for term in rule["terms"])
        body = sum(contains(source.text, term) for term in rule["terms"])
        if rule.get("currency_terms") and not any(contains(context, term) for term in rule["currency_terms"]):
            continue
        score = title * 3 + body
        if score >= rule.get("minimum_score", 1):
            result[topic] = score
    return result


def ranking(source, topic):
    event_terms = config()["events"]
    event_score = sum(contains(source.title, term) for term in event_terms) * 2
    event_score += sum(contains(source.text, term) for term in event_terms)
    return (topic_scores(source).get(topic, 0), event_score, source.published_at, len(source.text))


def duplicates(left, right):
    try:
        if canonical_url(left.url) == canonical_url(right.url):
            return True
    except ValueError:
        pass
    # Similar titles alone do not discard follow-up stories with materially
    # different bodies. Prefer the fuller story when both title and body agree.
    a, b = normalized(left.text), normalized(right.text)
    if a == b:
        return True
    titles_match = bool(left.title and right.title and
                        SequenceMatcher(None, normalized(left.title), normalized(right.title)).ratio() >= .85)
    body_match = SequenceMatcher(None, a[:12000], b[:12000]).ratio() >= .90
    return body_match and (titles_match or min(len(a), len(b)) >= 1000)


def same_event(left, right):
    # Conservative grouping: same headline/date/numeric facts. Different figures
    # or materially changed headlines remain separate follow-up stories.
    return (bool(left.title and right.title)
            and left.published_at.date() == right.published_at.date()
            and SequenceMatcher(None, normalized(left.title), normalized(right.title)).ratio() >= .9
            and set(re.findall(r"\d+(?:[.,]\d+)*", left.text)) == set(re.findall(r"\d+(?:[.,]\d+)*", right.text)))


def select_articles(snapshot, topic):
    candidates, decisions = [], []
    for source in snapshot.sources.values():
        if source.kind != "news":
            continue
        reason = article_quality(source)
        if not reason and not fresh(source.published_at, snapshot.as_of):
            reason = "FUTURE_ARTICLE" if source.published_at > snapshot.as_of else "STALE_ARTICLE"
        if not reason and topic not in topic_scores(source):
            reason = "UNRELATED_TOPIC"
        if reason:
            decisions.append({"source_id": source.id, "topic": topic, "status": "rejected", "reason": reason})
        else:
            candidates.append(source)
    # Deduplicate before relevance ranking so the shorter copy cannot win.
    unique = []
    for source in sorted(candidates, key=lambda s: (len(s.text), s.published_at), reverse=True):
        duplicate = next((old for old in unique if duplicates(source, old)), None)
        if duplicate:
            decisions.append({"source_id": source.id, "topic": topic, "status": "rejected",
                              "reason": "DUPLICATE_ARTICLE", "duplicate_of": duplicate.id})
        else:
            unique.append(source)
    ordered = sorted(unique, key=lambda s: ranking(s, topic), reverse=True)
    cap = config()["maximum_articles_per_topic"]
    selected = []
    for source in ordered:
        reason = "TOPIC_AND_EVENT_RELEVANCE"
        if sum(same_event(source, old) for old in selected) >= 2:
            reason = "EVENT_LIMIT"
        elif len(selected) >= cap:
            reason = "TOPIC_LIMIT"
        else:
            selected.append(source)
        decisions.append({"source_id": source.id, "topic": topic, "status": "selected" if reason == "TOPIC_AND_EVENT_RELEVANCE" else "rejected",
                          "reason": reason,
                          "score": list(ranking(source, topic)[:2])})
    return selected, decisions


def coverage(snapshot):
    result = {}
    for topic in config()["topics"]:
        articles, _ = select_articles(snapshot, topic)
        result[topic] = {"status": "covered" if articles else "price_data_only_or_missing",
                         "source_ids": [s.id for s in articles], "count": len(articles)}
    return result


def walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def extract_article(html, url, retrieved_at, title_hint="", rss_date=None):
    soup = BeautifulSoup(html, "html.parser")
    structured = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            structured.extend(node for node in walk_json(json.loads(tag.string or tag.get_text()))
                              if any(name in str(node.get("@type", "")) for name in ["NewsArticle", "Article"]))
        except (ValueError, TypeError):
            pass
    meta = soup.find("meta", property="article:published_time") or soup.find("meta", attrs={"itemprop": "datePublished"})
    raw_date = meta.get("content", "") if meta else ""
    raw_date = raw_date or next((n["datePublished"] for n in structured if n.get("datePublished")), "")
    published_from = "publisher"
    if raw_date:
        try:
            published = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("INVALID_PUBLICATION_TIME") from error
        if published.tzinfo is None:
            host = (urlsplit(url).hostname or "").removeprefix("www.")
            zone = config().get("publisher_timezones", {}).get(host)
            if not zone:
                raise ValueError("PUBLICATION_TIME_NO_TIMEZONE")
            published = published.replace(tzinfo=ZoneInfo(zone))
            published_from = "publisher_local_time"
    elif rss_date is not None:
        published, published_from = rss_date, "rss"
    else:
        raise ValueError("MISSING_PUBLICATION_TIME")
    tag = soup.find("h1") or soup.find("meta", property="og:title")
    title = (tag.get("content") or tag.get_text(" ", strip=True)) if tag else title_hint
    title = unescape(unescape(title or next((n.get("headline", "") for n in structured if n.get("headline")), "")))
    if not title:
        raise ValueError("MISSING_TITLE")
    canonical = soup.find("link", rel="canonical")
    if canonical:
        candidate = urljoin(url, canonical.get("href", ""))
        if urlsplit(candidate).hostname == urlsplit(url).hostname:
            url = candidate
    for tag in soup.select("script, style, nav, header, footer, .relate-container, .box-tin-lien-quan, .VnbArticleContentEmbed, .advertisement, .related-articles"):
        tag.decompose()
    bodies = []
    for selector in ["#abody", ".vnbcbc-body", ".detail-content", '[itemprop="articleBody"]', ".article-body", "article"]:
        for body in soup.select(selector):
            paragraphs = [p.get_text(" ", strip=True) for p in body.find_all(["p", "h2", "h3"])]
            bodies.append("\n\n".join(p for p in paragraphs if p) if paragraphs else body.get_text(" ", strip=True))
        if bodies:
            break
    bodies.extend(str(node["articleBody"]) for node in structured if node.get("articleBody"))
    text = unicodedata.normalize("NFC", max(bodies, key=len, default=""))
    url = canonical_url(url)
    source = Source(id="news_" + hashlib.sha256(url.encode()).hexdigest()[:12], url=url, title=title,
                    publisher=urlsplit(url).hostname or "", published_at=published, retrieved_at=retrieved_at,
                    text=text, sha256=hashlib.sha256(text.encode()).hexdigest(), kind="news", content_scope="article",
                    published_from=published_from)
    problem = article_quality(source)
    if problem:
        raise ValueError(problem)
    source.topics = list(topic_scores(source))
    return source


def fetch_article(http, url, title="", rss_date=None):
    """Resolve public Google wrappers using links/redirects; never pass a wrapper as an article."""
    if not hasattr(http, "article_cache"):
        http.article_cache = {}
    key = canonical_url(url)
    if key in http.article_cache:
        return http.article_cache[key]
    response = http.get(url)
    final_url = getattr(response, "url", None) or url
    if (urlsplit(final_url).hostname or "").endswith("news.google.com"):
        soup = BeautifulSoup(response.text, "html.parser")
        links = [urljoin(final_url, a["href"]) for a in soup.select("a[href]")]
        direct = next((link for link in links if publisher_allowed(link) and urlsplit(link).path not in {"", "/"}), None)
        if not direct:
            raise ValueError("PUBLISHER_LINK_UNRESOLVED")
        response = http.get(direct)
        final_url = getattr(response, "url", None) or direct
    if not publisher_allowed(final_url):
        raise ValueError("PUBLISHER_NOT_ALLOWED")
    source = extract_article(response.text, final_url, datetime.now(timezone.utc), title, rss_date)
    http.article_cache[key] = source
    if not hasattr(http, "article_html_cache"):
        http.article_html_cache = {}
    http.article_html_cache[key] = response.text
    return source


def log_path(http):
    return Path(http.directory).parent / "news-decisions.json"


def append_decision(http, row):
    path = log_path(http)
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows.append(row)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_news(http, snapshot):
    cfg = config()
    groups = defaultdict(list)
    days = max(2, (snapshot.as_of-window_start(snapshot.as_of)).days+1)
    domains = " OR ".join("site:" + host for host in cfg["allowed_publishers"])
    feeds = [(topic, "https://news.google.com/rss/search?q=" + quote_plus(f"({domains}) ({rule['query']}) when:{days}d")
              + "&hl=en-US&gl=US&ceid=US:en") for topic, rule in cfg["topics"].items()]
    feeds.extend(("all", url) for url in cfg["direct_feeds"])
    for topic, url in feeds:
        try:
            entries = feedparser.parse(http.get(url).content).entries
            if not entries:
                snapshot.add_issue("NEWS_FEED_EMPTY", f"{topic}: RSS không có bài; tiếp tục các nguồn khác")
                append_decision(http, {"url": url, "topic": topic, "status": "rejected", "reason": "FEED_EMPTY"})
            for entry in entries:
                link, title = entry.get("link", ""), entry.get("title", "")
                try:
                    date = rss_publication_date(entry.get("published", ""))
                    if not fresh(date, snapshot.as_of):
                        raise ValueError("FUTURE_OR_STALE_RSS")
                    targets = [topic] if topic != "all" else [name for name, rule in cfg["topics"].items()
                                                                  if any(contains(title, term) for term in rule["terms"])]
                    if not targets:
                        append_decision(http, {"url": link, "topic": topic, "status": "rejected", "reason": "UNRELATED_DISCOVERY"})
                    for target in targets:
                        groups[target].append((date, link, title))
                except (ValueError, TypeError):
                    append_decision(http, {"url": link, "topic": topic, "status": "rejected", "reason": "INVALID_OR_STALE_RSS_DATE"})
        except Exception as error:
            snapshot.add_issue("NEWS_FEED", f"{topic}: {type(error).__name__}")
            append_decision(http, {"url": url, "topic": topic, "status": "rejected", "reason": "FEED_ERROR"})
    for listing in cfg["direct_listings"]:
        url, topics = listing["url"], [topic for topic in listing["topics"] if topic in cfg["topics"]]
        if not topics:
            continue
        try:
            soup = BeautifulSoup(http.get(url).text, "html.parser")
            for a in soup.select("a[href]"):
                link, title = urljoin(url, a["href"]), a.get("title") or a.get_text(" ", strip=True)
                if urlsplit(link).hostname != urlsplit(url).hostname or "/chu-de/" in link or len(title) < 20:
                    continue
                for topic in topics:
                    if any(contains(title, term) for term in cfg["topics"][topic]["terms"]):
                        groups[topic].append((None, link, title))
        except Exception as error:
            snapshot.add_issue("NEWS_LISTING", f"{','.join(topics)}: {type(error).__name__}")
    shortlist = {}
    for topic, items in groups.items():
        seen = set()
        # A wrapper may be impossible to resolve. Give direct publisher links a
        # chance before exhausting the budget on Google wrappers, then sort by date.
        sorted_items = sorted(items, key=lambda item: (publisher_allowed(item[1]),
                              sum(contains(item[2], term) for term in cfg["events"]),
                              item[0] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        shortlisted = []
        for item in sorted_items:
            try:
                key = canonical_url(item[1])
            except ValueError:
                append_decision(http, {"url": item[1], "topic": topic, "status": "rejected", "reason": "INVALID_URL"})
                continue
            if key not in seen:
                seen.add(key)
                shortlisted.append(item)
            else:
                append_decision(http, {"url": item[1], "topic": topic, "status": "rejected", "reason": "DUPLICATE_DISCOVERY"})
        shortlist[topic] = shortlisted[:cfg["maximum_discovery_per_topic"]]
        for item in shortlisted[cfg["maximum_discovery_per_topic"]:]:
            append_decision(http, {"url": item[1], "topic": topic, "status": "rejected", "reason": "DISCOVERY_LIMIT"})
    visited = set()
    # Round robin gives each topic a fetch opportunity before the global cap.
    for index in range(cfg["maximum_discovery_per_topic"]):
        for topic, items in shortlist.items():
            if index >= len(items):
                continue
            date, url, title = items[index]
            key = canonical_url(url)
            if key in visited:
                append_decision(http, {"url": url, "topic": topic, "status": "rejected", "reason": "ALREADY_FETCHED"})
                continue
            if len(visited) >= cfg["maximum_fetches"]:
                append_decision(http, {"url": url, "topic": topic, "status": "rejected", "reason": "FETCH_LIMIT"})
                continue
            visited.add(key)
            try:
                article = fetch_article(http, url, title, date)
                if not fresh(article.published_at, snapshot.as_of):
                    raise ValueError("FUTURE_OR_STALE_ARTICLE")
                if not article.topics:
                    raise ValueError("UNRELATED_ARTICLE")
                if article.id not in snapshot.sources:
                    snapshot.sources[article.id] = article
                append_decision(http, {"url": article.url, "topic": topic, "source_id": article.id,
                                       "status": "collected", "reason": "PUBLISHER_ARTICLE", "words": len(article.text.split())})
            except Exception as error:
                reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                append_decision(http, {"url": url, "topic": topic, "status": "rejected", "reason": reason})
    decisions = []
    for topic in cfg["topics"]:
        _, rows = select_articles(snapshot, topic)
        decisions.extend(rows)
    for row in decisions:
        append_decision(http, row)
    (Path(http.directory).parent / "news-coverage.json").write_text(
        json.dumps(coverage(snapshot), ensure_ascii=False, indent=2), encoding="utf-8")
    for topic, value in coverage(snapshot).items():
        if not value["count"]:
            snapshot.add_issue("NEWS_TOPIC_EMPTY", f"{topic}: chưa có bài đủ dùng; chỉ được mô tả số liệu và giới hạn nguồn")


def main(argv=None):
    from .http import Http
    from .models import Snapshot, parse_as_of
    from .sources import collect_vietnambiz_coffee

    parser = argparse.ArgumentParser(description="Review news collection only; no AI, rendering or email")
    parser.add_argument("--as-of", help="Cutoff ISO timestamp with UTC offset")
    parser.add_argument("--output-dir", default=str(ROOT / "output"))
    parser.add_argument("--max-fetches", type=int, help="Reduce the article fetch cap for a bounded smoke check")
    args = parser.parse_args(argv)
    if args.max_fetches is not None:
        if not 1 <= args.max_fetches <= config()["maximum_fetches"]:
            parser.error("--max-fetches must be between one and the configured fetch cap")
        config()["maximum_fetches"] = args.max_fetches
    as_of = parse_as_of(args.as_of)
    directory = Path(args.output_dir) / ("news-review-" + as_of.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    http = Http(directory / "sources")
    snapshot = Snapshot(as_of=as_of)
    collect_vietnambiz_coffee(http, snapshot)
    collect_news(http, snapshot)
    (directory / "snapshot.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    report = {"status": "reviewed", "as_of": as_of.isoformat(), "articles": len(snapshot.sources),
              "fetch_cap": config()["maximum_fetches"], "coverage": coverage(snapshot)}
    if any(not entry["count"] for entry in report["coverage"].values()):
        report["status"] = "reviewed_with_gaps"
    (directory / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"directory": str(directory), "status": report["status"], "articles": report["articles"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

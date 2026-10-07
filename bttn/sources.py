import hashlib
import logging
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, quote_plus, urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

from .calculations import percentage
from .http import Http
from .models import Observation, Point, Snapshot, Source, previous_weekday

LOG = logging.getLogger("bttn.sources")
ROOT = Path(__file__).resolve().parents[1]

# Identity and units are explicit. Unsupported legacy rows remain unavailable.
INSTRUMENTS = {
    "EURUSD": ("EURUSD=X", "EUR-USD", "USD/EUR"),
    "USDJPY": ("USDJPY=X", "USD-JPY", "JPY/USD"),
    "USDCNY": ("USDCNY=X", "USD-CNY", "CNY/USD"),
    "USDVND": ("USDVND=X", "USD-VND", "VND/USD"),
    "DOW": ("^DJI", "Dow Jones", "điểm"),
    "NIKKEI": ("^N225", "Nikkei 225", "điểm"),
    "DAX": ("^GDAXI", "DAX", "điểm"),
    "DXY": ("DX-Y.NYB", "USD Index", "điểm"),
    "ROBUSTA": ("RC=F", "Robusta futures", "USD/tấn"),
    "ARABICA": ("KC=F", "Arabica futures", "USc/lbs"),
    "CORN": ("ZC=F", "Ngô futures", "USc/bushel"),
    "SOY": ("ZS=F", "Đậu tương futures", "USc/bushel"),
    "COTTON": ("CT=F", "Cotton futures", "USc/lb"),
    "BRENT": ("BZ=F", "Dầu Brent futures", "USD/thùng"),
    "GAS": ("NG=F", "Khí tự nhiên futures", "USD/MMBtu"),
    "GOLD": ("GC=F", "Vàng futures", "USD/oz"),
    "SILVER": ("SI=F", "Bạc futures", "USD/oz"),
}


def yahoo_observation(payload, key, as_of, source_id):
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    # Keep timestamps paired with prices: filtering only closes shifts sessions.
    raw_points = []
    for stamp, close in zip(result["timestamp"], result["indicators"]["quote"][0]["close"]):
        at = datetime.fromtimestamp(stamp, timezone.utc)
        if close is not None and math.isfinite(close) and at <= as_of:
            raw_points.append(Point(at=at, value=Decimal(str(close))))
    raw_points.sort(key=lambda p: p.at)
    market_at = datetime.fromtimestamp(meta.get("regularMarketTime", 0), timezone.utc)
    live = meta.get("regularMarketPrice")
    if live is not None and market_at <= as_of and market_at.year > 2000:
        latest = Point(at=market_at, value=Decimal(str(live)))
        # Same session's daily bar cannot be used as the preceding close.
        from zoneinfo import ZoneInfo

        exchange_tz = ZoneInfo(meta.get("exchangeTimezoneName", "UTC"))
        current_date = market_at.astimezone(exchange_tz).date()
        history = [p for p in raw_points if p.at.astimezone(exchange_tz).date() < current_date]
    else:
        # Historical intraday reproduction requires a stored snapshot. A daily
        # candle's timestamp is its OPEN, so today's full candle is excluded.
        history = [p for p in raw_points if p.at.date() < as_of.date()]
        if not history:
            raise ValueError(f"No completed observations for {key}")
        latest = history.pop()
    if latest.value <= 0 or not history:
        raise ValueError(f"Missing price or previous completed session for {key}")
    previous = history[-1]
    day_change = percentage(latest.value, previous.value)
    anniversary = latest.at.date() - timedelta(days=365)
    yearly = [p for p in history if p.at.date() <= anniversary]
    annual = None
    if yearly and (anniversary - yearly[-1].at.date()).days <= 7:
        annual = percentage(latest.value, yearly[-1].value)
    _, label, unit = INSTRUMENTS[key]
    return Observation(id=key, label=label, value=latest.value, unit=unit,
        source_id=source_id, trading_date=latest.at.date(), basis="Yahoo last price vs preceding completed session; futures continuous contract",
        prev_value=previous.value, prev_trading_date=previous.at.date(),
        daily_pct=day_change, annual_pct=annual, annual_basis="YoY" if annual is not None else None,
        series=(history + [latest])[-90:])


def collect_yahoo(http, snapshot):
    def fetch(item):
        key, (symbol, _, _) = item
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?interval=1d&range=2y"
        worker = Http(http.directory)
        response = worker.get(url)
        obs = yahoo_observation(response.json(), key, snapshot.as_of, f"yf_{key}")
        source = Source(id=obs.source_id, url=url, published_at=obs.series[-1].at,
            retrieved_at=datetime.now(timezone.utc), text=f"{obs.label}: {obs.value} {obs.unit}; {obs.basis}",
            sha256=hashlib.sha256(response.content).hexdigest())
        return obs, source
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch, item): item[0] for item in INSTRUMENTS.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                obs, source = future.result()
                snapshot.observations[key] = obs
                snapshot.sources[source.id] = source
            except Exception as exc:
                snapshot.add_issue("YAHOO_MISSING", f"{key}: {type(exc).__name__}")


def parse_vnd(text):
    text = str(text).strip().replace(" ", "")
    if re.fullmatch(r"\d{2}\.\d{3},\d{2}", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{2},\d{3}\.\d{2}", text):
        text = text.replace(",", "")
    elif re.fullmatch(r"\d{2}[.,]\d{3}", text):
        text = text.replace(".", "").replace(",", "")
    value = Decimal(text)
    if not Decimal("10000") <= value <= Decimal("100000"):
        raise ValueError("USD-VND rate outside expected range")
    return value


def parse_sbv(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    if "Request Rejected" in text:
        raise ValueError("SBV rejected the request")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("SBV did not return rate tables")
    central = re.search(r"1\s*Đô\s*la\s*Mỹ\s*=\s*([\d.,]+)", tables[0].get_text(" "), re.I)
    dates = re.findall(r"(\d{1,2})/(\d{1,2})/(20\d{2})", tables[0].get_text(" "))
    if not central or not dates:
        raise ValueError("Missing SBV fixing or effective date")
    day, month, year = dates[0]
    effective = datetime(int(year), int(month), int(day)).date()
    rates = {"SBV_CENTRAL": parse_vnd(central.group(1))}
    for table in tables[1:]:
        for row in table.select("tr"):
            cols = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if len(cols) >= 5 and "USD" in cols[1]:
                rates["SBV_BUY"] = parse_vnd(cols[3])
                rates["SBV_SELL"] = parse_vnd(cols[4])
                break
    return effective, rates


def collect_sbv(http, snapshot):
    url = "https://sbv.gov.vn/vi/t%E1%BB%B7-gi%C3%A1"
    response = http.get(url)
    day, rates = parse_sbv(response.text)
    at = datetime.combine(day, time(0), tzinfo=snapshot.as_of.tzinfo)
    snapshot.sources["sbv"] = Source(id="sbv", url=url, published_at=at,
        retrieved_at=datetime.now(timezone.utc), sha256=hashlib.sha256(response.content).hexdigest())
    central = rates["SBV_CENTRAL"]
    rates["SBV_CEILING"] = (central * Decimal("1.05")).to_integral_value(rounding=ROUND_FLOOR)
    rates["SBV_FLOOR"] = (central * Decimal("0.95")).to_integral_value(rounding=ROUND_CEILING)
    for key, value in rates.items():
        snapshot.observations[key] = Observation(id=key, label=key, value=value, unit="VND/USD",
            source_id="sbv", trading_date=day, basis="SBV published fixing; floor/ceiling derived at ±5%")


def parse_mb(payload):
    items = payload if isinstance(payload, list) else payload.get("lst", [])
    dollars = [i for i in items if i.get("currencyCode") == "USD"]
    chosen = [i for i in dollars if i.get("usd_default") or re.search(r"50[-,]100", i.get("name", ""))]
    if len(chosen) != 1:
        raise ValueError("Missing/ambiguous MB default USD denomination")
    row = chosen[0]
    buy, sell = parse_vnd(row["buy_bank_transfer"]), parse_vnd(row["sell_bank_transfer"])
    if buy > sell:
        raise ValueError("MB buy is greater than sell")
    return buy, sell


def collect_mb(http, snapshot):
    url = "https://www.mbbank.com.vn/ExchangeRate"
    page = BeautifulSoup(http.get(url).text, "html.parser")
    token = page.find("input", {"name": "__RequestVerificationToken"})
    headers = {"Referer": url, "X-Requested-With": "XMLHttpRequest",
               "Accept": "application/json, text/plain, */*"}
    if token:
        headers["MB-XSRF-Token-FormOnline"] = token.get("value", "")
    today = snapshot.as_of.date()
    for suffix, day in [("", today), ("_PREV", previous_weekday(today))]:
        endpoint = f"https://www.mbbank.com.vn/api/getExchangeRate/{day.isoformat()}"
        try:
            response = http.get(endpoint, headers=headers)
            buy, sell = parse_mb(response.json())
            sid = f"mb{suffix}"
            snapshot.sources[sid] = Source(id=sid, url=endpoint,
                published_at=datetime.combine(day, time(0), tzinfo=snapshot.as_of.tzinfo),
                retrieved_at=datetime.now(timezone.utc), sha256=hashlib.sha256(response.content).hexdigest())
            for name, value in [("BUY", buy), ("SELL", sell)]:
                key = f"MB_{name}{suffix}"
                snapshot.observations[key] = Observation(id=key, label=key, value=value, unit="VND/USD",
                    source_id=sid, trading_date=day, basis="MB transfer rate requested for explicit date; previous weekday, unavailable holidays remain missing")
        except Exception as exc:
            snapshot.add_issue("MB_MISSING", f"{day}: {type(exc).__name__}")


def collect_news(http, snapshot):
    query = 'site:tradingeconomics.com ("Euro Area" OR "United States" OR Japan OR China) when:1d'
    feeds = [f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en",
             "https://vietnambiz.vn/rss/tai-chinh.rss"]
    candidates = []
    for url in feeds:
        try:
            feed = feedparser.parse(http.get(url).content)
            for entry in feed.entries[:15]:
                try:
                    at = parsedate_to_datetime(entry.get("published", ""))
                    if at.tzinfo is None:
                        continue
                    text = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
                    candidates.append((entry.get("link", ""), at, entry.get("title", "") + "\n" + text))
                except (ValueError, TypeError):
                    continue
        except Exception as exc:
            snapshot.add_issue("NEWS_FEED", f"{urlparse(url).hostname}: {type(exc).__name__}")
    # Full articles for coffee/oil, VIRA domestic market commentary, and Tin Nhanh Chung Khoan macro.
    listings = [("https://vietnambiz.vn/chu-de/ca-phe-34.htm", "a[href]"),
                ("https://vietnambiz.vn/chu-de/dau-mo-60.htm", "a[href]"),
                ("https://vira.org.vn/tin/Ban-tin-Kinh-te-Tai-chinh-ngay.html", ".story__title a"),
                ("https://www.tinnhanhchungkhoan.vn/vi-mo/", "a[href]")]
    for url, selector in listings:
        try:
            soup = BeautifulSoup(http.get(url).text, "html.parser")
            links = []
            for a in soup.select(selector):
                href = urljoin(url, a.get("href", ""))
                title = a.get("title", "") or a.get_text(" ", strip=True)
                is_article = (len(title) > 20 and "/chu-de/" not in href and href.endswith(".htm"))
                is_vira = (urlparse(href).hostname == "vira.org.vn"
                           and "/Ban-tin-Kinh-te-Tai-chinh-ngay/Ban-tin" in href)
                is_tnck = (urlparse(href).hostname == "www.tinnhanhchungkhoan.vn"
                           and "-post" in href and href.endswith(".html") and len(title) > 20)
                if (urlparse(href).hostname == urlparse(url).hostname and href not in links
                        and (is_article or is_vira or is_tnck)):
                    links.append(href)
                if len(links) >= 6:
                    break
            for link in links:
                detail = BeautifulSoup(http.get(link).text, "html.parser")
                meta = detail.find("meta", property="article:published_time")
                if not meta or not meta.get("content"):
                    continue
                try:
                    at = datetime.fromisoformat(meta["content"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if at.tzinfo is None:
                    continue
                body = detail.select_one("#abody, .vnbcbc-body, .detail-content, .article__body, .cms-body")
                if body:
                    candidates.append((link, at, body.get_text(" ", strip=True)[:18000]))
        except Exception as exc:
            snapshot.add_issue("NEWS_ARTICLE", f"{urlparse(url).hostname}: {type(exc).__name__}")
    for url, at, text in candidates:
        if not url.startswith("https://") or not timedelta(0) <= snapshot.as_of - at <= timedelta(hours=36):
            continue
        sid = "news_" + hashlib.sha256(url.encode()).hexdigest()[:12]
        snapshot.sources[sid] = Source(id=sid, url=url, published_at=at,
            retrieved_at=datetime.now(timezone.utc), text=text, kind="news")


def parse_vietnambiz_coffee(html: str, published_at: datetime):
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("#abody, .vnbcbc-body, .detail-content")
    if not body:
        raise ValueError("Article body not found in VietnamBiz coffee page")
    text = body.get_text(" ", strip=True)

    # 1. Trading date
    date_m = re.search(r"phiên giao dịch ngày\s*(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", text, re.I)
    if date_m:
        d, m, y = date_m.group(1), date_m.group(2), date_m.group(3)
        year = int(y) if y else published_at.year
        trading_date = datetime(year, int(m), int(d)).date()
    else:
        trading_date = previous_weekday(published_at.date())

    # 2. Robusta London
    rob_m = re.search(
        r"robusta[^\n.]*?kỳ\s+hạn\s+(tháng\s+\d+(?:/\d{4})?)[^\n.]*?(tăng|giảm)[^\n.]*?([\d,.]+)\s*%[^\n.]*?(?:lên|xuống|đạt|về)[^\n.]*?([\d.,]+)\s*USD/tấn",
        text,
        re.I,
    )
    if not rob_m:
        rob_m = re.search(
            r"robusta[^.]*?London[^.]*?(tăng|giảm)[^.]*?([\d,.]+)\s*%[^.]*?(?:lên|xuống|đạt|về)[^.]*?([\d.,]+)\s*USD/tấn",
            text,
            re.I,
        )

    # 3. Arabica New York
    ara_m = re.search(
        r"arabica[^\n.]*?kỳ\s+hạn\s+(tháng\s+\d+(?:/\d{4})?)[^\n.]*?(tăng|giảm)[^\n.]*?([\d,.]+)\s*%[^\n.]*?(?:lên|xuống|đạt|về)[^\n.]*?([\d.,]+)\s*(?:US\s+cent/pound|USc/lb|cent/pound)",
        text,
        re.I,
    )
    if not ara_m:
        ara_m = re.search(
            r"arabica[^.]*?New York[^.]*?(tăng|giảm)[^.]*?([\d,.]+)\s*%[^.]*?(?:lên|xuống|đạt|về)[^.]*?([\d.,]+)\s*(?:US\s+cent/pound|USc/lb|cent/pound)",
            text,
            re.I,
        )

    results = {}
    if rob_m:
        groups = rob_m.groups()
        tenor = groups[0] if len(groups) == 4 else None
        direction = groups[-3].lower()
        pct = Decimal(groups[-2].replace(",", "."))
        if "giảm" in direction:
            pct = -pct
        price = Decimal(groups[-1].replace(".", "").replace(",", "."))
        results["ROBUSTA"] = {
            "tenor": tenor,
            "value": price,
            "daily_pct": pct,
            "unit": "USD/tấn",
            "trading_date": trading_date,
        }

    if ara_m:
        groups = ara_m.groups()
        tenor = groups[0] if len(groups) == 4 else None
        direction = groups[-3].lower()
        pct = Decimal(groups[-2].replace(",", "."))
        if "giảm" in direction:
            pct = -pct
        price = Decimal(groups[-1].replace(",", "."))
        results["ARABICA"] = {
            "tenor": tenor,
            "value": price,
            "daily_pct": pct,
            "unit": "USc/lbs",
            "trading_date": trading_date,
        }

    return results, trading_date


def collect_vietnambiz_coffee(http, snapshot):
    listing_url = "https://vietnambiz.vn/chu-de/ca-phe-34.htm"
    try:
        html = http.get(listing_url).text
        soup = BeautifulSoup(html, "html.parser")
        first_article_url = None
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            title = a.get("title", "") or a.get_text(" ", strip=True)
            if "/chu-de/" not in href and href.endswith(".htm") and len(title) > 20 and ("ca-phe" in href or "gia-ca-phe" in href):
                first_article_url = urljoin(listing_url, href)
                break

        if not first_article_url:
            snapshot.add_issue("VIETNAMBIZ_COFFEE", "No coffee article link found on VietnamBiz listing")
            return

        detail_resp = http.get(first_article_url)
        detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
        published_at = article_time(detail_soup)

        if published_at > snapshot.as_of:
            return

        rates, trading_date = parse_vietnambiz_coffee(detail_resp.text, published_at)
        sid = "news_" + hashlib.sha256(first_article_url.encode()).hexdigest()[:12]

        body = detail_soup.select_one("#abody, .vnbcbc-body, .detail-content")
        clean_text = ""
        if body:
            for rel in body.select(".relate-container, .box-tin-lien-quan, .VnbArticleContentEmbed, script, style"):
                rel.decompose()
            paras = [unicodedata.normalize("NFC", p.get_text(" ", strip=True)) for p in body.find_all(["p", "h2", "h3"])]
            clean_paras = [p for p in paras if p and len(p) > 15 and not p.startswith("TIN LIÊN QUAN") and not p.startswith("Xem thêm:")]
            full_body = "\n\n".join(clean_paras)
            m = re.search(r"(Cập nhật giá cà phê thế giới|Trên sàn giao dịch London|thị trường cà phê thế giới)", full_body, re.I)
            clean_text = full_body[m.start():] if m else full_body
        else:
            raise ValueError("COFFEE_ARTICLE_BODY_MISSING")

        title = detail_soup.find("h1")
        article_text = (title.get_text(" ", strip=True) if title else "Giá cà phê thế giới") + "\n\n" + clean_text

        snapshot.sources[sid] = Source(
            id=sid,
            url=first_article_url,
            published_at=published_at,
            retrieved_at=datetime.now(timezone.utc),
            text=article_text,
            sha256=hashlib.sha256(article_text.encode()).hexdigest(),
            kind="news", text_scope="article",
        )

        for key, info in rates.items():
            snapshot.observations[key] = Observation(
                id=key,
                label=f"{key.capitalize()} futures",
                value=info["value"],
                unit=info["unit"],
                source_id=sid,
                trading_date=info["trading_date"],
                basis=f"VietnamBiz bài giá cà phê; giao kỳ hạn {info.get('tenor') or 'chuẩn'}",
                daily_pct=info["daily_pct"],
                tenor=info.get("tenor"),
            )
        LOG.info("Collected VietnamBiz coffee data: %s", list(rates.keys()))
    except Exception as exc:
        snapshot.add_issue("VIETNAMBIZ_COFFEE", f"Failed to collect VietnamBiz coffee: {type(exc).__name__}: {exc}")


KEYWORDS_MACRO = [
    "tín dụng", "huy động", "xuất khẩu", "nhập khẩu", "fdi", "tỷ usd",
    "giải ngân", "đầu tư công", "tiêu dùng", "lạm phát", "gdp", "giá xăng dầu", "xăng dầu",
]

def score_macro_text(title: str, desc: str = "") -> int:
    full_text = (title + " " + desc).lower()
    score = 0
    for kw in KEYWORDS_MACRO:
        if kw in full_text:
            score += 1
    if re.search(r"\d+([.,]\d+)?\s*(%|tỷ|triệu)", full_text):
        score += 2
    return score


def article_time(soup):
    """Require source publication metadata; collection time is not publication."""
    meta = soup.select_one('meta[property="article:published_time"], meta[itemprop="datePublished"], time[datetime]')
    if meta is None:
        raise ValueError("ARTICLE_PUBLICATION_TIME_MISSING")
    value = meta.get("content") or meta.get("datetime")
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("ARTICLE_PUBLICATION_TIMEZONE_MISSING")
    return at


def collect_vietnam_macro_news(http, snapshot):
    listings = [
        "https://www.tinnhanhchungkhoan.vn/vi-mo/",
        "https://vneconomy.vn/tieu-diem.htm",
        "https://baodautu.vn/kinh-te-vi-mo-d2/",
    ]
    candidates = {}
    for url in listings:
        try:
            soup = BeautifulSoup(http.get(url).text, "html.parser")
            for item in soup.select("article, .story, .item-news"):
                title = item.select_one("h3 a, h2 a, .story__title a, a.title")
                if not title or not title.get("href"):
                    continue
                link = urljoin(url, title["href"])
                if urlparse(link).hostname != urlparse(url).hostname:
                    continue
                description = item.select_one(".story__summary, .s-content, p")
                score = score_macro_text(title.get_text(" ", strip=True), description.get_text(" ", strip=True) if description else "")
                if score:
                    candidates[link] = (score, title.get_text(" ", strip=True))
        except Exception as exc:
            LOG.debug("Macro listing failed: %s", type(exc).__name__)
    count = 0
    for url, (_, title) in sorted(candidates.items(), key=lambda item: (-item[1][0], item[0]))[:9]:
        try:
            detail = BeautifulSoup(http.get(url).text, "html.parser")
            at = article_time(detail)
            if not timedelta(0) <= snapshot.as_of - at <= timedelta(hours=36):
                continue
            body = detail.select_one("#abody, .cms-body, .detail-content, .article__body, .detail__content, .content-detail")
            if body is None:
                continue
            text = body.get_text(" ", strip=True)
            if len(text.split()) < 35:
                continue
            full = title + "\n\n" + text
            sid = "macro_news_" + hashlib.sha256(url.encode()).hexdigest()[:12]
            snapshot.sources[sid] = Source(id=sid, url=url, published_at=at, retrieved_at=datetime.now(timezone.utc),
                                           text=full, sha256=hashlib.sha256(full.encode()).hexdigest(), kind="news", text_scope="article")
            count += 1
            if count >= 3:
                break
        except Exception as exc:
            LOG.debug("Macro article failed: %s", type(exc).__name__)
    if count < 2:
        snapshot.add_issue("MACRO_NEWS_MISSING", "Không đủ bài vĩ mô có toàn văn và ngày xuất bản xác minh; không dùng tin mẫu.")


def collect_sjc_gold(http, snapshot):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    buy_str, sell_str = None, None
    url = "https://webgia.com/gia-vang/sjc/"
    try:
        r = http.get(url, headers=headers)
        soup = BeautifulSoup(r.text, "html.parser")
        for tr in soup.select("table tr"):
            tds = [td.get_text(strip=True) for td in tr.select("th, td")]
            if len(tds) >= 4 and any("1L" in x for x in tds):
                b_clean = re.sub(r"[^\d]", "", tds[2])
                s_clean = re.sub(r"[^\d]", "", tds[3])
                if b_clean and s_clean:
                    b_val = float(b_clean) * 10.0 / 1_000_000.0
                    s_val = float(s_clean) * 10.0 / 1_000_000.0
                    buy_str = f"{b_val:.1f}".replace(".", ",")
                    sell_str = f"{s_val:.1f}".replace(".", ",")
                    break
    except Exception as exc:
        LOG.debug("SJC gold fetch failed: %s", exc)

    if buy_str is None or sell_str is None:
        snapshot.add_issue("SJC_GOLD_MISSING", "Không đọc được giá SJC; không dùng báo giá mẫu.")
        return

    snapshot.sources["sjc_gold"] = Source(
        id="sjc_gold",
        url=url,
        published_at=snapshot.as_of,
        retrieved_at=datetime.now(timezone.utc),
        text=f"{buy_str} – {sell_str} triệu đồng/lượng",
        kind="market",
    )


def collect_vira_daily(http, snapshot):
    """Archive actual VIRA prose only; rates still come from Market Watch."""
    list_url = "https://vira.org.vn/tin/Ban-tin-Kinh-te-Tai-chinh-ngay.html"
    try:
        soup = BeautifulSoup(http.get(list_url).text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"/tin/Ban-tin-Kinh-te-Tai-chinh-ngay/Ban-tin.*\.html"))
        for link in links[:5]:
            url = urljoin(list_url, link["href"])
            if urlparse(url).hostname != "vira.org.vn":
                continue
            detail = BeautifulSoup(http.get(url).text, "html.parser")
            at = article_time(detail)
            if not timedelta(0) <= snapshot.as_of - at <= timedelta(hours=36):
                continue
            text = detail.get_text(" ", strip=True)
            match = re.search(r"Thị trường tiền tệ LNH:(.*?)(?=Thị trường chứng khoán|Tin quốc tế|$)", text, re.DOTALL)
            if not match or len(match[1].split()) < 35:
                continue
            body = match[1].strip()
            snapshot.sources["vira_daily"] = Source(id="vira_daily", url=url, published_at=at,
                retrieved_at=datetime.now(timezone.utc), text=body, sha256=hashlib.sha256(body.encode()).hexdigest(),
                kind="news", text_scope="excerpt")
            return
    except Exception as exc:
        LOG.debug("VIRA daily failed: %s", type(exc).__name__)
    snapshot.add_issue("VIRA_DAILY_MISSING", "Không đọc được đoạn VIRA có ngày xuất bản xác minh; không tạo lãi suất/OMO mẫu.")


def collect_china_news(http, snapshot):
    """Ingest China economy news within 48 hours from SCMP and Nikkei Asia."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # 1. South China Morning Post (SCMP) Economy
    try:
        r = http.get("https://www.scmp.com/economy", headers=headers)
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0]
            if "/economy/" in href and "/article/" in href:
                full = urljoin("https://www.scmp.com", href)
                if full not in links:
                    links.append(full)
        for link in links[:6]:
            try:
                r2 = http.get(link, headers=headers)
                s2 = BeautifulSoup(r2.text, "html.parser")
                m_time = s2.find("meta", property="article:published_time")
                if not m_time or not m_time.get("content"):
                    continue
                at = datetime.fromisoformat(m_time["content"].replace("Z", "+00:00"))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                age = snapshot.as_of - at
                if not timedelta(0) <= age <= timedelta(hours=48):
                    continue
                title_elem = s2.find("meta", property="og:title")
                title = title_elem["content"].strip() if title_elem and title_elem.get("content") else ""
                paras = [p.get_text(" ", strip=True) for p in s2.find_all("p") if len(p.get_text(" ", strip=True).split()) >= 8]
                body = "\n\n".join(paras)
                if len(body.split()) < 30 or not title:
                    continue
                sid = "news_china_" + hashlib.sha256(link.encode()).hexdigest()[:12]
                text = title + "\n\n" + body
                snapshot.sources[sid] = Source(
                    id=sid,
                    url=link,
                    published_at=at,
                    retrieved_at=datetime.now(timezone.utc),
                    text=text,
                    sha256=hashlib.sha256(text.encode()).hexdigest(),
                    kind="news",
                    text_scope="article",
                )
            except Exception as exc:
                LOG.debug("SCMP article failed: %s: %s", link, exc)
    except Exception as exc:
        snapshot.add_issue("SCMP_NEWS", f"SCMP: {type(exc).__name__}: {exc}")

    # 2. Nikkei Asia East Asia Economy
    try:
        r = http.get("https://asia.nikkei.com/economy/east-asia", headers=headers)
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].split("?")[0]
            slug = href.strip("/").split("/")[-1]
            if href.startswith("/economy/") and len(slug.split("-")) >= 4 and not any(tag in slug for tag in ["rest-of-the-world", "south-east-asia", "east-asia"]):
                full = urljoin("https://asia.nikkei.com", href)
                if full not in links:
                    links.append(full)
        for link in links[:6]:
            try:
                r2 = http.get(link, headers=headers)
                s2 = BeautifulSoup(r2.text, "html.parser")
                m_time = s2.find("meta", attrs={"name": "date"}) or s2.find("meta", property="article:published_time")
                if not m_time or not m_time.get("content"):
                    continue
                at = datetime.fromisoformat(m_time["content"].replace("Z", "+00:00"))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                age = snapshot.as_of - at
                if not timedelta(0) <= age <= timedelta(hours=48):
                    continue
                title_elem = s2.find("meta", property="og:title")
                title = title_elem["content"].strip() if title_elem and title_elem.get("content") else ""
                paras = [p.get_text(" ", strip=True) for p in s2.find_all("p") if len(p.get_text(" ", strip=True).split()) >= 8]
                body = "\n\n".join(paras)
                if len(body.split()) < 30 or not title:
                    continue
                sid = "news_china_" + hashlib.sha256(link.encode()).hexdigest()[:12]
                text = title + "\n\n" + body
                snapshot.sources[sid] = Source(
                    id=sid,
                    url=link,
                    published_at=at,
                    retrieved_at=datetime.now(timezone.utc),
                    text=text,
                    sha256=hashlib.sha256(text.encode()).hexdigest(),
                    kind="news",
                    text_scope="article",
                )
            except Exception as exc:
                LOG.debug("Nikkei article failed: %s: %s", link, exc)
    except Exception as exc:
        snapshot.add_issue("NIKKEI_NEWS", f"Nikkei: {type(exc).__name__}: {exc}")


def collect_snapshot(http, as_of):
    from .calculations import derive_swaps
    from .trader_quotes import collect_trader_quotes
    from .tradingeconomics_news import collect_tradingeconomics
    from .vira import collect_vira

    snapshot = Snapshot(as_of=as_of)
    for name, collector in [
        ("VIRA", collect_vira),
        ("SBV", collect_sbv),
        ("MB", collect_mb),
        ("YAHOO", collect_yahoo),
        ("COFFEE", collect_vietnambiz_coffee),
        ("NEWS", collect_news),
        ("TE_NEWS", collect_tradingeconomics),
        ("CHINA_NEWS", collect_china_news),
        ("MACRO_NEWS", collect_vietnam_macro_news),
        ("SJC_GOLD", collect_sjc_gold),
        ("VIRA_DAILY", collect_vira_daily),
        ("TRADER_QUOTES", collect_trader_quotes),
    ]:
        try:
            collector(http, snapshot)
        except Exception as exc:
            snapshot.add_issue(f"{name}_SOURCE", f"{name}: {type(exc).__name__}: {exc}")
    derive_swaps(snapshot)
    return snapshot

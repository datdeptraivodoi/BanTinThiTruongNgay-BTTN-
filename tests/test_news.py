import json
from datetime import timedelta
from email.utils import format_datetime
from types import SimpleNamespace

import pytest

from bttn import news
from bttn.editorial import prepare_evidence
from bttn.models import Snapshot, Source, parse_as_of
from bttn.sources import collect_vietnambiz_coffee

AT = parse_as_of("2026-10-06T12:00:00+07:00")
TEXT = " ".join(["Japan released its policy rate decision and discussed inflation and domestic demand with market participants after the scheduled meeting." ] * 5)


def article(sid="a", text=TEXT, title="Japan policy decision", hours=2):
    return Source(id=sid, url="https://tradingeconomics.com/news/"+sid, title=title, text=text,
                  published_at=AT-timedelta(hours=hours), retrieved_at=AT, kind="news", content_scope="article")


def html(text=TEXT, title="Japan policy decision", date=AT-timedelta(hours=2)):
    meta = f'<meta property="article:published_time" content="{date.isoformat()}">' if date else ""
    return f'<html><head>{meta}</head><body><h1>{title}</h1><article><p>{text}</p><div class="related-articles"><p>Gold adverts unrelated to the article</p></div></article></body></html>'


class FakeHttp:
    def __init__(self, directory, pages):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.pages, self.calls = pages, []

    def get(self, url):
        self.calls.append(url)
        content = self.pages.get(url)
        if content is None:
            content = '<rss version="2.0"><channel></channel></rss>' if "rss" in url else "<html></html>"
        return SimpleNamespace(url=url, text=content, content=content.encode())


def test_keywords_do_not_match_domains_or_word_fragments():
    assert not news.topic_scores(article(text="soil conditions and Goldman Sachs confederation", title="", sid="coffee"))
    coffee = article(text="Coffee harvest in Brazil. " * 30, title="Giá cà phê hôm nay")
    coffee.url = "https://vietnambiz.vn/gia-ca-phe.htm"
    assert "coffee" in news.topic_scores(coffee)
    assert "usd_vnd" not in news.topic_scores(coffee)
    foreign = article(text="Japan yen exchange rate. Tỷ giá đồng yên tăng. " * 20, title="Tỷ giá Nhật Bản")
    assert "usd_vnd" not in news.topic_scores(foreign)


def test_domestic_euro_quote_and_incidental_country_are_not_topic_coverage():
    euro = article(text="Tỷ giá euro tại Vietcombank được niêm yết bằng VND. " * 20, title="Tỷ giá euro ngày hôm nay")
    assert "usd_vnd" not in news.topic_scores(euro)
    assert "eur_usd" not in news.topic_scores(euro)
    incidental = article(text="Japan is one destination. " + "Exports to China expanded. " * 30, title="China import demand")
    assert "japan" not in news.topic_scores(incidental)
    assert "china" in news.topic_scores(incidental)


def test_listing_entities_do_not_hide_vietnamese_topic():
    assert news.contains("Gi&#225; c&#224; ph&#234; h&#244;m nay", "giá cà phê hôm nay")


def test_explicit_rss_gmt_offset_is_preserved():
    assert news.rss_publication_date("Tue, 06 Oct 2026 10:00:00 GMT+7") == AT-timedelta(hours=2)
    with pytest.raises(ValueError, match="PUBLICATION_TIME_NO_TIMEZONE"):
        news.rss_publication_date("Tue, 06 Oct 2026 10:00:00")
    with pytest.raises(ValueError, match="INVALID_RSS_OFFSET"):
        news.rss_publication_date("Tue, 06 Oct 2026 10:00:00 GMT+25")


def test_monday_news_window_keeps_friday_without_future_news():
    monday = parse_as_of("2026-10-05T12:00:00+07:00")
    assert news.fresh(parse_as_of("2026-10-02T16:00:00+07:00"), monday)
    assert not news.fresh(parse_as_of("2026-10-01T16:00:00+07:00"), monday)
    assert not news.fresh(monday+timedelta(minutes=1), monday)


def test_article_extraction_preserves_body_and_excludes_related_box():
    source = news.extract_article(html(), "https://tradingeconomics.com/news/a?utm_source=x", AT)
    assert source.text == TEXT
    assert source.url == "https://tradingeconomics.com/news/a"
    assert source.title == "Japan policy decision"
    assert source.publisher == "tradingeconomics.com"
    assert source.content_scope == "article"
    assert source.published_from == "publisher"
    assert "gold" not in source.topics
    assert source.sha256


def test_unknown_time_never_uses_cutoff_and_rss_date_is_identified():
    with pytest.raises(ValueError, match="MISSING_PUBLICATION_TIME"):
        news.extract_article(html(date=None), "https://tradingeconomics.com/news/a", AT)
    source = news.extract_article(html(date=None), "https://tradingeconomics.com/news/a", AT,
                                  rss_date=AT-timedelta(hours=5))
    assert source.published_at == AT-timedelta(hours=5)
    assert source.published_from == "rss"


def test_publisher_timezone_applies_to_actual_date_without_inventing_it():
    naive = AT.replace(tzinfo=None)-timedelta(hours=5)
    result = news.extract_article(html(date=naive), "https://vietnambiz.vn/article.htm", AT)
    assert result.published_at == AT-timedelta(hours=5)
    assert result.published_from == "publisher_local_time"
    with pytest.raises(ValueError, match="PUBLICATION_TIME_NO_TIMEZONE"):
        news.extract_article(html(date=naive), "https://tradingeconomics.com/news/a", AT)


def test_jsonld_body_and_publication_extraction():
    data = {"@graph": [{"@type": "NewsArticle", "headline": "Japan economic release",
                        "datePublished": AT.isoformat(), "articleBody": TEXT}]}
    source = news.extract_article('<script type="application/ld+json">'+json.dumps(data)+'</script>',
                                  "https://tradingeconomics.com/news/a", AT)
    assert source.text == TEXT and source.published_at == AT


def test_headline_empty_and_short_content_cannot_pass_coverage():
    from bttn.validation import validate_snapshot
    s = Snapshot(as_of=AT, sources={str(i): article(str(i), "short") for i in range(3)})
    assert any(i.code == "NEWS_COVERAGE" for i in validate_snapshot(s))
    s.sources["0"].text = TEXT
    s.sources["0"].content_scope = "headline"
    chosen, decisions = news.select_articles(s, "japan")
    assert not chosen
    assert {d["reason"] for d in decisions} == {"HEADLINE_ONLY", "SHORT_BODY"}


def test_filler_keyword_repetition_does_not_win_over_policy_event():
    repeated = article("repeated", "Japan Japan Japan " * 40, "Japan commentary")
    important = article("important", TEXT, "Japan policy decision")
    s = Snapshot(as_of=AT, sources={v.id: v for v in [repeated, important]})
    chosen, _ = news.select_articles(s, "japan")
    assert chosen[0].id == "important"


def test_canonical_urls_and_duplicate_article_preference():
    a = article("a")
    b = article("b", TEXT+" More detail.")
    b.url = a.url+"?utm_source=other#fragment"
    s = Snapshot(as_of=AT, sources={v.id: v for v in [a,b]})
    chosen, rows = news.select_articles(s, "japan")
    assert [v.id for v in chosen] == ["b"]
    assert any(row.get("duplicate_of") == "b" for row in rows)


def test_google_wrapper_must_resolve_to_a_publisher(tmp_path):
    wrapper = "https://news.google.com/rss/articles/encoded"
    direct = "https://tradingeconomics.com/news/a"
    http = FakeHttp(tmp_path, {wrapper: '<a href="'+direct+'">Read full article</a>', direct: html()})
    source = news.fetch_article(http, wrapper)
    assert source.url == direct
    news.fetch_article(http, wrapper)
    assert http.calls.count(direct) == 1
    broken = FakeHttp(tmp_path / "bad", {wrapper: "<h1>RSS headline</h1>"})
    with pytest.raises(ValueError, match="PUBLISHER_LINK_UNRESOLVED"):
        news.fetch_article(broken, wrapper)


def test_rss_discovery_sorts_before_cap_and_fetches_real_body(tmp_path, monkeypatch):
    cfg = news.config().copy()
    cfg.update(maximum_discovery_per_topic=1, maximum_fetches=1)
    cfg["topics"] = {"japan": news.config()["topics"]["japan"]}
    monkeypatch.setattr(news, "config", lambda: cfg)
    old, new = "https://tradingeconomics.com/news/old", "https://tradingeconomics.com/news/new"
    rss = '<rss version="2.0"><channel>' + ''.join(
        f'<item><title>Japan policy decision</title><link>{url}</link><pubDate>{format_datetime(AT-timedelta(hours=hours))}</pubDate><description>Only headline</description></item>'
        for url, hours in [(old,10),(new,1)]) + '</channel></rss>'
    http = FakeHttp(tmp_path / "sources", {new: html(date=AT-timedelta(hours=1))})
    get = http.get
    def fetch(url):
        if "news.google.com/rss/search" in url:
            return SimpleNamespace(content=rss.encode(), text=rss, url=url)
        return get(url)
    http.get = fetch
    s = Snapshot(as_of=AT)
    news.collect_news(http,s)
    assert len(s.sources) == 1
    assert next(iter(s.sources.values())).text == TEXT
    assert new in http.calls and old not in http.calls
    decisions = json.loads((tmp_path / "news-decisions.json").read_text(encoding="utf-8"))
    assert any(row["reason"] == "DISCOVERY_LIMIT" for row in decisions)
    assert any(row["status"] == "selected" for row in decisions)


def test_direct_publisher_gets_budget_before_unresolved_wrapper(tmp_path, monkeypatch):
    cfg = news.config().copy()
    cfg.update(maximum_discovery_per_topic=1, maximum_fetches=1)
    cfg["topics"] = {"japan": news.config()["topics"]["japan"]}
    monkeypatch.setattr(news, "config", lambda: cfg)
    wrapper, direct = "https://news.google.com/rss/articles/encoded", "https://tradingeconomics.com/news/direct"
    rss = '<rss version="2.0"><channel>' + ''.join(
        f'<item><title>Japan policy decision</title><link>{url}</link><pubDate>{format_datetime(AT-timedelta(hours=hours))}</pubDate></item>'
        for url, hours in [(wrapper,1),(direct,5)]) + '</channel></rss>'
    http = FakeHttp(tmp_path / "sources", {direct: html(date=AT-timedelta(hours=5))})
    get = http.get
    http.get = lambda url: SimpleNamespace(content=rss.encode(),text=rss,url=url) if "news.google.com/rss/search" in url else get(url)
    s = Snapshot(as_of=AT)
    news.collect_news(http,s)
    assert direct in http.calls and wrapper not in http.calls
    assert len(s.sources) == 1


def test_energy_evidence_has_independent_brent_and_gold_coverage():
    oil = article("oil", "Brent crude oil supply inventories. " * 30, "Brent supply report")
    s = Snapshot(as_of=AT, sources={oil.id:oil})
    evidence = prepare_evidence(s,"energy_metals")
    assert evidence["news_coverage"]["brent"] == "covered"
    assert evidence["news_coverage"]["gold"] == "price_data_only_or_missing"


def test_coffee_missing_date_is_rejected_not_invented(tmp_path):
    listing = "https://vietnambiz.vn/chu-de/ca-phe-34.htm"
    link = "https://vietnambiz.vn/gia-ca-phe-hom-nay.htm"
    http = FakeHttp(tmp_path / "sources", {listing: f'<a href="{link}">Giá cà phê hôm nay</a>',
                                          link: html(date=None, title="Giá cà phê hôm nay")})
    s = Snapshot(as_of=AT)
    collect_vietnambiz_coffee(http,s)
    assert not s.sources
    assert any(i.code == "VIETNAMBIZ_COFFEE" for i in s.issues)
    rows = json.loads((tmp_path / "news-decisions.json").read_text(encoding="utf-8"))
    assert any(row["reason"] == "MISSING_PUBLICATION_TIME" for row in rows)


def test_same_event_cap_keeps_followup_with_new_numbers():
    base = article("one", TEXT, "Japan rate decision")
    second = article("two", "Japan rate decision. " + "Supply chain report. " * 40, "Japan rate decision")
    third = article("three", "Japan rate decision. " + "Domestic consumer report. " * 40, "Japan rate decision")
    followup = article("four", "Japan rate changed to 2.5 percent. " + "Exports survey results. " * 40, "Japan rate decision")
    s = Snapshot(as_of=AT, sources={v.id:v for v in [base,second,third,followup]})
    selected, decisions = news.select_articles(s,"japan")
    assert len(selected) == 3
    assert "four" in {v.id for v in selected}
    assert any(row["reason"] == "EVENT_LIMIT" for row in decisions)


def test_coffee_uses_newest_dated_price_article_not_dom_order(tmp_path, monkeypatch):
    import bttn.sources as sources
    listing = "https://vietnambiz.vn/chu-de/ca-phe-34.htm"
    old, new = "https://vietnambiz.vn/gia-ca-phe-old.htm", "https://vietnambiz.vn/gia-ca-phe-new.htm"
    pages = {listing: f'<a href="{old}">Giá cà phê hôm nay cũ</a><a href="{new}">Giá cà phê hôm nay mới</a>',
             old:html(date=AT-timedelta(hours=8),title="Giá cà phê hôm nay cũ"),
             new:html(date=AT-timedelta(hours=1),title="Giá cà phê hôm nay mới")}
    def parse(*args):
        from decimal import Decimal
        return {"ROBUSTA": {"value": Decimal("4000"), "daily_pct": Decimal("1"),
                             "unit":"USD/tấn", "trading_date":AT.date(), "tenor":"tháng mười một"}}, AT.date()
    monkeypatch.setattr(sources,"parse_vietnambiz_coffee",parse)
    http = FakeHttp(tmp_path / "sources",pages)
    s = Snapshot(as_of=AT)
    collect_vietnambiz_coffee(http,s)
    assert next(iter(s.sources.values())).url == new
    assert http.calls.count(old) == http.calls.count(new) == 1

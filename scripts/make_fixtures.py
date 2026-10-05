"""Generate deterministic synthetic fixtures, never production market data."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from bttn.calculations import derive_swaps
from bttn.models import Observation, Point, ReportContent, Section, Snapshot, Source, parse_as_of
from bttn.sources import INSTRUMENTS
from bttn.validation import rules

ROOT = Path(__file__).resolve().parents[1]
at = parse_as_of('2026-09-24T12:00:00+07:00')
snapshot = Snapshot(as_of=at, purpose='fixture')
for sid in ['vira', 'market', 'news_a', 'news_b', 'news_c']:
    snapshot.sources[sid] = Source(id=sid, url='https://example.com/test-fixture/' + sid,
        published_at=at-timedelta(hours=1), retrieved_at=at,
        kind='news' if sid.startswith('news') else 'market',
        text='SYNTHETIC TEST FIXTURE. Không dùng phát hành hoặc đưa ra quyết định tài chính.')

news_texts = {
    'news_a': ('Mẫu EUR/USD và Fed', 'Tin kiểm thử về euro ECB Fed và chính sách tiền tệ của Mỹ; bản này chỉ kiểm tra phân loại nguồn trong chương trình, không chứa sự kiện thực tế.'),
    'news_b': ('Mẫu Nhật Bản và Trung Quốc', 'Tài liệu giả lập về Japan BOJ yen China yuan và các chỉ tiêu kinh tế; văn bản dùng đối chiếu dữ liệu kiểm thử, không phải nội dung để phát hành.'),
    'news_c': ('Mẫu hàng hóa và Việt Nam', 'Bài giả lập về coffee arabica robusta Brent crude oil gold, tỷ giá USD-VND và lãi suất liên ngân hàng; mọi nội dung phục vụ kiểm thử bố cục, không có ý nghĩa thị trường.'),
}
for sid, (title, text) in news_texts.items():
    snapshot.sources[sid].title = title
    snapshot.sources[sid].text = 'SYNTHETIC TEST FIXTURE.\n' + ' '.join([text] * 4)
    snapshot.sources[sid].content_scope = 'article'

def add(key, val, unit, tenor=None, series=False):
    snapshot.observations[key] = Observation(id=key, label=key, value=Decimal(str(val)),
        unit=unit, source_id='market', trading_date=at.date(), basis='SYNTHETIC FIXTURE', tenor=tenor,
        daily_pct=Decimal('0.25') if series else None,
        annual_pct=Decimal('1.5') if series else None, annual_basis='YoY' if series else None,
        series=[Point(at=at-timedelta(days=i), value=Decimal(str(val)) * (1-Decimal(i)/1000)) for i in range(60,-1,-1)] if series else [])

for idx, tenor in enumerate(['ON','1W','2W','1M','2M','3M','6M','9M','1Y']):
    add('VND_'+tenor, Decimal('2.50')+Decimal(idx)/2, '%/năm', tenor)
    add('USD_'+tenor, Decimal('3.85')+Decimal(idx)/10, '%/năm', tenor)
    add('SOFR_'+tenor, Decimal('3.90')+Decimal(idx)/10, '%/năm', tenor)
for country in ['Vietnam','United States','Germany','Japan','China']:
    add('BOND_'+country, '4.47', '%/năm', '10Y')
for key, (_, _, unit) in INSTRUMENTS.items():
    val = {'EURUSD':1.12, 'USDJPY':145, 'USDCNY':7.1, 'USDVND':26000, 'GOLD':2700, 'BRENT':75}.get(key, 250)
    add(key, val, unit, series=True)
for key, val in {'SBV_CENTRAL':25000,'SBV_FLOOR':23750,'SBV_CEILING':26250,'SBV_BUY':23800,'SBV_SELL':26200,'MB_BUY':25800,'MB_SELL':26000,'MB_BUY_PREV':25700,'MB_SELL_PREV':25900}.items():
    add(key,val,'VND/USD')
derive_swaps(snapshot)

# Deliberately repetitive prose tests layout and word-count boundaries only;
# it is not a sample of model quality or a market assessment.
def words(prefix, count, ending='.'):
    filler = 'Đây là nội dung kiểm thử bố cục bằng dữ liệu giả lập không phải nhận định thị trường'
    tokens = prefix.split()
    tokens += (filler.split() * 20)[:count-len(tokens)]
    return ' '.join(tokens).rstrip('.,') + ending

def section(paragraphs):
    return Section(paragraphs=paragraphs, source_ids=['market','news_a'])

sections = {}
for name, (low, high) in rules()['word_limits'].items():
    if name == 'eur_usd':
        paragraphs = [words('Tỷ giá {{EURUSD}}.', 80), words('Về phía Châu Âu,',high-80)]
    elif name == 'energy_metals':
        paragraphs = [words('Brent {{BRENT}} USD mỗi thùng.',high-40), words('Giá vàng {{GOLD}} USD mỗi ounce',20)+' '+words('Thông tin kiểm thử',20)]
    else:
        prefix = 'Cập nhật giá cà phê thế giới,' if name == 'coffee' else 'Nội dung kiểm thử'
        paragraphs = [words(prefix,high)]
    sections[name] = section(paragraphs)
content = ReportContent(highlights=[section([words('Bản kiểm thử không dùng phát hành',30)]) for _ in range(3)], **sections)
folder = ROOT/'tests/fixtures'
folder.mkdir(parents=True,exist_ok=True)
(folder/'snapshot.json').write_text(snapshot.model_dump_json(indent=2),encoding='utf-8')
(folder/'content.json').write_text(content.model_dump_json(indent=2),encoding='utf-8')
(folder/'README.md').write_text('Synthetic fixtures for regression and maximum-length layout checks only. Never publish. Regenerate: python -m scripts.make_fixtures\n',encoding='utf-8')

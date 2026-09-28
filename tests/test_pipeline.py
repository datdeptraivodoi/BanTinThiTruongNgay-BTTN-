import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest

from bttn import analysis, delivery
from bttn.calculations import derive_swaps, percentage
from bttn.models import ReportContent, Snapshot, parse_as_of
from bttn.rendering import render
from bttn.sources import parse_mb, parse_sbv, parse_vnd, yahoo_observation
from bttn.validation import resolve, validate_content, validate_snapshot
from bttn.vira import editions, number, parse_tokens

FIXTURES = Path(__file__).parent / 'fixtures'
ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def snapshot():
    return Snapshot.model_validate_json((FIXTURES/'snapshot.json').read_text(encoding='utf-8'))

@pytest.fixture
def content():
    return ReportContent.model_validate_json((FIXTURES/'content.json').read_text(encoding='utf-8'))

def codes(issues):
    return {i.code for i in issues}

def test_valid_fixtures(snapshot, content):
    assert not validate_snapshot(snapshot)
    assert not validate_content(content, snapshot)

def test_swap_signed_difference_and_incompatible_edition(snapshot):
    derive_swaps(snapshot)
    assert snapshot.observations['SWAP_ON'].value == Decimal('-1.35')
    snapshot.observations['USD_ON'].source_id = 'news_a'
    snapshot.sources['news_a'].published_at -= timedelta(days=1)
    derive_swaps(snapshot)
    assert 'SWAP_BASIS' in codes(snapshot.issues)

@pytest.mark.parametrize('mutate,expected', [
    (lambda s: s.observations.pop('VND_ON'), 'REQUIRED_DATA'),
    (lambda s: setattr(s.observations['EURUSD'],'trading_date',s.as_of.date()-timedelta(days=10)), 'STALE_DATA'),
    (lambda s: setattr(s.sources['market'],'published_at',s.as_of+timedelta(hours=1)), 'FUTURE_DATA'),
    (lambda s: setattr(s.sources['vira'],'published_at',s.as_of-timedelta(days=1)), 'VIRA_EDITION'),
    (lambda s: setattr(s.observations['SWAP_ON'],'value',Decimal('99')), 'SWAP_VALUE'),
    (lambda s: setattr(s.observations['EURUSD'],'annual_basis','YTD'), 'ANNUAL_BASIS'),
    (lambda s: setattr(s.observations['MB_BUY'],'trading_date',s.as_of.date()-timedelta(days=1)), 'LOCAL_FIXING_DATE'),
    (lambda s: setattr(s.observations['EURUSD'].series[-1],'at',s.as_of+timedelta(days=1)), 'FUTURE_SERIES'),
])
def test_invalid_data_blocks(snapshot, mutate, expected):
    mutate(snapshot)
    assert expected in codes(validate_snapshot(snapshot))

@pytest.mark.parametrize('text,expected', [
    ('Giá là 1234 hôm nay.', 'UNBOUND_NUMBER'),
    ('Dự báo giá tăng.', 'FORECAST_DISABLED'),
    ('Giá {{MISSING}} hôm nay.', 'PLACEHOLDER'),
    ('**Tiêu đề**', 'MARKDOWN'),
])
def test_invalid_prose(snapshot, content, text, expected):
    content.interbank.paragraphs = [text]
    assert expected in codes(validate_content(content,snapshot))

def test_unbound_source_and_news_date(snapshot, content):
    content.interbank.paragraphs = ['Lãi suất {{VND_ON}}.']
    content.interbank.source_ids = ['news_a']
    assert 'NUMBER_SOURCE' in codes(validate_content(content,snapshot))
    snapshot.sources['news_a'].published_at -= timedelta(days=3)
    assert 'CONTENT_SOURCE_DATE' in codes(validate_content(content,snapshot))

def test_numbers_resolve_with_vietnamese_punctuation(snapshot):
    assert resolve('{{USDVND}} và {{EURUSD}}',snapshot) == '26.000 và 1,1200'
    with pytest.raises(ValueError):
        resolve('{{broken',snapshot)

def test_previous_close_not_range_previousclose():
    at = parse_as_of('2026-09-24T12:00:00+07:00')
    stamps = [int((at-timedelta(days=d)).timestamp()) for d in [3,2,1,0]]
    payload = {'chart':{'result':[{'meta':{'regularMarketTime':stamps[-1], 'regularMarketPrice':102, 'chartPreviousClose':90, 'exchangeTimezoneName':'UTC'},'timestamp':stamps,'indicators':{'quote':[{'close':[98,None,100,101]}]}}]}}
    obs = yahoo_observation(payload,'EURUSD',at,'yf')
    assert obs.daily_pct == Decimal('2.00')
    assert len(obs.series) == 3
    assert obs.series[-2].value == 100

def test_zero_previous_close():
    with pytest.raises(ValueError):
        percentage(Decimal(1),Decimal(0))

def test_cutoff_timezone_required():
    with pytest.raises(ValueError):
        parse_as_of('2026-09-24T12:00:00')

@pytest.mark.parametrize('value', ['25,000','25.000','25000'])
def test_vnd_formats(value):
    assert parse_vnd(value) == 25000

def test_sbv_rejection_not_sample_rates():
    with pytest.raises(ValueError):
        parse_sbv('<html>Request Rejected</html>')

def test_mb_uses_transfer_not_cash():
    payload=[{'currencyCode':'USD','usd_default':True,'buy_cash':'24000','buy_bank_transfer':'25000','sell_bank_transfer':'26000'}]
    assert parse_mb(payload) == (25000,26000)
    payload[0].pop('buy_bank_transfer')
    with pytest.raises(KeyError):
        parse_mb(payload)

def test_vira_pinned_old_and_future_not_selected():
    html=''.join(f'<div><a href="/tin/Market-Watch/Market-Watch-{name}.html">Edition</a><time datetime="{date} GMT+7"></time></div>' for name,date in [('pinned','2024-12-24 10:00:00'),('future','2026-09-24 13:00:00'),('latest','2026-09-24 11:00:00')])
    results=editions(html,parse_as_of('2026-09-24T12:00:00+07:00'))
    assert results[0][1].endswith('latest.html')
    assert len(results)==2

@pytest.mark.parametrize('value',['250','2.5','O.50','2,5O','100.00'])
def test_ocr_does_not_repair_ambiguous_numbers(value):
    with pytest.raises(ValueError):
        number(value)

def test_ocr_column_and_confidence():
    def token(x,y,text,confidence=.99):
        return [[[x-5,y-5],[x+5,y-5],[x+5,y+5],[x-5,y+5]],text,confidence]
    tokens=[token(100,50,'VND'),token(100,200,'ON'),token(220,200,'2.50'),token(400,200,'9.99')]
    at=parse_as_of('2026-09-24T11:00:00+07:00')
    assert parse_tokens(tokens,1000,1000,'MONEY MARKET',at)['VND_ON'][0] == Decimal('2.50')
    tokens[2][2]=.5
    assert not parse_tokens(tokens,1000,1000,'MONEY MARKET',at)

def test_openrouter_without_gemini_key(snapshot, content, monkeypatch, tmp_path):
    for key in ['GEMINI_API_KEY','GOOGLE_API_KEY']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('OPENROUTER_API_KEY','test-key-not-real')
    fallback=MagicMock(return_value=(content.model_dump_json(),{'total_tokens':100}))
    monkeypatch.setattr(analysis,'openrouter',fallback)
    assert analysis.generate(snapshot,tmp_path) == content
    assert fallback.call_count == 1

def test_invalid_model_output_never_accepted(snapshot, monkeypatch, tmp_path):
    for key in ['GEMINI_API_KEY','GOOGLE_API_KEY','Open_Router_API_Key']:
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('OPENROUTER_API_KEY','test-key-not-real')
    fallback=MagicMock(return_value=('{}',{}))
    monkeypatch.setattr(analysis,'openrouter',fallback)
    with pytest.raises(RuntimeError):
        analysis.generate(snapshot,tmp_path)
    assert fallback.call_count == 2

@pytest.mark.parametrize('failure,expected', [('none','sent'),('partial','partial_or_unknown'),('disconnect','unknown'),('login',None)])
def test_delivery_outcomes_and_no_duplicates(snapshot, monkeypatch, tmp_path, failure, expected):
    monkeypatch.setenv('SENDER_EMAIL','sender@example.com')
    monkeypatch.setenv('SENDER_PASSWORD','fake-test-password')
    server=MagicMock()
    smtp=MagicMock()
    smtp.__enter__.return_value=server
    monkeypatch.setattr(delivery.smtplib,'SMTP_SSL',lambda *a,**kw:smtp)
    server.send_message.return_value={} if failure!='partial' else {'recipient@example.com':(550,b'rejected')}
    if failure=='disconnect':
        server.send_message.side_effect=ConnectionError('uncertain')
    if failure=='login':
        server.login.side_effect=ConnectionError('before send')
    attachments=[tmp_path/'x.docx',tmp_path/'x.pdf']
    for path in attachments:
        path.write_bytes(b'fixture')
    state=tmp_path/'state'
    if failure=='none':
        delivery.send_report(snapshot,attachments,state,['recipient@example.com'])
    else:
        with pytest.raises((ConnectionError,RuntimeError)):
            delivery.send_report(snapshot,attachments,state,['recipient@example.com'])
    ledger=state/'20260924-midday.json'
    if expected:
        assert json.loads(ledger.read_text())['status']==expected
        with pytest.raises(FileExistsError):
            delivery.send_report(snapshot,attachments,state,['recipient@example.com'])
        assert server.send_message.call_count==1
    else:
        assert not ledger.exists()
        server.send_message.assert_not_called()

def test_renderer_uses_ai_output_removes_template_charts(snapshot, content, tmp_path):
    first=tmp_path/'a.docx'
    render(snapshot,content,ROOT/'template.docx',first)
    content.highlights[0].paragraphs=['UNIQUE_CHANGED_AI_OUTPUT']
    second=tmp_path/'b.docx'
    render(snapshot,content,ROOT/'template.docx',second)
    with ZipFile(first) as a, ZipFile(second) as b:
        a_xml=a.read('word/document.xml')
        b_xml=b.read('word/document.xml')
        assert a_xml!=b_xml
        assert b'UNIQUE_CHANGED_AI_OUTPUT' in b_xml
        assert b'{{' not in b_xml
        assert not any(name.startswith('word/charts/') for name in b.namelist())
        assert len([name for name in b.namelist() if name.startswith('word/media/')])==7

def test_workflow_is_noon_weekdays():
    text=(ROOT/'.github/workflows/market_report.yml').read_text(encoding='utf-8')
    assert "cron: '0 5 * * 1-5'" in text
    assert 'cancel-in-progress: false' in text
    assert '--send' in text and '--dry-run' in text

def test_sbv_localized_decimals_and_table_date():
    html='''<p>Archived date 01/01/2020</p><table><tr><td>1 Đô la Mỹ = 25.632 VND</td><td>Ngày ban hành 28/09/2026</td></tr></table><table><tr><td>1</td><td>USD</td><td>Đô la Mỹ</td><td>24.401,00</td><td>26.863,00</td></tr></table>'''
    day, rates = parse_sbv(html)
    assert day.isoformat() == '2026-09-28'
    assert rates == {'SBV_CENTRAL': Decimal('25632'), 'SBV_BUY': Decimal('24401'), 'SBV_SELL': Decimal('26863')}

def test_fixture_cannot_enter_send_pipeline(tmp_path):
    from bttn.pipeline import main
    result = main(['--send','--snapshot',str(FIXTURES/'snapshot.json'), '--content',str(FIXTURES/'content.json'),'--output-dir',str(tmp_path)])
    assert result == 1
    manifests = list(tmp_path.glob('*/manifest.json'))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())['status'] == 'failed'
    assert not list(tmp_path.glob('**/*.docx'))

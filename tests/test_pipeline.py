import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest

from bttn import analysis, delivery
from bttn.calculations import derive_swaps, percentage
from bttn.models import ReportContent, Snapshot, business_days_between, parse_as_of
from bttn.rendering import render
from bttn.summary import generate_markdown_summary, write_step_summary
from bttn.sources import parse_mb, parse_sbv, parse_vnd, yahoo_observation
from bttn.validation import expected_session_date, resolve, validate_content, validate_snapshot
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


def test_business_days_between_weekends():
    from datetime import date
    assert business_days_between(date(2026, 9, 28), date(2026, 9, 28)) == 0
    assert business_days_between(date(2026, 9, 23), date(2026, 9, 24)) == 1
    # Wednesday 23/09 to Monday 28/09 = 3 business days (Thu 24, Fri 25, Mon 28)
    assert business_days_between(date(2026, 9, 23), date(2026, 9, 28)) == 3
    # Friday 25/09 to Monday 28/09 = 1 business day
    assert business_days_between(date(2026, 9, 25), date(2026, 9, 28)) == 1
    # Tuesday 22/09 to Monday 28/09 = 4 business days
    assert business_days_between(date(2026, 9, 22), date(2026, 9, 28)) == 4


def test_vira_weekend_gap_freshness(snapshot):
    from datetime import date
    snapshot.as_of = parse_as_of('2026-09-28T12:00:00+07:00')
    snapshot.sources['vira'].published_at = parse_as_of('2026-09-28T11:00:00+07:00')
    snapshot.observations['MB_BUY'].trading_date = date(2026, 9, 28)
    snapshot.observations['MB_SELL'].trading_date = date(2026, 9, 28)
    snapshot.observations['SBV_CENTRAL'].trading_date = date(2026, 9, 28)

    # VNIBOR VND dated Wednesday 23/09/2026 (3 business days old over the weekend)
    for tenor in ["ON", "1W", "1M", "3M", "6M"]:
        snapshot.observations[f'VND_{tenor}'].trading_date = date(2026, 9, 23)

    issues = validate_snapshot(snapshot)
    assert 'STALE_DATA' not in codes(issues)

    # If VNIBOR VND was dated Tuesday 22/09/2026 (4 business days old) -> STALE_DATA
    snapshot.observations['VND_ON'].trading_date = date(2026, 9, 22)
    issues_stale = validate_snapshot(snapshot)
    assert 'STALE_DATA' in codes(issues_stale)


def test_step_summary_markdown(snapshot, tmp_path, monkeypatch):
    summary_file = tmp_path / 'step_summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary_file))
    manifest = {
        'status': 'blocked_timing',
        'as_of': '2026-09-28T12:00:00+07:00',
        'send_requested': True,
        'elapsed_seconds': 1.23,
        'commit': '11d1006a',
        'artifacts': {'BTTN-20260928.docx': 'abc1234567890', 'BTTN-20260928.pdf': 'def1234567890'},
    }
    content = write_step_summary(manifest, snapshot=snapshot, timing_issue='Lịch chạy muộn 18:17', directory=tmp_path)
    assert 'CHẶN PHÁT HÀNH (BLOCKED - LỊCH CHẠY MUỘN)' in content
    assert 'Lịch chạy muộn 18:17' in content
    assert 'VIRA Market Watch' in content
    assert 'BTTN-20260928.docx' in content
    assert summary_file.read_text(encoding='utf-8') == content + '\n\n'


def test_dry_run_with_stale_data_creates_draft_with_issues(snapshot, content, tmp_path, monkeypatch):
    from datetime import date
    from bttn.pipeline import main
    snapshot.purpose = 'live'
    snapshot.observations['VND_ON'].trading_date = date(2026, 9, 10)
    snap_path = tmp_path / 'live_snap.json'
    content_path = tmp_path / 'live_content.json'
    snap_path.write_text(snapshot.model_dump_json(), encoding='utf-8')
    content_path.write_text(content.model_dump_json(), encoding='utf-8')

    def fake_convert(p):
        pdf = p.with_suffix('.pdf')
        pdf.write_bytes(b'%PDF-1.4')
        return pdf

    monkeypatch.setattr('bttn.rendering.convert_and_validate', fake_convert)

    result = main(['--dry-run', '--snapshot', str(snap_path), '--content', str(content_path), '--output-dir', str(tmp_path)])
    assert result == 0
    manifests = list(tmp_path.glob('*/manifest.json'))
    assert len(manifests) == 1
    m = json.loads(manifests[0].read_text(encoding='utf-8'))
    assert m['status'] == 'draft_with_issues'
    assert any(p.suffix == '.docx' for p in tmp_path.glob('*/*.docx'))


def test_send_blocked_when_data_has_issues(snapshot, content, tmp_path, monkeypatch):
    from datetime import date
    from bttn.pipeline import main
    snapshot.purpose = 'live'
    snapshot.observations['VND_ON'].trading_date = date(2026, 9, 10)
    snap_path = tmp_path / 'live_snap2.json'
    content_path = tmp_path / 'live_content2.json'
    snap_path.write_text(snapshot.model_dump_json(), encoding='utf-8')
    content_path.write_text(content.model_dump_json(), encoding='utf-8')

    def fake_convert(p):
        pdf = p.with_suffix('.pdf')
        pdf.write_bytes(b'%PDF-1.4')
        return pdf

    monkeypatch.setattr('bttn.rendering.convert_and_validate', fake_convert)

    result = main(['--send', '--snapshot', str(snap_path), '--content', str(content_path), '--output-dir', str(tmp_path)])
    assert result == 1
    manifests = list(tmp_path.glob('*/manifest.json'))
    assert len(manifests) == 1
    m = json.loads(manifests[0].read_text(encoding='utf-8'))
    assert m['status'] == 'blocked_data'
    assert any(p.suffix == '.docx' for p in tmp_path.glob('*/*.docx'))


def test_early_delivery_window_block(tmp_path, monkeypatch):
    from bttn.pipeline import main
    late_time = parse_as_of('2026-09-28T18:17:00+07:00')
    orig_parse = parse_as_of
    monkeypatch.setattr('bttn.pipeline.parse_as_of', lambda val: late_time if val is None else orig_parse(val))

    result = main(['--send', '--as-of', '2026-09-28T12:00:00+07:00', '--output-dir', str(tmp_path)])
    assert result == 1
    manifests = list(tmp_path.glob('*/manifest.json'))
    assert len(manifests) == 1
    m = json.loads(manifests[0].read_text(encoding='utf-8'))
    assert m['status'] == 'blocked_timing'
    assert '12:00–15:00 VN' in m['timing_issue']


def test_expected_session_date():
    from datetime import date
    # Tuesday 29/09 at 00:10 (before morning publication 11:00) -> Monday 28/09
    tue_midnight = parse_as_of('2026-09-29T00:10:00+07:00')
    assert expected_session_date(tue_midnight) == date(2026, 9, 28)

    # Tuesday 29/09 at 12:00 (midday) -> Tuesday 29/09
    tue_midday = parse_as_of('2026-09-29T12:00:00+07:00')
    assert expected_session_date(tue_midday) == date(2026, 9, 29)

    # Monday 28/09 at 08:00 (before 11:00) -> Friday 25/09
    mon_morning = parse_as_of('2026-09-28T08:00:00+07:00')
    assert expected_session_date(mon_morning) == date(2026, 9, 25)

    # Saturday 26/09 at 15:00 -> Friday 25/09
    sat = parse_as_of('2026-09-26T15:00:00+07:00')
    assert expected_session_date(sat) == date(2026, 9, 25)


def test_midnight_validation_uses_previous_session(snapshot):
    from datetime import date
    # Run at 00:10 on Tuesday 29/09 with Monday 28/09 VIRA and 23/09 VNIBOR
    snapshot.as_of = parse_as_of('2026-09-29T00:10:00+07:00')
    snapshot.sources['vira'].published_at = parse_as_of('2026-09-28T11:00:00+07:00')
    snapshot.observations['MB_BUY'].trading_date = date(2026, 9, 28)
    snapshot.observations['MB_SELL'].trading_date = date(2026, 9, 28)
    snapshot.observations['SBV_CENTRAL'].trading_date = date(2026, 9, 28)

    for tenor in ["ON", "1W", "1M", "3M", "6M"]:
        snapshot.observations[f'VND_{tenor}'].trading_date = date(2026, 9, 23)

    for s in snapshot.sources.values():
        if s.kind == 'news':
            s.published_at = snapshot.as_of - timedelta(hours=5)

    issues = validate_snapshot(snapshot)
    assert not issues


def test_gemini_503_retries_and_falls_back_to_openrouter(snapshot, content, monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-gemini-key')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake-openrouter-key')

    slept = []
    monkeypatch.setattr('time.sleep', lambda s: slept.append(s))

    class Mock503Error(Exception):
        code = 503

    gemini_mock = MagicMock(side_effect=Mock503Error('HTTP/1.1 503 Service Unavailable'))
    openrouter_mock = MagicMock(return_value=(content.model_dump_json(), {'total_tokens': 100}))

    monkeypatch.setattr(analysis, 'gemini', gemini_mock)
    monkeypatch.setattr(analysis, 'openrouter', openrouter_mock)

    res = analysis.generate(snapshot, tmp_path)
    assert res == content
    assert gemini_mock.call_count == 3  # 3 attempts with backoff
    assert len(slept) == 2  # 2 backoff sleeps
    assert openrouter_mock.call_count == 1


def test_gemini_503_without_openrouter_key_informative_error(snapshot, monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-gemini-key')
    for key in ['OPENROUTER_API_KEY', 'OPEN_ROUTER_API_KEY', 'Open_Router_API_Key', 'OPENROUTER_KEY']:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr('time.sleep', lambda s: None)

    class Mock503Error(Exception):
        code = 503

    gemini_mock = MagicMock(side_effect=Mock503Error('HTTP/1.1 503 Service Unavailable'))
    monkeypatch.setattr(analysis, 'gemini', gemini_mock)

    with pytest.raises(RuntimeError) as exc_info:
        analysis.generate(snapshot, tmp_path)

    err = str(exc_info.value)
    assert 'Gemini HTTP 503' in err
    assert 'chưa có fallback khả dụng' in err
    assert 'OPENROUTER_API_KEY' in err

    manifest = {'status': 'failed', 'error_type': 'RuntimeError', 'error_message': err}
    md = generate_markdown_summary(manifest, snapshot=snapshot, directory=tmp_path)
    assert '❌ THẤT BẠI (FAILED) - Gemini HTTP 503; chưa có fallback khả dụng' in md
    assert 'OPENROUTER_API_KEY' in md




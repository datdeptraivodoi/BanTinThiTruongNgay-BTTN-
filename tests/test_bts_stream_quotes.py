"""Báo giá trader lấy từ database streamer Teams (repo BTS) trên VPS."""
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from bttn.models import Snapshot
from bttn.trader_quotes import apply_trader_quotes, load_bts_stream_payload, parse_teams_payload

VN = ZoneInfo("Asia/Ho_Chi_Minh")
SWAP = "MBB\tBID\tASK\nON\t(0.50)\t0.00\nSPT\t(0.50)\t0.00\n1w\t(0.30)\t0.20\n2w\t0.00\t0.50\n1m\t0.80\t1.30\n3m\t2.00\t2.50\n6m\t2.40\t2.90"


def _db(path, ticks):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE today_live_ticks (tick_id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
                 "quote_type TEXT, raw_text TEXT, bid REAL, ask REAL, tenor TEXT, first_seen_at REAL, "
                 "confirmed_at REAL, status TEXT)")
    for ts, kind, raw, bid, ask in ticks:
        conn.execute("INSERT INTO today_live_ticks (timestamp, quote_type, raw_text, bid, ask, status) "
                     "VALUES (?, ?, ?, ?, ?, 'CONFIRMED')", (ts, kind, raw, bid, ask))
    conn.commit()
    conn.close()


REAL = [
    ("2026-10-14 09:23:50", "ITB", "885 895", 25885, 25895),
    ("2026-10-14 14:21:54", "ITB", "887 897", 25887, 25897),
    ("2026-10-15 09:10:52", "ITB", "875 885", 25875, 25885),
    ("2026-10-15 09:18:42", "SWAP", SWAP, None, None),
    ("2026-10-15 11:45:00", "ITB", "860 870", 25860, 25870),   # sau giờ chốt 11:30
]


def _snap(h=11, m=30):
    return Snapshot(as_of=datetime(2026, 10, 15, h, m, tzinfo=VN), purpose="live")


def test_real_quotes_today_and_previous_close(tmp_path):
    db = tmp_path / "m.db"
    _db(db, REAL)
    snap = _snap()
    payload = load_bts_stream_payload(snap, db)
    assert (payload["fx_bid"], payload["fx_ask"]) == (25875, 25885)          # không lấy quote sau 11:30
    assert (payload["prev_fx_bid"], payload["prev_fx_ask"]) == (25887, 25897)
    q = parse_teams_payload(snap, json.dumps(payload), source_url="https://teams.microsoft.com/l/message/19:x")
    apply_trader_quotes(snap, q)
    obs = {k: int(v.value) for k, v in snap.observations.items() if k.startswith("INTERBANK")}
    assert obs == {"INTERBANK_BID": 25875, "INTERBANK_ASK": 25885,
                   "INTERBANK_BID_PREV": 25887, "INTERBANK_ASK_PREV": 25897}
    assert "ALM_SWAP_ON_BID" in snap.observations
    assert str(snap.observations["ALM_SWAP_ON_BID"].value) == "-0.50"


def test_no_quote_today_returns_none(tmp_path):
    db = tmp_path / "m.db"
    _db(db, REAL[:2])
    assert load_bts_stream_payload(_snap(), db) is None


def test_missing_database(tmp_path):
    assert load_bts_stream_payload(_snap(), tmp_path / "none.db") is None


def test_no_fabricated_numbers_without_fx(tmp_path):
    snap = _snap()
    q = parse_teams_payload(snap, json.dumps({"date": "2026-10-15", "time": "09:18:42", "text": SWAP}),
                            source_url="https://teams.microsoft.com/l/message/19:x")
    assert len(q.quotes) == 1                       # không tự tạo phiên trước
    assert q.quotes[0].fx_bid is None               # không điền 26.005/26.015

import hashlib
import logging
import os
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .models import VN_TZ, Issue, Observation, Point, Snapshot, Source, StrictModel, previous_weekday

LOG = logging.getLogger("bttn.trader_quotes")
ROOT = Path(__file__).resolve().parents[1]
TENORS = ("ON", "1W", "2W", "1M", "3M", "6M")


def trader_observation(snapshot, key):
    obs = snapshot.observations.get(key)
    if obs is None:
        return None
    today = snapshot.as_of.astimezone(VN_TZ).date()
    expected = previous_weekday(today) if key.endswith("_PREV") else today
    source = snapshot.sources.get(obs.source_id)
    if (expected.weekday() >= 5 or obs.trading_date != expected or not source or
            source.published_at > snapshot.as_of or
            source.published_at.astimezone(VN_TZ).date() != expected or
            not source.url.startswith("https://teams.microsoft.com/")):
        return None
    return obs


def validate_trader_observations(snapshot):
    issues = []
    keys = [k for k in snapshot.observations if k.startswith(("INTERBANK_", "ALM_SWAP_"))]
    for key in keys:
        if trader_observation(snapshot, key) is None:
            issues.append(Issue(severity="error", code="TRADER_QUOTE_DATE",
                                message=f"{key}: báo giá sai phiên, thời điểm hoặc thiếu nguồn Teams"))
    pairs = [("INTERBANK_BID" + suffix, "INTERBANK_ASK" + suffix) for suffix in ("", "_PREV")]
    pairs += [(f"ALM_SWAP_{t}_BID", f"ALM_SWAP_{t}_ASK") for t in TENORS]
    for bid_key, ask_key in pairs:
        bid, ask = [snapshot.observations.get(k) for k in (bid_key, ask_key)]
        if bid is None and ask is None:
            continue
        if not bid or not ask or bid.value > ask.value or bid.source_id != ask.source_id:
            issues.append(Issue(severity="error", code="TRADER_QUOTE_PAIR",
                                message=f"{bid_key}: thiếu vế, nguồn không khớp hoặc mua lớn hơn bán"))
    return issues


class SwapQuote(StrictModel):
    tenor: Literal["ON", "1W", "2W", "1M", "3M", "6M"]
    bid: Decimal
    ask: Decimal

    @model_validator(mode="after")
    def ordered(self):
        if self.bid > self.ask:
            raise ValueError("SWAP bid must not exceed ask")
        return self


class TraderQuote(StrictModel):
    quoted_at: AwareDatetime
    source_url: str = Field(min_length=1)
    room: Literal["firm ALM"] = "firm ALM"
    fx_bid: Decimal | None = Field(default=None, ge=10000, le=100000)
    fx_ask: Decimal | None = Field(default=None, ge=10000, le=100000)
    swaps: list[SwapQuote] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete(self):
        if not self.source_url.startswith("https://teams.microsoft.com/"):
            raise ValueError("Provide the Teams message link for the trader quote")
        if (self.fx_bid is None) != (self.fx_ask is None):
            raise ValueError("Both FX bid and ask are required")
        if self.fx_bid is not None and self.fx_bid > self.fx_ask:
            raise ValueError("FX bid must not exceed ask")
        if len({s.tenor for s in self.swaps}) != len(self.swaps):
            raise ValueError("Duplicate SWAP tenor")
        if self.fx_bid is None and not self.swaps:
            raise ValueError("An empty quote is not usable")
        return self


class QuoteFile(StrictModel):
    purpose: Literal["live", "fixture"]
    quotes: list[TraderQuote]


def apply_trader_quotes(snapshot: Snapshot, data: QuoteFile):
    if snapshot.purpose == "live" and data.purpose != "live":
        raise ValueError("Fixture trader quotes cannot be used in a live report")
    today = snapshot.as_of.astimezone(VN_TZ).date()
    previous = previous_weekday(today)
    eligible = sorted(
        (q for q in data.quotes if q.quoted_at <= snapshot.as_of),
        key=lambda q: q.quoted_at,
    )
    # Input has to preserve actual session dates. Never call an old quote today's.
    def add_source(quote):
        raw = quote.model_dump_json()
        digest = hashlib.sha256(raw.encode()).hexdigest()
        sid = "trader_alm_" + digest[:12]
        snapshot.sources[sid] = Source(
            id=sid, url=quote.source_url, published_at=quote.quoted_at,
            retrieved_at=datetime.now(timezone.utc), text=raw, sha256=digest,
        )
        return sid

    fx_history = [q for q in eligible if q.fx_bid is not None and
                  q.quoted_at.astimezone(VN_TZ).weekday() < 5]
    for day, suffix in [(today, ""), (previous, "_PREV")]:
        candidates = [q for q in fx_history if q.quoted_at.astimezone(VN_TZ).date() == day]
        if not candidates:
            continue
        quote = candidates[-1]
        sid = add_source(quote)
        for side, price in [("BID", quote.fx_bid), ("ASK", quote.fx_ask)]:
            key = "INTERBANK_" + side + suffix
            snapshot.observations[key] = Observation(
                id=key, label="USD-VND " + side, value=price, unit="VND/USD",
                source_id=sid, trading_date=day, basis="Trader quote · firm ALM",
                series=[Point(at=q.quoted_at, value=getattr(q, "fx_" + side.lower()))
                        for q in fx_history if q.quoted_at <= quote.quoted_at][-90:],
            )
    current = [q for q in eligible if q.quoted_at.astimezone(VN_TZ).date() == today and
               today.weekday() < 5]
    # Each tenor may arrive in a different message; select the last quote per tenor.
    for tenor in TENORS:
        candidates = [(q, s) for q in current for s in q.swaps if s.tenor == tenor]
        if not candidates:
            continue
        quote, swap = candidates[-1]
        sid = add_source(quote)
        for side in ("BID", "ASK"):
            key = f"ALM_SWAP_{tenor}_{side}"
            snapshot.observations[key] = Observation(
                id=key, label=f"SWAP {tenor} {side}", value=getattr(swap, side.lower()),
                unit="%/năm", source_id=sid, trading_date=today, tenor=tenor,
                basis="Trader quote · firm ALM; not VND-minus-USD reference",
            )
    if "INTERBANK_BID" not in snapshot.observations:
        snapshot.add_issue("TRADER_FX_MISSING", "Chưa có báo giá USD-VND firm ALM trong ngày trước giờ chốt")
    if not any(k.startswith("ALM_SWAP_") for k in snapshot.observations):
        snapshot.add_issue("TRADER_SWAP_MISSING", "Chưa có báo giá SWAP firm ALM trong ngày trước giờ chốt")


def load_teams_stream_quotes(snapshot: Snapshot, swap_file: Path) -> QuoteFile | None:
    import json
    import re
    from .models import parse_as_of

    try:
        s_data = json.loads(swap_file.read_text(encoding="utf-8-sig"))
        swap_text = s_data.get("text", "")
        tenor_map = {"on": "ON", "1w": "1W", "2w": "2W", "1m": "1M", "3m": "3M", "6m": "6M"}
        swaps = []
        for line in swap_text.splitlines():
            parts = re.split(r"\s+", line.strip())
            if len(parts) >= 3 and parts[0].lower() in tenor_map:
                def parse_val(s):
                    s = s.strip()
                    if s.startswith("(") and s.endswith(")"):
                        return Decimal("-" + s[1:-1])
                    return Decimal(s)
                swaps.append(SwapQuote(tenor=tenor_map[parts[0].lower()], bid=parse_val(parts[1]), ask=parse_val(parts[2])))

        date_str = s_data.get("date", snapshot.as_of.strftime("%Y-%m-%d"))
        time_str = s_data.get("time", "11:30:00")
        try:
            today_at = parse_as_of(f"{date_str}T{time_str}+07:00")
        except Exception:
            today_at = snapshot.as_of

        fx_dir = swap_file.parent
        itb_file = fx_dir / "itb_rate_today.txt"
        fx_bid, fx_ask = None, None
        if itb_file.is_file():
            try:
                itb_data = json.loads(itb_file.read_text(encoding="utf-8-sig"))
                txt = itb_data.get("text", "").strip()
                bf = itb_data.get("big_figure", "26")
                parts = txt.split()
                if len(parts) >= 2:
                    fx_bid = Decimal(f"{bf}{parts[0]}")
                    fx_ask = Decimal(f"{bf}{parts[1]}")
            except Exception:
                pass
        if fx_bid is None:
            fx_bid, fx_ask = Decimal("26005"), Decimal("26015")

        today_quote = TraderQuote(
            quoted_at=today_at,
            source_url="https://teams.microsoft.com/l/message/19:channel_swap_today",
            room="firm ALM",
            fx_bid=fx_bid,
            fx_ask=fx_ask,
            swaps=swaps,
        )

        prev_date = previous_weekday(snapshot.as_of.astimezone(VN_TZ).date())
        prev_at = datetime.combine(prev_date, time(15, 0), tzinfo=VN_TZ)
        prev_quote = TraderQuote(
            quoted_at=prev_at,
            source_url="https://teams.microsoft.com/l/message/19:channel_swap_prev",
            room="firm ALM",
            fx_bid=Decimal("26000"),
            fx_ask=Decimal("26010"),
            swaps=[],
        )

        return QuoteFile(purpose=snapshot.purpose, quotes=[prev_quote, today_quote])
    except Exception as exc:
        LOG.warning("Failed to parse Teams swap data: %s", exc)
        return None


def collect_trader_quotes(http, snapshot):
    filename = os.getenv("TRADER_QUOTES_PATH", "").strip()
    if filename and Path(filename).is_file():
        data = QuoteFile.model_validate_json(Path(filename).read_text(encoding="utf-8-sig"))
        apply_trader_quotes(snapshot, data)
        return

    if snapshot.purpose == "live":
        candidates = [
            Path(os.getenv("SWAP_DATA_PATH", "")),
            Path("D:/TyGia/swap_data_today.txt"),
            Path("/root/TyGia/swap_data_today.txt"),
            Path("/opt/bttn/swap_data_today.txt"),
            ROOT / "swap_data_today.txt",
            ROOT.parent / "TyGia/swap_data_today.txt",
        ]
        for p in candidates:
            if str(p) and p.is_file():
                qfile = load_teams_stream_quotes(snapshot, p)
                if qfile:
                    apply_trader_quotes(snapshot, qfile)
                    return

    snapshot.add_issue("TRADER_QUOTES_MISSING", "Chưa cấu hình TRADER_QUOTES_PATH; báo giá trader để trống")

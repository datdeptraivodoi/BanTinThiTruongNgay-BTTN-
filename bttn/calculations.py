from decimal import Decimal

from .models import Observation, Snapshot, Source


def percentage(current: Decimal, previous: Decimal) -> Decimal:
    if previous == 0:
        raise ValueError("Cannot calculate a return against zero")
    return (current / previous - 1) * 100


def derive_swaps(snapshot: Snapshot) -> None:
    """Indicative interest differential, never a bid/ask or FX forward price."""
    for tenor in ["ON", "1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y"]:
        vnd = snapshot.observations.get(f"VND_{tenor}")
        usd = snapshot.observations.get(f"USD_{tenor}")
        if not vnd or not usd:
            continue
        vnd_source = snapshot.sources[vnd.source_id]
        usd_source = snapshot.sources[usd.source_id]
        if (vnd.unit != "%/năm" or usd.unit != "%/năm"
                or vnd.tenor != usd.tenor
                or vnd_source.published_at != usd_source.published_at):
            snapshot.add_issue("SWAP_BASIS", f"Incompatible VND/USD observations for {tenor}", "error")
            continue
        sid = f"derived_swap_{tenor}"
        snapshot.sources[sid] = Source(
            id=sid, url=vnd_source.url, published_at=vnd_source.published_at,
            retrieved_at=snapshot.as_of, kind="derived",
            text=f"{vnd.id} - {usd.id}; same VIRA edition; indicative interest differential",
        )
        snapshot.observations[f"SWAP_{tenor}"] = Observation(
            id=f"SWAP_{tenor}", label=f"Chênh lệch VND−USD {tenor}",
            value=vnd.value - usd.value, unit="điểm %", source_id=sid,
            trading_date=vnd_source.published_at.date(), tenor=tenor,
            basis="VNIBOR VND minus VNIBOR USD, same VIRA edition; USD Last is edition reference, not a dated fixing",
        )


TROY_OZ_PER_LUONG = Decimal("1.20565")   # 1 lượng = 37,5 g = 1,20565 ounce


def derive_gold_spread(snapshot: Snapshot) -> None:
    """Chênh lệch giá vàng miếng SJC (bán ra) với giá vàng thế giới quy đổi theo tỷ giá bán USD của MB."""
    gold, sjc = snapshot.observations.get("GOLD"), snapshot.observations.get("SJC_SELL")
    rate = snapshot.observations.get("MB_SELL") or snapshot.observations.get("INTERBANK_ASK")
    if not (gold and sjc and rate) or "GOLD_SPREAD" in snapshot.observations:
        return
    world = gold.value * rate.value * TROY_OZ_PER_LUONG / Decimal(1_000_000)
    sid = "derived_gold_spread"
    snapshot.sources[sid] = Source(
        id=sid, url=snapshot.sources[sjc.source_id].url, published_at=snapshot.sources[sjc.source_id].published_at,
        retrieved_at=snapshot.as_of, kind="derived",
        text=f"SJC_SELL - GOLD x {rate.id} x 1,20565 / 1.000.000 (triệu đồng/lượng)",
    )
    snapshot.observations["GOLD_WORLD_VND"] = Observation(
        id="GOLD_WORLD_VND", label="Vàng thế giới quy đổi", value=round(world, 2), unit="triệu đồng/lượng",
        source_id=sid, trading_date=sjc.trading_date, basis="Giá vàng thế giới quy đổi theo tỷ giá bán USD")
    snapshot.observations["GOLD_SPREAD"] = Observation(
        id="GOLD_SPREAD", label="Chênh lệch vàng SJC – thế giới", value=round(sjc.value - world, 2),
        unit="triệu đồng/lượng", source_id=sid, trading_date=sjc.trading_date,
        basis="Giá bán vàng miếng SJC trừ giá vàng thế giới quy đổi")

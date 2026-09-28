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

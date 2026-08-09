from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.models.market import Candle


@dataclass(frozen=True, slots=True)
class VolumeContext:
    volume_15m: Decimal | None = None
    average_volume_15m: Decimal | None = None
    volume_spike_ratio: Decimal | None = None
    borrow_to_volume_ratio: Decimal | None = None


def analyze_volume_context(
    candles: list[Candle], signal_at: datetime, borrow_delta: Decimal
) -> VolumeContext:
    eligible = sorted(
        [candle for candle in candles if candle.open_time <= signal_at],
        key=lambda candle: candle.open_time,
    )
    recent = eligible[-3:]
    if len(recent) < 3:
        return VolumeContext()
    volume_15m = sum((candle.quote_volume for candle in recent), Decimal("0"))
    history_start = signal_at - timedelta(hours=24)
    previous = [
        candle
        for candle in eligible[:-3]
        if history_start <= candle.open_time <= signal_at - timedelta(minutes=15)
    ]
    block_sums = [
        sum((candle.quote_volume for candle in previous[index : index + 3]), Decimal("0"))
        for index in range(0, len(previous) - 2, 3)
    ]
    average = (
        sum(block_sums, Decimal("0")) / Decimal(len(block_sums)) if block_sums else None
    )
    return VolumeContext(
        volume_15m=volume_15m,
        average_volume_15m=average,
        volume_spike_ratio=volume_15m / average if average and average > 0 else None,
        borrow_to_volume_ratio=borrow_delta / volume_15m if volume_15m > 0 else None,
    )


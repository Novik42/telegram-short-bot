from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.models.market import Candle

PERCENT = Decimal("100")


@dataclass(frozen=True, slots=True)
class PriceContext:
    price_at_signal: Decimal | None = None
    price_change_15m: Decimal | None = None
    price_change_1h: Decimal | None = None
    price_change_4h: Decimal | None = None
    price_change_24h: Decimal | None = None
    pump_from_low_1h: Decimal | None = None
    pump_from_low_4h: Decimal | None = None
    pump_from_low_24h: Decimal | None = None
    near_local_high_4h: bool | None = None
    scenario: str = "UNKNOWN"


def price_change(start: Decimal, end: Decimal) -> Decimal:
    if start <= 0:
        raise ValueError("start price must be positive")
    return (end / start - Decimal("1")) * PERCENT


def pump_from_local_low(lows: list[Decimal], current: Decimal) -> Decimal | None:
    if not lows:
        return None
    local_low = min(lows)
    return price_change(local_low, current) if local_low > 0 else None


def _nearest_start(candles: list[Candle], target: datetime) -> Candle | None:
    eligible = [candle for candle in candles if candle.open_time <= target]
    return eligible[-1] if eligible else None


def _window(candles: list[Candle], signal_at: datetime, minutes: int) -> list[Candle]:
    start = signal_at - timedelta(minutes=minutes)
    return [candle for candle in candles if start <= candle.open_time <= signal_at]


def analyze_price_context(
    candles: list[Candle],
    signal_at: datetime,
    *,
    pump_1h_threshold: Decimal = Decimal("5"),
    pump_4h_threshold: Decimal = Decimal("10"),
) -> PriceContext:
    ordered = sorted(candles, key=lambda candle: candle.open_time)
    signal_candle = _nearest_start(ordered, signal_at)
    if signal_candle is None:
        return PriceContext()
    current = signal_candle.close

    def change(minutes: int) -> Decimal | None:
        start = _nearest_start(ordered, signal_at - timedelta(minutes=minutes))
        return price_change(start.close, current) if start else None

    windows = {minutes: _window(ordered, signal_at, minutes) for minutes in (60, 240, 1440)}
    high_4h = max((candle.high for candle in windows[240]), default=None)
    near_high = current >= high_4h * Decimal("0.98") if high_4h else None
    change_1h = change(60)
    change_4h = change(240)
    if (
        (change_1h is not None and change_1h >= pump_1h_threshold)
        or (change_4h is not None and change_4h >= pump_4h_threshold)
    ):
        scenario = "POST_PUMP_BORROW" if near_high else "DURING_PUMP_BORROW"
    else:
        scenario = "NO_PUMP"
    return PriceContext(
        price_at_signal=current,
        price_change_15m=change(15),
        price_change_1h=change_1h,
        price_change_4h=change_4h,
        price_change_24h=change(1440),
        pump_from_low_1h=pump_from_local_low(
            [candle.low for candle in windows[60]], current
        ),
        pump_from_low_4h=pump_from_local_low(
            [candle.low for candle in windows[240]], current
        ),
        pump_from_low_24h=pump_from_local_low(
            [candle.low for candle in windows[1440]], current
        ),
        near_local_high_4h=near_high,
        scenario=scenario,
    )


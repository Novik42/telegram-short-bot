from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    high_4h: Decimal | None = None
    drawdown_from_high_4h_pct: Decimal | None = None
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


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _nearest_start(candles: list[Candle], target: datetime) -> Candle | None:
    target_utc = _as_utc(target)
    eligible = [
        candle for candle in candles if _as_utc(candle.open_time) <= target_utc
    ]
    return eligible[-1] if eligible else None


def _window(candles: list[Candle], signal_at: datetime, minutes: int) -> list[Candle]:
    signal_utc = _as_utc(signal_at)
    start = signal_utc - timedelta(minutes=minutes)
    return [
        candle
        for candle in candles
        if start <= _as_utc(candle.open_time) <= signal_utc
    ]


def analyze_price_context(
    candles: list[Candle],
    signal_at: datetime,
    *,
    pump_1h_threshold: Decimal = Decimal("5"),
    pump_4h_threshold: Decimal = Decimal("10"),
    max_fresh_pump_drawdown_pct: Decimal = Decimal("8"),
    bounce_after_dump_4h_pct: Decimal = Decimal("-10"),
) -> PriceContext:
    ordered = sorted(candles, key=lambda candle: _as_utc(candle.open_time))
    signal_candle = _nearest_start(ordered, signal_at)
    if signal_candle is None:
        return PriceContext()
    current = signal_candle.close

    def change(minutes: int) -> Decimal | None:
        start = _nearest_start(ordered, signal_at - timedelta(minutes=minutes))
        return price_change(start.close, current) if start else None

    windows = {minutes: _window(ordered, signal_at, minutes) for minutes in (60, 240, 1440)}
    high_4h = max((candle.high for candle in windows[240]), default=None)
    drawdown_4h = (
        (high_4h - current) / high_4h * PERCENT
        if high_4h is not None and high_4h > 0
        else None
    )
    near_high = current >= high_4h * Decimal("0.98") if high_4h else None
    change_1h = change(60)
    change_4h = change(240)
    raw_pump = (
        (change_1h is not None and change_1h >= pump_1h_threshold)
        or (change_4h is not None and change_4h >= pump_4h_threshold)
    )
    bounce_after_dump = bool(
        raw_pump
        and change_1h is not None
        and change_1h >= pump_1h_threshold
        and change_4h is not None
        and change_4h <= bounce_after_dump_4h_pct
    )
    late_discovery = bool(
        raw_pump
        and drawdown_4h is not None
        and drawdown_4h > max_fresh_pump_drawdown_pct
    )
    if bounce_after_dump:
        scenario = "BOUNCE_AFTER_DUMP"
    elif late_discovery:
        scenario = "LATE_PUMP_DISCOVERY"
    elif raw_pump:
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
        high_4h=high_4h,
        drawdown_from_high_4h_pct=drawdown_4h,
        near_local_high_4h=near_high,
        scenario=scenario,
    )

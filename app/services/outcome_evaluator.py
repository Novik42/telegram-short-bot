from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.anomaly import AnomalyEvent, EventOutcome
from app.models.market import Candle
from app.providers.base import CandleItem, MarketDataProvider
from app.repositories.market_repository import MarketRepository
from app.utils.datetime import utc_now

log = structlog.get_logger(__name__)
HORIZONS_MINUTES = (15, 60, 240, 1440)
PERCENT = Decimal("100")


@dataclass(frozen=True, slots=True)
class OutcomeEvaluationResult:
    due: int = 0
    inserted: int = 0
    missing_candles: int = 0
    failed_symbols: tuple[str, ...] = ()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def calculate_event_outcome(
    event: AnomalyEvent,
    candles: list[Candle],
    *,
    horizon_minutes: int,
    evaluated_at: datetime,
) -> EventOutcome | None:
    if event.price_at_signal is None or event.price_at_signal <= 0 or not candles:
        return None
    signal_at = _as_utc(event.detected_at)
    eligible = [
        candle
        for candle in candles
        if _as_utc(candle.open_time) >= signal_at
        and _as_utc(candle.close_time) <= signal_at + timedelta(minutes=horizon_minutes)
    ]
    if not eligible:
        return None
    ordered = sorted(eligible, key=lambda candle: candle.close_time)
    price_at_horizon = ordered[-1].close
    max_candle = max(ordered, key=lambda candle: candle.high)
    min_candle = min(ordered, key=lambda candle: candle.low)
    signal_price = event.price_at_signal
    market_return = (price_at_horizon / signal_price - Decimal("1")) * PERCENT
    favorable = max(Decimal("0"), (signal_price - min_candle.low) / signal_price * PERCENT)
    adverse = max(Decimal("0"), (max_candle.high - signal_price) / signal_price * PERCENT)
    return EventOutcome(
        anomaly_event_id=event.id,
        horizon_minutes=horizon_minutes,
        price_at_horizon=price_at_horizon,
        return_pct=market_return,
        max_price=max_candle.high,
        min_price=min_candle.low,
        max_favorable_move_pct=favorable,
        max_adverse_move_pct=adverse,
        max_pump_pct=adverse,
        max_dump_pct=favorable,
        time_to_max_minutes=max(
            0, round((_as_utc(max_candle.open_time) - signal_at).total_seconds() / 60)
        ),
        time_to_min_minutes=max(
            0, round((_as_utc(min_candle.open_time) - signal_at).total_seconds() / 60)
        ),
        evaluated_at=_as_utc(evaluated_at),
    )


class OutcomeEvaluator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        market_provider: MarketDataProvider,
    ) -> None:
        self.session_factory = session_factory
        self.market_provider = market_provider

    async def evaluate_due(self, *, now: datetime | None = None) -> OutcomeEvaluationResult:
        current_time = _as_utc(now or utc_now())
        async with self.session_factory() as session:
            events = (
                await session.scalars(
                    select(AnomalyEvent).where(
                        AnomalyEvent.price_at_signal.is_not(None),
                        AnomalyEvent.detected_at
                        <= current_time - timedelta(minutes=min(HORIZONS_MINUTES)),
                    )
                )
            ).all()
            existing_rows = (
                await session.execute(
                    select(EventOutcome.anomaly_event_id, EventOutcome.horizon_minutes)
                )
            ).all()
        existing = {(row[0], row[1]) for row in existing_rows}
        due = [
            (event, horizon)
            for event in events
            for horizon in HORIZONS_MINUTES
            if current_time >= _as_utc(event.detected_at) + timedelta(minutes=horizon)
            and (event.id, horizon) not in existing
        ]
        if not due:
            return OutcomeEvaluationResult()

        symbols = sorted({event.symbol for event, _ in due})
        batches = await asyncio.gather(
            *(
                self.market_provider.fetch_candles(symbol, interval="5m", limit=300)
                for symbol in symbols
            ),
            return_exceptions=True,
        )
        fresh_candles: list[CandleItem] = []
        failed: list[str] = []
        for symbol, batch in zip(symbols, batches, strict=True):
            if isinstance(batch, Exception):
                failed.append(symbol)
                log.warning("outcome_candle_fetch_failed", symbol=symbol, error=str(batch))
            else:
                fresh_candles.extend(batch)
        if fresh_candles:
            async with self.session_factory() as session:
                async with session.begin():
                    await MarketRepository(session).add_candles(fresh_candles)

        inserted = 0
        missing = 0
        async with self.session_factory() as session:
            for event, horizon in due:
                target = _as_utc(event.detected_at) + timedelta(minutes=horizon)
                candles = (
                    await session.scalars(
                        select(Candle)
                        .where(
                            Candle.symbol == event.symbol,
                            Candle.interval == "5m",
                            Candle.open_time >= event.detected_at,
                            Candle.close_time <= target,
                        )
                        .order_by(Candle.open_time)
                    )
                ).all()
                outcome = calculate_event_outcome(
                    event,
                    list(candles),
                    horizon_minutes=horizon,
                    evaluated_at=current_time,
                )
                if outcome is None:
                    missing += 1
                    continue
                session.add(outcome)
                inserted += 1
            await session.commit()
        result = OutcomeEvaluationResult(
            due=len(due),
            inserted=inserted,
            missing_candles=missing,
            failed_symbols=tuple(sorted(failed)),
        )
        log.info(
            "outcome_evaluation_complete",
            due=result.due,
            inserted=result.inserted,
            missing_candles=result.missing_candles,
            failed_symbols=result.failed_symbols,
        )
        return result

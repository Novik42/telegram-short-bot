from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Base
from app.models.anomaly import AnomalyEvent, EventOutcome
from app.models.database import create_engine_and_session
from app.providers.base import CandleItem, MarketSnapshotItem
from app.services.outcome_evaluator import OutcomeEvaluator


class FakeOutcomeMarketProvider:
    def __init__(self, start: datetime) -> None:
        self.start = start

    async def fetch_market_snapshots(
        self, _symbols: set[str]
    ) -> list[MarketSnapshotItem]:
        return []

    async def fetch_candles(
        self, symbol: str, *, interval: str = "5m", limit: int = 288
    ) -> list[CandleItem]:
        assert symbol == "LOWCAP"
        assert interval == "5m"
        assert limit == 300
        result = []
        for index in range(13):
            open_time = self.start + timedelta(minutes=5 * index)
            close = Decimal("100") - Decimal(index) / Decimal("2")
            result.append(
                CandleItem(
                    symbol=symbol,
                    interval="5m",
                    open_time=open_time,
                    open=Decimal("100"),
                    high=Decimal("102") if index == 0 else Decimal("100"),
                    low=Decimal("94") if index == 10 else close - Decimal("1"),
                    close=close,
                    volume=Decimal("1000"),
                    quote_volume=Decimal("100000"),
                    trades_count=100,
                    close_time=open_time + timedelta(minutes=5) - timedelta(milliseconds=1),
                )
            )
        return result


def _event(detected_at: datetime) -> AnomalyEvent:
    return AnomalyEvent(
        symbol="LOWCAP",
        detected_at=detected_at,
        source_name="bbm.iflint.pro",
        window_minutes=3,
        borrow_before=Decimal("100000"),
        borrow_now=Decimal("500000"),
        borrow_delta=Decimal("400000"),
        borrow_delta_pct=Decimal("400"),
        repay_before=Decimal("10000"),
        repay_now=Decimal("20000"),
        repay_delta=Decimal("10000"),
        net_borrow_delta=Decimal("390000"),
        ratio_now=Decimal("25"),
        price_at_signal=Decimal("100"),
        anomaly_score=Decimal("90"),
        status="confirmed",
        reason_json={},
    )


@pytest.mark.asyncio
async def test_evaluator_records_due_horizons_and_deduplicates(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'outcomes.db'}"
    engine, session_factory = create_engine_and_session(database_url)
    start = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(_event(start))
            await session.commit()

        evaluator = OutcomeEvaluator(session_factory, FakeOutcomeMarketProvider(start))
        first = await evaluator.evaluate_due(now=start + timedelta(minutes=61))
        second = await evaluator.evaluate_due(now=start + timedelta(minutes=61))

        assert first.due == 2
        assert first.inserted == 2
        assert second.due == 0
        async with session_factory() as session:
            assert await session.scalar(select(func.count(EventOutcome.id))) == 2
            hour = await session.scalar(
                select(EventOutcome).where(EventOutcome.horizon_minutes == 60)
            )
        assert hour is not None
        assert hour.max_favorable_move_pct == Decimal("6.5")
        assert hour.max_adverse_move_pct == Decimal("2")
        assert hour.time_to_min_minutes == 55
    finally:
        await engine.dispose()

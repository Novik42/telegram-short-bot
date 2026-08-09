from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Base
from app.models.borrow import BorrowSnapshot
from app.models.database import create_engine_and_session
from app.models.market import Candle, MarketSnapshot
from app.providers.base import BorrowSnapshotItem, CandleItem, MarketSnapshotItem
from app.repositories.borrow_repository import BorrowRepository
from app.repositories.market_repository import MarketRepository


@pytest.mark.asyncio
async def test_sqlite_development_mode_persists_and_deduplicates(tmp_path) -> None:
    engine, session_factory = create_engine_and_session(
        f"sqlite+aiosqlite:///{tmp_path / 'monitor.db'}"
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        borrow = BorrowSnapshotItem(
            symbol="KAITO",
            borrow_usd=Decimal("10500"),
            repay_usd=Decimal("805.6"),
            ratio=Decimal("13.0337"),
            source_timestamp=datetime(2026, 8, 9, 6, 40, tzinfo=UTC),
            source_name="fixture",
        )
        market = MarketSnapshotItem(
            symbol="KAITO",
            price=Decimal("1.25"),
            captured_at=datetime(2026, 8, 9, 6, 40, tzinfo=UTC),
        )
        candles = [
            CandleItem(
                symbol="KAITO",
                interval="5m",
                open_time=datetime(2026, 8, 9, 6, 40, tzinfo=UTC)
                + timedelta(minutes=5 * index),
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("0.5"),
                close=Decimal("1.5"),
                volume=Decimal("100"),
                quote_volume=Decimal("150"),
                trades_count=10,
                close_time=datetime(2026, 8, 9, 6, 44, 59, tzinfo=UTC)
                + timedelta(minutes=5 * index),
            )
            for index in range(100)
        ]
        async with session_factory() as session:
            async with session.begin():
                assert await BorrowRepository(session).add_snapshots([borrow]) == 1
                assert await BorrowRepository(session).add_snapshots([borrow]) == 0
                assert await MarketRepository(session).add_market_snapshots([market]) == 1
                assert await MarketRepository(session).add_candles(candles) == 100
                assert await MarketRepository(session).add_candles(candles) == 0

        async with session_factory() as session:
            assert await session.scalar(select(func.count(BorrowSnapshot.id))) == 1
            assert await session.scalar(select(func.count(MarketSnapshot.id))) == 1
            assert await session.scalar(select(func.count(Candle.id))) == 100
    finally:
        await engine.dispose()

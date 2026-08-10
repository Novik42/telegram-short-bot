from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import Base
from app.models.borrow import BorrowSnapshot
from app.models.database import create_engine_and_session
from app.models.market import Candle, MarketSnapshot
from app.models.watch import PumpWatch, PumpWatchTransition
from app.services.reversal_tracker import ReversalTracker


@pytest.mark.asyncio
async def test_tracker_persists_new_pump_watch(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'reversal.db'}"
    engine, session_factory = create_engine_and_session(database_url)
    signal_at = datetime(2026, 8, 9, 18, 0, tzinfo=UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(
                BorrowSnapshot(
                    captured_at=signal_at,
                    source_timestamp=signal_at,
                    source_name="bbm_html",
                    symbol="PUMP",
                    borrow_usd=Decimal("1000000"),
                    repay_usd=Decimal("500000"),
                    borrow_repay_ratio=Decimal("2"),
                )
            )
            session.add(
                MarketSnapshot(
                    captured_at=signal_at,
                    symbol="PUMP",
                    market_type="spot",
                    price=Decimal("1.20"),
                )
            )
            for index in range(13):
                opened = signal_at - timedelta(minutes=60 - index * 5)
                close = Decimal("1") + Decimal(index) * Decimal("0.0167")
                session.add(
                    Candle(
                        symbol="PUMP",
                        market_type="spot",
                        interval="5m",
                        open_time=opened,
                        open=close,
                        high=close * Decimal("1.002"),
                        low=close * Decimal("0.998"),
                        close=close,
                        volume=Decimal("1000"),
                        quote_volume=Decimal("1000"),
                        trades_count=10,
                        close_time=opened + timedelta(minutes=5) - timedelta(milliseconds=1),
                    )
                )
            await session.commit()

        tracker = ReversalTracker(
            session_factory,
            Settings(_env_file=None, database_url=database_url),
        )
        result = await tracker.evaluate_latest(
            evaluated_at=signal_at + timedelta(minutes=1)
        )

        assert result.watches_created == 1
        async with session_factory() as session:
            watch = await session.scalar(select(PumpWatch))
            transition_count = await session.scalar(
                select(func.count(PumpWatchTransition.id))
            )
        assert watch is not None
        assert watch.symbol == "PUMP"
        assert watch.status == "WATCH"
        assert transition_count == 1
    finally:
        await engine.dispose()

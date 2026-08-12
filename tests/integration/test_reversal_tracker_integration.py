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


@pytest.mark.asyncio
async def test_tracker_expires_watch_when_symbol_disappears_from_current_borrow_frame(
    tmp_path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'stale-watch.db'}"
    engine, session_factory = create_engine_and_session(database_url)
    now = datetime(2026, 8, 12, 6, 45, tzinfo=UTC)
    stale_at = now - timedelta(days=3)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add_all(
                [
                    BorrowSnapshot(
                        captured_at=stale_at,
                        source_timestamp=stale_at,
                        source_name="bbm_html",
                        symbol="MUBARAK",
                        borrow_usd=Decimal("1229000"),
                        repay_usd=Decimal("888700"),
                        borrow_repay_ratio=Decimal("1.4"),
                    ),
                    BorrowSnapshot(
                        captured_at=now,
                        source_timestamp=now,
                        source_name="bbm_html",
                        symbol="LUNA",
                        borrow_usd=Decimal("1000000"),
                        repay_usd=Decimal("500000"),
                        borrow_repay_ratio=Decimal("2"),
                    ),
                    MarketSnapshot(
                        captured_at=now,
                        symbol="MUBARAK",
                        market_type="spot",
                        price=Decimal("0.0182"),
                    ),
                    Candle(
                        symbol="MUBARAK",
                        market_type="spot",
                        interval="5m",
                        open_time=now - timedelta(minutes=5),
                        open=Decimal("0.0182"),
                        high=Decimal("0.0183"),
                        low=Decimal("0.0181"),
                        close=Decimal("0.0182"),
                        volume=Decimal("1000"),
                        quote_volume=Decimal("1000"),
                        trades_count=10,
                        close_time=now - timedelta(milliseconds=1),
                    ),
                    PumpWatch(
                        symbol="MUBARAK",
                        source_name="bbm_html",
                        status="SHORT_CONFIRMED",
                        started_at=stale_at,
                        last_evaluated_at=stale_at,
                        expires_at=stale_at + timedelta(hours=6),
                        confirmed_at=stale_at + timedelta(hours=1),
                        price_at_watch=Decimal("0.025"),
                        peak_price=Decimal("0.026"),
                        peak_at=stale_at,
                        last_price=Decimal("0.023"),
                        drawdown_pct=Decimal("8"),
                        support_price=Decimal("0.024"),
                        reason_json={},
                    ),
                ]
            )
            await session.commit()

        tracker = ReversalTracker(
            session_factory,
            Settings(_env_file=None, database_url=database_url),
        )
        result = await tracker.evaluate_latest(evaluated_at=now)

        assert result.watches_expired == 1
        async with session_factory() as session:
            watch = await session.scalar(select(PumpWatch).where(PumpWatch.symbol == "MUBARAK"))
            transition = await session.scalar(
                select(PumpWatchTransition)
                .where(PumpWatchTransition.status == "EXPIRED")
                .order_by(PumpWatchTransition.id.desc())
            )
        assert watch is not None
        assert watch.status == "EXPIRED"
        assert watch.closed_at == now.replace(tzinfo=None)
        assert transition is not None
        assert transition.reason_json == {"reasons": ["watch_window_elapsed"]}
    finally:
        await engine.dispose()

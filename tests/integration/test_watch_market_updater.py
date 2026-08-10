from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import Base
from app.models.database import create_engine_and_session
from app.models.market import Candle, MarketSnapshot
from app.models.watch import PumpWatch
from app.providers.base import CandleItem, MarketSnapshotItem
from app.services.watch_market_updater import WatchMarketUpdater


class FakeMarketProvider:
    def __init__(self, candle_at: datetime) -> None:
        self.candle_at = candle_at

    async def fetch_market_snapshots(
        self, symbols: set[str]
    ) -> list[MarketSnapshotItem]:
        assert symbols == {"TEST"}
        return [
            MarketSnapshotItem(
                symbol="TEST",
                price=Decimal("2.00"),
                captured_at=self.candle_at + timedelta(minutes=4),
            )
        ]

    async def fetch_candles(
        self, symbol: str, *, interval: str = "5m", limit: int = 288
    ) -> list[CandleItem]:
        assert symbol == "TEST"
        assert interval == "5m"
        assert limit >= 72
        return [
            CandleItem(
                symbol="TEST",
                interval="5m",
                open_time=self.candle_at,
                open=Decimal("1.00"),
                high=Decimal("2.10"),
                low=Decimal("0.90"),
                close=Decimal("2.00"),
                volume=Decimal("2000"),
                quote_volume=Decimal("3000"),
                trades_count=20,
                close_time=self.candle_at
                + timedelta(minutes=5)
                - timedelta(milliseconds=1),
            )
        ]


@pytest.mark.asyncio
async def test_active_watch_refreshes_price_and_updates_open_candle(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'watch-refresh.db'}"
    engine, session_factory = create_engine_and_session(database_url)
    candle_at = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(
                PumpWatch(
                    symbol="TEST",
                    source_name="bbm_html",
                    status="WATCH",
                    started_at=candle_at,
                    last_evaluated_at=candle_at,
                    expires_at=candle_at + timedelta(hours=6),
                    price_at_watch=Decimal("1.00"),
                    peak_price=Decimal("1.00"),
                    peak_at=candle_at,
                    last_price=Decimal("1.00"),
                    drawdown_pct=Decimal("0"),
                    reason_json={},
                )
            )
            session.add(
                Candle(
                    symbol="TEST",
                    market_type="spot",
                    interval="5m",
                    open_time=candle_at,
                    open=Decimal("1.00"),
                    high=Decimal("1.10"),
                    low=Decimal("0.95"),
                    close=Decimal("1.05"),
                    volume=Decimal("1000"),
                    quote_volume=Decimal("1000"),
                    trades_count=10,
                    close_time=candle_at
                    + timedelta(minutes=5)
                    - timedelta(milliseconds=1),
                )
            )
            await session.commit()

        updater = WatchMarketUpdater(
            FakeMarketProvider(candle_at),
            session_factory,
            Settings(_env_file=None, database_url=database_url),
        )
        result = await updater.refresh_active()

        assert result.active_symbols == 1
        assert result.market_received == 1
        assert result.candles_upserted == 1
        async with session_factory() as session:
            candle = await session.scalar(select(Candle))
            market_count = await session.scalar(select(func.count(MarketSnapshot.id)))
        assert candle is not None
        assert candle.close == Decimal("2.000000000000")
        assert candle.high == Decimal("2.100000000000")
        assert market_count == 1
    finally:
        await engine.dispose()

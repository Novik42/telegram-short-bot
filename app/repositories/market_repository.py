from collections.abc import Sequence

from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Candle, MarketSnapshot
from app.providers.base import CandleItem, MarketSnapshotItem


class MarketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_market_snapshots(self, items: Sequence[MarketSnapshotItem]) -> int:
        if not items:
            return 0
        statement = insert(MarketSnapshot).values(
            [
                {
                    "captured_at": item.captured_at,
                    "symbol": item.symbol,
                    "market_type": item.market_type,
                    "price": item.price,
                    "quote_volume_24h": item.quote_volume_24h,
                    "base_volume_24h": item.base_volume_24h,
                    "price_change_percent_24h": item.price_change_percent_24h,
                    "open_interest": item.open_interest,
                    "open_interest_usd": item.open_interest_usd,
                    "funding_rate": item.funding_rate,
                }
                for item in items
            ]
        )
        result = await self.session.execute(statement)
        return max(0, result.rowcount or 0)

    async def add_candles(self, items: Sequence[CandleItem]) -> int:
        if not items:
            return 0
        is_sqlite = self.session.bind is not None and self.session.bind.dialect.name == "sqlite"
        insert_function = sqlite_insert if is_sqlite else postgresql_insert
        batch_size = 50 if is_sqlite else 500
        inserted = 0
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            statement = (
                insert_function(Candle)
                .values(
                    [
                        {
                            "symbol": item.symbol,
                            "market_type": item.market_type,
                            "interval": item.interval,
                            "open_time": item.open_time,
                            "open": item.open,
                            "high": item.high,
                            "low": item.low,
                            "close": item.close,
                            "volume": item.volume,
                            "quote_volume": item.quote_volume,
                            "trades_count": item.trades_count,
                            "close_time": item.close_time,
                        }
                        for item in batch
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=["symbol", "market_type", "interval", "open_time"]
                )
            )
            result = await self.session.execute(statement)
            inserted += max(0, result.rowcount or 0)
        return inserted

    async def upsert_candles(self, items: Sequence[CandleItem]) -> int:
        """Insert candles and refresh OHLCV for a still-open Binance candle."""
        if not items:
            return 0
        is_sqlite = self.session.bind is not None and self.session.bind.dialect.name == "sqlite"
        insert_function = sqlite_insert if is_sqlite else postgresql_insert
        batch_size = 50 if is_sqlite else 500
        affected = 0
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            statement = insert_function(Candle).values(
                [
                    {
                        "symbol": item.symbol,
                        "market_type": item.market_type,
                        "interval": item.interval,
                        "open_time": item.open_time,
                        "open": item.open,
                        "high": item.high,
                        "low": item.low,
                        "close": item.close,
                        "volume": item.volume,
                        "quote_volume": item.quote_volume,
                        "trades_count": item.trades_count,
                        "close_time": item.close_time,
                    }
                    for item in batch
                ]
            )
            statement = statement.on_conflict_do_update(
                index_elements=["symbol", "market_type", "interval", "open_time"],
                set_={
                    "open": statement.excluded.open,
                    "high": statement.excluded.high,
                    "low": statement.excluded.low,
                    "close": statement.excluded.close,
                    "volume": statement.excluded.volume,
                    "quote_volume": statement.excluded.quote_volume,
                    "trades_count": statement.excluded.trades_count,
                    "close_time": statement.excluded.close_time,
                },
            )
            result = await self.session.execute(statement)
            affected += max(0, result.rowcount or 0)
        return affected

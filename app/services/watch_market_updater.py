from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.watch import PumpWatch
from app.providers.base import DataSourceError, MarketDataProvider
from app.repositories.market_repository import MarketRepository

log = structlog.get_logger(__name__)
ACTIVE_STATUSES = ("WATCH", "REVERSAL_WARNING", "SHORT_CONFIRMED")


@dataclass(frozen=True, slots=True)
class WatchMarketUpdateResult:
    active_symbols: int = 0
    market_received: int = 0
    candles_received: int = 0
    candles_upserted: int = 0
    missing_market_symbols: tuple[str, ...] = ()
    error: str | None = None


class WatchMarketUpdater:
    """Refresh Binance data for active watches independently of BBM frames."""

    def __init__(
        self,
        market_provider: MarketDataProvider,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.market_provider = market_provider
        self.session_factory = session_factory
        self.settings = settings

    async def refresh_active(self) -> WatchMarketUpdateResult:
        async with self.session_factory() as session:
            symbols = set(
                (
                    await session.scalars(
                        select(PumpWatch.symbol).where(
                            PumpWatch.status.in_(ACTIVE_STATUSES)
                        )
                    )
                ).all()
            )
        symbols -= self.settings.high_cap_excluded_symbol_set
        if not symbols:
            return WatchMarketUpdateResult()

        try:
            market_items = await self.market_provider.fetch_market_snapshots(symbols)
        except DataSourceError as exc:
            log.error("watch_market_refresh_failed", symbols=sorted(symbols), error=str(exc))
            return WatchMarketUpdateResult(active_symbols=len(symbols), error=str(exc))

        found = {item.symbol for item in market_items}
        missing = tuple(sorted(symbols - found))
        candle_limit = min(1000, self.settings.reversal_watch_hours * 12 + 24)
        ordered_found = sorted(found)
        candle_batches = await asyncio.gather(
            *(
                self.market_provider.fetch_candles(
                    symbol, interval="5m", limit=candle_limit
                )
                for symbol in ordered_found
            ),
            return_exceptions=True,
        )
        candles = []
        for symbol, batch in zip(ordered_found, candle_batches, strict=True):
            if isinstance(batch, Exception):
                log.warning("watch_candle_refresh_failed", symbol=symbol, error=str(batch))
            else:
                candles.extend(batch)

        async with self.session_factory() as session:
            async with session.begin():
                repository = MarketRepository(session)
                await repository.add_market_snapshots(market_items)
                candles_upserted = await repository.upsert_candles(candles)

        result = WatchMarketUpdateResult(
            active_symbols=len(symbols),
            market_received=len(market_items),
            candles_received=len(candles),
            candles_upserted=candles_upserted,
            missing_market_symbols=missing,
        )
        log.info("watch_market_refresh_complete", **asdict(result))
        return result

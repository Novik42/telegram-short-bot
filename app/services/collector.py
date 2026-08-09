from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.providers.base import BorrowDataProvider, DataSourceError, MarketDataProvider
from app.repositories.borrow_repository import BorrowRepository
from app.repositories.market_repository import MarketRepository

if TYPE_CHECKING:
    from app.services.anomaly_detector import AnomalyDetector

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    borrow_received: int = 0
    borrow_inserted: int = 0
    market_received: int = 0
    market_inserted: int = 0
    candles_received: int = 0
    candles_inserted: int = 0
    missing_market_symbols: tuple[str, ...] = ()
    anomaly_event_ids: tuple[int, ...] = ()
    error: str | None = None


class Collector:
    def __init__(
        self,
        borrow_provider: BorrowDataProvider,
        market_provider: MarketDataProvider,
        session_factory: async_sessionmaker[AsyncSession],
        anomaly_detector: AnomalyDetector | None = None,
    ) -> None:
        self.borrow_provider = borrow_provider
        self.market_provider = market_provider
        self.session_factory = session_factory
        self.anomaly_detector = anomaly_detector

    async def collect_once(self) -> CollectionResult:
        try:
            borrow_items = await self.borrow_provider.fetch_snapshots()
        except DataSourceError as exc:
            log.error("borrow_collection_failed", error=str(exc))
            return CollectionResult(error=str(exc))

        if not borrow_items:
            log.info("borrow_provider_returned_no_new_frame")
            return CollectionResult()

        async with self.session_factory() as session:
            async with session.begin():
                inserted = await BorrowRepository(session).add_snapshots(borrow_items)

        symbols = {item.symbol for item in borrow_items}
        try:
            market_items = await self.market_provider.fetch_market_snapshots(symbols)
        except DataSourceError as exc:
            log.error("market_collection_failed", symbols=sorted(symbols), error=str(exc))
            return CollectionResult(
                borrow_received=len(borrow_items),
                borrow_inserted=inserted,
                error=str(exc),
            )

        found = {item.symbol for item in market_items}
        missing = tuple(sorted(symbols - found))
        async with self.session_factory() as session:
            async with session.begin():
                market_inserted = await MarketRepository(session).add_market_snapshots(market_items)

        ordered_found = sorted(found)
        candle_batches = await asyncio.gather(
            *(
                self.market_provider.fetch_candles(symbol, interval="5m", limit=288)
                for symbol in ordered_found
            ),
            return_exceptions=True,
        )
        candles = []
        for symbol, batch in zip(ordered_found, candle_batches, strict=True):
            if isinstance(batch, Exception):
                log.warning("candle_collection_failed", symbol=symbol, error=str(batch))
            else:
                candles.extend(batch)
        async with self.session_factory() as session:
            async with session.begin():
                candles_inserted = await MarketRepository(session).add_candles(candles)

        anomaly_events = (
            await self.anomaly_detector.detect_for_snapshots(borrow_items)
            if self.anomaly_detector is not None
            else []
        )

        result = CollectionResult(
            borrow_received=len(borrow_items),
            borrow_inserted=inserted,
            market_received=len(market_items),
            market_inserted=market_inserted,
            candles_received=len(candles),
            candles_inserted=candles_inserted,
            missing_market_symbols=missing,
            anomaly_event_ids=tuple(event.id for event in anomaly_events),
        )
        log.info("collection_complete", **asdict(result))
        return result

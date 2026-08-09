from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.providers.base import BorrowSnapshotItem, DataSourceError, MarketSnapshotItem
from app.services.collector import Collector


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()


class FakeSessionFactory:
    def __call__(self) -> FakeSession:
        return FakeSession()


class FakeBorrowProvider:
    async def fetch_snapshots(self) -> list[BorrowSnapshotItem]:
        return [
            BorrowSnapshotItem(
                symbol="KAITO",
                borrow_usd=Decimal("100"),
                repay_usd=Decimal("20"),
                ratio=Decimal("5"),
                source_timestamp=datetime(2026, 8, 9, tzinfo=UTC),
                source_name="fixture",
            )
        ]


class FakeMarketProvider:
    async def fetch_market_snapshots(self, symbols: set[str]) -> list[MarketSnapshotItem]:
        assert symbols == {"KAITO"}
        return [
            MarketSnapshotItem(
                symbol="KAITO",
                price=Decimal("1.25"),
                captured_at=datetime(2026, 8, 9, tzinfo=UTC),
            )
        ]

    async def fetch_candles(self, *_: object, **__: object) -> list[object]:
        return []


class FailingMarketProvider(FakeMarketProvider):
    async def fetch_market_snapshots(self, symbols: set[str]) -> list[MarketSnapshotItem]:
        raise DataSourceError("market unavailable")


@pytest.mark.asyncio
async def test_collector_persists_borrow_and_market(monkeypatch) -> None:
    async def add_borrow(_self: object, items: list[BorrowSnapshotItem]) -> int:
        return len(items)

    async def add_market(_self: object, items: list[MarketSnapshotItem]) -> int:
        return len(items)

    monkeypatch.setattr("app.services.collector.BorrowRepository.add_snapshots", add_borrow)
    monkeypatch.setattr(
        "app.services.collector.MarketRepository.add_market_snapshots", add_market
    )
    collector = Collector(FakeBorrowProvider(), FakeMarketProvider(), FakeSessionFactory())

    result = await collector.collect_once()

    assert result.borrow_inserted == 1
    assert result.market_inserted == 1
    assert result.error is None


@pytest.mark.asyncio
async def test_collector_keeps_borrow_result_when_market_fails(monkeypatch) -> None:
    async def add_borrow(_self: object, items: list[BorrowSnapshotItem]) -> int:
        return len(items)

    monkeypatch.setattr("app.services.collector.BorrowRepository.add_snapshots", add_borrow)
    collector = Collector(FakeBorrowProvider(), FailingMarketProvider(), FakeSessionFactory())

    result = await collector.collect_once()

    assert result.borrow_inserted == 1
    assert result.market_inserted == 0
    assert result.error == "market unavailable"


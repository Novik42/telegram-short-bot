from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from app.utils.datetime import ensure_utc
from app.utils.symbols import normalize_asset_symbol


class DataSourceError(RuntimeError):
    """A provider returned an error that may be retried later."""


class DataSourceUnavailable(DataSourceError):
    """The configured source cannot provide the required server-side data."""


class BorrowSnapshotItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    borrow_usd: Decimal
    repay_usd: Decimal
    ratio: Decimal | None = None
    source_timestamp: datetime
    source_name: str
    raw_payload_hash: str | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return normalize_asset_symbol(value)

    @field_validator("source_timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("borrow_usd", "repay_usd")
    @classmethod
    def amounts_must_be_nonnegative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("Borrow and repay amounts cannot be negative")
        return value


class MarketSnapshotItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    market_type: str = "spot"
    price: Decimal
    quote_volume_24h: Decimal | None = None
    base_volume_24h: Decimal | None = None
    price_change_percent_24h: Decimal | None = None
    open_interest: Decimal | None = None
    open_interest_usd: Decimal | None = None
    funding_rate: Decimal | None = None
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class CandleItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    market_type: str = "spot"
    interval: str = "5m"
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades_count: int
    close_time: datetime

    @field_validator("open_time", "close_time")
    @classmethod
    def times_must_be_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class BorrowDataProvider(ABC):
    @abstractmethod
    async def fetch_snapshots(self) -> list[BorrowSnapshotItem]:
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release provider resources when the runtime stops."""
        return None



class MarketDataProvider(ABC):
    @abstractmethod
    async def fetch_market_snapshots(self, symbols: set[str]) -> list[MarketSnapshotItem]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_candles(
        self, symbol: str, *, interval: str = "5m", limit: int = 288
    ) -> list[CandleItem]:
        raise NotImplementedError

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base, CreatedAtMixin


class MarketSnapshot(CreatedAtMixin, Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (Index("ix_market_snapshots_symbol_captured_at", "symbol", "captured_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    quote_volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    base_volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    price_change_percent_24h: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    open_interest_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))


class Candle(CreatedAtMixin, Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "market_type", "interval", "open_time"),
        Index("ix_candles_symbol_open_time", "symbol", "open_time"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    quote_volume: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

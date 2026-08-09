from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base, CreatedAtMixin


class PumpWatch(CreatedAtMixin, Base):
    __tablename__ = "pump_watches"
    __table_args__ = (
        Index("ix_pump_watches_symbol_started_at", "symbol", "started_at"),
        Index("ix_pump_watches_status_expires_at", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    warning_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_at_watch: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    peak_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    peak_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    support_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    reason_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    transitions: Mapped[list[PumpWatchTransition]] = relationship(
        back_populates="watch", cascade="all, delete-orphan"
    )


class PumpWatchTransition(CreatedAtMixin, Base):
    __tablename__ = "pump_watch_transitions"
    __table_args__ = (
        Index("ix_pump_watch_transitions_watch_occurred", "pump_watch_id", "occurred_at"),
        Index("ix_pump_watch_transitions_notification", "status", "notified_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    pump_watch_id: Mapped[int] = mapped_column(
        ForeignKey("pump_watches.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    peak_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    support_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    borrow_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    repay_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    borrow_repay_ratio: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    borrow_delta_3m: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    borrow_delta_15m: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    price_change_1h: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    price_change_4h: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    reason_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    watch: Mapped[PumpWatch] = relationship(back_populates="transitions")

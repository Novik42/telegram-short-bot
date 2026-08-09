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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.database import Base, CreatedAtMixin


class AnomalyEvent(CreatedAtMixin, Base):
    __tablename__ = "anomaly_events"
    __table_args__ = (Index("ix_anomaly_events_symbol_detected_at", "symbol", "detected_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    borrow_before: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    borrow_now: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    borrow_delta: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    borrow_delta_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    repay_before: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    repay_now: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    repay_delta: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    net_borrow_delta: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    ratio_now: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    price_at_signal: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    price_change_15m_before: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    price_change_1h_before: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    price_change_4h_before: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    price_change_24h_before: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    volume_15m: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    average_volume_15m: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    volume_spike_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    borrow_to_volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    open_interest_change_15m: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 12))
    anomaly_score: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    outcomes: Mapped[list[EventOutcome]] = relationship(
        back_populates="anomaly_event", cascade="all, delete-orphan"
    )


class EventOutcome(Base):
    __tablename__ = "event_outcomes"
    __table_args__ = (
        UniqueConstraint("anomaly_event_id", "horizon_minutes"),
        Index("ix_event_outcomes_event_horizon", "anomaly_event_id", "horizon_minutes"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    anomaly_event_id: Mapped[int] = mapped_column(
        ForeignKey("anomaly_events.id", ondelete="CASCADE"), nullable=False
    )
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price_at_horizon: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    return_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    min_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    max_favorable_move_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_adverse_move_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_pump_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_dump_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    time_to_max_minutes: Mapped[int | None] = mapped_column(Integer)
    time_to_min_minutes: Mapped[int | None] = mapped_column(Integer)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    anomaly_event: Mapped[AnomalyEvent] = relationship(back_populates="outcomes")

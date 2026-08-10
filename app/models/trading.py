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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base, CreatedAtMixin


class DemoTrade(CreatedAtMixin, Base):
    """Persistent state machine for one manually confirmed Bybit demo trade."""

    __tablename__ = "demo_trades"
    __table_args__ = (
        UniqueConstraint(
            "pump_watch_transition_id", name="uq_demo_trades_pump_watch_transition_id"
        ),
        Index("ix_demo_trades_status_created_at", "status", "created_at"),
        Index("ix_demo_trades_symbol_created_at", "symbol", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    pump_watch_transition_id: Mapped[int] = mapped_column(
        ForeignKey("pump_watch_transitions.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False, default="SHORT")
    signal_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    signal_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    proposal_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    peak_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    support_price: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer(), nullable=False)
    balance_usd: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    risk_usd: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    margin_usd: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    realized_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    realized_pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))

    order_link_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    entry_order_id: Mapped[str | None] = mapped_column(String(64))
    close_order_id: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text())
    reason_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    notion_page_url: Mapped[str | None] = mapped_column(Text())

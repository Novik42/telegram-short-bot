from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base, CreatedAtMixin


class BorrowSnapshot(CreatedAtMixin, Base):
    __tablename__ = "borrow_snapshots"
    __table_args__ = (
        UniqueConstraint("source_name", "symbol", "source_timestamp"),
        Index("ix_borrow_snapshots_symbol_source_timestamp", "symbol", "source_timestamp"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    borrow_usd: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    repay_usd: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    borrow_repay_ratio: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    raw_payload_hash: Mapped[str | None] = mapped_column(String(64))

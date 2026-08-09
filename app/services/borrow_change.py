from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from app.models.borrow import BorrowSnapshot


@dataclass(frozen=True, slots=True)
class BorrowChange:
    delta_usd: Decimal
    delta_pct: Decimal | None


def calculate_borrow_change(
    rows: list[BorrowSnapshot], latest: BorrowSnapshot, *, minutes: int
) -> BorrowChange | None:
    target = latest.source_timestamp - timedelta(minutes=minutes)
    before = max(
        (
            row
            for row in rows
            if row.symbol == latest.symbol
            and row.source_name == latest.source_name
            and row.source_timestamp <= target
            and row.source_timestamp < latest.source_timestamp
        ),
        key=lambda row: row.source_timestamp,
        default=None,
    )
    if before is None:
        return None
    delta = latest.borrow_usd - before.borrow_usd
    delta_pct = (
        delta / before.borrow_usd * Decimal("100") if before.borrow_usd > 0 else None
    )
    return BorrowChange(delta_usd=delta, delta_pct=delta_pct)

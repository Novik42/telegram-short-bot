from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

PERCENT = Decimal("100")


@dataclass(frozen=True, slots=True)
class BorrowMetrics:
    window_minutes: int
    borrow_before: Decimal
    borrow_now: Decimal
    borrow_delta: Decimal
    borrow_delta_pct: Decimal
    repay_before: Decimal
    repay_now: Decimal
    repay_delta: Decimal
    net_borrow_delta: Decimal
    borrow_velocity: Decimal
    ratio_now: Decimal


def calculate_borrow_metrics(
    *,
    window_minutes: int,
    borrow_before: Decimal,
    borrow_now: Decimal,
    repay_before: Decimal,
    repay_now: Decimal,
    safe_repay_epsilon: Decimal = Decimal("100"),
    percentage_epsilon: Decimal = Decimal("1"),
) -> BorrowMetrics:
    if window_minutes <= 0:
        raise ValueError("window_minutes must be positive")
    borrow_delta = borrow_now - borrow_before
    repay_delta = repay_now - repay_before
    borrow_delta_pct = borrow_delta / max(abs(borrow_before), percentage_epsilon) * PERCENT
    return BorrowMetrics(
        window_minutes=window_minutes,
        borrow_before=borrow_before,
        borrow_now=borrow_now,
        borrow_delta=borrow_delta,
        borrow_delta_pct=borrow_delta_pct,
        repay_before=repay_before,
        repay_now=repay_now,
        repay_delta=repay_delta,
        net_borrow_delta=borrow_delta - repay_delta,
        borrow_velocity=borrow_delta / Decimal(window_minutes),
        ratio_now=borrow_now / max(repay_now, safe_repay_epsilon),
    )


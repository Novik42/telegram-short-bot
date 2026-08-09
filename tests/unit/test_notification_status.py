from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.bot.keyboards import main_keyboard
from app.models.borrow import BorrowSnapshot
from app.services.notification_service import (
    BorrowChange,
    TelegramNotificationService,
    calculate_borrow_change,
)
from app.services.price_analyzer import PriceContext


def _snapshot(minutes: int, borrow: str) -> BorrowSnapshot:
    timestamp = datetime(2026, 8, 9, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)
    return BorrowSnapshot(
        captured_at=timestamp,
        source_timestamp=timestamp,
        source_name="bbm_html",
        symbol="KAITO",
        borrow_usd=Decimal(borrow),
        repay_usd=Decimal("100000"),
        borrow_repay_ratio=Decimal("1"),
    )


def test_calculate_borrow_change_uses_baseline_at_or_before_window() -> None:
    rows = [_snapshot(0, "100000"), _snapshot(4, "150000"), _snapshot(16, "300000")]

    change_3m = calculate_borrow_change(rows, rows[-1], minutes=3)
    change_15m = calculate_borrow_change(rows, rows[-1], minutes=15)

    assert change_3m == BorrowChange(Decimal("150000"), Decimal("100"))
    assert change_15m == BorrowChange(Decimal("200000"), Decimal("200"))


def test_status_labels_pump_and_formats_negative_delta() -> None:
    assert (
        TelegramNotificationService._pump_label(
            PriceContext(scenario="POST_PUMP_BORROW")
        )
        == "🔥 PUMP біля 4h high"
    )
    assert (
        TelegramNotificationService._format_borrow_change(
            BorrowChange(Decimal("-125000"), Decimal("-25"))
        )
        == "-$125.00K (-25.0%)"
    )


def test_main_keyboard_contains_watch_and_reports() -> None:
    texts = [button.text for row in main_keyboard().keyboard for button in row]

    assert texts == ["👀 WATCH", "📊 STATUS", "🚨 RECENT", "📈 STATS"]

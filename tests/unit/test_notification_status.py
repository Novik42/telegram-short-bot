from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

from app.bot.keyboards import main_keyboard
from app.models import Base
from app.models.anomaly import AnomalyEvent
from app.models.borrow import BorrowSnapshot
from app.models.database import create_engine_and_session
from app.models.market import Candle, MarketSnapshot
from app.models.watch import PumpWatchTransition
from app.services.collector import CollectionResult
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
        TelegramNotificationService._pump_label(PriceContext(scenario="POST_PUMP_BORROW"))
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

    assert texts == ["👀 WATCH", "📊 STATUS", "🚨 RECENT", "📈 STATS", "🧪 DEMO"]


def test_no_pump_anomaly_is_saved_but_not_selected_for_telegram() -> None:
    event = AnomalyEvent(reason_json={"scenario": "NO_PUMP"})

    assert TelegramNotificationService._should_notify_anomaly(event) is False

    event.reason_json = {"scenario": "POST_PUMP_BORROW"}
    assert TelegramNotificationService._should_notify_anomaly(event) is True


async def test_successful_collection_does_not_send_automatic_status() -> None:
    bot = AsyncMock()
    notifier = TelegramNotificationService(
        bot,
        AsyncMock(),
        configured_chat_id="123",
    )

    await notifier.notify_collection(CollectionResult(borrow_received=14))

    bot.send_message.assert_not_awaited()


def test_only_initial_watch_transition_is_a_pump_notification() -> None:
    initial = PumpWatchTransition(
        status="WATCH",
        reason_json={"reasons": ["pump_detected:POST_PUMP_BORROW"]},
    )
    reset = PumpWatchTransition(
        status="WATCH",
        reason_json={"reasons": ["new_peak_reset_warning"]},
    )

    assert TelegramNotificationService._is_initial_pump_transition(initial) is True
    assert TelegramNotificationService._is_initial_pump_transition(reset) is False


async def test_status_marks_stale_borrow_and_still_calculates_price_changes(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'status.db'}"
    engine, session_factory = create_engine_and_session(database_url)
    now = datetime(2026, 8, 12, 6, 45, tzinfo=UTC)
    stale_at = now - timedelta(days=3)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add_all(
                [
                    BorrowSnapshot(
                        captured_at=stale_at,
                        source_timestamp=stale_at,
                        source_name="bbm_html",
                        symbol="MUBARAK",
                        borrow_usd=Decimal("1229000"),
                        repay_usd=Decimal("888700"),
                        borrow_repay_ratio=Decimal("1.4"),
                    ),
                    BorrowSnapshot(
                        captured_at=now,
                        source_timestamp=now,
                        source_name="bbm_html",
                        symbol="LUNA",
                        borrow_usd=Decimal("1000000"),
                        repay_usd=Decimal("500000"),
                        borrow_repay_ratio=Decimal("2"),
                    ),
                    MarketSnapshot(
                        captured_at=now,
                        symbol="MUBARAK",
                        market_type="spot",
                        price=Decimal("1.048"),
                        quote_volume_24h=Decimal("3950000"),
                    ),
                ]
            )
            for index in range(49):
                opened = now - timedelta(hours=4) + timedelta(minutes=index * 5)
                close = Decimal("1") + Decimal(index) * Decimal("0.001")
                session.add(
                    Candle(
                        symbol="MUBARAK",
                        market_type="spot",
                        interval="5m",
                        open_time=opened,
                        open=close,
                        high=close,
                        low=close,
                        close=close,
                        volume=Decimal("1000"),
                        quote_volume=Decimal("1000"),
                        trades_count=10,
                        close_time=opened + timedelta(minutes=5) - timedelta(milliseconds=1),
                    )
                )
            await session.commit()

        notifier = TelegramNotificationService(AsyncMock(), session_factory)
        status = await notifier.render_status()

        mubarak_block = status.split("🪙 MUBARAK", 1)[1]
        assert "BOR $1.23M | REP $888.70K | B/R 1.4" in mubarak_block
        assert "⚠️ BOR застарілий" in mubarak_block
        assert "1h n/a" not in mubarak_block
        assert "4h n/a" not in mubarak_block
        assert "Режим: UNKNOWN" not in mubarak_block
    finally:
        await engine.dispose()

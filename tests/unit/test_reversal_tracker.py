from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.market import Candle
from app.services.reversal_tracker import analyze_reversal


def _candle(start: datetime, *, high: str, low: str, close: str) -> Candle:
    return Candle(
        symbol="PUMP",
        market_type="spot",
        interval="5m",
        open_time=start,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        quote_volume=Decimal("25000"),
        trades_count=100,
        close_time=start + timedelta(minutes=5) - timedelta(milliseconds=1),
    )


def test_drawdown_alone_does_not_create_reversal_warning() -> None:
    now = datetime(2026, 8, 9, 18, 30, tzinfo=UTC)
    candles = [
        _candle(
            now - timedelta(minutes=25 - index * 5),
            high=str(Decimal("100") - Decimal(index)),
            low=str(Decimal("98") - Decimal(index)),
            close=str(Decimal("99") - Decimal(index)),
        )
        for index in range(5)
    ]

    result = analyze_reversal(
        candles,
        evaluated_at=now,
        current_price=Decimal("96.5"),
        previous_peak=Decimal("100"),
        warning_at=None,
        warning_support=None,
    )

    assert result.warning is False
    assert result.confirmed is False
    assert result.drawdown_pct == Decimal("3.500")


def test_break_of_upper_structure_creates_warning_before_confirmation() -> None:
    start = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)
    candles = [
        _candle(start, high="240", low="232", close="237"),
        _candle(start + timedelta(minutes=5), high="238", low="224", close="230"),
        _candle(start + timedelta(minutes=10), high="235", low="228", close="233"),
        _candle(start + timedelta(minutes=15), high="234", low="226", close="229"),
        _candle(start + timedelta(minutes=20), high="230", low="220", close="222"),
    ]

    warning = analyze_reversal(
        candles,
        evaluated_at=start + timedelta(minutes=25),
        current_price=Decimal("222"),
        previous_peak=Decimal("240"),
        warning_at=None,
        warning_support=None,
    )

    assert warning.support_price == Decimal("224")
    assert warning.support_break is True
    assert warning.warning is True
    assert warning.confirmed is False

    failed_reclaim = _candle(
        start + timedelta(minutes=25), high="224", low="218", close="221"
    )
    confirmation = analyze_reversal(
        [*candles, failed_reclaim],
        evaluated_at=start + timedelta(minutes=30),
        current_price=Decimal("221"),
        previous_peak=Decimal("240"),
        warning_at=warning.latest_closed_at,
        warning_support=warning.support_price,
    )

    assert confirmation.confirmed is True
    assert "next_5m_close_failed_to_reclaim_support" in confirmation.reasons


def test_next_close_below_warning_support_confirms_reversal() -> None:
    start = datetime(2026, 8, 9, 18, 0, tzinfo=UTC)
    initial = [
        _candle(start + timedelta(minutes=index * 5), high="101", low="98", close="99")
        for index in range(4)
    ]
    initial.append(
        _candle(start + timedelta(minutes=20), high="99", low="96", close="97")
    )
    first_evaluation = start + timedelta(minutes=25)
    warning = analyze_reversal(
        initial,
        evaluated_at=first_evaluation,
        current_price=Decimal("97"),
        previous_peak=Decimal("101"),
        warning_at=None,
        warning_support=None,
    )

    assert warning.warning is True
    assert warning.support_break is True
    assert warning.support_price == Decimal("98")

    next_candle = _candle(
        start + timedelta(minutes=25), high="98", low="95", close="96"
    )
    confirmed = analyze_reversal(
        [*initial, next_candle],
        evaluated_at=start + timedelta(minutes=30),
        current_price=Decimal("96"),
        previous_peak=Decimal("101"),
        warning_at=warning.latest_closed_at,
        warning_support=warning.support_price,
    )

    assert confirmed.confirmed is True
    assert "next_5m_close_failed_to_reclaim_support" in confirmed.reasons


def test_new_peak_is_tracked_without_warning() -> None:
    now = datetime(2026, 8, 9, 18, 30, tzinfo=UTC)
    result = analyze_reversal(
        [],
        evaluated_at=now,
        current_price=Decimal("105"),
        previous_peak=Decimal("100"),
        warning_at=None,
        warning_support=None,
    )

    assert result.new_peak is True
    assert result.peak_price == Decimal("105")
    assert result.drawdown_pct == 0
    assert result.warning is False

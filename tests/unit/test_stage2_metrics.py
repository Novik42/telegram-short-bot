from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.market import Candle
from app.services.anomaly_score import calculate_anomaly_score
from app.services.borrow_metrics import calculate_borrow_metrics
from app.services.price_analyzer import analyze_price_context, price_change, pump_from_local_low
from app.services.volume_analyzer import analyze_volume_context


def test_borrow_metrics_large_spike() -> None:
    metrics = calculate_borrow_metrics(
        window_minutes=15,
        borrow_before=Decimal("100000"),
        borrow_now=Decimal("600000"),
        repay_before=Decimal("20000"),
        repay_now=Decimal("70000"),
    )
    assert metrics.borrow_delta == Decimal("500000")
    assert metrics.borrow_delta_pct == Decimal("500")
    assert metrics.repay_delta == Decimal("50000")
    assert metrics.net_borrow_delta == Decimal("450000")
    assert metrics.borrow_velocity == Decimal("500000") / Decimal("15")


def test_safe_ratio_does_not_become_infinite() -> None:
    metrics = calculate_borrow_metrics(
        window_minutes=5,
        borrow_before=Decimal("10000"),
        borrow_now=Decimal("20000"),
        repay_before=Decimal("0"),
        repay_now=Decimal("0"),
        safe_repay_epsilon=Decimal("100"),
    )
    assert metrics.ratio_now == Decimal("200")


def test_equal_borrow_and_repay_growth_has_zero_net() -> None:
    metrics = calculate_borrow_metrics(
        window_minutes=5,
        borrow_before=Decimal("100000"),
        borrow_now=Decimal("200000"),
        repay_before=Decimal("50000"),
        repay_now=Decimal("150000"),
    )
    assert metrics.net_borrow_delta == 0


def _candle(open_time: datetime, close: str, low: str, high: str, volume: str) -> Candle:
    value = Decimal(close)
    return Candle(
        symbol="ZBT",
        market_type="spot",
        interval="5m",
        open_time=open_time,
        open=value,
        high=Decimal(high),
        low=Decimal(low),
        close=value,
        volume=Decimal("1000"),
        quote_volume=Decimal(volume),
        trades_count=10,
        close_time=open_time + timedelta(minutes=5) - timedelta(milliseconds=1),
    )


def test_price_context_detects_post_pump_near_high() -> None:
    signal = datetime(2026, 8, 6, 16, 0, tzinfo=UTC)
    candles = [
        _candle(
            signal - timedelta(minutes=60 - index * 5),
            str(Decimal("0.15") + Decimal(index) * Decimal("0.0025")),
            "0.149",
            "0.181",
            "100000",
        )
        for index in range(13)
    ]
    context = analyze_price_context(candles, signal)
    assert context.price_change_1h is not None
    assert context.price_change_1h > Decimal("15")
    assert context.pump_from_low_1h is not None
    assert context.scenario == "POST_PUMP_BORROW"


def test_price_change_and_pump_from_low() -> None:
    assert price_change(Decimal("100"), Decimal("130")) == Decimal("30.0")
    assert pump_from_local_low([Decimal("90"), Decimal("80")], Decimal("100")) == Decimal(
        "25.00"
    )


def test_volume_context_and_combined_score() -> None:
    signal = datetime(2026, 8, 6, 18, 0, tzinfo=UTC)
    candles = [
        _candle(
            signal - timedelta(minutes=(60 - index * 5)),
            str(Decimal("0.15") + Decimal(index) * Decimal("0.002")),
            "0.149",
            "0.18",
            "100000" if index < 10 else "300000",
        )
        for index in range(13)
    ]
    metrics = calculate_borrow_metrics(
        window_minutes=15,
        borrow_before=Decimal("100000"),
        borrow_now=Decimal("600000"),
        repay_before=Decimal("20000"),
        repay_now=Decimal("70000"),
    )
    price = analyze_price_context(candles, signal)
    volume = analyze_volume_context(candles, signal, metrics.borrow_delta)
    score = calculate_anomaly_score(
        metrics,
        price,
        volume,
        min_borrow_delta=Decimal("100000"),
        min_borrow_delta_pct=Decimal("30"),
        min_net_borrow_delta=Decimal("75000"),
        min_price_pump_1h_pct=Decimal("5"),
        min_volume_spike_ratio=Decimal("1.5"),
    )
    assert volume.volume_15m == Decimal("900000")
    assert volume.volume_spike_ratio == Decimal("3")
    assert score.total >= Decimal("60")


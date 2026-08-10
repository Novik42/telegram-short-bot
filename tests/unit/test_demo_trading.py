from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base
from app.models.watch import PumpWatch, PumpWatchTransition
from app.providers.bybit_demo import (
    BybitApiError,
    BybitBalance,
    BybitDemoClient,
    BybitInstrument,
    BybitOrderAck,
    BybitOrderStatus,
    BybitPosition,
    BybitTicker,
)
from app.services.demo_trading import DemoTradingError, DemoTradingService
from app.utils.datetime import utc_now


def demo_settings() -> Settings:
    return Settings(
        _env_file=None,
        trading_mode="demo",
        bybit_api_key="demo-key",
        bybit_api_secret="demo-secret",
        high_cap_excluded_symbols="BTC,ETH",
    )


def instrument() -> BybitInstrument:
    return BybitInstrument(
        symbol="TESTUSDT",
        status="Trading",
        contract_type="LinearPerpetual",
        settle_coin="USDT",
        tick_size=Decimal("0.001"),
        qty_step=Decimal("0.1"),
        min_qty=Decimal("0.1"),
        max_market_qty=Decimal("100000"),
        min_notional=Decimal("5"),
        max_leverage=Decimal("50"),
    )


def test_bybit_demo_client_rejects_live_endpoint() -> None:
    with pytest.raises(ValueError, match="only permits"):
        BybitDemoClient("key", "secret", base_url="https://api.bybit.com")


async def test_closed_bybit_contract_is_rejected_before_ticker_request(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'closed.db'}")
    client = AsyncMock()
    closed = instrument()
    object.__setattr__(closed, "symbol", "LISTAUSDT")
    object.__setattr__(closed, "status", "Closed")
    client.get_instrument.return_value = closed
    service = DemoTradingService(client, async_sessionmaker(engine), demo_settings())

    with pytest.raises(DemoTradingError, match="LISTAUSDT.*Closed"):
        await service._load_trade_context("LISTAUSDT")

    client.get_ticker.assert_not_awaited()
    client.get_balance.assert_not_awaited()
    await engine.dispose()


async def test_missing_bybit_contract_has_actionable_error(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}")
    client = AsyncMock()
    client.get_instrument.side_effect = BybitApiError(
        -1,
        "Linear instrument UNKNOWNUSDT not found",
        endpoint="instrument",
    )
    service = DemoTradingService(client, async_sessionmaker(engine), demo_settings())

    with pytest.raises(DemoTradingError, match="UNKNOWNUSDT.*Bybit Demo"):
        await service._load_trade_context("UNKNOWNUSDT")

    await engine.dispose()


async def test_risk_sizing_is_one_percent_and_leverage_only_caps_notional(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'risk.db'}")
    service = DemoTradingService(AsyncMock(), async_sessionmaker(engine), demo_settings())

    quantity, stop, risk, notional, margin = service._calculate_order(
        price=Decimal("0.98"),
        support=Decimal("1"),
        balance=Decimal("500"),
        instrument=instrument(),
    )

    assert stop == Decimal("1.005")
    assert quantity == Decimal("190.0")
    assert risk == Decimal("4.7500")
    assert notional == Decimal("186.200")
    assert margin == Decimal("37.240")
    await engine.dispose()


async def test_two_step_demo_short_is_persisted_and_opened_once(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'trade.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    now = utc_now()
    async with session_factory() as session:
        watch = PumpWatch(
            symbol="TEST",
            source_name="test",
            status="SHORT_CONFIRMED",
            started_at=now - timedelta(minutes=10),
            last_evaluated_at=now,
            expires_at=now + timedelta(hours=1),
            confirmed_at=now,
            price_at_watch=Decimal("0.90"),
            peak_price=Decimal("1.10"),
            peak_at=now - timedelta(minutes=5),
            last_price=Decimal("0.98"),
            drawdown_pct=Decimal("10"),
            support_price=Decimal("1"),
            reason_json={},
        )
        session.add(watch)
        await session.flush()
        transition = PumpWatchTransition(
            pump_watch_id=watch.id,
            status="SHORT_CONFIRMED",
            occurred_at=now,
            price=Decimal("0.98"),
            peak_price=Decimal("1.10"),
            drawdown_pct=Decimal("10"),
            support_price=Decimal("1"),
            reason_json={},
        )
        session.add(transition)
        await session.commit()
        transition_id = transition.id

    client = AsyncMock()
    client.get_open_positions.return_value = []
    client.get_instrument.return_value = instrument()
    client.get_ticker.return_value = BybitTicker(
        "TESTUSDT",
        Decimal("0.98"),
        Decimal("0.98"),
        Decimal("0.979"),
        Decimal("0.981"),
    )
    client.get_balance.return_value = BybitBalance(Decimal("500"), Decimal("500"), Decimal("500"))
    client.place_market_short.return_value = BybitOrderAck("order-1", "link-1")
    client.wait_for_position.return_value = BybitPosition(
        "TESTUSDT",
        "Sell",
        Decimal("190"),
        Decimal("0.979"),
        Decimal("0.98"),
        Decimal("1.005"),
        Decimal("5"),
        0,
    )
    service = DemoTradingService(client, session_factory, demo_settings())

    proposal = await service.prepare_short(transition_id, user_id=42)
    duplicate = await service.prepare_short(transition_id, user_id=42)
    opened = await service.execute_short(proposal.id, user_id=42)

    assert proposal.status == "PROPOSED"
    assert duplicate.id == proposal.id
    assert opened.status == "OPEN"
    assert opened.entry_order_id == "order-1"
    assert opened.stop_loss == Decimal("1.005")
    client.place_market_short.assert_awaited_once()
    client.set_isolated_margin.assert_awaited_once()
    client.switch_one_way.assert_awaited_once_with("TESTUSDT")
    client.set_leverage.assert_awaited_once_with("TESTUSDT", 5)

    with pytest.raises(DemoTradingError, match="already|вже|кнопка"):
        await service.execute_short(proposal.id, user_id=42)
    client.place_market_short.assert_awaited_once()
    await engine.dispose()


async def test_unknown_create_response_is_reconciled_by_order_link_id(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'reconcile.db'}")
    client = AsyncMock()
    client.get_order_status.return_value = BybitOrderStatus(
        "order-unknown-response",
        "demo-short-1-token",
        "Filled",
        Decimal("10"),
        Decimal("0.98"),
    )
    client.wait_for_position.return_value = BybitPosition(
        "TESTUSDT",
        "Sell",
        Decimal("10"),
        Decimal("0.98"),
        Decimal("0.98"),
        Decimal("0"),
        Decimal("5"),
        0,
    )
    client.emergency_close_short.return_value = BybitOrderAck("emergency-close", "emergency-link")
    service = DemoTradingService(client, async_sessionmaker(engine), demo_settings())
    service._update_trade = AsyncMock(return_value="updated")

    result = await service._handle_execution_failure(
        1,
        "TESTUSDT",
        "demo-short-1-token",
        None,
        True,
        TimeoutError("response lost"),
    )

    assert result == "updated"
    client.get_order_status.assert_awaited_once_with(
        order_id=None, order_link_id="demo-short-1-token"
    )
    client.emergency_close_short.assert_awaited_once()
    assert service._update_trade.await_args.kwargs["status"] == "EMERGENCY_CLOSED"
    await engine.dispose()

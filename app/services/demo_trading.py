from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from uuid import uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.trading import DemoTrade
from app.models.watch import PumpWatch, PumpWatchTransition
from app.providers.bybit_demo import (
    BybitApiError,
    BybitDemoClient,
    BybitInstrument,
    BybitPosition,
    BybitTicker,
)
from app.utils.datetime import utc_now

log = structlog.get_logger(__name__)

ACTIVE_TRADE_STATES = frozenset({"CONFIRMING", "ORDER_SUBMITTED", "OPEN", "CLOSING"})


class DemoTradingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TradeUpdate:
    trade_id: int
    status: str
    symbol: str
    text: str


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise DemoTradingError("Bybit повернув некоректний крок кількості")
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise DemoTradingError("Bybit повернув некоректний крок ціни")
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class DemoTradingService:
    def __init__(
        self,
        client: BybitDemoClient,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        if not settings.demo_trading_enabled:
            raise ValueError("DemoTradingService requires TRADING_MODE=demo")
        self.client = client
        self.session_factory = session_factory
        self.settings = settings
        self._execution_lock = asyncio.Lock()

    def is_authorized_user(self, user_id: int, chat_id: int) -> bool:
        configured = self.settings.telegram_authorized_user_id
        if configured is not None:
            return user_id == configured
        return chat_id > 0 and user_id == chat_id

    async def healthcheck(self) -> tuple[str, Decimal, Decimal]:
        key_info, account_info, balance = await asyncio.gather(
            self.client.get_api_key_info(),
            self.client.get_account_info(),
            self.client.get_balance(),
        )
        permissions = key_info.get("permissions") or {}
        contract = set(permissions.get("ContractTrade") or [])
        if int(key_info.get("readOnly", 1)) != 0 or not {"Order", "Position"}.issubset(contract):
            raise DemoTradingError("API key не має Read-Write дозволів Order і Position")
        margin_mode = str(account_info.get("marginMode") or "UNKNOWN")
        return margin_mode, balance.wallet_balance, balance.available_balance

    async def prepare_short(self, transition_id: int, user_id: int) -> DemoTrade:
        now = utc_now()
        async with self._execution_lock:
            async with self.session_factory() as session:
                row = (
                    await session.execute(
                        select(PumpWatchTransition, PumpWatch)
                        .join(PumpWatch, PumpWatch.id == PumpWatchTransition.pump_watch_id)
                        .where(PumpWatchTransition.id == transition_id)
                    )
                ).one_or_none()
                if row is None:
                    raise DemoTradingError("Сигнал розвороту не знайдено")
                transition, watch = row
                if transition.status != "SHORT_CONFIRMED" or watch.status != "SHORT_CONFIRMED":
                    raise DemoTradingError("Цей сигнал уже не має статусу SHORT_CONFIRMED")
                signal_at = as_utc(transition.occurred_at)
                if now - signal_at > timedelta(seconds=self.settings.demo_signal_max_age_seconds):
                    raise DemoTradingError(
                        "Сигнал застарів — нову позицію за ним відкривати не можна"
                    )
                if watch.symbol in self.settings.high_cap_excluded_symbol_set:
                    raise DemoTradingError("Монета входить до high-cap виключень")
                support = transition.support_price or watch.support_price
                if support is None:
                    raise DemoTradingError("У сигналу немає структурного рівня для stop-loss")

                existing = await session.scalar(
                    select(DemoTrade).where(DemoTrade.pump_watch_transition_id == transition_id)
                )
                if (
                    existing is not None
                    and existing.status == "PROPOSED"
                    and as_utc(existing.expires_at) > now
                ):
                    if existing.requested_by_user_id != user_id:
                        raise DemoTradingError("Цю пропозицію створив інший користувач")
                    return existing
                if existing is not None and existing.status in ACTIVE_TRADE_STATES | {
                    "CLOSED",
                    "EMERGENCY_CLOSED",
                }:
                    raise DemoTradingError("Угода за цим сигналом уже була оброблена")

                active = await session.scalar(
                    select(DemoTrade.id).where(
                        DemoTrade.status.in_(tuple(ACTIVE_TRADE_STATES | {"PROPOSED"})),
                        DemoTrade.pump_watch_transition_id != transition_id,
                    )
                )
                if active is not None:
                    raise DemoTradingError(
                        "Уже існує активна позиція або непідтверджена пропозиція"
                    )

            try:
                bybit_positions = await self.client.get_open_positions()
            except BybitApiError as exc:
                raise DemoTradingError(
                    f"Не вдалося перевірити відкриті позиції Bybit Demo: {exc.message}"
                ) from exc
            if bybit_positions:
                symbols = ", ".join(sorted({position.symbol for position in bybit_positions}))
                raise DemoTradingError(f"На Bybit Demo вже є відкрита позиція: {symbols}")

            symbol = f"{watch.symbol}USDT"
            instrument, ticker, balance = await self._load_trade_context(symbol)
            price = ticker.mark_price
            self._validate_price(price, Decimal(transition.price))
            quantity, stop_loss, risk, notional, margin = self._calculate_order(
                price=price,
                entry_reference=self._entry_reference(ticker),
                support=Decimal(support),
                balance=balance.available_balance,
                instrument=instrument,
            )
            order_link_id = f"demo-short-{transition_id}-{uuid4().hex[:10]}"
            expires_at = now + timedelta(seconds=self.settings.demo_proposal_ttl_seconds)

            async with self.session_factory() as session:
                trade = await session.scalar(
                    select(DemoTrade).where(DemoTrade.pump_watch_transition_id == transition_id)
                )
                values = {
                    "requested_by_user_id": user_id,
                    "status": "PROPOSED",
                    "symbol": symbol,
                    "direction": "SHORT",
                    "signal_at": signal_at,
                    "proposed_at": now,
                    "expires_at": expires_at,
                    "updated_at": now,
                    "signal_price": Decimal(transition.price),
                    "proposal_price": price,
                    "stop_loss": stop_loss,
                    "peak_price": Decimal(transition.peak_price),
                    "support_price": Decimal(support),
                    "quantity": quantity,
                    "leverage": self.settings.demo_leverage,
                    "balance_usd": balance.wallet_balance,
                    "risk_usd": risk,
                    "notional_usd": notional,
                    "margin_usd": margin,
                    "order_link_id": order_link_id,
                    "error_message": None,
                    "reason_json": {
                        "environment": "demo",
                        "category": "linear",
                        "trigger": "SHORT_CONFIRMED",
                        "max_price_deviation_pct": str(self.settings.demo_max_price_deviation_pct),
                        "entry_slippage_buffer_pct": str(
                            self.settings.demo_entry_slippage_buffer_pct
                        ),
                    },
                }
                if trade is None:
                    trade = DemoTrade(
                        pump_watch_transition_id=transition_id,
                        **values,
                    )
                    session.add(trade)
                else:
                    for key, value in values.items():
                        setattr(trade, key, value)
                await session.commit()
                await session.refresh(trade)
                return trade

    async def cancel_proposal(self, trade_id: int, user_id: int) -> DemoTrade:
        async with self._execution_lock:
            async with self.session_factory() as session:
                trade = await session.get(DemoTrade, trade_id)
                if trade is None:
                    raise DemoTradingError("Пропозицію не знайдено")
                if trade.requested_by_user_id != user_id:
                    raise DemoTradingError("Цю пропозицію створив інший користувач")
                if trade.status != "PROPOSED":
                    raise DemoTradingError("Пропозиція вже не очікує підтвердження")
                trade.status = "CANCELLED"
                trade.updated_at = utc_now()
                await session.commit()
                return trade

    async def close_trade(self, trade_id: int, user_id: int) -> DemoTrade:
        async with self._execution_lock:
            async with self.session_factory() as session:
                trade = await session.get(DemoTrade, trade_id)
                if trade is None:
                    raise DemoTradingError("Позицію не знайдено")
                if trade.requested_by_user_id != user_id:
                    raise DemoTradingError("Цю позицію відкрив інший користувач")
                if trade.status != "OPEN":
                    raise DemoTradingError("Позиція вже не має статусу OPEN")
                trade.status = "CLOSING"
                trade.updated_at = utc_now()
                await session.commit()
            try:
                position = await self.client.get_position(trade.symbol)
                if position is None:
                    closed = await self.client.get_latest_closed_pnl(trade.symbol)
                    pnl = Decimal(str((closed or {}).get("closedPnl") or "0"))
                    exit_price = Decimal(str((closed or {}).get("avgExitPrice") or "0"))
                    pnl_pct = (
                        pnl / Decimal(trade.margin_usd) * Decimal("100")
                        if Decimal(trade.margin_usd) > 0
                        else Decimal("0")
                    )
                    return await self._update_trade(
                        trade.id,
                        status="CLOSED",
                        exit_price=exit_price or None,
                        realized_pnl_usd=pnl,
                        realized_pnl_pct=pnl_pct,
                        closed_at=utc_now(),
                        updated_at=utc_now(),
                    )
                self._validate_open_short(position)
                close_link_id = f"demo-manual-{trade.id}-{uuid4().hex[:8]}"
                close_ack = await self.client.emergency_close_short(
                    trade.symbol, position.size, close_link_id
                )
                return await self._update_trade(
                    trade.id,
                    status="CLOSING",
                    close_order_id=close_ack.order_id,
                    updated_at=utc_now(),
                )
            except Exception as exc:
                await self._update_trade(
                    trade.id,
                    status="OPEN",
                    error_message=f"Manual close failed: {self._safe_error(exc)}",
                    updated_at=utc_now(),
                )
                raise DemoTradingError(
                    f"Закриття не підтверджено: {self._safe_error(exc)}"
                ) from exc

    async def get_active_trades(self) -> list[DemoTrade]:
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(DemoTrade)
                        .where(DemoTrade.status.in_(tuple(ACTIVE_TRADE_STATES | {"PROPOSED"})))
                        .order_by(DemoTrade.created_at.desc())
                    )
                ).all()
            )

    async def execute_short(self, trade_id: int, user_id: int) -> DemoTrade:
        async with self._execution_lock:
            trade = await self._claim_proposal(trade_id, user_id)
            order_ack = None
            order_attempted = False
            try:
                instrument, ticker, balance = await self._load_trade_context(trade.symbol)
                self._validate_price(ticker.mark_price, Decimal(trade.proposal_price))
                if ticker.mark_price >= Decimal(trade.support_price):
                    raise DemoTradingError("Ціна вже повернула пробиту підтримку — вхід скасовано")
                positions = await self.client.get_open_positions()
                if positions:
                    raise DemoTradingError("На Bybit Demo вже є відкрита позиція")
                quantity, stop_loss, risk, notional, margin = self._calculate_order(
                    price=ticker.mark_price,
                    entry_reference=self._entry_reference(ticker),
                    support=Decimal(trade.support_price),
                    balance=balance.available_balance,
                    instrument=instrument,
                )
                await self.client.set_isolated_margin()
                await self.client.switch_one_way(trade.symbol)
                await self.client.set_leverage(trade.symbol, trade.leverage)

                order_attempted = True
                order_ack = await self.client.place_market_short(
                    trade.symbol,
                    quantity,
                    stop_loss,
                    trade.order_link_id,
                )
                now = utc_now()
                await self._update_trade(
                    trade.id,
                    status="ORDER_SUBMITTED",
                    entry_order_id=order_ack.order_id,
                    submitted_at=now,
                    updated_at=now,
                    proposal_price=ticker.mark_price,
                    stop_loss=stop_loss,
                    quantity=quantity,
                    balance_usd=balance.wallet_balance,
                    risk_usd=risk,
                    notional_usd=notional,
                    margin_usd=margin,
                )
                position = await self.client.wait_for_position(trade.symbol)
                if position is None:
                    raise DemoTradingError(
                        "Bybit прийняв ордер, але відкриту позицію не вдалося підтвердити"
                    )
                self._validate_open_short(position)
                if not self._stop_matches(position.stop_loss, stop_loss, instrument.tick_size):
                    await self.client.set_stop_loss(trade.symbol, stop_loss)
                    position = await self._wait_for_protected_position(
                        trade.symbol, stop_loss, instrument.tick_size
                    )
                if position is None:
                    raise DemoTradingError("Stop-loss не підтверджено після відкриття")
                actual_risk = position.size * (stop_loss - position.average_price)
                maximum_risk = (
                    balance.available_balance * self.settings.demo_risk_percent / Decimal("100")
                )
                actual_notional = position.size * position.average_price
                actual_margin = actual_notional / Decimal(trade.leverage)
                filled_at = utc_now()
                await self._update_trade(
                    trade.id,
                    entry_price=position.average_price,
                    quantity=position.size,
                    risk_usd=actual_risk,
                    notional_usd=actual_notional,
                    margin_usd=actual_margin,
                    filled_at=filled_at,
                    updated_at=filled_at,
                )
                if actual_risk > maximum_risk:
                    raise DemoTradingError(
                        f"Фактичний ризик після fill ${actual_risk:.2f} "
                        f"перевищив ліміт ${maximum_risk:.2f}"
                    )
                return await self._update_trade(
                    trade.id,
                    status="OPEN",
                    entry_price=position.average_price,
                    quantity=position.size,
                    risk_usd=actual_risk,
                    notional_usd=actual_notional,
                    margin_usd=actual_margin,
                    filled_at=filled_at,
                    updated_at=filled_at,
                )
            except Exception as exc:
                return await self._handle_execution_failure(
                    trade.id,
                    trade.symbol,
                    trade.order_link_id,
                    order_ack,
                    order_attempted,
                    exc,
                )

    async def _claim_proposal(self, trade_id: int, user_id: int) -> DemoTrade:
        now = utc_now()
        async with self.session_factory() as session:
            trade = await session.get(DemoTrade, trade_id)
            if trade is None:
                raise DemoTradingError("Пропозицію не знайдено")
            if trade.requested_by_user_id != user_id:
                raise DemoTradingError("Цю пропозицію створив інший користувач")
            if trade.status != "PROPOSED":
                raise DemoTradingError("Ця кнопка вже була використана")
            if as_utc(trade.expires_at) <= now:
                trade.status = "EXPIRED"
                trade.updated_at = now
                await session.commit()
                raise DemoTradingError("Час підтвердження минув — сформуйте нову пропозицію")
            if now - as_utc(trade.signal_at) > timedelta(
                seconds=self.settings.demo_signal_max_age_seconds
            ):
                trade.status = "EXPIRED"
                trade.updated_at = now
                await session.commit()
                raise DemoTradingError("Сигнал уже застарів")
            trade.status = "CONFIRMING"
            trade.confirmed_at = now
            trade.updated_at = now
            await session.commit()
            await session.refresh(trade)
            return trade

    async def _handle_execution_failure(
        self,
        trade_id: int,
        symbol: str,
        order_link_id: str,
        order_ack: object | None,
        order_attempted: bool,
        exc: Exception,
    ) -> DemoTrade:
        message = self._safe_error(exc)
        status = "FAILED"
        close_order_id = None
        closed_at = None
        if order_attempted:
            try:
                order_id = str(getattr(order_ack, "order_id", ""))
                order_status = await self.client.get_order_status(
                    order_id=order_id or None,
                    order_link_id=None if order_id else order_link_id,
                )
                if order_status is not None and order_status.status in {
                    "New",
                    "PartiallyFilled",
                    "Untriggered",
                }:
                    await self.client.cancel_order(symbol, order_status.order_id)
                    await asyncio.sleep(0.5)
                if order_status is not None and order_status.cumulative_quantity > 0:
                    position = await self.client.wait_for_position(symbol, timeout_seconds=3.0)
                else:
                    position = await self.client.get_position(symbol)
                if position is not None and position.side == "Sell" and position.size > 0:
                    close_link_id = f"demo-emergency-{trade_id}-{uuid4().hex[:8]}"
                    close_ack = await self.client.emergency_close_short(
                        symbol, position.size, close_link_id
                    )
                    close_order_id = close_ack.order_id
                    if await self.client.wait_for_position_closed(symbol):
                        status = "EMERGENCY_CLOSED"
                        closed_at = utc_now()
                        message = (
                            f"{message}; аварійне закриття підтверджено risk-guard"
                        )
                    else:
                        status = "UNPROTECTED_ERROR"
                        message = (
                            f"{message}; emergency close надіслано, але фактичне "
                            "закриття не підтверджено — перевірте Bybit Demo вручну"
                        )
                elif order_status is None:
                    status = "UNPROTECTED_ERROR"
                    message = (
                        f"{message}; стан прийнятого ордера не вдалося підтвердити — "
                        "перевірте Bybit Demo вручну"
                    )
            except Exception as close_exc:  # pragma: no cover - catastrophic external failure
                status = "UNPROTECTED_ERROR"
                message = (
                    f"{message}; АВАРІЙНЕ ЗАКРИТТЯ НЕ ПІДТВЕРДЖЕНО: {self._safe_error(close_exc)}"
                )
                log.critical(
                    "demo_trade_unprotected_position",
                    trade_id=trade_id,
                    symbol=symbol,
                    error=message,
                )
        return await self._update_trade(
            trade_id,
            status=status,
            close_order_id=close_order_id,
            closed_at=closed_at,
            error_message=message,
            updated_at=utc_now(),
        )

    async def _wait_for_protected_position(
        self, symbol: str, stop_loss: Decimal, tick_size: Decimal
    ) -> BybitPosition | None:
        for _ in range(8):
            position = await self.client.get_position(symbol)
            if position is not None and self._stop_matches(
                position.stop_loss, stop_loss, tick_size
            ):
                return position
            await asyncio.sleep(0.5)
        return None

    async def _update_trade(self, trade_id: int, **values: object) -> DemoTrade:
        async with self.session_factory() as session:
            trade = await session.get(DemoTrade, trade_id)
            if trade is None:
                raise DemoTradingError("Локальний запис угоди втрачено")
            for key, value in values.items():
                setattr(trade, key, value)
            await session.commit()
            await session.refresh(trade)
            return trade

    def _validate_instrument(self, instrument: BybitInstrument) -> None:
        if instrument.status != "Trading":
            raise DemoTradingError(
                f"{instrument.symbol} недоступний для торгівлі у Bybit Demo "
                f"(статус контракту: {instrument.status or 'невідомий'})"
            )
        if instrument.settle_coin != "USDT" or "Perpetual" not in instrument.contract_type:
            raise DemoTradingError(
                f"{instrument.symbol} не є активним USDT perpetual контрактом у Bybit Demo"
            )
        if instrument.max_leverage < self.settings.demo_leverage:
            raise DemoTradingError(
                f"Для {instrument.symbol} Bybit не дозволяє плече {self.settings.demo_leverage}×"
            )

    async def _load_trade_context(self, symbol: str):
        try:
            instrument = await self.client.get_instrument(symbol)
        except BybitApiError as exc:
            raise DemoTradingError(
                f"Контракт {symbol} не знайдено у Bybit Demo: {exc.message}"
            ) from exc

        # Validate the contract before requesting a ticker. Closed Bybit instruments can
        # still be returned by instruments-info, but intentionally have no live ticker.
        self._validate_instrument(instrument)
        try:
            ticker, balance = await asyncio.gather(
                self.client.get_ticker(symbol),
                self.client.get_balance(),
            )
        except BybitApiError as exc:
            raise DemoTradingError(
                f"Bybit Demo не повернув торгові дані для {symbol}: {exc.message}"
            ) from exc
        return instrument, ticker, balance

    def _validate_price(self, current: Decimal, reference: Decimal) -> None:
        if current <= 0 or reference <= 0:
            raise DemoTradingError("Некоректна ціна для розрахунку позиції")
        deviation = abs(current - reference) / reference * Decimal("100")
        if deviation > self.settings.demo_max_price_deviation_pct:
            raise DemoTradingError(
                f"Ціна змінилася на {deviation:.2f}% — більше дозволеного порога"
            )

    def _calculate_order(
        self,
        *,
        price: Decimal,
        support: Decimal,
        balance: Decimal,
        instrument: BybitInstrument,
        entry_reference: Decimal | None = None,
    ) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
        if price <= 0 or support <= price:
            raise DemoTradingError("Ціна вже повернула підтримку або support некоректний")
        if balance <= 0:
            raise DemoTradingError("На деморахунку немає доступного USDT балансу")
        buffered_stop = support * (
            Decimal("1") + self.settings.demo_stop_buffer_pct / Decimal("100")
        )
        minimum_stop = price * (
            Decimal("1") + self.settings.demo_min_stop_distance_pct / Decimal("100")
        )
        stop_loss = ceil_to_step(max(buffered_stop, minimum_stop), instrument.tick_size)
        distance = (stop_loss - price) / price * Decimal("100")
        if distance > self.settings.demo_max_stop_distance_pct:
            raise DemoTradingError(f"Stop-loss занадто далекий ({distance:.2f}%), угоду пропущено")
        target_risk = balance * self.settings.demo_risk_percent / Decimal("100")
        risk_budget = target_risk * Decimal("0.95")
        reference = entry_reference if entry_reference and entry_reference > 0 else price
        conservative_entry = min(price, reference) * (
            Decimal("1")
            - self.settings.demo_entry_slippage_buffer_pct / Decimal("100")
        )
        risk_quantity = risk_budget / (stop_loss - conservative_entry)
        notional_cap = balance * Decimal(self.settings.demo_leverage) * Decimal("0.95")
        cap_quantity = notional_cap / price
        quantity = floor_to_step(min(risk_quantity, cap_quantity), instrument.qty_step)
        if quantity < instrument.min_qty or quantity <= 0:
            raise DemoTradingError("Розрахована кількість нижча мінімуму Bybit")
        if instrument.max_market_qty > 0:
            quantity = min(quantity, instrument.max_market_qty)
            quantity = floor_to_step(quantity, instrument.qty_step)
        notional = quantity * price
        if notional < instrument.min_notional:
            raise DemoTradingError("Розмір позиції нижчий мінімального notional Bybit")
        risk = quantity * (stop_loss - conservative_entry)
        margin = notional / Decimal(self.settings.demo_leverage)
        if margin > balance:
            raise DemoTradingError("Недостатньо демо-балансу для розрахованої маржі")
        return quantity, stop_loss, risk, notional, margin

    @staticmethod
    def _entry_reference(ticker: BybitTicker) -> Decimal:
        if ticker.bid_price > 0:
            return min(ticker.mark_price, ticker.bid_price)
        return ticker.mark_price

    @staticmethod
    def _validate_open_short(position: BybitPosition) -> None:
        if position.side != "Sell" or position.size <= 0 or position.position_idx != 0:
            raise DemoTradingError("Bybit не підтвердив очікувану one-way SHORT позицію")

    @staticmethod
    def _stop_matches(actual: Decimal, expected: Decimal, tick_size: Decimal) -> bool:
        return actual > 0 and abs(actual - expected) <= max(tick_size, Decimal("0.00000001"))

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, (DemoTradingError, BybitApiError)):
            return str(exc)
        return f"{type(exc).__name__}: {exc}"

    async def sync_open_trades(self) -> list[TradeUpdate]:
        updates: list[TradeUpdate] = []
        async with self._execution_lock:
            async with self.session_factory() as session:
                trades = list(
                    (
                        await session.scalars(
                            select(DemoTrade)
                            .where(DemoTrade.status.in_(("OPEN", "CLOSING")))
                            .order_by(DemoTrade.filled_at)
                        )
                    ).all()
                )
            for trade in trades:
                position = await self.client.get_position(trade.symbol)
                if position is not None:
                    if trade.status == "OPEN" and position.stop_loss <= 0:
                        emergency_id = f"demo-watchdog-{trade.id}-{uuid4().hex[:8]}"
                        close_ack = await self.client.emergency_close_short(
                            trade.symbol, position.size, emergency_id
                        )
                        updated = await self._update_trade(
                            trade.id,
                            status="EMERGENCY_CLOSED",
                            close_order_id=close_ack.order_id,
                            closed_at=utc_now(),
                            updated_at=utc_now(),
                            error_message="Watchdog: позиція втратила stop-loss",
                        )
                        updates.append(
                            TradeUpdate(
                                updated.id,
                                updated.status,
                                updated.symbol,
                                "🛑 Позицію аварійно закрито: Bybit не показує stop-loss.",
                            )
                        )
                    continue
                closed = await self.client.get_latest_closed_pnl(trade.symbol)
                if not closed:
                    continue
                updated_ms = int(closed.get("updatedTime") or 0)
                closed_at = (
                    datetime.fromtimestamp(updated_ms / 1000, tz=UTC) if updated_ms else utc_now()
                )
                if trade.filled_at and closed_at < as_utc(trade.filled_at):
                    continue
                pnl = Decimal(str(closed.get("closedPnl") or "0"))
                exit_price = Decimal(str(closed.get("avgExitPrice") or "0"))
                pnl_pct = (
                    pnl / Decimal(trade.margin_usd) * Decimal("100")
                    if Decimal(trade.margin_usd) > 0
                    else Decimal("0")
                )
                updated = await self._update_trade(
                    trade.id,
                    status="CLOSED",
                    exit_price=exit_price or None,
                    realized_pnl_usd=pnl,
                    realized_pnl_pct=pnl_pct,
                    closed_at=closed_at,
                    updated_at=utc_now(),
                )
                updates.append(
                    TradeUpdate(
                        updated.id,
                        updated.status,
                        updated.symbol,
                        f"✅ DEMO позицію закрито. P&L: {pnl:+.2f} USDT ({pnl_pct:+.2f}% маржі).",
                    )
                )
        return updates

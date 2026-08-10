import structlog
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    close_trade_keyboard,
    confirm_short_keyboard,
    main_keyboard,
)
from app.models.trading import DemoTrade
from app.services.demo_trading import DemoTradingError, DemoTradingService
from app.services.notification_service import TelegramNotificationService

log = structlog.get_logger(__name__)


def build_router(
    notifier: TelegramNotificationService,
    demo_trading: DemoTradingService | None = None,
) -> Router:
    router = Router(name="margin_monitor")
    preparing_transitions: set[int] = set()

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if message.chat.id is None:
            return
        if not notifier.register_chat(message.chat.id):
            await message.answer("Цей бот уже прив’язаний до іншого Telegram-чату.")
            return
        await message.answer(
            "✅ Монітор підключено.\n"
            "Повний звіт надсилається лише після натискання 📊 STATUS.\n"
            "Автоматично приходитимуть тільки PUMP/BOR і сигнали розвороту.",
            reply_markup=main_keyboard(),
        )
        recent = await notifier.recent_anomaly_messages(limit=1)
        for text in recent:
            await message.answer(text)

    @router.message(Command("status"))
    @router.message(F.text == "📊 STATUS")
    async def status(message: Message) -> None:
        if not notifier.is_authorized(message.chat.id):
            await message.answer("Спочатку надішліть /start.")
            return
        await message.answer(await notifier.render_status())

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "/start — підключити цей чат\n"
            "/status — показати кількість і останні зібрані дані\n"
            "/watch — активні монети з PUMP-режимом\n"
            "/recent — останні знайдені BOR-аномалії\n"
            "/stats — результати сигналів через 15m/1h/4h/24h\n"
            "/demo — Bybit Demo баланс і позиції\n"
            "/whoami — показати Telegram user ID",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("watch"))
    @router.message(F.text == "👀 WATCH")
    async def watch(message: Message) -> None:
        if not notifier.is_authorized(message.chat.id):
            await message.answer("Спочатку надішліть /start.")
            return
        await message.answer(await notifier.render_watchlist())

    @router.message(Command("recent"))
    @router.message(F.text == "🚨 RECENT")
    async def recent(message: Message) -> None:
        if not notifier.is_authorized(message.chat.id):
            await message.answer("Спочатку надішліть /start.")
            return
        texts = await notifier.recent_anomaly_messages(limit=3)
        if not texts:
            await message.answer("Підтверджених BOR-аномалій ще немає.")
            return
        for text in texts:
            await message.answer(text)

    @router.message(Command("stats"))
    @router.message(F.text == "📈 STATS")
    async def stats(message: Message) -> None:
        if not notifier.is_authorized(message.chat.id):
            await message.answer("Спочатку надішліть /start.")
            return
        await message.answer(await notifier.render_research_stats())

    @router.message(Command("whoami"))
    async def whoami(message: Message) -> None:
        await message.answer(
            f"Ваш Telegram user ID: {message.from_user.id if message.from_user else 'n/a'}"
        )

    @router.message(Command("demo"))
    @router.message(F.text == "🧪 DEMO")
    async def demo_status(message: Message) -> None:
        if not notifier.is_authorized(message.chat.id):
            await message.answer("Спочатку надішліть /start.")
            return
        if demo_trading is None:
            await message.answer("🧪 Bybit Demo trading вимкнено.")
            return
        if message.from_user is None or not demo_trading.is_authorized_user(
            message.from_user.id, message.chat.id
        ):
            await message.answer("⛔ Цей Telegram-користувач не має дозволу на demo trading.")
            return
        try:
            margin_mode, wallet, available = await demo_trading.healthcheck()
            trades = await demo_trading.get_active_trades()
        except Exception as exc:
            await message.answer(f"⚠️ Bybit Demo недоступний: {exc}")
            return
        lines = [
            "🧪 BYBIT DEMO",
            f"Wallet: {wallet:.2f} USDT",
            f"Available: {available:.2f} USDT",
            f"Margin mode: {margin_mode}",
            f"Ризик: {demo_trading.settings.demo_risk_percent}% | "
            f"Плече: {demo_trading.settings.demo_leverage}×",
            f"Активних локальних записів: {len(trades)}",
        ]
        for trade in trades[:5]:
            lines.append(
                f"• #{trade.id} {trade.symbol} — {trade.status} | "
                f"qty {trade.quantity} | SL {trade.stop_loss}"
            )
        await message.answer("\n".join(lines), reply_markup=main_keyboard())

    async def authorized_callback(callback: CallbackQuery) -> bool:
        message = callback.message
        if demo_trading is None:
            await callback.answer("Demo trading вимкнено", show_alert=True)
            return False
        if not isinstance(message, Message) or not notifier.is_authorized(message.chat.id):
            await callback.answer("Чат не авторизований", show_alert=True)
            return False
        if not demo_trading.is_authorized_user(callback.from_user.id, message.chat.id):
            await callback.answer("Немає дозволу на demo trading", show_alert=True)
            return False
        return True

    @router.callback_query(F.data.startswith("trade:prepare:"))
    async def prepare_short(callback: CallbackQuery) -> None:
        if not await authorized_callback(callback):
            return
        assert demo_trading is not None
        assert isinstance(callback.message, Message)
        try:
            transition_id = int((callback.data or "").rsplit(":", 1)[1])
        except ValueError:
            await callback.answer("Некоректна кнопка", show_alert=True)
            return
        if transition_id in preparing_transitions:
            await callback.answer("Розрахунок уже виконується")
            return

        preparing_transitions.add(transition_id)
        try:
            await callback.answer("Перевіряю Bybit Demo…")
            trade = await demo_trading.prepare_short(transition_id, callback.from_user.id)
        except DemoTradingError as exc:
            await callback.message.answer(f"⚠️ DEMO SHORT скасовано: {exc}")
            return
        except Exception as exc:
            log.exception("demo_trade_proposal_failed", error=type(exc).__name__)
            await callback.message.answer(
                "⚠️ DEMO SHORT не підготовлено через технічну помилку. Ордер не створено."
            )
            return
        finally:
            preparing_transitions.discard(transition_id)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            _format_trade_preview(trade),
            reply_markup=confirm_short_keyboard(trade.id),
        )

    @router.callback_query(F.data.startswith("trade:cancel:"))
    async def cancel_short(callback: CallbackQuery) -> None:
        if not await authorized_callback(callback):
            return
        assert demo_trading is not None
        assert isinstance(callback.message, Message)
        try:
            trade_id = int((callback.data or "").rsplit(":", 1)[1])
            await demo_trading.cancel_proposal(trade_id, callback.from_user.id)
        except (ValueError, DemoTradingError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Скасовано")
        await callback.message.answer("❌ DEMO пропозицію скасовано.")

    @router.callback_query(F.data.startswith("trade:confirm:"))
    async def confirm_short(callback: CallbackQuery) -> None:
        if not await authorized_callback(callback):
            return
        assert demo_trading is not None
        assert isinstance(callback.message, Message)
        try:
            trade_id = int((callback.data or "").rsplit(":", 1)[1])
        except ValueError:
            await callback.answer("Некоректна кнопка", show_alert=True)
            return
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Перевіряю та відкриваю DEMO позицію…")
        progress = await callback.message.answer("⏳ Bybit Demo: повторна перевірка ризику…")
        try:
            trade = await demo_trading.execute_short(trade_id, callback.from_user.id)
        except DemoTradingError as exc:
            await progress.edit_text(f"⚠️ DEMO вхід скасовано: {exc}")
            return
        if trade.status == "OPEN":
            await progress.edit_text(
                _format_open_trade(trade),
                reply_markup=close_trade_keyboard(trade.id),
            )
        elif trade.status == "UNPROTECTED_ERROR":
            await progress.edit_text(
                "🚨 КРИТИЧНО: захисний stop-loss і аварійне закриття не підтверджені. "
                "Негайно перевірте Bybit Demo вручну.\n"
                f"Причина: {trade.error_message}"
            )
        else:
            await progress.edit_text(
                f"⚠️ DEMO позицію не відкрито. Статус: {trade.status}\n"
                f"Причина: {trade.error_message or 'невідома'}"
            )

    @router.callback_query(F.data.startswith("trade:close:"))
    async def close_short(callback: CallbackQuery) -> None:
        if not await authorized_callback(callback):
            return
        assert demo_trading is not None
        assert isinstance(callback.message, Message)
        try:
            trade_id = int((callback.data or "").rsplit(":", 1)[1])
            trade = await demo_trading.close_trade(trade_id, callback.from_user.id)
        except (ValueError, DemoTradingError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Закриття надіслано")
        await callback.message.answer(
            f"🛑 DEMO {trade.symbol}: статус {trade.status}. "
            "Бот перевірить фактичне закриття та P&L."
        )

    return router


def _format_trade_preview(trade: DemoTrade) -> str:
    distance = (trade.stop_loss - trade.proposal_price) / trade.proposal_price * 100
    return (
        f"🧪 ПІДТВЕРДЖЕННЯ DEMO SHORT: {trade.symbol}\n\n"
        f"Mark price: ${trade.proposal_price}\n"
        f"Stop-loss: ${trade.stop_loss} ({distance:.2f}% вище)\n"
        f"Quantity: {trade.quantity}\n"
        f"Notional: ${trade.notional_usd:.2f}\n"
        f"Маржа при {trade.leverage}×: ${trade.margin_usd:.2f}\n"
        f"Максимальний ризик до SL: ${trade.risk_usd:.2f}\n"
        f"Баланс під час розрахунку: ${trade.balance_usd:.2f}\n\n"
        "Після натискання ✅ ціна та ризик будуть перевірені повторно. "
        "Це демо-угода, але кнопка створить реальний ордер у Demo Trading."
    )


def _format_open_trade(trade: DemoTrade) -> str:
    return (
        f"✅ DEMO SHORT ВІДКРИТО: {trade.symbol}\n\n"
        f"Entry: ${trade.entry_price}\n"
        f"Stop-loss: ${trade.stop_loss}\n"
        f"Quantity: {trade.quantity}\n"
        f"Notional: ${trade.notional_usd:.2f}\n"
        f"Маржа: ${trade.margin_usd:.2f} | Плече: {trade.leverage}×\n"
        f"Ризик до SL: ${trade.risk_usd:.2f}\n"
        f"Bybit order: {trade.entry_order_id}\n\n"
        "Stop-loss підтверджено через Bybit Position API."
    )

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.keyboards import main_keyboard
from app.services.notification_service import TelegramNotificationService


def build_router(notifier: TelegramNotificationService) -> Router:
    router = Router(name="margin_monitor")

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
            "/stats — результати сигналів через 15m/1h/4h/24h",
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

    return router

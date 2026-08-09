from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

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
        report = await notifier.render_status()
        await message.answer(
            "✅ Монітор підключено. Після кожного нового збору я надсилатиму дані сюди.\n\n"
            + report
        )
        recent = await notifier.recent_anomaly_messages(limit=1)
        for text in recent:
            await message.answer(text)

    @router.message(Command("status"))
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
            "/recent — останні знайдені BOR-аномалії\n"
            "/stats — результати сигналів через 15m/1h/4h/24h"
        )

    @router.message(Command("recent"))
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
    async def stats(message: Message) -> None:
        if not notifier.is_authorized(message.chat.id):
            await message.answer("Спочатку надішліть /start.")
            return
        await message.answer(await notifier.render_research_stats())

    return router

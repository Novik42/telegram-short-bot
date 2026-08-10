from __future__ import annotations

import asyncio
import signal
from contextlib import suppress

import structlog
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.handlers import build_router
from app.config import get_settings
from app.logging import configure_logging
from app.runtime import build_runtime
from app.services.notification_service import TelegramNotificationService
from app.utils.datetime import utc_now

log = structlog.get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    runtime = build_runtime(settings)
    stop_event = asyncio.Event()
    bot: Bot | None = None
    polling_task: asyncio.Task[None] | None = None
    notifier: TelegramNotificationService | None = None
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    if settings.telegram_bot_token:
        bot = Bot(settings.telegram_bot_token)
        notifier = TelegramNotificationService(
            bot,
            runtime.session_factory,
            configured_chat_id=settings.telegram_chat_id,
            excluded_symbols=settings.high_cap_excluded_symbol_set,
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(notifier))
        polling_task = asyncio.create_task(
            dispatcher.start_polling(bot, handle_signals=False), name="telegram-polling"
        )
        log.info("telegram_polling_started")

    async def collect_and_notify() -> None:
        result = await runtime.collector.collect_once()
        await runtime.outcome_evaluator.evaluate_due()
        await runtime.watch_market_updater.refresh_active()
        await runtime.reversal_tracker.evaluate_latest(evaluated_at=utc_now())
        if notifier is not None:
            await notifier.notify_collection(result)
            await notifier.notify_anomalies(result.anomaly_event_ids)
            await notifier.notify_reversal_transitions()

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        collect_and_notify,
        "interval",
        seconds=settings.collection_seconds,
        id="collect_snapshots",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    log.info("monitor_started", interval_seconds=settings.collection_seconds)
    try:
        await collect_and_notify()
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        if polling_task is not None:
            polling_task.cancel()
            with suppress(asyncio.CancelledError):
                await polling_task
        if bot is not None:
            await bot.session.close()
        await runtime.close()
        log.info("monitor_stopped")


if __name__ == "__main__":
    asyncio.run(run())

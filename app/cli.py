from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from aiogram import Bot
from sqlalchemy import func, select

from app.config import get_settings
from app.logging import configure_logging
from app.models import Base
from app.models.anomaly import AnomalyEvent
from app.models.borrow import BorrowSnapshot
from app.models.database import create_engine_and_session
from app.models.market import MarketSnapshot
from app.runtime import build_runtime
from app.services.notification_service import TelegramNotificationService


async def _init_db() -> None:
    settings = get_settings()
    engine, _ = create_engine_and_session(settings.database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def _collect_once() -> None:
    runtime = build_runtime(get_settings())
    try:
        result = await runtime.collector.collect_once()
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
    finally:
        await runtime.close()


async def _evaluate_outcomes() -> None:
    runtime = build_runtime(get_settings())
    try:
        result = await runtime.outcome_evaluator.evaluate_due()
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
    finally:
        await runtime.close()


async def _replay_fixture() -> None:
    runtime = build_runtime(get_settings())
    try:
        while True:
            result = await runtime.collector.collect_once()
            if result.borrow_received == 0:
                break
            print(json.dumps(asdict(result), ensure_ascii=False, default=str))
    finally:
        await runtime.close()


async def _show_data() -> None:
    settings = get_settings()
    engine, session_factory = create_engine_and_session(settings.database_url)
    try:
        async with session_factory() as session:
            borrow_count = int(
                await session.scalar(select(func.count(BorrowSnapshot.id))) or 0
            )
            market_count = int(
                await session.scalar(select(func.count(MarketSnapshot.id))) or 0
            )
            borrow_rows = (
                await session.scalars(
                    select(BorrowSnapshot)
                    .order_by(BorrowSnapshot.source_timestamp.desc(), BorrowSnapshot.symbol)
                    .limit(10)
                )
            ).all()
            market_rows = (
                await session.scalars(
                    select(MarketSnapshot)
                    .order_by(MarketSnapshot.captured_at.desc(), MarketSnapshot.symbol)
                    .limit(10)
                )
            ).all()

        print(f"Borrow snapshots: {borrow_count}")
        for row in borrow_rows:
            print(
                f"  {row.source_timestamp.isoformat()}  {row.symbol:<10} "
                f"BOR=${row.borrow_usd}  REP=${row.repay_usd}"
            )
        print(f"Market snapshots: {market_count}")
        for row in market_rows:
            print(
                f"  {row.captured_at.isoformat()}  {row.symbol:<10} "
                f"price=${row.price}  volume24h=${row.quote_volume_24h}"
            )
    finally:
        await engine.dispose()


async def _send_recent_anomaly() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    engine, session_factory = create_engine_and_session(settings.database_url)
    bot = Bot(settings.telegram_bot_token)
    try:
        async with session_factory() as session:
            event_id = await session.scalar(
                select(AnomalyEvent.id).order_by(AnomalyEvent.detected_at.desc()).limit(1)
            )
        if event_id is None:
            print("No anomaly events found")
            return
        notifier = TelegramNotificationService(
            bot,
            session_factory,
            configured_chat_id=settings.telegram_chat_id,
            excluded_symbols=settings.high_cap_excluded_symbol_set,
        )
        await notifier.notify_anomalies((event_id,))
        print(f"Sent anomaly event {event_id} to Telegram")
    finally:
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Create tables directly (use Alembic in production)")
    subparsers.add_parser("collect-once", help="Collect one fixture frame and market batch")
    subparsers.add_parser("replay-fixture", help="Replay every fixture frame at configured speed")
    subparsers.add_parser("show-data", help="Show stored snapshot counts and recent rows")
    subparsers.add_parser("send-recent", help="Send the latest anomaly event to Telegram")
    subparsers.add_parser("evaluate-outcomes", help="Evaluate due anomaly outcomes")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.command == "init-db":
        asyncio.run(_init_db())
    elif args.command == "collect-once":
        asyncio.run(_collect_once())
    elif args.command == "replay-fixture":
        asyncio.run(_replay_fixture())
    elif args.command == "show-data":
        asyncio.run(_show_data())
    elif args.command == "send-recent":
        asyncio.run(_send_recent_anomaly())
    elif args.command == "evaluate-outcomes":
        asyncio.run(_evaluate_outcomes())


if __name__ == "__main__":
    main()

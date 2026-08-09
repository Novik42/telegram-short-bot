from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.anomaly import AnomalyEvent, EventOutcome
from app.models.borrow import BorrowSnapshot
from app.models.market import MarketSnapshot
from app.services.collector import CollectionResult

log = structlog.get_logger(__name__)


def compact_number(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    absolute = abs(value)
    for divisor, suffix in (
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    ):
        if absolute >= divisor:
            return f"{value / divisor:.2f}{suffix}"
    return f"{value:.4f}".rstrip("0").rstrip(".")


class TelegramNotificationService:
    def __init__(
        self,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        configured_chat_id: str | None = None,
        chat_id_file: Path = Path(".telegram_chat_id"),
    ) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.chat_id_file = chat_id_file
        self.chat_id = self._initial_chat_id(configured_chat_id)

    def _initial_chat_id(self, configured_chat_id: str | None) -> int | None:
        value = configured_chat_id
        if not value and self.chat_id_file.exists():
            value = self.chat_id_file.read_text(encoding="utf-8").strip()
        try:
            return int(value) if value else None
        except ValueError:
            log.error("invalid_telegram_chat_id", value=value)
            return None

    def register_chat(self, chat_id: int) -> bool:
        if self.chat_id is not None and self.chat_id != chat_id:
            return False
        self.chat_id = chat_id
        self.chat_id_file.write_text(str(chat_id), encoding="utf-8")
        return True

    def is_authorized(self, chat_id: int) -> bool:
        return self.chat_id == chat_id

    async def notify_collection(self, result: CollectionResult) -> None:
        if self.chat_id is None or result.borrow_received == 0:
            return
        if result.error:
            text = (
                "⚠️ Збір завершився частково\n"
                f"Borrow отримано: {result.borrow_received}\n"
                f"Помилка: {result.error}"
            )
        else:
            text = "✅ Нові дані зібрано\n\n" + await self.render_status()
        try:
            await self.bot.send_message(self.chat_id, text)
        except TelegramAPIError as exc:
            log.error("telegram_notification_failed", error=str(exc))

    async def notify_anomalies(self, event_ids: tuple[int, ...]) -> None:
        if self.chat_id is None or not event_ids:
            return
        async with self.session_factory() as session:
            events = (
                await session.scalars(
                    select(AnomalyEvent)
                    .where(AnomalyEvent.id.in_(event_ids))
                    .order_by(AnomalyEvent.detected_at)
                )
            ).all()
        for event in events:
            try:
                await self.bot.send_message(self.chat_id, self._format_anomaly(event))
            except TelegramAPIError as exc:
                log.error(
                    "telegram_anomaly_notification_failed",
                    event_id=event.id,
                    error=str(exc),
                )

    async def recent_anomaly_messages(self, *, limit: int = 3) -> list[str]:
        async with self.session_factory() as session:
            events = (
                await session.scalars(
                    select(AnomalyEvent)
                    .order_by(AnomalyEvent.detected_at.desc())
                    .limit(limit)
                )
            ).all()
        return [self._format_anomaly(event) for event in events]

    def _format_anomaly(self, event: AnomalyEvent) -> str:
        reason = event.reason_json or {}
        spike_started = self._parse_time(reason.get("spike_started_at"))
        first_jump = self._parse_time(reason.get("first_jump_at"))
        confirmed = self._parse_time(reason.get("confirmed_at")) or event.detected_at
        delay_minutes = (
            int((self._as_utc(confirmed) - self._as_utc(first_jump)).total_seconds() / 60)
            if first_jump
            else None
        )
        scenario = str(reason.get("scenario") or "UNKNOWN")
        scenario_label, interpretation = {
            "POST_PUMP_BORROW": (
                "BOR біля локальної вершини після пампу",
                "підвищений ризик зниження ціни",
            ),
            "DURING_PUMP_BORROW": (
                "BOR під час сильного росту",
                "можливий набір short; потрібне підтвердження розвороту",
            ),
            "NO_PUMP": (
                "аномальний BOR без підтвердженого пампу",
                "напрямок поки не підтверджений ціною",
            ),
        }.get(scenario, (scenario, "недостатньо ринкового контексту"))
        velocity = Decimal(str(reason.get("borrow_velocity_usd_per_min") or "0"))
        source_label = (
            f"{event.source_name} (TEST DATA)"
            if event.source_name == "fixture"
            else event.source_name
        )
        lines = [
            f"🚨 МАРЖИНАЛЬНИЙ ЗАЙМ: {event.symbol}",
            "",
            f"Score: {event.anomaly_score:.1f}/100",
            f"Сценарій: {scenario_label}",
            f"Інтерпретація: {interpretation}",
            f"Source: {source_label}",
            "",
            "⏱ Час аномалії:",
        ]
        if spike_started:
            lines.append(f"База перед ростом: {self._format_time(spike_started)}")
        if first_jump:
            lines.append(f"Перший стрибок BOR: {self._format_time(first_jump)}")
        lines.append(f"Підтверджено: {self._format_time(confirmed)}")
        if delay_minutes is not None:
            lines.append(f"Затримка підтвердження: {delay_minutes} хв")
        lines.extend(
            [
                "",
                f"Borrow ({event.window_minutes}m):",
                f"${compact_number(event.borrow_before)} → ${compact_number(event.borrow_now)}",
                f"ΔBOR: +${compact_number(event.borrow_delta)} ({event.borrow_delta_pct:+.1f}%)",
                f"ΔREP: {event.repay_delta:+,.0f}",
                f"Net Borrow: +${compact_number(event.net_borrow_delta)}",
                f"Velocity: ${compact_number(velocity)}/min",
            ]
        )
        window_deltas = reason.get("window_deltas")
        if isinstance(window_deltas, dict) and window_deltas:
            lines.extend(["", "ΔBOR за вікнами:"])
            for label, raw_metrics in window_deltas.items():
                if not isinstance(raw_metrics, dict):
                    continue
                delta = Decimal(str(raw_metrics.get("borrow_delta_usd") or "0"))
                delta_pct = Decimal(str(raw_metrics.get("borrow_delta_pct") or "0"))
                lines.append(f"{label}: +${compact_number(delta)} ({delta_pct:+.1f}%)")
        if event.price_at_signal is not None:
            lines.extend(
                [
                    "",
                    f"Price at signal: ${compact_number(event.price_at_signal)}",
                    f"15m: {self._percent(event.price_change_15m_before)}",
                    f"1h: {self._percent(event.price_change_1h_before)}",
                    f"4h: {self._percent(event.price_change_4h_before)}",
                ]
            )
        if event.volume_15m is not None:
            lines.extend(
                [
                    "",
                    f"Volume 15m: ${compact_number(event.volume_15m)}",
                    f"Volume spike: {self._multiple(event.volume_spike_ratio)}",
                    f"Borrow / volume: {self._ratio_percent(event.borrow_to_volume_ratio)}",
                ]
            )
        lines.extend(["", "Research signal — no automatic trading"])
        return "\n".join(lines)

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _format_time(self, value: datetime) -> str:
        utc_value = self._as_utc(value)
        kyiv = utc_value.astimezone(ZoneInfo("Europe/Kyiv"))
        return f"{utc_value:%Y-%m-%d %H:%M} UTC / {kyiv:%H:%M} Київ"

    @staticmethod
    def _percent(value: Decimal | None) -> str:
        return f"{value:+.2f}%" if value is not None else "n/a"

    @staticmethod
    def _multiple(value: Decimal | None) -> str:
        return f"{value:.2f}x" if value is not None else "n/a"

    @staticmethod
    def _ratio_percent(value: Decimal | None) -> str:
        return f"{value * Decimal('100'):.2f}%" if value is not None else "n/a"

    async def render_status(self) -> str:
        async with self.session_factory() as session:
            borrow_count = int(
                await session.scalar(select(func.count(BorrowSnapshot.id))) or 0
            )
            market_count = int(
                await session.scalar(select(func.count(MarketSnapshot.id))) or 0
            )
            borrow_rows = (
                await session.scalars(
                    select(BorrowSnapshot)
                    .order_by(BorrowSnapshot.source_timestamp.desc(), BorrowSnapshot.id.desc())
                    .limit(50)
                )
            ).all()
            market_rows = (
                await session.scalars(
                    select(MarketSnapshot)
                    .order_by(MarketSnapshot.captured_at.desc(), MarketSnapshot.id.desc())
                    .limit(50)
                )
            ).all()

        latest_borrow: dict[str, BorrowSnapshot] = {}
        for row in borrow_rows:
            latest_borrow.setdefault(row.symbol, row)
        latest_market: dict[str, MarketSnapshot] = {}
        for row in market_rows:
            latest_market.setdefault(row.symbol, row)

        lines = [
            "📊 MARGIN MONITOR",
            f"Borrow snapshots: {borrow_count}",
            f"Market snapshots: {market_count}",
        ]
        if borrow_rows:
            lines.append(f"Оновлення джерела: {self._format_time(borrow_rows[0].source_timestamp)}")
        lines.append("")
        for symbol in sorted(set(latest_borrow) | set(latest_market)):
            borrow = latest_borrow.get(symbol)
            market = latest_market.get(symbol)
            lines.append(f"🪙 {symbol}")
            if borrow:
                ratio = (
                    compact_number(borrow.borrow_repay_ratio)
                    if borrow.borrow_repay_ratio is not None
                    else "n/a"
                )
                lines.append(
                    f"BOR ${compact_number(borrow.borrow_usd)} | "
                    f"REP ${compact_number(borrow.repay_usd)} | B/R {ratio}"
                )
            if market:
                lines.append(
                    f"Price ${compact_number(market.price)} | "
                    f"Vol24h ${compact_number(market.quote_volume_24h)}"
                )
            lines.append("")
        lines.append("Research data only — no automatic trading")
        return "\n".join(lines)

    async def render_research_stats(self) -> str:
        async with self.session_factory() as session:
            live_events = int(
                await session.scalar(
                    select(func.count(AnomalyEvent.id)).where(
                        AnomalyEvent.source_name != "fixture"
                    )
                )
                or 0
            )
            rows = (
                await session.execute(
                    select(EventOutcome, AnomalyEvent)
                    .join(AnomalyEvent, AnomalyEvent.id == EventOutcome.anomaly_event_id)
                    .where(AnomalyEvent.source_name != "fixture")
                    .order_by(EventOutcome.horizon_minutes)
                )
            ).all()

        grouped: dict[int, list[EventOutcome]] = {}
        for outcome, _event in rows:
            grouped.setdefault(outcome.horizon_minutes, []).append(outcome)
        lines = [
            "📈 СТАТИСТИКА BOR-СИГНАЛІВ",
            f"Живих сигналів записано: {live_events}",
            "",
        ]
        labels = {15: "15 хв", 60: "1 год", 240: "4 год", 1440: "24 год"}
        for horizon in (15, 60, 240, 1440):
            outcomes = grouped.get(horizon, [])
            if not outcomes:
                lines.append(f"{labels[horizon]}: ще немає результатів")
                continue
            target = Decimal("5") if horizon == 1440 else Decimal("2")
            successful = [
                outcome
                for outcome in outcomes
                if outcome.max_favorable_move_pct >= target
                and outcome.max_adverse_move_pct <= Decimal("4")
            ]
            average_dump = sum(
                (outcome.max_favorable_move_pct for outcome in outcomes), Decimal("0")
            ) / Decimal(len(outcomes))
            average_adverse = sum(
                (outcome.max_adverse_move_pct for outcome in outcomes), Decimal("0")
            ) / Decimal(len(outcomes))
            success_rate = Decimal(len(successful)) / Decimal(len(outcomes)) * Decimal("100")
            lines.append(
                f"{labels[horizon]}: {len(outcomes)} | успішних {len(successful)} "
                f"({success_rate:.0f}%)"
            )
            lines.append(
                f"  середнє max падіння {average_dump:.2f}% | "
                f"рух проти short {average_adverse:.2f}%"
            )
        lines.extend(
            [
                "",
                "Критерій: падіння ≥2% (24h: ≥5%) і рух проти short ≤4%.",
                "Статистика дослідницька; надійні висновки — після накопичення вибірки.",
            ]
        )
        return "\n".join(lines)

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import main_keyboard
from app.models.anomaly import AnomalyEvent, EventOutcome
from app.models.borrow import BorrowSnapshot
from app.models.market import Candle, MarketSnapshot
from app.models.watch import PumpWatch, PumpWatchTransition
from app.services.borrow_change import BorrowChange, calculate_borrow_change
from app.services.collector import CollectionResult
from app.services.price_analyzer import PriceContext, analyze_price_context
from app.utils.datetime import utc_now

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
        excluded_symbols: frozenset[str] = frozenset(),
    ) -> None:
        self.bot = bot
        self.session_factory = session_factory
        self.chat_id_file = chat_id_file
        self.excluded_symbols = excluded_symbols
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
        if self.chat_id is None or not result.error:
            return
        text = (
            "⚠️ Збір завершився частково\n"
            f"Borrow отримано: {result.borrow_received}\n"
            f"Помилка: {result.error}"
        )
        try:
            await self.bot.send_message(self.chat_id, text, reply_markup=main_keyboard())
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
            if event.symbol in self.excluded_symbols or not self._should_notify_anomaly(
                event
            ):
                log.info(
                    "telegram_anomaly_suppressed_for_research",
                    event_id=event.id,
                    symbol=event.symbol,
                    scenario=(event.reason_json or {}).get("scenario"),
                )
                continue
            try:
                await self.bot.send_message(self.chat_id, self._format_anomaly(event))
            except TelegramAPIError as exc:
                log.error(
                    "telegram_anomaly_notification_failed",
                    event_id=event.id,
                    error=str(exc),
                )

    async def notify_reversal_transitions(self) -> None:
        if self.chat_id is None:
            return
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(PumpWatchTransition, PumpWatch)
                    .join(PumpWatch, PumpWatch.id == PumpWatchTransition.pump_watch_id)
                    .where(
                        PumpWatchTransition.status.in_(
                            ("WATCH", "REVERSAL_WARNING", "SHORT_CONFIRMED")
                        ),
                        PumpWatchTransition.notified_at.is_(None),
                    )
                    .order_by(PumpWatchTransition.occurred_at)
                )
            ).all()
            for transition, watch in rows:
                if transition.status == "WATCH" and (
                    not self._is_initial_pump_transition(transition)
                    or self._as_utc(transition.occurred_at)
                    < utc_now() - timedelta(minutes=15)
                ):
                    transition.notified_at = utc_now()
                    continue
                if watch.symbol in self.excluded_symbols:
                    transition.notified_at = utc_now()
                    log.info(
                        "telegram_reversal_suppressed_high_cap",
                        transition_id=transition.id,
                        symbol=watch.symbol,
                    )
                    continue
                try:
                    await self.bot.send_message(
                        self.chat_id,
                        self._format_reversal_transition(transition, watch),
                        reply_markup=main_keyboard(),
                    )
                except TelegramAPIError as exc:
                    log.error(
                        "telegram_reversal_notification_failed",
                        transition_id=transition.id,
                        error=str(exc),
                    )
                    continue
                transition.notified_at = utc_now()
            await session.commit()

    def _format_reversal_transition(
        self, transition: PumpWatchTransition, watch: PumpWatch
    ) -> str:
        reasons = transition.reason_json.get("reasons", []) if transition.reason_json else []
        reason_labels = {
            "closed_5m_below_local_support": "5m закрилась нижче локальної підтримки",
            "confirmed_lower_high": "сформовано нижчий локальний максимум",
            "next_5m_close_failed_to_reclaim_support": (
                "наступна 5m свічка не повернула підтримку"
            ),
        }
        readable_reasons = []
        for reason in reasons:
            label = reason_labels.get(str(reason))
            if label is None and str(reason).startswith("drawdown_from_peak_gte_"):
                label = "відкат від піку досяг порога попередження"
            elif label is None and str(reason).startswith("drawdown_gte_"):
                label = "сильний відкат разом із пробоєм підтримки"
            if label and label not in readable_reasons:
                readable_reasons.append(label)

        pump_detected = transition.status == "WATCH"
        confirmed = transition.status == "SHORT_CONFIRMED"
        lines = [
            (
                f"🔥 PUMP DETECTED: {watch.symbol}"
                if pump_detected
                else (
                    f"🔻 РОЗВОРОТ ПІДТВЕРДЖЕНО: {watch.symbol}"
                    if confirmed
                    else f"⚠️ ПОПЕРЕДЖЕННЯ РОЗВОРОТУ: {watch.symbol}"
                )
            ),
            "",
            f"Час: {self._format_time(transition.occurred_at)}",
            f"Price: ${compact_number(transition.price)}",
            f"Peak: ${compact_number(transition.peak_price)}",
            f"Відкат від піку: -{transition.drawdown_pct:.2f}%",
        ]
        if transition.support_price is not None:
            lines.append(f"5m support: ${compact_number(transition.support_price)}")
        lines.extend(
            [
                f"1h: {self._percent(transition.price_change_1h)} | "
                f"4h: {self._percent(transition.price_change_4h)}",
                f"BOR: ${compact_number(transition.borrow_usd)} | "
                f"B/R {compact_number(transition.borrow_repay_ratio)}",
                f"ΔBOR 3m: {self._signed_money(transition.borrow_delta_3m)} | "
                f"15m: {self._signed_money(transition.borrow_delta_15m)}",
            ]
        )
        if readable_reasons:
            lines.extend(["", "Ознаки:", *(f"• {reason}" for reason in readable_reasons)])
        lines.extend(
            [
                "",
                (
                    "Монету додано у WATCH. Чекаємо BOR або ознаки розвороту."
                    if pump_detected
                    else (
                        "Структура ціни підтвердила розворот, але це не автоматичний вхід."
                        if confirmed
                        else "Це раннє попередження. Чекаємо закріплення нижче підтримки."
                    )
                ),
                "Research signal — no automatic trading",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _is_initial_pump_transition(transition: PumpWatchTransition) -> bool:
        reasons = transition.reason_json.get("reasons", []) if transition.reason_json else []
        return any(str(reason).startswith("pump_detected:") for reason in reasons)

    async def recent_anomaly_messages(self, *, limit: int = 3) -> list[str]:
        async with self.session_factory() as session:
            events = (
                await session.scalars(
                    select(AnomalyEvent)
                    .order_by(AnomalyEvent.detected_at.desc())
                    .limit(max(limit * 20, 100))
                )
            ).all()
        actionable = [
            event
            for event in events
            if event.symbol not in self.excluded_symbols
            and self._should_notify_anomaly(event)
        ]
        return [self._format_anomaly(event) for event in actionable[:limit]]

    @staticmethod
    def _should_notify_anomaly(event: AnomalyEvent) -> bool:
        scenario = str((event.reason_json or {}).get("scenario") or "UNKNOWN")
        return scenario in {"POST_PUMP_BORROW", "DURING_PUMP_BORROW"}

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

    async def render_watchlist(self) -> str:
        async with self.session_factory() as session:
            latest_source_at = await session.scalar(
                select(func.max(BorrowSnapshot.source_timestamp))
            )
            if latest_source_at is None:
                return "👀 WATCH\n\nДаних для аналізу ще немає."

            watch_rows = list(
                (
                    await session.scalars(
                        select(PumpWatch)
                        .where(
                            PumpWatch.status.in_(
                                ("WATCH", "REVERSAL_WARNING", "SHORT_CONFIRMED")
                            )
                        )
                        .order_by(PumpWatch.started_at.desc())
                    )
                ).all()
            )
            watch_rows = [
                row for row in watch_rows if row.symbol not in self.excluded_symbols
            ]
            borrow_rows = list(
                (
                    await session.scalars(
                        select(BorrowSnapshot)
                        .where(
                            BorrowSnapshot.source_timestamp
                            >= latest_source_at - timedelta(hours=1)
                        )
                        .order_by(
                            BorrowSnapshot.source_timestamp.desc(),
                            BorrowSnapshot.id.desc(),
                        )
                    )
                ).all()
            )
            borrow_rows = [
                row for row in borrow_rows if row.symbol not in self.excluded_symbols
            ]
            symbols = sorted(
                {row.symbol for row in borrow_rows}
                | {row.symbol for row in watch_rows}
            )
            market_rows: list[MarketSnapshot] = []
            candle_rows: list[Candle] = []
            event_rows: list[AnomalyEvent] = []
            if symbols:
                market_rows = list(
                    (
                        await session.scalars(
                            select(MarketSnapshot)
                            .where(MarketSnapshot.symbol.in_(symbols))
                            .order_by(
                                MarketSnapshot.captured_at.desc(),
                                MarketSnapshot.id.desc(),
                            )
                        )
                    ).all()
                )
                candle_rows = list(
                    (
                        await session.scalars(
                            select(Candle)
                            .where(
                                Candle.symbol.in_(symbols),
                                Candle.interval == "5m",
                                Candle.open_time
                                >= latest_source_at - timedelta(hours=5),
                                Candle.open_time <= latest_source_at,
                            )
                            .order_by(Candle.symbol, Candle.open_time)
                        )
                    ).all()
                )
                event_rows = list(
                    (
                        await session.scalars(
                            select(AnomalyEvent)
                            .where(
                                AnomalyEvent.symbol.in_(symbols),
                                AnomalyEvent.detected_at
                                >= latest_source_at - timedelta(hours=4),
                            )
                            .order_by(AnomalyEvent.detected_at.desc())
                        )
                    ).all()
                )

        latest_borrow: dict[str, BorrowSnapshot] = {}
        borrow_history: dict[str, list[BorrowSnapshot]] = {}
        for row in borrow_rows:
            latest_borrow.setdefault(row.symbol, row)
            borrow_history.setdefault(row.symbol, []).append(row)
        latest_market: dict[str, MarketSnapshot] = {}
        for row in market_rows:
            latest_market.setdefault(row.symbol, row)
        candle_history: dict[str, list[Candle]] = {}
        for row in candle_rows:
            candle_history.setdefault(row.symbol, []).append(row)
        latest_event: dict[str, AnomalyEvent] = {}
        for row in event_rows:
            latest_event.setdefault(row.symbol, row)
        latest_watch: dict[str, PumpWatch] = {}
        for row in watch_rows:
            latest_watch.setdefault(row.symbol, row)

        watches: list[
            tuple[
                Decimal,
                str,
                BorrowSnapshot,
                MarketSnapshot,
                PriceContext,
                BorrowChange | None,
                BorrowChange | None,
                AnomalyEvent | None,
                PumpWatch | None,
            ]
        ] = []
        for symbol, borrow in latest_borrow.items():
            market = latest_market.get(symbol)
            if market is None:
                continue
            price = analyze_price_context(
                candle_history.get(symbol, []), borrow.source_timestamp
            )
            watch = latest_watch.get(symbol)
            if watch is None and price.scenario not in {
                "POST_PUMP_BORROW",
                "DURING_PUMP_BORROW",
            }:
                continue
            strength = max(
                price.price_change_1h or Decimal("0"),
                price.price_change_4h or Decimal("0"),
            )
            watches.append(
                (
                    strength,
                    symbol,
                    borrow,
                    market,
                    price,
                    calculate_borrow_change(
                        borrow_history.get(symbol, []), borrow, minutes=3
                    ),
                    calculate_borrow_change(
                        borrow_history.get(symbol, []), borrow, minutes=15
                    ),
                    latest_event.get(symbol),
                    watch,
                )
            )

        lines = [
            "👀 WATCH — АКТИВНІ ПАМПИ",
            f"Оновлення: {self._format_time(latest_source_at)}",
            "",
        ]
        if not watches:
            lines.extend(
                [
                    "Зараз немає монет, що виконують поріг пампу:",
                    "1h ≥5% або 4h ≥10%.",
                ]
            )
            return "\n".join(lines)

        for (
            _strength,
            symbol,
            borrow,
            market,
            price,
            change_3m,
            change_15m,
            event,
            watch,
        ) in sorted(watches, key=lambda item: item[0], reverse=True):
            lines.extend(
                [
                    f"🪙 {symbol} — "
                    f"{self._watch_state_label(watch) if watch else self._pump_label(price)}",
                    f"Price ${compact_number(market.price)} | "
                    f"1h {self._percent(price.price_change_1h)} | "
                    f"4h {self._percent(price.price_change_4h)}",
                    f"BOR ${compact_number(borrow.borrow_usd)} | "
                    f"B/R {compact_number(borrow.borrow_repay_ratio)}",
                    f"ΔBOR 3m {self._format_borrow_change(change_3m)} | "
                    f"15m {self._format_borrow_change(change_15m)}",
                    (
                        f"BOR anomaly: ✅ score {event.anomaly_score:.1f}/100"
                        if event is not None
                        else "BOR anomaly: ⏳ не підтверджено"
                    ),
                ]
            )
            if watch is not None:
                lines.append(
                    f"Peak ${compact_number(watch.peak_price)} | "
                    f"відкат -{watch.drawdown_pct:.2f}%"
                )
                if watch.support_price is not None:
                    lines.append(f"5m support ${compact_number(watch.support_price)}")
            lines.append("")
        lines.append("SHORT CONFIRMED — це дослідницький сигнал, не автоматичний вхід.")
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
            latest_source_at = await session.scalar(
                select(func.max(BorrowSnapshot.source_timestamp))
            )
            borrow_rows = []
            if latest_source_at is not None:
                borrow_rows = list(
                    (
                        await session.scalars(
                            select(BorrowSnapshot)
                            .where(
                                BorrowSnapshot.source_timestamp
                                >= latest_source_at - timedelta(hours=1)
                            )
                            .order_by(
                                BorrowSnapshot.source_timestamp.desc(),
                                BorrowSnapshot.id.desc(),
                            )
                        )
                    ).all()
                )
            market_rows = (
                await session.scalars(
                    select(MarketSnapshot)
                    .order_by(MarketSnapshot.captured_at.desc(), MarketSnapshot.id.desc())
                    .limit(100)
                )
            ).all()

            excluded_seen = sorted(
                {
                    row.symbol
                    for row in borrow_rows
                    if row.symbol in self.excluded_symbols
                }
            )
            borrow_rows = [
                row for row in borrow_rows if row.symbol not in self.excluded_symbols
            ]
            market_rows = [
                row for row in market_rows if row.symbol not in self.excluded_symbols
            ]

            symbols = sorted({row.symbol for row in borrow_rows})
            candle_rows: list[Candle] = []
            if symbols and latest_source_at is not None:
                candle_rows = list(
                    (
                        await session.scalars(
                            select(Candle)
                            .where(
                                Candle.symbol.in_(symbols),
                                Candle.interval == "5m",
                                Candle.open_time
                                >= latest_source_at - timedelta(hours=5),
                                Candle.open_time <= latest_source_at,
                            )
                            .order_by(Candle.symbol, Candle.open_time)
                        )
                    ).all()
                )

        latest_borrow: dict[str, BorrowSnapshot] = {}
        for row in borrow_rows:
            latest_borrow.setdefault(row.symbol, row)
        latest_market: dict[str, MarketSnapshot] = {}
        for row in market_rows:
            latest_market.setdefault(row.symbol, row)
        borrow_history: dict[str, list[BorrowSnapshot]] = {}
        for row in borrow_rows:
            borrow_history.setdefault(row.symbol, []).append(row)
        candle_history: dict[str, list[Candle]] = {}
        for row in candle_rows:
            candle_history.setdefault(row.symbol, []).append(row)

        lines = [
            "📊 MARGIN MONITOR",
            f"Borrow snapshots: {borrow_count}",
            f"Market snapshots: {market_count}",
        ]
        if borrow_rows:
            lines.append(f"Оновлення джерела: {self._format_time(borrow_rows[0].source_timestamp)}")
        if excluded_seen:
            lines.append(f"High-cap приховано: {', '.join(excluded_seen)}")
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
                change_3m = calculate_borrow_change(
                    borrow_history.get(symbol, []), borrow, minutes=3
                )
                change_15m = calculate_borrow_change(
                    borrow_history.get(symbol, []), borrow, minutes=15
                )
                lines.append(
                    f"ΔBOR 3m {self._format_borrow_change(change_3m)} | "
                    f"15m {self._format_borrow_change(change_15m)}"
                )
            if market:
                price = (
                    analyze_price_context(
                        candle_history.get(symbol, []), borrow.source_timestamp
                    )
                    if borrow
                    else PriceContext()
                )
                lines.append(
                    f"Price ${compact_number(market.price)} | "
                    f"1h {self._percent(price.price_change_1h)} | "
                    f"4h {self._percent(price.price_change_4h)}"
                )
                lines.append(
                    f"Режим: {self._pump_label(price)} | "
                    f"Vol24h ${compact_number(market.quote_volume_24h)}"
                )
            lines.append("")
        lines.append("Research data only — no automatic trading")
        return "\n".join(lines)

    @staticmethod
    def _format_borrow_change(change: BorrowChange | None) -> str:
        if change is None:
            return "n/a"
        amount = (
            f"+${compact_number(change.delta_usd)}"
            if change.delta_usd >= 0
            else f"-${compact_number(abs(change.delta_usd))}"
        )
        percent = f" ({change.delta_pct:+.1f}%)" if change.delta_pct is not None else ""
        return amount + percent

    @staticmethod
    def _signed_money(value: Decimal | None) -> str:
        if value is None:
            return "n/a"
        return (
            f"+${compact_number(value)}"
            if value >= 0
            else f"-${compact_number(abs(value))}"
        )

    @staticmethod
    def _watch_state_label(watch: PumpWatch) -> str:
        return {
            "WATCH": "👀 WATCH / памп активний",
            "REVERSAL_WARNING": "⚠️ REVERSAL WARNING",
            "SHORT_CONFIRMED": "🔻 SHORT CONFIRMED",
        }.get(watch.status, watch.status)

    @staticmethod
    def _pump_label(price: PriceContext) -> str:
        return {
            "POST_PUMP_BORROW": "🔥 PUMP біля 4h high",
            "DURING_PUMP_BORROW": "⚠️ PUMP / відкат",
            "NO_PUMP": "NO PUMP",
        }.get(price.scenario, "UNKNOWN")

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

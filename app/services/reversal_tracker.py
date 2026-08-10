from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.borrow import BorrowSnapshot
from app.models.market import Candle, MarketSnapshot
from app.models.watch import PumpWatch, PumpWatchTransition
from app.services.borrow_change import BorrowChange, calculate_borrow_change
from app.services.price_analyzer import PriceContext, analyze_price_context

log = structlog.get_logger(__name__)
ACTIVE_STATUSES = ("WATCH", "REVERSAL_WARNING", "SHORT_CONFIRMED")
PUMP_SCENARIOS = {"POST_PUMP_BORROW", "DURING_PUMP_BORROW"}
PERCENT = Decimal("100")


@dataclass(frozen=True, slots=True)
class ReversalAnalysis:
    peak_price: Decimal
    drawdown_pct: Decimal
    support_price: Decimal | None
    latest_closed_at: datetime | None
    support_break: bool
    lower_high: bool
    new_peak: bool
    warning: bool
    confirmed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReversalTrackingResult:
    watches_created: int = 0
    watches_rearmed: int = 0
    warnings_created: int = 0
    reversals_confirmed: int = 0
    watches_expired: int = 0


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def can_rearm_confirmed_watch(
    watch: PumpWatch,
    price_context: PriceContext,
    current_price: Decimal,
) -> bool:
    """Treat a fresh pump above the old broken support as a new watch episode."""
    return (
        watch.status == "SHORT_CONFIRMED"
        and price_context.scenario in PUMP_SCENARIOS
        and watch.support_price is not None
        and current_price > watch.support_price
    )


def _closed_candles(candles: list[Candle], evaluated_at: datetime) -> list[Candle]:
    evaluated_utc = _as_utc(evaluated_at)
    return sorted(
        (candle for candle in candles if _as_utc(candle.close_time) <= evaluated_utc),
        key=lambda candle: candle.open_time,
    )


def _has_lower_high(candles: list[Candle], *, minimum_drop_pct: Decimal) -> bool:
    if len(candles) < 5:
        return False
    pivots: list[Decimal] = []
    for index in range(1, len(candles) - 1):
        previous = candles[index - 1].high
        current = candles[index].high
        following = candles[index + 1].high
        if current >= previous and current > following:
            pivots.append(current)
    if len(pivots) < 2:
        return False
    return pivots[-1] <= pivots[-2] * (Decimal("1") - minimum_drop_pct / PERCENT)


def _primary_structure_support(
    candles: list[Candle],
    *,
    peak_price: Decimal,
    lookback_candles: int,
    top_zone_depth_pct: Decimal,
) -> Decimal | None:
    """Return the latest confirmed 5m swing low inside the pump's top zone.

    The newest candle is excluded because it is the candle being tested for a
    break. A pivot needs a candle on both sides, so the level is available
    without using future data.
    """
    if len(candles) < lookback_candles + 1 or peak_price <= 0:
        return None
    reference = candles[:-1]
    top_zone_floor = peak_price * (Decimal("1") - top_zone_depth_pct / PERCENT)
    pivots: list[Decimal] = []
    for index in range(1, len(reference) - 1):
        previous = reference[index - 1].low
        current = reference[index].low
        following = reference[index + 1].low
        if current <= previous and current < following and current >= top_zone_floor:
            pivots.append(current)
    if pivots:
        return pivots[-1]

    fallback = min(candle.low for candle in reference[-lookback_candles:])
    return fallback if fallback >= top_zone_floor else None


def analyze_reversal(
    candles: list[Candle],
    *,
    evaluated_at: datetime,
    current_price: Decimal,
    previous_peak: Decimal,
    warning_at: datetime | None,
    warning_support: Decimal | None,
    warning_drawdown_pct: Decimal = Decimal("3"),
    confirm_drawdown_pct: Decimal = Decimal("5"),
    support_lookback_candles: int = 4,
    lower_high_drop_pct: Decimal = Decimal("0.5"),
    top_zone_depth_pct: Decimal = Decimal("8"),
) -> ReversalAnalysis:
    closed = _closed_candles(candles, evaluated_at)
    latest = closed[-1] if closed else None
    latest_high = latest.high if latest else current_price
    peak = max(previous_peak, current_price, latest_high)
    new_peak = peak > previous_peak
    drawdown = (peak - current_price) / peak * PERCENT if peak > 0 else Decimal("0")

    support = _primary_structure_support(
        closed,
        peak_price=peak,
        lookback_candles=support_lookback_candles,
        top_zone_depth_pct=top_zone_depth_pct,
    )
    support_break = bool(latest and support is not None and latest.close < support)
    lower_high = _has_lower_high(closed[-24:], minimum_drop_pct=lower_high_drop_pct)

    reasons: list[str] = []
    drawdown_warning = drawdown >= warning_drawdown_pct
    if drawdown_warning:
        reasons.append(f"drawdown_from_peak_gte_{warning_drawdown_pct}")
    if support_break:
        reasons.append("closed_5m_below_local_support")
    if lower_high:
        reasons.append("confirmed_lower_high")
    warning = support_break or (drawdown_warning and lower_high)

    failed_reclaim = False
    if latest and warning_at and warning_support is not None:
        failed_reclaim = (
            _as_utc(latest.close_time) > _as_utc(warning_at) and latest.close < warning_support
        )
    confirmed = failed_reclaim
    if failed_reclaim:
        reasons.append("next_5m_close_failed_to_reclaim_support")
        if drawdown >= confirm_drawdown_pct:
            reasons.append(f"drawdown_gte_{confirm_drawdown_pct}_with_failed_reclaim")

    return ReversalAnalysis(
        peak_price=peak,
        drawdown_pct=drawdown,
        support_price=warning_support if warning_at and warning_support else support,
        latest_closed_at=latest.close_time if latest else None,
        support_break=support_break,
        lower_high=lower_high,
        new_peak=new_peak,
        warning=warning,
        confirmed=confirmed,
        reasons=tuple(reasons),
    )


class ReversalTracker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def evaluate_latest(
        self, *, evaluated_at: datetime | None = None
    ) -> ReversalTrackingResult:
        counters = {
            "watches_created": 0,
            "watches_rearmed": 0,
            "warnings_created": 0,
            "reversals_confirmed": 0,
            "watches_expired": 0,
        }
        async with self.session_factory() as session:
            latest_source_at = await session.scalar(
                select(BorrowSnapshot.source_timestamp)
                .order_by(BorrowSnapshot.source_timestamp.desc())
                .limit(1)
            )
            if latest_source_at is None:
                return ReversalTrackingResult()
            effective_evaluated_at = evaluated_at or latest_source_at

            borrow_rows = list(
                (
                    await session.scalars(
                        select(BorrowSnapshot)
                        .where(
                            BorrowSnapshot.source_timestamp
                            >= latest_source_at
                            - timedelta(hours=self.settings.reversal_watch_hours)
                        )
                        .order_by(BorrowSnapshot.source_timestamp.desc())
                    )
                ).all()
            )
            latest_borrow: dict[str, BorrowSnapshot] = {}
            histories: dict[str, list[BorrowSnapshot]] = {}
            current_source_symbols: set[str] = set()
            for row in borrow_rows:
                latest_borrow.setdefault(row.symbol, row)
                histories.setdefault(row.symbol, []).append(row)
                if _as_utc(row.source_timestamp) == _as_utc(latest_source_at):
                    current_source_symbols.add(row.symbol)
            symbols = sorted(latest_borrow)
            if not symbols:
                return ReversalTrackingResult()

            market_rows = list(
                (
                    await session.scalars(
                        select(MarketSnapshot)
                        .where(MarketSnapshot.symbol.in_(symbols))
                        .order_by(MarketSnapshot.captured_at.desc(), MarketSnapshot.id.desc())
                    )
                ).all()
            )
            latest_market: dict[str, MarketSnapshot] = {}
            for row in market_rows:
                latest_market.setdefault(row.symbol, row)

            candle_rows = list(
                (
                    await session.scalars(
                        select(Candle)
                        .where(
                            Candle.symbol.in_(symbols),
                            Candle.interval == "5m",
                            Candle.open_time >= effective_evaluated_at - timedelta(hours=8),
                            Candle.open_time <= effective_evaluated_at,
                        )
                        .order_by(Candle.symbol, Candle.open_time)
                    )
                ).all()
            )
            candle_history: dict[str, list[Candle]] = {}
            for row in candle_rows:
                candle_history.setdefault(row.symbol, []).append(row)

            active_rows = list(
                (
                    await session.scalars(
                        select(PumpWatch)
                        .where(PumpWatch.status.in_(ACTIVE_STATUSES))
                        .order_by(PumpWatch.started_at.desc())
                    )
                ).all()
            )
            active: dict[tuple[str, str], PumpWatch] = {}
            for row in active_rows:
                active.setdefault((row.symbol, row.source_name), row)

            for symbol, borrow in latest_borrow.items():
                if symbol in self.settings.high_cap_excluded_symbol_set:
                    continue
                market = latest_market.get(symbol)
                if market is None:
                    continue
                candles = candle_history.get(symbol, [])
                price_context = analyze_price_context(
                    candles,
                    effective_evaluated_at,
                    pump_1h_threshold=self.settings.min_price_pump_1h_pct,
                    pump_4h_threshold=self.settings.min_price_pump_4h_pct,
                    max_fresh_pump_drawdown_pct=(self.settings.max_fresh_pump_drawdown_pct),
                    bounce_after_dump_4h_pct=self.settings.bounce_after_dump_4h_pct,
                )
                key = (symbol, borrow.source_name)
                watch = active.get(key)
                if watch is not None and _as_utc(watch.expires_at) <= _as_utc(
                    effective_evaluated_at
                ):
                    self._expire_watch(
                        session,
                        watch,
                        evaluated_at=effective_evaluated_at,
                        borrow=borrow,
                        current_price=market.price,
                        price_context=price_context,
                        histories=histories,
                    )
                    counters["watches_expired"] += 1
                    active.pop(key, None)
                    continue

                if watch is None:
                    if symbol not in current_source_symbols:
                        continue
                    if price_context.scenario not in PUMP_SCENARIOS:
                        continue
                    watch = self._create_watch(
                        session,
                        borrow=borrow,
                        current_price=market.price,
                        price_context=price_context,
                        candles=candles,
                        evaluated_at=effective_evaluated_at,
                        histories=histories,
                    )
                    active[key] = watch
                    counters["watches_created"] += 1

                warning_at = watch.warning_at
                analysis = analyze_reversal(
                    [
                        candle
                        for candle in candles
                        if _as_utc(candle.open_time)
                        >= _as_utc(watch.started_at)
                        - timedelta(
                            minutes=(self.settings.reversal_support_lookback_candles + 2) * 5
                        )
                    ],
                    evaluated_at=effective_evaluated_at,
                    current_price=market.price,
                    previous_peak=watch.peak_price,
                    warning_at=warning_at,
                    warning_support=watch.support_price if warning_at else None,
                    warning_drawdown_pct=self.settings.reversal_warning_drawdown_pct,
                    confirm_drawdown_pct=self.settings.reversal_confirm_drawdown_pct,
                    support_lookback_candles=self.settings.reversal_support_lookback_candles,
                    lower_high_drop_pct=self.settings.reversal_lower_high_drop_pct,
                    top_zone_depth_pct=self.settings.reversal_top_zone_depth_pct,
                )
                previous_peak = watch.peak_price
                watch.last_evaluated_at = effective_evaluated_at
                watch.last_price = market.price
                watch.drawdown_pct = analysis.drawdown_pct
                if analysis.peak_price > watch.peak_price:
                    watch.peak_price = analysis.peak_price
                    watch.peak_at = effective_evaluated_at

                recovered_to_new_peak = (
                    watch.status == "REVERSAL_WARNING"
                    and analysis.new_peak
                    and market.price >= analysis.peak_price * Decimal("0.99")
                )
                change_3m = calculate_borrow_change(histories[symbol], borrow, minutes=3)
                change_15m = calculate_borrow_change(histories[symbol], borrow, minutes=15)

                recovered_after_confirmation = can_rearm_confirmed_watch(
                    watch,
                    price_context,
                    market.price,
                )

                if recovered_after_confirmation:
                    watch.status = "WATCH"
                    watch.started_at = effective_evaluated_at
                    watch.expires_at = effective_evaluated_at + timedelta(
                        hours=self.settings.reversal_watch_hours
                    )
                    watch.warning_at = None
                    watch.confirmed_at = None
                    watch.closed_at = None
                    watch.price_at_watch = market.price
                    watch.support_price = None
                    self._add_transition(
                        session,
                        watch,
                        status="WATCH",
                        occurred_at=effective_evaluated_at,
                        borrow=borrow,
                        price_context=price_context,
                        current_price=market.price,
                        change_3m=change_3m,
                        change_15m=change_15m,
                        reason_json={
                            "reasons": [f"pump_detected:REARMED_{price_context.scenario}"],
                            "previous_confirmation_reclaimed": True,
                        },
                    )
                    counters["watches_rearmed"] += 1
                elif recovered_to_new_peak:
                    watch.status = "WATCH"
                    watch.warning_at = None
                    watch.support_price = None
                    self._add_transition(
                        session,
                        watch,
                        status="WATCH",
                        occurred_at=effective_evaluated_at,
                        borrow=borrow,
                        price_context=price_context,
                        current_price=market.price,
                        change_3m=change_3m,
                        change_15m=change_15m,
                        reason_json={"reasons": ["new_peak_reset_warning"]},
                    )
                elif watch.status == "REVERSAL_WARNING" and analysis.confirmed:
                    watch.status = "SHORT_CONFIRMED"
                    watch.confirmed_at = analysis.latest_closed_at or effective_evaluated_at
                    watch.support_price = analysis.support_price
                    self._add_transition(
                        session,
                        watch,
                        status="SHORT_CONFIRMED",
                        occurred_at=watch.confirmed_at,
                        borrow=borrow,
                        price_context=price_context,
                        current_price=market.price,
                        change_3m=change_3m,
                        change_15m=change_15m,
                        reason_json={"reasons": list(analysis.reasons)},
                    )
                    counters["reversals_confirmed"] += 1
                elif watch.status == "WATCH" and analysis.confirmed:
                    watch.status = "SHORT_CONFIRMED"
                    watch.warning_at = analysis.latest_closed_at or effective_evaluated_at
                    watch.confirmed_at = watch.warning_at
                    watch.support_price = analysis.support_price
                    self._add_transition(
                        session,
                        watch,
                        status="SHORT_CONFIRMED",
                        occurred_at=watch.confirmed_at,
                        borrow=borrow,
                        price_context=price_context,
                        current_price=market.price,
                        change_3m=change_3m,
                        change_15m=change_15m,
                        reason_json={"reasons": list(analysis.reasons)},
                    )
                    counters["reversals_confirmed"] += 1
                elif watch.status == "WATCH" and analysis.warning:
                    watch.status = "REVERSAL_WARNING"
                    watch.warning_at = (
                        analysis.latest_closed_at
                        if analysis.support_break or analysis.lower_high
                        else effective_evaluated_at
                    )
                    watch.support_price = analysis.support_price
                    self._add_transition(
                        session,
                        watch,
                        status="REVERSAL_WARNING",
                        occurred_at=watch.warning_at,
                        borrow=borrow,
                        price_context=price_context,
                        current_price=market.price,
                        change_3m=change_3m,
                        change_15m=change_15m,
                        reason_json={"reasons": list(analysis.reasons)},
                    )
                    counters["warnings_created"] += 1

                if watch.status == "REVERSAL_WARNING" and watch.support_price is None:
                    watch.support_price = analysis.support_price

                watch.reason_json = {
                    **(watch.reason_json or {}),
                    "latest_reversal_analysis": {
                        "evaluated_at": effective_evaluated_at.isoformat(),
                        "previous_peak": str(previous_peak),
                        "peak_price": str(analysis.peak_price),
                        "drawdown_pct": str(analysis.drawdown_pct),
                        "support_price": (
                            str(analysis.support_price)
                            if analysis.support_price is not None
                            else None
                        ),
                        "support_break": analysis.support_break,
                        "lower_high": analysis.lower_high,
                        "reasons": list(analysis.reasons),
                    },
                }

            await session.commit()

        result = ReversalTrackingResult(**counters)
        log.info("reversal_tracking_complete", **counters)
        return result

    def _create_watch(
        self,
        session: AsyncSession,
        *,
        borrow: BorrowSnapshot,
        current_price: Decimal,
        price_context: PriceContext,
        candles: list[Candle],
        evaluated_at: datetime,
        histories: dict[str, list[BorrowSnapshot]],
    ) -> PumpWatch:
        recent = [
            candle
            for candle in candles
            if _as_utc(candle.open_time) >= _as_utc(evaluated_at) - timedelta(hours=4)
        ]
        peak_candle = max(recent, key=lambda candle: candle.high, default=None)
        peak_price = max(current_price, peak_candle.high if peak_candle else current_price)
        peak_at = (
            peak_candle.open_time
            if peak_candle and peak_candle.high >= current_price
            else evaluated_at
        )
        watch = PumpWatch(
            symbol=borrow.symbol,
            source_name=borrow.source_name,
            status="WATCH",
            started_at=evaluated_at,
            last_evaluated_at=evaluated_at,
            expires_at=evaluated_at + timedelta(hours=self.settings.reversal_watch_hours),
            price_at_watch=current_price,
            peak_price=peak_price,
            peak_at=peak_at,
            last_price=current_price,
            drawdown_pct=(peak_price - current_price) / peak_price * PERCENT,
            reason_json={
                "activation_scenario": price_context.scenario,
                "price_change_1h": (
                    str(price_context.price_change_1h)
                    if price_context.price_change_1h is not None
                    else None
                ),
                "price_change_4h": (
                    str(price_context.price_change_4h)
                    if price_context.price_change_4h is not None
                    else None
                ),
                "high_4h": (
                    str(price_context.high_4h) if price_context.high_4h is not None else None
                ),
                "drawdown_from_high_4h_pct": (
                    str(price_context.drawdown_from_high_4h_pct)
                    if price_context.drawdown_from_high_4h_pct is not None
                    else None
                ),
            },
        )
        session.add(watch)
        change_3m = calculate_borrow_change(histories[borrow.symbol], borrow, minutes=3)
        change_15m = calculate_borrow_change(histories[borrow.symbol], borrow, minutes=15)
        self._add_transition(
            session,
            watch,
            status="WATCH",
            occurred_at=evaluated_at,
            borrow=borrow,
            price_context=price_context,
            current_price=current_price,
            change_3m=change_3m,
            change_15m=change_15m,
            reason_json={"reasons": [f"pump_detected:{price_context.scenario}"]},
        )
        return watch

    def _expire_watch(
        self,
        session: AsyncSession,
        watch: PumpWatch,
        *,
        evaluated_at: datetime,
        borrow: BorrowSnapshot,
        current_price: Decimal,
        price_context: PriceContext,
        histories: dict[str, list[BorrowSnapshot]],
    ) -> None:
        watch.status = "EXPIRED"
        watch.closed_at = evaluated_at
        watch.last_evaluated_at = evaluated_at
        watch.last_price = current_price
        change_3m = calculate_borrow_change(histories[borrow.symbol], borrow, minutes=3)
        change_15m = calculate_borrow_change(histories[borrow.symbol], borrow, minutes=15)
        self._add_transition(
            session,
            watch,
            status="EXPIRED",
            occurred_at=evaluated_at,
            borrow=borrow,
            price_context=price_context,
            current_price=current_price,
            change_3m=change_3m,
            change_15m=change_15m,
            reason_json={"reasons": ["watch_window_elapsed"]},
        )

    @staticmethod
    def _add_transition(
        session: AsyncSession,
        watch: PumpWatch,
        *,
        status: str,
        occurred_at: datetime,
        borrow: BorrowSnapshot,
        price_context: PriceContext,
        current_price: Decimal,
        change_3m: BorrowChange | None,
        change_15m: BorrowChange | None,
        reason_json: dict[str, object],
    ) -> None:
        session.add(
            PumpWatchTransition(
                watch=watch,
                status=status,
                occurred_at=occurred_at,
                price=current_price,
                peak_price=watch.peak_price,
                drawdown_pct=watch.drawdown_pct,
                support_price=watch.support_price,
                borrow_usd=borrow.borrow_usd,
                repay_usd=borrow.repay_usd,
                borrow_repay_ratio=borrow.borrow_repay_ratio,
                borrow_delta_3m=change_3m.delta_usd if change_3m else None,
                borrow_delta_15m=change_15m.delta_usd if change_15m else None,
                price_change_1h=price_context.price_change_1h,
                price_change_4h=price_context.price_change_4h,
                reason_json=reason_json,
            )
        )

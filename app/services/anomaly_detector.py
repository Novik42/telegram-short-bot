from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.anomaly import AnomalyEvent
from app.models.borrow import BorrowSnapshot
from app.models.market import Candle
from app.providers.base import BorrowSnapshotItem
from app.services.anomaly_score import ScoreResult, calculate_anomaly_score
from app.services.borrow_metrics import BorrowMetrics, calculate_borrow_metrics
from app.services.price_analyzer import PriceContext, analyze_price_context
from app.services.volume_analyzer import VolumeContext, analyze_volume_context

log = structlog.get_logger(__name__)
WINDOWS_MINUTES = (3, 5, 15, 30, 60, 240, 1440)


class AnomalyDetector:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def detect_for_snapshots(
        self, items: list[BorrowSnapshotItem]
    ) -> list[AnomalyEvent]:
        events: list[AnomalyEvent] = []
        for item in items:
            if item.symbol in self.settings.high_cap_excluded_symbol_set:
                log.debug("high_cap_anomaly_skipped", symbol=item.symbol)
                continue
            event = await self._detect_one(item)
            if event is not None:
                events.append(event)
        return events

    async def _detect_one(self, item: BorrowSnapshotItem) -> AnomalyEvent | None:
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(BorrowSnapshot)
                    .where(
                        BorrowSnapshot.symbol == item.symbol,
                        BorrowSnapshot.source_name == item.source_name,
                        BorrowSnapshot.source_timestamp <= item.source_timestamp,
                    )
                    .order_by(BorrowSnapshot.source_timestamp)
                )
            ).all()
            if len(rows) < 2:
                return None
            current = rows[-1]
            candidates = self._window_candidates(rows, current)
            if not candidates:
                return None

            candles = (
                await session.scalars(
                    select(Candle)
                    .where(
                        Candle.symbol == item.symbol,
                        Candle.interval == "5m",
                        Candle.open_time >= current.source_timestamp - timedelta(hours=24),
                        Candle.open_time <= current.source_timestamp,
                    )
                    .order_by(Candle.open_time)
                )
            ).all()
            best = self._best_candidate(candidates, list(candles), current.source_timestamp)
            if best is None:
                return None
            metrics, price, volume, score, window_start = best
            consecutive, spike_started_at, first_jump_at = self._confirmation_details(rows)
            is_extreme = metrics.borrow_delta >= self.settings.min_borrow_delta_usd * Decimal("3")
            if consecutive < self.settings.require_confirmation_snapshots and not is_extreme:
                log.info(
                    "anomaly_waiting_confirmation",
                    symbol=item.symbol,
                    consecutive=consecutive,
                    required=self.settings.require_confirmation_snapshots,
                    score=str(score.total),
                )
                return None
            if score.total < self.settings.min_anomaly_score:
                return None

            existing = await session.scalar(
                select(AnomalyEvent.id).where(
                    AnomalyEvent.symbol == item.symbol,
                    AnomalyEvent.source_name == item.source_name,
                    AnomalyEvent.detected_at == current.source_timestamp,
                    AnomalyEvent.window_minutes == metrics.window_minutes,
                )
            )
            if existing is not None:
                return None

            reason_json = self._reason_json(
                metrics=metrics,
                price=price,
                volume=volume,
                score=score,
                window_start=window_start.source_timestamp,
                spike_started_at=spike_started_at,
                first_jump_at=first_jump_at,
                confirmed_at=current.source_timestamp,
                confirmation_snapshots=consecutive,
                window_deltas={
                    f"{candidate_metrics.window_minutes}m": {
                        "start": candidate_before.source_timestamp.isoformat(),
                        "borrow_delta_usd": str(candidate_metrics.borrow_delta),
                        "borrow_delta_pct": str(candidate_metrics.borrow_delta_pct),
                        "net_borrow_delta_usd": str(candidate_metrics.net_borrow_delta),
                    }
                    for candidate_metrics, candidate_before in candidates
                },
            )
            event = AnomalyEvent(
                symbol=item.symbol,
                detected_at=current.source_timestamp,
                source_name=item.source_name,
                window_minutes=metrics.window_minutes,
                borrow_before=metrics.borrow_before,
                borrow_now=metrics.borrow_now,
                borrow_delta=metrics.borrow_delta,
                borrow_delta_pct=metrics.borrow_delta_pct,
                repay_before=metrics.repay_before,
                repay_now=metrics.repay_now,
                repay_delta=metrics.repay_delta,
                net_borrow_delta=metrics.net_borrow_delta,
                ratio_now=metrics.ratio_now,
                price_at_signal=price.price_at_signal,
                price_change_15m_before=price.price_change_15m,
                price_change_1h_before=price.price_change_1h,
                price_change_4h_before=price.price_change_4h,
                price_change_24h_before=price.price_change_24h,
                volume_15m=volume.volume_15m,
                average_volume_15m=volume.average_volume_15m,
                volume_spike_ratio=volume.volume_spike_ratio,
                borrow_to_volume_ratio=volume.borrow_to_volume_ratio,
                open_interest_change_15m=None,
                funding_rate=None,
                anomaly_score=score.total,
                status="confirmed",
                reason_json=reason_json,
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            log.info(
                "anomaly_created",
                event_id=event.id,
                symbol=event.symbol,
                detected_at=event.detected_at.isoformat(),
                score=str(event.anomaly_score),
            )
            return event

    def _window_candidates(
        self, rows: list[BorrowSnapshot], current: BorrowSnapshot
    ) -> list[tuple[BorrowMetrics, BorrowSnapshot]]:
        candidates: list[tuple[BorrowMetrics, BorrowSnapshot]] = []
        for window in WINDOWS_MINUTES:
            target = current.source_timestamp - timedelta(minutes=window)
            before = next(
                (row for row in reversed(rows[:-1]) if row.source_timestamp <= target), None
            )
            if before is None:
                continue
            actual_minutes = max(
                1, round((current.source_timestamp - before.source_timestamp).total_seconds() / 60)
            )
            metrics = calculate_borrow_metrics(
                window_minutes=actual_minutes,
                borrow_before=before.borrow_usd,
                borrow_now=current.borrow_usd,
                repay_before=before.repay_usd,
                repay_now=current.repay_usd,
                safe_repay_epsilon=self.settings.safe_repay_epsilon_usd,
            )
            if self._passes_core_filter(metrics):
                candidates.append((metrics, before))
        return candidates

    def _passes_core_filter(self, metrics: BorrowMetrics) -> bool:
        return (
            metrics.borrow_delta >= self.settings.min_borrow_delta_usd
            and metrics.net_borrow_delta >= self.settings.min_net_borrow_delta_usd
            and metrics.borrow_delta_pct >= self.settings.min_borrow_delta_pct
        )

    def _best_candidate(
        self,
        candidates: list[tuple[BorrowMetrics, BorrowSnapshot]],
        candles: list[Candle],
        signal_at: datetime,
    ) -> tuple[
        BorrowMetrics,
        PriceContext,
        VolumeContext,
        ScoreResult,
        BorrowSnapshot,
    ] | None:
        scored = []
        for metrics, before in candidates:
            price = analyze_price_context(
                candles,
                signal_at,
                pump_1h_threshold=self.settings.min_price_pump_1h_pct,
                pump_4h_threshold=self.settings.min_price_pump_4h_pct,
            )
            volume = analyze_volume_context(candles, signal_at, metrics.borrow_delta)
            score = calculate_anomaly_score(
                metrics,
                price,
                volume,
                min_borrow_delta=self.settings.min_borrow_delta_usd,
                min_borrow_delta_pct=self.settings.min_borrow_delta_pct,
                min_net_borrow_delta=self.settings.min_net_borrow_delta_usd,
                min_price_pump_1h_pct=self.settings.min_price_pump_1h_pct,
                min_volume_spike_ratio=self.settings.min_volume_spike_ratio,
            )
            scored.append((metrics, price, volume, score, before))
        return max(scored, key=lambda item: item[3].total) if scored else None

    def _confirmation_details(
        self, rows: list[BorrowSnapshot]
    ) -> tuple[int, datetime, datetime]:
        consecutive = 0
        spike_started_at = rows[-2].source_timestamp
        first_jump_at = rows[-1].source_timestamp
        for index in range(len(rows) - 1, 0, -1):
            now = rows[index]
            before = rows[index - 1]
            minutes = max(
                1, round((now.source_timestamp - before.source_timestamp).total_seconds() / 60)
            )
            metrics = calculate_borrow_metrics(
                window_minutes=minutes,
                borrow_before=before.borrow_usd,
                borrow_now=now.borrow_usd,
                repay_before=before.repay_usd,
                repay_now=now.repay_usd,
                safe_repay_epsilon=self.settings.safe_repay_epsilon_usd,
            )
            if not self._passes_core_filter(metrics):
                break
            consecutive += 1
            spike_started_at = before.source_timestamp
            first_jump_at = now.source_timestamp
        return consecutive, spike_started_at, first_jump_at

    def _reason_json(self, **values: object) -> dict[str, object]:
        metrics = values.pop("metrics")
        price = values.pop("price")
        volume = values.pop("volume")
        score = values.pop("score")
        assert isinstance(metrics, BorrowMetrics)
        assert isinstance(price, PriceContext)
        assert isinstance(volume, VolumeContext)
        assert isinstance(score, ScoreResult)
        return {
            **{
                key: value.isoformat() if hasattr(value, "isoformat") else value
                for key, value in values.items()
            },
            "borrow_velocity_usd_per_min": str(metrics.borrow_velocity),
            "scenario": price.scenario,
            "pump_from_low_1h_pct": (
                str(price.pump_from_low_1h) if price.pump_from_low_1h is not None else None
            ),
            "pump_from_low_4h_pct": (
                str(price.pump_from_low_4h) if price.pump_from_low_4h is not None else None
            ),
            "near_local_high_4h": price.near_local_high_4h,
            "components": {key: str(value) for key, value in score.components.items()},
            "limitations": [
                "Borrow is USD-valued; borrowed token quantity is unavailable",
                "historical rarity is not scored until sufficient history exists",
            ],
        }

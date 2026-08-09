from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import Settings
from app.models import Base
from app.models.database import create_engine_and_session
from app.providers.base import BorrowSnapshotItem
from app.repositories.borrow_repository import BorrowRepository
from app.services.anomaly_detector import AnomalyDetector


@pytest.mark.asyncio
async def test_detector_records_exact_first_jump_and_confirmation_time(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'anomaly.db'}"
    settings = Settings(_env_file=None, database_url=database_url)
    engine, session_factory = create_engine_and_session(database_url)
    start = datetime(2026, 8, 9, 6, 40, tzinfo=UTC)
    items = [
        BorrowSnapshotItem(
            symbol="KAITO",
            borrow_usd=borrow,
            repay_usd=repay,
            source_timestamp=start + timedelta(minutes=index * 5),
            source_name="fixture",
        )
        for index, (borrow, repay) in enumerate(
            [
                (Decimal("10500"), Decimal("805.6")),
                (Decimal("257500"), Decimal("68500")),
                (Decimal("612000"), Decimal("110500")),
            ]
        )
    ]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        detector = AnomalyDetector(session_factory, settings)

        async with session_factory() as session:
            async with session.begin():
                await BorrowRepository(session).add_snapshots(items[:2])
        assert await detector.detect_for_snapshots([items[1]]) == []

        async with session_factory() as session:
            async with session.begin():
                await BorrowRepository(session).add_snapshots(items[2:])
        events = await detector.detect_for_snapshots([items[2]])

        assert len(events) == 1
        assert events[0].reason_json["spike_started_at"].startswith("2026-08-09T06:40")
        assert events[0].reason_json["first_jump_at"].startswith("2026-08-09T06:45")
        assert events[0].reason_json["confirmed_at"].startswith("2026-08-09T06:50")
        assert events[0].reason_json["confirmation_snapshots"] == 2
    finally:
        await engine.dispose()


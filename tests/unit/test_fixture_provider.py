import json
from decimal import Decimal

import pytest

from app.providers.base import DataSourceUnavailable
from app.providers.borrow_fixture import FixtureBorrowProvider


@pytest.mark.asyncio
async def test_fixture_replays_frames_and_calculates_ratio(tmp_path) -> None:
    path = tmp_path / "fixture.json"
    path.write_text(
        json.dumps(
            [
                {
                    "timestamp": "2026-08-09T06:40:00Z",
                    "items": [{"symbol": "kaito", "borrow_usd": 100, "repay_usd": 20}],
                },
                {
                    "timestamp": "2026-08-09T06:45:00Z",
                    "items": [{"symbol": "KAITO", "borrow_usd": 250, "repay_usd": 50}],
                },
            ]
        ),
        encoding="utf-8",
    )
    provider = FixtureBorrowProvider(path, replay_speed=1_000_000_000)

    first = await provider.fetch_snapshots()
    second = await provider.fetch_snapshots()
    empty = await provider.fetch_snapshots()

    assert first[0].symbol == "KAITO"
    assert first[0].ratio == Decimal("5")
    assert len(first[0].raw_payload_hash or "") == 64
    assert second[0].borrow_usd == Decimal("250")
    assert empty == []
    assert provider.exhausted


@pytest.mark.asyncio
async def test_fixture_rejects_naive_timestamp(tmp_path) -> None:
    path = tmp_path / "fixture.json"
    path.write_text(
        '[{"timestamp":"2026-08-09T06:40:00","items":[]}]', encoding="utf-8"
    )
    provider = FixtureBorrowProvider(path)
    with pytest.raises(DataSourceUnavailable, match="timestamps must include a timezone"):
        await provider.fetch_snapshots()


@pytest.mark.asyncio
async def test_fixture_rejects_invalid_shape(tmp_path) -> None:
    path = tmp_path / "fixture.json"
    path.write_text('{"timestamp":"not-a-list"}', encoding="utf-8")
    provider = FixtureBorrowProvider(path)
    with pytest.raises(DataSourceUnavailable):
        await provider.fetch_snapshots()


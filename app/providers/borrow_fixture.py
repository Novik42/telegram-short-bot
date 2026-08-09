from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.providers.base import BorrowDataProvider, BorrowSnapshotItem, DataSourceUnavailable
from app.utils.datetime import ensure_utc
from app.utils.hashing import stable_payload_hash


class FixtureItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    borrow_usd: Decimal = Field(ge=0)
    repay_usd: Decimal = Field(ge=0)
    ratio: Decimal | None = None


class FixtureFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime
    items: list[FixtureItem]


_fixture_adapter = TypeAdapter(list[FixtureFrame])


class FixtureBorrowProvider(BorrowDataProvider):
    """Replays fixture frames in timestamp order, preserving their relative timing."""

    def __init__(self, path: Path | str, *, replay_speed: float = 60.0) -> None:
        if replay_speed <= 0:
            raise ValueError("replay_speed must be greater than zero")
        self.path = Path(path)
        self.replay_speed = replay_speed
        self._frames: list[FixtureFrame] | None = None
        self._index = 0
        self._previous_timestamp: datetime | None = None

    def _load(self) -> list[FixtureFrame]:
        try:
            payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
            frames = _fixture_adapter.validate_python(payload)
            frames.sort(key=lambda frame: ensure_utc(frame.timestamp))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise DataSourceUnavailable(f"Cannot load fixture {self.path}: {exc}") from exc
        if not frames:
            raise DataSourceUnavailable(f"Fixture {self.path} contains no frames")
        return frames

    async def fetch_snapshots(self) -> list[BorrowSnapshotItem]:
        if self._frames is None:
            self._frames = self._load()
        if self._index >= len(self._frames):
            return []

        frame = self._frames[self._index]
        timestamp = ensure_utc(frame.timestamp)
        if self._previous_timestamp is not None:
            delay = (timestamp - self._previous_timestamp).total_seconds() / self.replay_speed
            if delay > 0:
                await asyncio.sleep(delay)

        self._index += 1
        self._previous_timestamp = timestamp
        return [self._to_snapshot(item, timestamp) for item in frame.items]

    def _to_snapshot(self, item: FixtureItem, timestamp: datetime) -> BorrowSnapshotItem:
        ratio = item.ratio
        if ratio is None and item.repay_usd > 0:
            ratio = item.borrow_usd / item.repay_usd
        raw = {
            "timestamp": timestamp.isoformat(),
            "symbol": item.symbol,
            "borrow_usd": str(item.borrow_usd),
            "repay_usd": str(item.repay_usd),
            "ratio": str(ratio) if ratio is not None else None,
        }
        return BorrowSnapshotItem(
            symbol=item.symbol,
            borrow_usd=item.borrow_usd,
            repay_usd=item.repay_usd,
            ratio=ratio,
            source_timestamp=timestamp,
            source_name="fixture",
            raw_payload_hash=stable_payload_hash(raw),
        )

    @property
    def exhausted(self) -> bool:
        return self._frames is not None and self._index >= len(self._frames)

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.borrow import BorrowSnapshot
from app.providers.base import BorrowSnapshotItem
from app.utils.datetime import utc_now


class BorrowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_snapshots(self, items: Sequence[BorrowSnapshotItem]) -> int:
        if not items:
            return 0
        captured_at = utc_now()
        values = [
            {
                "captured_at": captured_at,
                "source_timestamp": item.source_timestamp,
                "source_name": item.source_name,
                "symbol": item.symbol,
                "borrow_usd": item.borrow_usd,
                "repay_usd": item.repay_usd,
                "borrow_repay_ratio": item.ratio,
                "raw_payload_hash": item.raw_payload_hash,
            }
            for item in items
        ]
        insert_function = (
            sqlite_insert
            if self.session.bind is not None and self.session.bind.dialect.name == "sqlite"
            else insert
        )
        statement = (
            insert_function(BorrowSnapshot)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=["source_name", "symbol", "source_timestamp"]
            )
        )
        result = await self.session.execute(statement)
        return max(0, result.rowcount or 0)

    async def active_symbols(self) -> set[str]:
        result = await self.session.scalars(select(BorrowSnapshot.symbol).distinct())
        return set(result.all())

    async def count(self) -> int:
        return int(await self.session.scalar(select(func.count(BorrowSnapshot.id))) or 0)

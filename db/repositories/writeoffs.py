from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import DraftStatus, WriteoffDraft, WriteoffItem, WriteoffLog


class WriteoffRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_draft(self, client_id: int, staff_tg_id: int, staff_name: str) -> WriteoffDraft:
        draft = WriteoffDraft(client_id=client_id, staff_tg_id=staff_tg_id, staff_name=staff_name)
        self.session.add(draft)
        await self.session.flush()
        return draft

    async def add_item(
        self,
        draft_id: int,
        ingredient_id: int,
        ingredient_name: str,
        amount: float,
        unit: str,
        item_type: str = "4",
    ) -> WriteoffItem:
        item = WriteoffItem(
            draft_id=draft_id,
            ingredient_id=ingredient_id,
            ingredient_name=ingredient_name,
            amount=amount,
            unit=unit,
            item_type=item_type,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def get_with_items(self, draft_id: int) -> WriteoffDraft | None:
        result = await self.session.execute(
            select(WriteoffDraft)
            .options(selectinload(WriteoffDraft.items))
            .where(WriteoffDraft.id == draft_id)
        )
        return result.scalar_one_or_none()

    async def count_pending_by_staff_tg_id(self, staff_tg_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).where(
                WriteoffDraft.staff_tg_id == staff_tg_id,
                WriteoffDraft.status == DraftStatus.pending,
            )
        )
        return result.scalar_one()

    async def get_pending(self, client_id: int) -> list[WriteoffDraft]:
        result = await self.session.execute(
            select(WriteoffDraft)
            .options(selectinload(WriteoffDraft.items))
            .where(
                WriteoffDraft.client_id == client_id,
                WriteoffDraft.status == DraftStatus.pending,
            )
        )
        return list(result.scalars().all())

    async def confirm(self, draft: WriteoffDraft, admin_tg_id: int, poster_id: str | None) -> WriteoffLog:
        draft.status = DraftStatus.confirmed
        log = WriteoffLog(draft_id=draft.id, admin_tg_id=admin_tg_id, poster_writeoff_id=poster_id)
        self.session.add(log)
        await self.session.flush()
        return log

    async def reject(self, draft: WriteoffDraft, reason: str) -> None:
        draft.status = DraftStatus.rejected
        draft.reject_reason = reason
        await self.session.flush()

    async def get_stats(self, client_id: int, since: datetime) -> dict:
        """Return write-off statistics for the given client since `since`."""
        # counts per status
        counts_result = await self.session.execute(
            select(WriteoffDraft.status, func.count().label("cnt"))
            .where(WriteoffDraft.client_id == client_id, WriteoffDraft.created_at >= since)
            .group_by(WriteoffDraft.status)
        )
        counts = {row.status: row.cnt for row in counts_result}

        # confirmed write-offs per staff member
        staff_result = await self.session.execute(
            select(WriteoffDraft.staff_name, func.count().label("cnt"))
            .where(
                WriteoffDraft.client_id == client_id,
                WriteoffDraft.created_at >= since,
                WriteoffDraft.status == DraftStatus.confirmed,
            )
            .group_by(WriteoffDraft.staff_name)
            .order_by(func.count().desc())
        )
        by_staff = [(row.staff_name, row.cnt) for row in staff_result]

        # confirmed write-offs per reason
        reason_result = await self.session.execute(
            select(WriteoffDraft.reason_name, func.count().label("cnt"))
            .where(
                WriteoffDraft.client_id == client_id,
                WriteoffDraft.created_at >= since,
                WriteoffDraft.status == DraftStatus.confirmed,
            )
            .group_by(WriteoffDraft.reason_name)
            .order_by(func.count().desc())
        )
        by_reason = [(row.reason_name or "Без причини", row.cnt) for row in reason_result]

        return {
            "confirmed": counts.get(DraftStatus.confirmed, 0),
            "rejected": counts.get(DraftStatus.rejected, 0),
            "pending": counts.get(DraftStatus.pending, 0),
            "by_staff": by_staff,
            "by_reason": by_reason,
        }

    async def get_unreminded_pending(self, threshold: datetime) -> list[WriteoffDraft]:
        """Pending drafts older than threshold that have not yet been reminded about."""
        result = await self.session.execute(
            select(WriteoffDraft)
            .where(
                WriteoffDraft.status == DraftStatus.pending,
                WriteoffDraft.created_at <= threshold,
                WriteoffDraft.reminded_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def mark_reminded(self, draft: WriteoffDraft) -> None:
        draft.reminded_at = datetime.utcnow()
        await self.session.flush()

    async def get_recent_logs(
        self, client_id: int, limit: int = 5, offset: int = 0
    ) -> list[WriteoffDraft]:
        result = await self.session.execute(
            select(WriteoffDraft)
            .options(selectinload(WriteoffDraft.items), selectinload(WriteoffDraft.log))
            .where(
                WriteoffDraft.client_id == client_id,
                WriteoffDraft.status == DraftStatus.confirmed,
            )
            .order_by(WriteoffDraft.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_confirmed(self, client_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).where(
                WriteoffDraft.client_id == client_id,
                WriteoffDraft.status == DraftStatus.confirmed,
            )
        )
        return result.scalar_one()

    async def cancel(self, draft: WriteoffDraft) -> None:
        draft.status = DraftStatus.cancelled
        await self.session.flush()

    async def get_staff_history(
        self, staff_tg_id: int, limit: int = 5, offset: int = 0
    ) -> list[WriteoffDraft]:
        result = await self.session.execute(
            select(WriteoffDraft)
            .options(selectinload(WriteoffDraft.items))
            .where(WriteoffDraft.staff_tg_id == staff_tg_id)
            .order_by(WriteoffDraft.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_staff_history(self, staff_tg_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).where(WriteoffDraft.staff_tg_id == staff_tg_id)
        )
        return result.scalar_one()

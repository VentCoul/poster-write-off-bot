from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WriteoffReason


class ReasonRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_client(self, client_id: int) -> list[WriteoffReason]:
        result = await self.session.execute(
            select(WriteoffReason).where(WriteoffReason.client_id == client_id)
        )
        return list(result.scalars().all())

    async def get_by_name(self, client_id: int, name: str) -> WriteoffReason | None:
        result = await self.session.execute(
            select(WriteoffReason).where(
                WriteoffReason.client_id == client_id,
                WriteoffReason.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def add(self, client_id: int, name: str, poster_reason_id: int) -> WriteoffReason:
        # Upsert by name
        existing = await self.get_by_name(client_id, name)
        if existing:
            existing.poster_reason_id = poster_reason_id
            await self.session.flush()
            return existing
        reason = WriteoffReason(client_id=client_id, name=name, poster_reason_id=poster_reason_id)
        self.session.add(reason)
        await self.session.flush()
        return reason

    async def remove(self, client_id: int, name: str) -> bool:
        reason = await self.get_by_name(client_id, name)
        if not reason:
            return False
        await self.session.delete(reason)
        await self.session.flush()
        return True

    async def remove_by_id(self, client_id: int, reason_id: int) -> bool:
        result = await self.session.execute(
            select(WriteoffReason).where(
                WriteoffReason.id == reason_id,
                WriteoffReason.client_id == client_id,
            )
        )
        reason = result.scalar_one_or_none()
        if not reason:
            return False
        await self.session.delete(reason)
        await self.session.flush()
        return True

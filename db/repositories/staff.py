import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Staff


class StaffRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_tg_id(self, tg_id: int) -> Staff | None:
        result = await self.session.execute(
            select(Staff).where(Staff.tg_id == tg_id, Staff.is_active == True)
        )
        return result.scalar_one_or_none()

    async def get_by_invite_token(self, token: str) -> Staff | None:
        result = await self.session.execute(
            select(Staff).where(Staff.invite_token == token)
        )
        return result.scalar_one_or_none()

    async def list_by_client(self, client_id: int) -> list[Staff]:
        result = await self.session.execute(
            select(Staff).where(Staff.client_id == client_id, Staff.is_active == True)
        )
        return list(result.scalars().all())

    async def create(self, client_id: int, name: str) -> Staff:
        token = secrets.token_urlsafe(16)
        staff = Staff(client_id=client_id, name=name, invite_token=token)
        self.session.add(staff)
        await self.session.flush()
        return staff

    async def activate(self, staff: Staff, tg_id: int) -> Staff:
        staff.tg_id = tg_id
        staff.is_active = True
        await self.session.flush()
        return staff

    async def delete(self, staff: Staff) -> None:
        await self.session.delete(staff)
        await self.session.flush()

    async def deactivate(self, staff: Staff) -> None:
        staff.is_active = False
        await self.session.flush()

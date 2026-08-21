from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Admin, Client, PendingOAuth, Staff, WriteoffDraft


class ClientRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_account(self, account_name: str) -> Client | None:
        result = await self.session.execute(
            select(Client).where(Client.account_name == account_name, Client.is_active == True)
        )
        return result.scalar_one_or_none()

    async def get_active(self) -> list[Client]:
        """All connected accounts — used by the background catalog refresh."""
        result = await self.session.execute(
            select(Client).where(Client.is_active == True)
        )
        return list(result.scalars().all())

    async def get_by_admin_tg_id(self, tg_id: int) -> Client | None:
        """Returns the most recently connected client for this admin.

        An admin can end up linked to more than one client (e.g. reconnecting
        without /disconnect first, or genuinely managing multiple businesses),
        so this picks one deterministically instead of raising on multiple rows.
        """
        result = await self.session.execute(
            select(Client)
            .join(Admin, Admin.client_id == Client.id)
            .where(Admin.tg_id == tg_id, Client.is_active == True)
            .order_by(Admin.created_at.desc())
        )
        return result.scalars().first()

    async def upsert(self, account_name: str, access_token: str) -> Client:
        client = await self.get_by_account(account_name)
        if client:
            client.access_token = access_token
        else:
            client = Client(account_name=account_name, access_token=access_token)
            self.session.add(client)
        await self.session.flush()
        return client

    async def add_admin(self, tg_id: int, client_id: int) -> Admin:
        # Scoped to THIS specific client — a bare `tg_id` check would silently
        # no-op whenever the admin already administers a different client.
        existing = await self.session.execute(
            select(Admin).where(Admin.tg_id == tg_id, Admin.client_id == client_id)
        )
        admin = existing.scalar_one_or_none()
        if not admin:
            admin = Admin(tg_id=tg_id, client_id=client_id)
            self.session.add(admin)
            await self.session.flush()
        return admin

    async def remove_admin(self, tg_id: int) -> None:
        """Unlink an admin from their Poster account (client + its data stay intact,
        so reconnecting the same account or another admin managing it is unaffected)."""
        rows = (await self.session.execute(
            select(Admin).where(Admin.tg_id == tg_id)
        )).scalars().all()
        for row in rows:
            await self.session.delete(row)
        await self.session.flush()

    async def save_pending_oauth(self, token: str, tg_id: int) -> None:
        self.session.add(PendingOAuth(token=token, tg_id=tg_id))
        await self.session.flush()

    async def pop_pending_oauth(self, token: str) -> int | None:
        result = await self.session.execute(
            select(PendingOAuth).where(PendingOAuth.token == token)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        tg_id = row.tg_id
        await self.session.delete(row)
        await self.session.flush()
        return tg_id

    async def get_all_clients_overview(self) -> list[dict]:
        """Full overview of all clients for super-admin panel."""
        clients = list((await self.session.execute(
            select(Client).order_by(Client.created_at)
        )).scalars().all())

        result = []
        for client in clients:
            admins = list((await self.session.execute(
                select(Admin).where(Admin.client_id == client.id)
            )).scalars().all())

            staff_count = await self.session.scalar(
                select(func.count()).where(Staff.client_id == client.id)
            ) or 0

            confirmed = await self.session.scalar(
                select(func.count()).where(
                    WriteoffDraft.client_id == client.id,
                    WriteoffDraft.status == "confirmed",
                )
            ) or 0

            pending = await self.session.scalar(
                select(func.count()).where(
                    WriteoffDraft.client_id == client.id,
                    WriteoffDraft.status == "pending",
                )
            ) or 0

            result.append({
                "client": client,
                "admins": admins,
                "staff_count": staff_count,
                "confirmed_count": confirmed,
                "pending_count": pending,
            })
        return result

    async def get_admin_tg_ids(self, client_id: int) -> list[int]:
        result = await self.session.execute(
            select(Admin.tg_id).where(Admin.client_id == client_id)
        )
        return list(result.scalars().all())

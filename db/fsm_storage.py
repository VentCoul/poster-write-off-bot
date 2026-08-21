"""
SQLite-backed FSM storage for aiogram v3.
Persists state and data across bot restarts.
"""

import json
from typing import Any

import aiosqlite
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, db_path: str = "data/fsm.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS fsm_data (
                    key   TEXT PRIMARY KEY,
                    state TEXT,
                    data  TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            await self._db.commit()
        return self._db

    @staticmethod
    def _key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        db = await self._conn()
        k = self._key(key)
        state_str = state.state if hasattr(state, "state") else state
        await db.execute(
            """
            INSERT INTO fsm_data (key, state, data) VALUES (?, ?, '{}')
            ON CONFLICT(key) DO UPDATE SET state = excluded.state
            """,
            (k, state_str),
        )
        await db.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        db = await self._conn()
        async with db.execute(
            "SELECT state FROM fsm_data WHERE key = ?", (self._key(key),)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        db = await self._conn()
        k = self._key(key)
        await db.execute(
            """
            INSERT INTO fsm_data (key, state, data) VALUES (?, NULL, ?)
            ON CONFLICT(key) DO UPDATE SET data = excluded.data
            """,
            (k, json.dumps(data, ensure_ascii=False)),
        )
        await db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        db = await self._conn()
        async with db.execute(
            "SELECT data FROM fsm_data WHERE key = ?", (self._key(key),)
        ) as cur:
            row = await cur.fetchone()
        if not row or not row[0]:
            return {}
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return {}

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

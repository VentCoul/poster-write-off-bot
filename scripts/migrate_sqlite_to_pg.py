import asyncio
import os
import sys

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def migrate():
    sqlite_url = "sqlite+aiosqlite:///./data/writeoff.db"
    pg_url = os.getenv("DB_URL", "postgresql+asyncpg://poster_bot:secret_password@localhost:5432/writeoff")

    sqlite_engine = create_async_engine(sqlite_url)
    pg_engine = create_async_engine(pg_url)

    tables = [
        "alembic_version",
        "clients", 
        "staff", 
        "admins", 
        "writeoff_drafts",
        "writeoff_items", 
        "writeoff_reasons", 
        "pending_oauth", 
        "writeoff_logs"
    ]

    print(f"Starting migration from {sqlite_url} to {pg_url}")
    
    async with sqlite_engine.connect() as sqlite_conn:
        async with pg_engine.begin() as pg_conn:
            # We first truncate the tables in postgres if they have data (e.g. alembic_version)
            for table in tables:
                try:
                    await pg_conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
                except Exception:
                    pass

            for table in tables:
                print(f"Migrating {table}...")
                try:
                    result = await sqlite_conn.execute(text(f"SELECT * FROM {table}"))
                    rows = result.mappings().all()
                    if not rows:
                        print(f"No data in {table}")
                        continue
                    
                    columns = ", ".join(rows[0].keys())
                    placeholders = ", ".join([f":{k}" for k in rows[0].keys()])
                    insert_query = text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})")
                    
                    for row in rows:
                        await pg_conn.execute(insert_query, dict(row))
                    
                    print(f"Migrated {len(rows)} rows for {table}")

                    # Reset sequence for PostgreSQL if the table has an auto-incrementing ID
                    if "id" in rows[0].keys():
                        await pg_conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1)) FROM {table}"))
                except Exception as e:
                    print(f"Failed to migrate table {table}: {e}")

    print("Migration finished.")

if __name__ == "__main__":
    asyncio.run(migrate())

"""Read-only metadata check inside the existing isolated dev API container."""
import asyncio
import json

from sqlalchemy import text
from src.core.database import close_db, get_db_context


async def main():
    try:
        async with get_db_context() as db:
            revision = await db.scalar(text('SELECT version_num FROM alembic_version'))
            columns = list((await db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='execution_workers' ORDER BY column_name"
            ))).scalars())
        required = ['active_handler_counts', 'capacity_scope', 'capacity_resource_uid', 'capacity_resource_deleted_at']
        print(json.dumps({'schema_revision': revision, 'execution_worker_columns': columns,
                          'missing_canonical_columns': [name for name in required if name not in columns]}))
    finally:
        await close_db()


if __name__ == '__main__':
    asyncio.run(main())

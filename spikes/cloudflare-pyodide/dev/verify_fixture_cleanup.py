"""Read-only cleanup evidence in the owned disposable development API."""
import asyncio
import json
from sqlalchemy import func, select
from src.core.database import init_db, close_db, get_db_context
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.models.orm.config import Config


async def main():
    await init_db()
    try:
        async with get_db_context() as session:
            counts = {}
            for label, model, column, prefix in [('tables', Table, Table.name, 'pyodide-qualification-'),
                                                  ('workflows', Workflow, Workflow.path, 'workflows/pyodide_qualification_'),
                                                  ('configs', Config, Config.key, 'pyodide-qualification-')]:
                counts[label] = (await session.execute(select(func.count()).select_from(model).where(column.startswith(prefix)))).scalar_one()
            print(json.dumps({'owned_fixture_rows_remaining': counts}))
            if any(counts.values()):
                raise RuntimeError('Qualification fixture cleanup incomplete')
    finally:
        await close_db()


asyncio.run(main())

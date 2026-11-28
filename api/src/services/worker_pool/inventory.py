"""Read-only operator inventory of canonical execution workers."""

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import (
    WorkerInstance,
    WorkerInstanceCursor,
    WorkerInstanceListQuery,
    WorkerInstancePage,
)
from src.models.orm.execution_workers import ExecutionWorker


async def list_worker_instances(
    db: AsyncSession, query: WorkerInstanceListQuery
) -> WorkerInstancePage:
    worker = ExecutionWorker
    # Select only fields from the public projection, including for terminal workers.
    stmt = select(
        worker.id,
        worker.form,
        worker.state,
        worker.capacity_scope,
        worker.capacity_resource_uid,
        worker.version,
        worker.created_at,
        worker.ended_at,
        worker.end_reason,
    )
    if query.form:
        stmt = stmt.where(worker.form == query.form)
    if query.before_created_at is not None and query.before_id is not None:
        stmt = stmt.where(
            tuple_(worker.created_at, worker.id)
            < tuple_(query.before_created_at, query.before_id)
        )
    rows = (
        await db.execute(
            stmt.order_by(worker.created_at.desc(), worker.id.desc()).limit(query.limit + 1)
        )
    ).all()
    items = [WorkerInstance.model_validate(row) for row in rows[:query.limit]]
    return WorkerInstancePage(
        items=items,
        next_cursor=WorkerInstanceCursor(
            before_created_at=items[-1].created_at, before_id=items[-1].id
        )
        if len(rows) > query.limit
        else None,
    )

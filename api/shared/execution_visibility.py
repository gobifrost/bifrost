"""Which workflow executions a caller reads as their own.

A caller's own executions are the ones they acted in (``executed_by``) and the
root runs they started (``started_by_user_id`` with the row as its own root).
A root started with Run As acts as someone else, so its initiator reaches it
through the second clause; its children keep the root's
``root_execution_id`` and are not the initiator's. Reading anything else takes
the ``executions.read.all`` power, decided by each caller.

One rule in three shapes: a SQL filter, a row check, and a Redis pending
record check (an execution the worker has not persisted yet).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_

from src.models.orm.executions import Execution


def own_executions(user_id: UUID) -> ColumnElement[bool]:
    """Filter a query on ``Execution`` to the caller's own executions."""
    return or_(
        Execution.executed_by == user_id,
        and_(
            Execution.started_by_user_id == user_id,
            Execution.root_execution_id == Execution.id,
        ),
    )


def is_own_execution(
    user_id: UUID | str,
    *,
    execution_id: UUID | str,
    executed_by: UUID | str | None,
    started_by_user_id: UUID | str | None,
    root_execution_id: UUID | str | None,
) -> bool:
    """One execution, judged as ``own_executions`` filters a query."""
    me = str(user_id)
    if executed_by is not None and str(executed_by) == me:
        return True
    return (
        started_by_user_id is not None
        and str(started_by_user_id) == me
        and root_execution_id is not None
        and str(root_execution_id) == str(execution_id)
    )


def is_own_pending_execution(user_id: UUID | str, execution_id: UUID | str, pending: Mapping[str, Any]) -> bool:
    """A Redis pending record, judged as ``is_own_execution``.

    Records written before lineage was recorded carry none; only their
    ``user_id`` decides.
    """
    lineage = pending.get("lineage") or {}
    return is_own_execution(
        user_id,
        execution_id=execution_id,
        executed_by=pending.get("user_id"),
        started_by_user_id=lineage.get("started_by_user_id"),
        root_execution_id=lineage.get("root_execution_id"),
    )

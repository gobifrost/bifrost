"""Shared durable admission policy for physical execution workers.

The dispatcher stores only resolved lanes. ``auto`` is a workflow policy, not
a queue value, so handback and recovery never reinterpret it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ExecutionLane = Literal["normal", "fast_path"]

NORMAL: ExecutionLane = "normal"
FAST_PATH: ExecutionLane = "fast_path"


class FastPathUnavailable(ValueError):
    """A requested lane has no configured admission capacity."""


def resolve_execution_lane(
    workflow_lane: str | None,
    *,
    auto_priority_app_workflows: bool = False,
    trusted_app_context: bool = False,
) -> ExecutionLane:
    """Resolve a workflow policy once at durable acceptance.

    ``trusted_app_context`` must come from the authorization gate; a request
    body ``app_id`` is deliberately insufficient.
    """
    if workflow_lane == FAST_PATH:
        return FAST_PATH
    if workflow_lane == "auto" and auto_priority_app_workflows and trusted_app_context:
        return FAST_PATH
    return NORMAL


async def has_static_fast_path_capacity(db: AsyncSession) -> bool:
    """An admitted or starting fixed worker can provide this lane."""
    from sqlalchemy import exists, select

    from src.models.orm.execution_workers import ExecutionWorker

    return bool(await db.scalar(select(exists().where(
        ExecutionWorker.form == "static",
        ExecutionWorker.execution_lane == FAST_PATH,
        ExecutionWorker.state.in_(("starting", "ready")),
        ExecutionWorker.supported_handlers.contains(["workflow.execute"]),
    ))))


async def resolve_admitted_execution_lane(
    db: AsyncSession, workflow_lane: str | None, *, trusted_app_context: bool = False,
) -> ExecutionLane:
    """Resolve new work while fencing removal of its configured capacity.

    Concurrent Fast Path acceptances share the policy lock; disabling the
    reservation takes it exclusively. Normal runs never wait on this lock.
    Existing scheduled/handed-back runs retain their original snapshot.
    """
    from src.services.kubernetes_execution import load_kubernetes_policy, lock_kubernetes_policy

    if workflow_lane != FAST_PATH:
        if workflow_lane != "auto" or not trusted_app_context:
            return NORMAL
        if not (await load_kubernetes_policy(db)).auto_priority_app_workflows:
            return NORMAL
    await lock_kubernetes_policy(db, shared=True)
    policy = await load_kubernetes_policy(db)
    lane = resolve_execution_lane(
        workflow_lane,
        auto_priority_app_workflows=policy.auto_priority_app_workflows,
        trusted_app_context=trusted_app_context,
    )
    if lane == FAST_PATH and not policy.reserved_fast_path_workers:
        if not await has_static_fast_path_capacity(db):
            raise FastPathUnavailable(
                "Fast Path has no dedicated workers. Configure a reservation or a fixed Fast Path worker, "
                "or set this workflow's priority to Normal."
            )
    return lane



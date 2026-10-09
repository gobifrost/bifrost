"""Judge a request's report-only access checks and write the ones that matter.

Called by the request middleware after the response, with its own session.
Every note is judged against the run's user by the decision core
(``src.services.authorization.explain``); a person's own request is judged
against their own roles. Written to the audit log:

- ``access.check`` with outcome ``failure``: whenever the model would block
  what today allows.
- ``access.check`` with outcome ``success``: when a run crosses into another
  organization, acts as another user (``run_as``), or is a policy or secret
  decision. A person's request writes failures only.
- ``access.check_gap``: when the request carries no run user, the run user no
  longer exists, or a check could not be judged.

Each decision (kind, operation, target, run user, outcome and subject: the
table, secret, path, user or object it concerns) is written once per run, or
once per person acting directly (Redis marks it for a day). A note already
judged for that run or person that day is not judged again, so a repeated
launch costs one judgement a day. Without Redis nothing is written. Nothing
here raises into the request.

A note on an object the request did not load (``Owned``) is resolved here:
judged in the object's organization, and dropped when its actor owns it.
"""

from __future__ import annotations

import hashlib
import logging
from functools import cache
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.access_checks import Collector, Note
from src.core.cache.redis_client import get_shared_redis
from src.core.database import get_db_context
from src.models.contracts.access_list import AccessEntry
from src.models.orm import AgentRun, Artifact, Execution
from src.repositories.audit_logs import AuditLogRepository
from src.services.authorization.explain import (
    ALL_ORGS,
    Powers,
    RunUser,
    Trace,
    check_entry,
    check_permission,
    check_policy,
    check_run_as,
    check_secret,
    check_target,
    load_powers,
    load_run_user,
)

logger = logging.getLogger(__name__)

_DEDUPE_SECONDS = 86_400
_ALWAYS_WRITTEN = frozenset({"run_as", "policy", "secret"})
# Facts that vary between calls of the same decision (a query's row counts).
_PER_CALL_FACTS = frozenset({"hidden", "returned"})


@cache
def _entries_by_route() -> dict[Any, AccessEntry]:
    from src.services.access_list import ACCESS_LIST

    return {entry.key: entry for entry in ACCESS_LIST if entry.mcp_tool is None}


def _plain(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _target_org(target: Any) -> UUID | None:
    return target if isinstance(target, UUID) else None


_OWNED_FACTS = ("owned", "object_id", "actor")


async def _resolve_owned(db: AsyncSession, note: Note) -> Note | None:
    """``note`` in its object's organization, or None when the object is gone
    or its actor owns it (nothing elevated happened)."""
    facts = note.facts
    object_id, actor = UUID(facts["object_id"]), UUID(facts["actor"])
    if facts["owned"] == "execution":
        execution = (
            await db.execute(select(Execution.organization_id, Execution.executed_by).where(Execution.id == object_id))
        ).one_or_none()
        if execution is None or execution.executed_by == actor:
            return None
        organization_id = execution.organization_id
    elif facts["owned"] == "agent_run":
        run = (
            await db.execute(select(AgentRun.org_id, AgentRun.caller_user_id).where(AgentRun.id == object_id))
        ).one_or_none()
        if run is None or run.caller_user_id == str(actor):
            return None
        organization_id = run.org_id
    else:
        # A workspace is someone else's when another user's artifact is in it.
        other = (
            await db.execute(
                select(Artifact.organization_id)
                .where(Artifact.workspace_id == object_id, Artifact.created_by_user_id != actor)
                .limit(1)
            )
        ).one_or_none()
        if other is None:
            return None
        organization_id = other.organization_id
    return Note(note.kind, organization_id, {key: value for key, value in facts.items() if key not in _OWNED_FACTS})


def judge(run_user: RunUser, powers: Powers | None, note: Note, entry: AccessEntry | None) -> Trace:
    """Judge one note; ``powers`` is None for a person acting directly."""
    facts = note.facts
    if note.kind == "permission":
        return check_permission(run_user, powers, facts["permission"], note.target)
    if note.kind in ("scope_switch", "child_run"):
        return check_target(run_user, powers, note.target, entry)
    assert powers is not None, "only a run's request notes the other kinds"
    if note.kind == "run_as":
        return check_run_as(run_user, powers, UUID(str(facts["run_as_user_id"])))
    if note.kind == "entry":
        return check_entry(run_user, allowed=facts["allowed"], subject=facts["subject"])
    if note.kind == "policy":
        return check_policy(run_user, powers, today=facts["today"], model=facts["model"], missing=facts["missing"])
    return check_secret(run_user, powers, _target_org(note.target), kind=facts["kind"], name=facts["name"])


def _written(note: Note, trace: Trace, run_user: RunUser, *, direct: bool) -> bool:
    if direct:
        # A person's own request records would-deny decisions only.
        return trace.outcome == "failure"
    if trace.outcome == "failure":
        # Only what today allows: a denial today already stops the request.
        return bool(note.facts.get("today", True))
    if note.kind in _ALWAYS_WRITTEN:
        return True
    if note.kind in ("scope_switch", "child_run"):
        return note.target == ALL_ORGS or (isinstance(note.target, UUID) and note.target != run_user.home)
    return False


class _Writer:
    def __init__(self, db: AsyncSession, redis: Any, collector: Collector, operation: str) -> None:
        self.repo = AuditLogRepository(db)
        self.redis = redis
        self.collector = collector
        self.operation = operation
        self.run_key = (
            f"person:{collector.run_user_id}"
            if collector.direct
            else str(collector.execution_id or f"run-user:{collector.run_user_id}")
        )

    async def _first_time(self, *parts: Any) -> bool:
        digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()[:16]
        return bool(
            await self.redis.set(f"bifrost:access_check:{self.run_key}:{digest}", "1", nx=True, ex=_DEDUPE_SECONDS)
        )

    def _decision(self, note: Note) -> list[Any]:
        target = "*" if note.target == ALL_ORGS else _plain(note.target)
        subject = sorted((key, str(value)) for key, value in note.facts.items() if key not in _PER_CALL_FACTS)
        return [note.kind, self.operation, target, subject]

    async def first_judgement(self, note: Note) -> bool:
        """Whether ``note`` has not been judged for this run or person today."""
        return await self._first_time("judged", *self._decision(note))

    async def gap(self, kind: str, reason: str, user_id: UUID | None = None) -> None:
        if not await self._first_time("gap", kind, self.operation, reason):
            return
        await self.repo.create(
            action="access.check_gap",
            user_id=user_id,
            organization_id=None,
            resource_type=kind,
            resource_id=None,
            outcome="failure",
            source="http",
            ip_address=None,
            user_agent=None,
            details={"reason": reason, "operation": self.operation},
            execution_id=self.collector.execution_id,
            operation_id=self.operation,
        )

    async def check(self, note: Note, trace: Trace, run_user: RunUser) -> None:
        kind, operation, target, subject = self._decision(note)
        if not await self._first_time(kind, operation, target, run_user.user_id, trace.outcome, subject):
            return
        today = note.facts.get("today", True)
        await self.repo.create(
            action="access.check",
            user_id=run_user.user_id,
            organization_id=_target_org(note.target),
            resource_type=note.kind,
            resource_id=None,
            outcome=trace.outcome,
            source="http",
            ip_address=None,
            user_agent=None,
            details={
                "enforced": False,
                "direct": self.collector.direct,
                "workflow_id": _plain(self.collector.workflow_id),
                "trace": trace.as_dict(),
                "inputs": {
                    "operation": self.operation,
                    "target": target,
                    **{key: _plain(value) for key, value in note.facts.items()},
                },
                "today": "allowed" if today else "denied",
                "proposed_attribution": {
                    "actor_user_id": str(run_user.user_id),
                    "organization_id": _plain(run_user.home),
                },
            },
            execution_id=self.collector.execution_id,
            operation_id=self.operation,
        )


async def _write(db: AsyncSession, collector: Collector, *, operation: str, route: tuple[str, str] | None) -> None:
    writer = _Writer(db, await get_shared_redis(), collector, operation)
    notes = [note for note in collector.notes if "gap" in note.facts or await writer.first_judgement(note)]
    if not notes:
        return
    kinds = sorted({note.kind for note in notes})
    if collector.run_user_id is None:
        for kind in kinds:
            await writer.gap(kind, "missing_lineage")
        return
    run_user = await load_run_user(db, collector.run_user_id)
    if run_user is None:
        for kind in kinds:
            await writer.gap(kind, "run_user_missing")
        return
    powers = None if collector.direct else await load_powers(db, collector.workflow_id)
    entry = None if route is None else _entries_by_route().get(route)
    for noted in notes:
        if "gap" in noted.facts:
            await writer.gap(noted.kind, noted.facts["gap"], run_user.user_id)
            continue
        try:
            note = await _resolve_owned(db, noted) if "owned" in noted.facts else noted
            if note is None:
                continue
            trace = judge(run_user, powers, note, entry)
        except Exception as exc:
            logger.warning("access check could not be judged (kind=%s): %s", noted.kind, type(exc).__name__)
            await writer.gap(noted.kind, f"observer_error:{type(exc).__name__}", run_user.user_id)
            continue
        if _written(note, trace, run_user, direct=collector.direct):
            await writer.check(note, trace, run_user)


async def flush(
    db: AsyncSession, collector: Collector, *, operation: str, route: tuple[str, str] | None
) -> None:
    """Judge and write ``collector``'s notes; never raises.

    ``route`` is the request's (method, path template), which names its
    access-list entry; None outside a request.
    """
    collector.closed = True
    if not collector.notes:
        return
    try:
        await _write(db, collector, operation=operation, route=route)
    except Exception as exc:
        logger.warning("access checks not written (operation=%s): %s", operation, type(exc).__name__)


async def flush_detached(collector: Collector, *, operation: str, route: tuple[str, str] | None) -> None:
    """``flush`` in a session of its own, after a response or between a
    connection's messages; never raises. Nothing noted (most requests) opens
    no session."""
    if not collector.notes:
        collector.closed = True
        return
    try:
        async with get_db_context() as db:
            await flush(db, collector, operation=operation, route=route)
    except Exception as exc:
        logger.warning("access checks not written (operation=%s): %s", operation, type(exc).__name__)

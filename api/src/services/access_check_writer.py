"""Persist Access Checks after a response without changing its outcome.

Enforced notes retain the judgment made during the request. Other notes are
judged against current original-user roles and the exact workflow's powers.
Denials, scope crossings, Run As, policy, and secret decisions are retained;
permission successes are not sampled. Missing facts produce coverage gaps.

Each decision is deduplicated per run or direct user for a day. Enforced Run As
is recorded on every call. Redis claims prevent concurrent duplicate writes.
Unloaded objects are resolved here; objects owned by the requester need no
additional ownership permission check. Writer failures never raise into the
request.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from functools import cache
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.access_checks import Collector, Note
from shared.access_trace_storage import compact_access_check_details
from src.core.cache.redis_client import get_shared_redis
from src.core.database import get_db_context
from src.models.contracts.access_list import AccessEntry
from src.models.orm import Agent, AgentRun, Artifact, Execution
from src.services.agent_write_policy import may_use_tools
from src.repositories.audit_logs import AuditLogRepository
from src.services.authorization.explain import (
    ALL_ORGS,
    Powers,
    RunAsTarget,
    RunUser,
    Trace,
    check_entry,
    check_permission,
    check_policy,
    check_run_as,
    check_run_as_outside_reach,
    check_secret,
    check_target,
    load_powers,
    load_run_as_target,
    load_run_user,
)

logger = logging.getLogger(__name__)

_DEDUPE_SECONDS = 86_400
# A judgement in progress holds its marker this long at most; a judgement
# that completes keeps it for the day.
_IN_FLIGHT_SECONDS = 300
_IMPERSONATE_PERMISSION = "users.impersonate"
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


_OWNED_FACTS = ("owned", "object_id", "actor", "tools")
# What resolving an unloaded object found besides a note to judge: the actor
# owns it (a completed decision), or it does not exist (yet).
Unresolved = Literal["own", "absent"]


async def _resolve_owned(db: AsyncSession, note: Note) -> Note | Unresolved:
    """``note`` in its object's organization, or why there is nothing to judge."""
    facts = note.facts
    object_id, actor = UUID(facts["object_id"]), UUID(facts["actor"])
    if facts["owned"] == "execution":
        execution = (
            await db.execute(select(Execution.organization_id, Execution.executed_by).where(Execution.id == object_id))
        ).one_or_none()
        if execution is None:
            return "absent"
        if execution.executed_by == actor:
            return "own"
        organization_id = execution.organization_id
    elif facts["owned"] == "agent_run":
        run = (
            await db.execute(select(AgentRun.org_id, AgentRun.caller_user_id).where(AgentRun.id == object_id))
        ).one_or_none()
        if run is None:
            return "absent"
        if run.caller_user_id == str(actor):
            return "own"
        organization_id = run.org_id
    elif facts["owned"] == "agent_tools":
        agent = (await db.execute(select(Agent.organization_id).where(Agent.id == object_id))).one_or_none()
        if agent is None:
            return "absent"
        if await may_use_tools(db, actor, list(facts["tools"])):
            return "own"
        organization_id = agent.organization_id
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
            return "own"
        organization_id = other.organization_id
    return Note(note.kind, organization_id, {key: value for key, value in facts.items() if key not in _OWNED_FACTS})


def names_a_user_to_load(note: Note) -> bool:
    """Whether judging ``note`` needs the user its ``run_as`` names loaded:
    not when the person held Impersonate Users nowhere and was refused
    before any user was looked up (``held_nowhere``), which is judged as
    the permission at the note's organization, theirs."""
    return note.kind == "run_as" and not (note.facts.get("held_nowhere") or note.facts.get("outside_reach"))


def judge(
    run_user: RunUser, powers: Powers | None, note: Note, entry: AccessEntry | None, run_as: RunAsTarget | None
) -> Trace:
    """Judge one note; ``powers`` is None for a person acting directly.
    ``run_as`` is the user a ``run_as`` note names, loaded by the caller
    when ``names_a_user_to_load``."""
    facts = note.facts
    if note.kind == "permission":
        return check_permission(run_user, powers, facts["permission"], note.target)
    if note.kind in ("scope_switch", "child_run"):
        return check_target(run_user, powers, note.target, entry)
    if note.kind == "run_as":
        if facts.get("held_nowhere"):
            return check_permission(run_user, powers, _IMPERSONATE_PERMISSION, note.target)
        if facts.get("outside_reach"):
            return check_run_as_outside_reach(run_user)
        assert run_as is not None, "the caller loads the user a run_as names"
        return check_run_as(run_user, powers, run_as)
    if note.kind == "entry":
        return check_entry(run_user, allowed=facts["allowed"], subject=facts["subject"])
    if note.kind == "policy":
        return check_policy(run_user, powers, today=facts["today"], model=facts["model"], missing=facts["missing"])
    return check_secret(run_user, powers, _target_org(note.target), kind=facts["kind"], name=facts["name"])


def _written(
    note: Note, trace: Trace, run_user: RunUser, *, direct: bool
) -> bool:
    if direct:
        return trace.outcome == "failure" or note.kind in _ALWAYS_WRITTEN
    if trace.outcome == "failure":
        return _enforced(note) or bool(note.facts.get("today", True))
    if note.kind in _ALWAYS_WRITTEN:
        return True
    if note.kind in ("scope_switch", "child_run"):
        return note.target == ALL_ORGS or (isinstance(note.target, UUID) and note.target != run_user.home)
    return False


# A marker's value is the token of the judgement or write holding it, or
# _DONE once that work is complete for the day.
_DONE = "done"

# Settle a claim only while it is still ours: a claim that expired and was
# taken by another judgement is left alone. ARGV: token, then the new TTL in
# seconds (keep as done) or nothing (release).
_SETTLE_SCRIPT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then
    return 0
end
if ARGV[2] then
    return redis.call('set', KEYS[1], ARGV[3], 'EX', ARGV[2])
end
return redis.call('del', KEYS[1])
"""


@dataclass(frozen=True)
class Claim:
    """A marker this judgement holds: its key and the token proving it."""

    key: str
    token: str


class _Writer:
    def __init__(self, db: AsyncSession, redis: Any, collector: Collector, operation: str) -> None:
        self.db = db
        self.repo = AuditLogRepository(db)
        self.redis = redis
        self.collector = collector
        self.operation = operation
        self.stored_run_user_id = collector.run_user_id
        self.run_key = (
            f"agent-run:{collector.agent_run_id}"
            if collector.agent_run_id
            else f"person:{collector.run_user_id}"
            if collector.direct and not collector.is_run
            else str(collector.execution_id or f"run-user:{collector.run_user_id}")
        )

    def _key(self, *parts: Any) -> str:
        provenance = (
            ()
            if self.collector.impersonated_by_user_id is None
            and self.collector.impersonation_session_id is None
            else (
                "impersonation",
                self.collector.impersonated_by_user_id,
                self.collector.impersonation_session_id,
            )
        )
        digest = hashlib.sha256(
            "|".join(
                str(part)
                for part in (*provenance, *parts)
            ).encode()
        ).hexdigest()[:16]
        return f"bifrost:access_check:{self.run_key}:{digest}"

    async def _claim(self, key: str) -> Claim | None:
        """Hold ``key`` briefly, unless someone holds it or it was kept for the day."""
        token = uuid4().hex
        return Claim(key, token) if await self.redis.set(key, token, nx=True, ex=_IN_FLIGHT_SECONDS) else None

    async def settle(self, claim: Claim, *, done: bool) -> None:
        """Keep a claim for the day when its work is done, release it when
        not (a later request does the work); only while it is still ours."""
        if done:
            await self.redis.eval(_SETTLE_SCRIPT, 1, claim.key, claim.token, _DEDUPE_SECONDS, _DONE)
        else:
            await self.redis.eval(_SETTLE_SCRIPT, 1, claim.key, claim.token)

    async def _insert(self, **row: Any) -> None:
        await self.repo.create(**row)
        await self.db.commit()

    async def _write_once(self, parts: list[Any], **row: Any) -> bool:
        """Insert the audit ``row`` unless it was written for ``parts`` today;
        whether the row exists. The day's marker is kept only once the row is
        committed, so a row another writer is still inserting does not exist
        yet: that writer may fail, and then a later attempt must write it."""
        key = self._key(*parts)
        claim = await self._claim(key)
        if claim is None:
            return await self.redis.get(key) == _DONE
        try:
            await self._insert(**row)
        except Exception:
            await self.settle(claim, done=False)
            raise
        await self.settle(claim, done=True)
        return True

    def _decision(self, note: Note) -> list[Any]:
        target = "*" if note.target == ALL_ORGS else _plain(note.target)
        subject = sorted((key, str(value)) for key, value in note.facts.items() if key not in _PER_CALL_FACTS)
        return [note.kind, self.operation, target, subject]

    async def reserve(self, note: Note) -> Claim | None:
        """Claim judging ``note`` unless it was judged for this run or person
        today, or is being judged now."""
        return await self._claim(self._key("judged", *self._decision(note)))

    async def gap(self, kind: str, reason: str, user_id: UUID | None = None) -> bool:
        return await self._write_once(["gap", kind, self.operation, reason], **self._gap_row(kind, reason, user_id))

    async def missing_run_as(self, note: Note, user_id: UUID | None) -> bool:
        """A gap for a ``run_as`` naming a user that does not exist, naming
        the requested user at the note's organization (the caller's); an
        enforced one is written on every call."""
        requested = str(note.facts["run_as_user_id"])
        row = self._gap_row(note.kind, "run_as_user_missing", user_id)
        row["organization_id"] = _target_org(note.target)
        row["details"]["run_as_user_id"] = requested
        if _enforced(note):
            await self._insert(**row)
            return True
        return await self._write_once(["gap", note.kind, self.operation, "run_as_user_missing", requested], **row)

    def _gap_row(self, kind: str, reason: str, user_id: UUID | None) -> dict[str, Any]:
        return dict(
            action="access.check_gap",
            user_id=user_id,
            organization_id=None,
            resource_type=kind,
            resource_id=None,
            outcome="failure",
            source="http",
            surface=self.collector.surface,
            ip_address=None,
            user_agent=None,
            details={
                "reason": reason,
                "operation": self.operation,
                **({"agent_run_id": str(self.collector.agent_run_id)} if self.collector.agent_run_id else {}),
            },
            execution_id=self.collector.execution_id,
            operation_id=self.operation,
            impersonated_by_user_id=self.collector.impersonated_by_user_id,
            impersonation_session_id=self.collector.impersonation_session_id,
        )

    async def check(self, note: Note, trace: Trace, run_user: RunUser) -> bool:
        kind, operation, target, subject = self._decision(note)
        enforced = _enforced(note)
        row = dict(
            action="access.check",
            user_id=self.stored_run_user_id,
            organization_id=_target_org(note.target),
            resource_type=note.kind,
            resource_id=None,
            outcome=trace.outcome,
            source="http",
            surface=self.collector.surface,
            ip_address=None,
            user_agent=None,
            details=compact_access_check_details(
                {
                    "enforced": enforced,
                    "direct": self.collector.direct,
                    **({"agent_run_id": str(self.collector.agent_run_id)} if self.collector.agent_run_id else {}),
                    "workflow_id": _plain(self.collector.workflow_id),
                    "trace": replace(trace, enforced=enforced).as_dict(),
                    "inputs": {
                        "operation": self.operation,
                        "target": target,
                        **{key: _plain(value) for key, value in note.facts.items()},
                    },
                }
            ),
            execution_id=self.collector.execution_id,
            operation_id=self.operation,
            impersonated_by_user_id=self.collector.impersonated_by_user_id,
            impersonation_session_id=self.collector.impersonation_session_id,
        )
        if _every_call(note):
            await self._insert(**row)
            return True
        return await self._write_once([kind, operation, target, run_user.user_id, trace.outcome, subject], **row)


def _every_call(note: Note) -> bool:
    return note.kind == "run_as" and _enforced(note)


def _enforced(note: Note) -> bool:
    """Whether the request enforced this decision."""
    return bool(note.facts.get("enforced", False))


async def _write(db: AsyncSession, collector: Collector, *, operation: str, route: tuple[str, str] | None) -> None:
    writer = _Writer(db, await get_shared_redis(), collector, operation)
    notes: list[tuple[Note, Claim | None]] = []
    for note in collector.notes:
        if "gap" in note.facts or _every_call(note):
            notes.append((note, None))
        elif (claim := await writer.reserve(note)) is not None:
            notes.append((note, claim))
    if not notes:
        return
    run_user = None if collector.run_user_id is None else await load_run_user(db, collector.run_user_id)
    writer.stored_run_user_id = None if run_user is None else run_user.user_id
    powers = None if collector.direct or collector.workflow_id is None else await load_powers(db, collector.workflow_id)
    entry = None if route is None else _entries_by_route().get(route)
    for noted, claim in notes:
        decided_user = run_user if noted.decision is None else noted.decision[0]
        judged = False
        try:
            if decided_user is None:
                reason = "missing_lineage" if collector.run_user_id is None else "run_user_missing"
                judged = await writer.gap(noted.kind, reason)
            elif "gap" in noted.facts:
                judged = await writer.gap(noted.kind, noted.facts["gap"], writer.stored_run_user_id)
            else:
                judged = await _judge_and_write(db, writer, decided_user, powers, noted, entry, direct=collector.direct)
        finally:
            if claim is not None:
                await writer.settle(claim, done=judged)


async def _judge_and_write(
    db: AsyncSession,
    writer: _Writer,
    run_user: RunUser,
    powers: Powers | None,
    noted: Note,
    entry: AccessEntry | None,
    *,
    direct: bool,
) -> bool:
    """Judge one note and write it if it matters; whether the judgement
    completed. An object that does not exist yet, a check that failed, or a
    row another writer is still inserting returns False, and a failed write
    raises, so a later request judges it again. A ``run_as`` naming a user
    that no longer exists is a gap."""
    try:
        resolved = await _resolve_owned(db, noted) if "owned" in noted.facts else noted
        if resolved == "own":
            return True
        if resolved == "absent":
            return False
        assert isinstance(resolved, Note)
        run_as = None
        if resolved.decision is None and names_a_user_to_load(resolved):
            run_as = await load_run_as_target(db, UUID(str(resolved.facts["run_as_user_id"])))
            if run_as is None:
                return await writer.missing_run_as(resolved, writer.stored_run_user_id)
        trace = judge(run_user, powers, resolved, entry, run_as) if resolved.decision is None else resolved.decision[1]
    except Exception as exc:
        logger.warning("access check could not be judged (kind=%s): %s", noted.kind, type(exc).__name__)
        await writer.gap(noted.kind, f"observer_error:{type(exc).__name__}", writer.stored_run_user_id)
        return False
    if _written(resolved, trace, run_user, direct=direct):
        return await writer.check(resolved, trace, run_user)
    return True


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

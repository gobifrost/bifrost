"""Collect authorization decisions for the Access Checks audit view.

Verified runtime credentials retain the original user and exact workflow.
Enforcement helpers capture their actual judgment before mutations change role
facts. Other notes retain policy, secret, scope, and object inputs for the
writer. Permission successes are not sampled. Run As is recorded on every call.

FastAPI-free: imported by shared resolvers that worker closures load.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Literal
from uuid import UUID

from shared.system_account_guard import is_system_account

if TYPE_CHECKING:
    from src.services.authorization.explain import RunUser, Trace

logger = logging.getLogger(__name__)

NoteKind = Literal["scope_switch", "child_run", "run_as", "entry", "policy", "secret", "permission"]
# Every organization: the target of a list read with no scope.
ALL_ORGS: Literal["*"] = "*"
# A target organization; None is Global.
NoteTarget = UUID | None | Literal["*"]
# What a person's own request notes; every kind is noted for a run.
_DIRECT_KINDS = frozenset({"permission", "scope_switch", "run_as"})
# The verified runtime subject used only to record an enforced policy decision.
# Keep this name shared with the policy recorder so the request-local cache
# cannot silently diverge from its producer.
POLICY_RECORD_SUBJECT_CACHE_KEY = "policy_record_subject"
# Objects whose organization and owner are read when the note is judged,
# after the response, instead of in the request.
OwnedKind = Literal["execution", "agent_run", "artifact_workspace", "agent_tools"]


@dataclass(frozen=True)
class Owned:
    """An object a request used without loading it: the note is judged in
    the object's organization, and only when ``actor`` does not own it."""

    kind: OwnedKind
    id: UUID
    actor: UUID
    # agent_tools: the tools attached; elevated when the actor could not use them.
    tools: tuple[str, ...] = ()


@dataclass
class Note:
    kind: NoteKind
    target: NoteTarget
    facts: dict[str, Any] = field(default_factory=dict)
    decision: tuple[RunUser, Trace] | None = None


@dataclass
class Collector:
    execution_id: UUID | None
    run_user_id: UUID | None
    workflow_id: UUID | None
    notes: list[Note] = field(default_factory=list)
    # Set once the request's notes have been judged; later notes are dropped.
    closed: bool = False
    # Per-request cache for helpers that need the run user's principal.
    cache: dict[str, Any] = field(default_factory=dict)
    # A person acting directly (no run): only ``_DIRECT_KINDS`` are kept,
    # judged against their own roles.
    direct: bool = False
    # Request attribution only; an agent bridge still acts directly as its user.
    surface: str | None = None
    agent_run_id: UUID | None = None
    # Some chat bridges have run lineage without a persistent AgentRun.
    is_run: bool = False
    # Browser impersonation provenance for access-check audit rows. The
    # collector's run user remains the effective target.
    impersonated_by_user_id: UUID | None = None
    impersonation_session_id: UUID | None = None


_current: ContextVar[Collector | None] = ContextVar("access_check_collector", default=None)


def _uuid(value: Any) -> UUID | None:
    return UUID(str(value)) if value else None


def _optional_uuid(value: Any) -> UUID | None:
    """Treat malformed attribution-only claims as absent."""
    try:
        return _uuid(value)
    except (ValueError, TypeError, AttributeError):
        return None


def start_collecting(
    payload: dict[str, Any] | None, *, surface: str | None = None, principal=None,
) -> Token[Collector | None] | None:
    """Collect verified runtime facts or an ordinary person's request."""
    if not payload:
        return None
    runtime = None if principal is None else principal.runtime_authority
    if runtime is not None:
        token = collect_run(
            runtime.credential_id if runtime.kind == "workflow_execution" else None,
            runtime.run_user.user_id, runtime.workflow_id,
        )
        collector = _current.get()
        assert collector is not None
        collector.agent_run_id = runtime.credential_id if runtime.kind == "agent_run" else None
        collector.surface = "agent" if collector.agent_run_id else surface
        # An agent adds no workflow power; it is judged on its original user.
        collector.direct = runtime.powers is None
        # RuntimeAuthority was freshly verified before the collector began.
        # Recording can reuse its durable subject; authorization still loads
        # and enforces current policy facts independently.
        collector.cache[POLICY_RECORD_SUBJECT_CACHE_KEY] = (
            runtime.run_user,
            runtime.powers,
        )
        collector.impersonated_by_user_id = runtime.impersonated_by_user_id
        collector.impersonation_session_id = runtime.impersonation_session_id
        return token
    if any(key in payload for key in ("credential_kind", "engine_execution_id", "service_id", "engine_agent_run_id")):
        return None
    token = collect_person(_uuid(payload.get("sub")))
    collector = _current.get()
    if token is not None and collector is not None:
        collector.surface = surface
        collector.impersonated_by_user_id = _optional_uuid(payload.get("impersonated_by_user_id"))
        collector.impersonation_session_id = _optional_uuid(payload.get("impersonation_session_id"))
    return token


def collect_run(
    execution_id: UUID | None, run_user_id: UUID | None, workflow_id: UUID | None
) -> Token[Collector | None]:
    """Start a collector for a run's request: every note kind, judged
    against ``run_user_id`` with ``workflow_id``'s powers."""
    return _current.set(Collector(execution_id=execution_id, run_user_id=run_user_id, workflow_id=workflow_id, is_run=True))


def collect_person(user_id: UUID | None) -> Token[Collector | None] | None:
    """Start a collector for a person acting directly; None for no subject
    or the system subject."""
    if user_id is None or is_system_account(user_id):
        return None
    return _current.set(Collector(execution_id=None, run_user_id=user_id, workflow_id=None, direct=True))


def stop_collecting(token: Token[Collector | None] | None) -> None:
    if token is not None:
        _current.reset(token)


def current() -> Collector | None:
    return _current.get()


@contextmanager
def suspend_collecting() -> Iterator[None]:
    """Temporarily discard notes from an internal authorization recheck."""
    token = _current.set(None)
    try:
        yield
    finally:
        _current.reset(token)


def renew() -> Collector | None:
    """Hand back the current collector and collect afresh for the same caller:
    a long-lived connection is judged message by message. The token from
    ``start_collecting`` still restores what came before."""
    collector = _current.get()
    if collector is None:
        return None
    _current.set(
        Collector(
            execution_id=collector.execution_id,
            run_user_id=collector.run_user_id,
            workflow_id=collector.workflow_id,
            direct=collector.direct,
            surface=collector.surface,
            agent_run_id=collector.agent_run_id,
            is_run=collector.is_run,
            impersonated_by_user_id=collector.impersonated_by_user_id,
            impersonation_session_id=collector.impersonation_session_id,
        )
    )
    return collector


def note(kind: NoteKind, target: NoteTarget, /, **facts: Any) -> None:
    """Note one decision-relevant input for the current request.

    ``enforced=True`` marks a decision the request enforces; its row is
    written as enforced. Use ``note_decision`` to preserve the actual judgment."""
    _append_note(Note(kind, target, facts))


def note_decision(kind: NoteKind, target: NoteTarget, *, run_user: RunUser, trace: Trace, **facts: Any) -> None:
    """Retain the actual enforced judgment before a mutation changes its role facts."""
    _append_note(Note(kind, target, {**facts, "enforced": True}, (run_user, trace)))


def _append_note(noted: Note) -> None:
    collector = _current.get()
    kind = noted.kind
    if collector is None or (collector.direct and kind not in _DIRECT_KINDS):
        return
    if collector.closed:
        logger.warning("access check noted after the request was judged; dropped (kind=%s)", kind)
        return
    collector.notes.append(noted)


def note_power(permission: str, target: NoteTarget | Owned, *, subject: str) -> None:
    """Note that an elevated branch uses ``permission`` on ``subject`` in
    ``target``, the object's organization (None is Global), or on an
    ``Owned`` object resolved when judged."""
    if isinstance(target, Owned):
        owned: dict[str, Any] = {"owned": target.kind, "object_id": str(target.id), "actor": str(target.actor)}
        if target.tools:
            owned["tools"] = list(target.tools)
        note("permission", None, permission=permission, subject=subject, **owned)
        return
    note("permission", target, permission=permission, subject=subject)


def launch_target(object_org: UUID | None, caller_org: UUID | None) -> NoteTarget:
    """Where launching or opening an object acts: its organization, or for a
    Global object the caller's own. Run Workflows (and every launch
    permission) means "may launch at all, in this organization", and Global
    objects are shared defaults read at home, so using one is never a
    Global action."""
    return object_org if object_org is not None else caller_org


def note_launch(permission: str, object_org: UUID | None, caller_org: UUID | None, *, subject: str) -> None:
    """Note that the request launches or opens ``subject`` (a workflow, agent,
    AI call, app or form) under ``permission``, at ``launch_target``."""
    note("permission", launch_target(object_org, caller_org), permission=permission, subject=subject)


def note_failure(kind: NoteKind, target: NoteTarget, error: BaseException) -> None:
    """Note that a check could not be computed; written as a coverage gap."""
    logger.warning("access check could not be computed (kind=%s): %s", kind, type(error).__name__)
    note(kind, target, gap=f"observer_error:{type(error).__name__}")

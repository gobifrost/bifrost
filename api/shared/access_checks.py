"""Report-only access checks: what a request did, noted for the model to judge.

Runs act through the API with execution credentials (the engine token, or the
MCP bridge token of an agent run), which today's code treats as a platform
superuser. The managed-identity model judges the same actions against the
run's user instead. Shared code notes each decision-relevant input (a target
organization, a child run, ``run_as``, a policy or secret decision) into the
request's collector; after the response, the request middleware judges every
note (``src.services.access_check_writer``) and writes the differences that
matter to the audit log as ``access.check`` events. Nothing here changes what
a request does.

Every elevated branch (a superuser, platform-admin or provider-org check that
unlocks more) and every launch of a workflow, agent or AI also notes the named
permission that gates it (``note_power``). A person's own request is collected
too, for those notes only: their own roles decide, and only would-deny
decisions are written.

FastAPI-free: imported by shared resolvers that worker closures load.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from shared.system_account_guard import is_system_account

logger = logging.getLogger(__name__)

NoteKind = Literal["scope_switch", "child_run", "run_as", "entry", "policy", "secret", "permission"]
# Every organization: the target of a list read with no scope.
ALL_ORGS: Literal["*"] = "*"
# A target organization; None is Global.
NoteTarget = UUID | None | Literal["*"]


@dataclass
class Note:
    kind: NoteKind
    target: NoteTarget
    facts: dict[str, Any] = field(default_factory=dict)


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
    # A person acting directly (no run): only ``permission`` notes are kept,
    # judged against their own roles.
    direct: bool = False


_current: ContextVar[Collector | None] = ContextVar("access_check_collector", default=None)


def _uuid(value: Any) -> UUID | None:
    return UUID(str(value)) if value else None


def start_collecting(payload: dict[str, Any] | None) -> Token[Collector | None] | None:
    """Start a collector for an authenticated request; None for anyone else.

    A run's request: the engine token (superuser with an execution id, as
    ``src.core.auth`` treats it) and any token acting for a run user other
    than its own subject (the MCP bridge for an agent run nobody started).
    Every note kind is collected, judged against the run user.

    A person's request (their own token, or the bridge for an agent they
    started): collected with the person as run user, for named permissions
    only (``direct``). The system subject (supervised services, embedded
    sessions, the legacy engine credential) is not a person and is not
    collected.
    """
    if not payload:
        return None
    engine = bool(payload.get("engine_execution_id")) and bool(payload.get("is_superuser"))
    run_user = payload.get("engine_run_user_id")
    subject = payload.get("sub")
    if engine or (run_user and run_user != subject):
        return _current.set(
            Collector(
                execution_id=_uuid(payload.get("engine_execution_id")) if engine else None,
                run_user_id=_uuid(run_user),
                workflow_id=_uuid(payload.get("engine_workflow_id")),
            )
        )
    return collect_person(_uuid(subject))


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
        )
    )
    return collector


def note(kind: NoteKind, target: NoteTarget, /, **facts: Any) -> None:
    """Note one decision-relevant input for the current request."""
    collector = _current.get()
    if collector is None or (collector.direct and kind != "permission"):
        return
    if collector.closed:
        logger.warning("access check noted after the request was judged; dropped (kind=%s)", kind)
        return
    collector.notes.append(Note(kind, target, facts))


def note_power(permission: str, target: NoteTarget, *, subject: str) -> None:
    """Note that the request uses ``permission`` on ``subject`` in ``target``
    (the object's organization; None is Global): an elevated branch, or the
    launch or opening of a workflow, agent, AI call, app or form."""
    note("permission", target, permission=permission, subject=subject)


def launch_target(object_org: UUID | None, caller_org: UUID | None) -> NoteTarget:
    """Where launching or opening an object acts: its organization, or for a
    Global object the caller's own (Global objects are shared defaults, used
    at home)."""
    return object_org if object_org is not None else caller_org


def note_failure(kind: NoteKind, target: NoteTarget, error: BaseException) -> None:
    """Note that a check could not be computed; written as a coverage gap."""
    logger.warning("access check could not be computed (kind=%s): %s", kind, type(error).__name__)
    note(kind, target, gap=f"observer_error:{type(error).__name__}")

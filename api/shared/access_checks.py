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

FastAPI-free: imported by shared resolvers that worker closures load.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

logger = logging.getLogger(__name__)

NoteKind = Literal["scope_switch", "child_run", "run_as", "entry", "policy", "secret"]
# A target organization; None is Global; "*" is every organization (a list
# read with no scope).
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


_current: ContextVar[Collector | None] = ContextVar("access_check_collector", default=None)


def _uuid(value: Any) -> UUID | None:
    return UUID(str(value)) if value else None


def start_collecting(payload: dict[str, Any] | None) -> Token[Collector | None] | None:
    """Start a collector for a run's request; None for anyone else.

    Collected: the engine token (superuser with an execution id, as
    ``src.core.auth`` treats it) and any token carrying a run user (the MCP
    bridge for an agent run). People act as themselves and supervised
    services act with their own non-admin principal, so neither is judged
    differently by the model.
    """
    if not payload:
        return None
    engine = bool(payload.get("engine_execution_id")) and bool(payload.get("is_superuser"))
    if not engine and not payload.get("engine_run_user_id"):
        return None
    return _current.set(
        Collector(
            execution_id=_uuid(payload.get("engine_execution_id")) if engine else None,
            run_user_id=_uuid(payload.get("engine_run_user_id")),
            workflow_id=_uuid(payload.get("engine_workflow_id")),
        )
    )


def stop_collecting(token: Token[Collector | None] | None) -> None:
    if token is not None:
        _current.reset(token)


def current() -> Collector | None:
    return _current.get()


def note(kind: NoteKind, target: NoteTarget, /, **facts: Any) -> None:
    """Note one decision-relevant input for the current run's request."""
    collector = _current.get()
    if collector is None:
        return
    if collector.closed:
        logger.warning("access check noted after the request was judged; dropped (kind=%s)", kind)
        return
    collector.notes.append(Note(kind, target, facts))

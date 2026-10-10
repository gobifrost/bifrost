"""Running a workflow or an agent as another user (``run_as``).

``authorize_run_as`` is the one decision every launch path makes before it
changes the acting user. It changes nothing else: the run user (whose reach
the run has), the initiator and the lineage stay the caller's.
``acting_target`` is what a launch path calls: that decision, plus the run
user itself when a run names it.

- A person (their own token, the CLI, the SDK, an MCP user token, or the MCP
  bridge token of an agent run's acting user) needs Impersonate Users in the
  target user's organization (a Global target at the platform boundary), and
  Read and Write Privileged Access there too when the target holds privileged
  access. ``explain.check_run_as`` decides, so the enforced decision and the
  recorded trace are the same. Someone who holds Impersonate Users nowhere is
  refused before any user is looked up. Someone who holds it is refused alike
  for an unknown user and for one whose organization their Impersonate Users
  does not cover, so they learn nothing about users outside it. Both
  refusals are recorded at the caller's own organization, naming the
  requested user.
- An execution credential (a workflow's engine token, a service token) is
  decided by the staged rule ``enforce.decide_for`` applies to every
  execution credential: allowed iff the token is a superuser token. The check
  against the run user's roles is recorded report-only.
- An embedded session is always refused.

Every target must be an active person: not the system account and not a
managed identity (assign the identity to the workflow or agent instead).
Naming the run user itself is not impersonation: nothing changes, nothing is
noted.

A scheduled run keeps what was decided (``scheduled_run_as``) and is decided
again when it fires (``recheck_run_as``): a person's impersonation on the
initiator as they are then (their roles; someone inactive can't act as
anyone, live or later), an execution credential's on the target alone. Every
refusal is noted as a live one is: a person's enforced, a run's report-only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from src.core.principal import UserPrincipal
from src.models.orm.users import User
from src.services.authorization.enforce import Caller, load_caller, permitted_organizations
from src.services.authorization.explain import (
    RunAsTarget,
    RunUser,
    Trace,
    check_run_as,
    load_run_as_target,
    load_run_user,
    run_as_user_step,
)

IMPERSONATE_PERMISSION = "users.impersonate"
PRIVILEGED_PERMISSION = "privilegedaccess.readwrite"

DENIED_MESSAGE = "You don't have permission to run as this user"
NOT_FOUND_MESSAGE = "Run As user '{user_id}' not found"

_UNUSABLE = {
    "inactive": "is inactive",
    "system_account": "is the system account",
    "managed_identity": "is a managed identity; assign the identity to the workflow or agent instead",
}


class RunAsError(Exception):
    """A refused ``run_as`` with an HTTP-style status.

    Raised by the shared helper so an HTTP handler and callers reached over
    the worker-local engine socket read the same status and detail.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def authorize_run_as(
    db: AsyncSession, principal: UserPrincipal, run_as_user_id: UUID
) -> RunAsTarget | None:
    """The user to act as, or None when ``run_as_user_id`` is the run user.

    Raises ``RunAsError``: 403 when the caller may not act as the user (for
    a person, also when no such user exists), 404 when an execution
    credential names no user, 400 when the user is inactive, the system
    account or a managed identity.
    """
    if principal.embed:
        raise RunAsError(403, DENIED_MESSAGE)
    execution_credential = _is_execution_credential(principal)
    # The staged execution-credential rule comes first, so a service token is
    # refused even when it names its own run user.
    if execution_credential and not principal.is_superuser:
        raise RunAsError(403, DENIED_MESSAGE)
    if run_as_user_id == _run_user_id(principal):
        return None
    if execution_credential:
        target = _usable_for_run(await load_run_as_target(db, run_as_user_id), run_as_user_id)
        _note(target, enforced=False)
        return target
    return await _authorize_for_person(db, await load_caller(db, principal), run_as_user_id)


async def acting_target(
    db: AsyncSession, principal: UserPrincipal, run_as_user_id: UUID
) -> RunAsTarget | None:
    """Who a launch naming ``run_as_user_id`` acts as: the user
    ``authorize_run_as`` allows, or None when a person names themselves
    (nothing changes). An execution credential naming its run user acts as
    that user, since its token is not the run user; only existence is
    checked, because a run whose user is a managed identity passes
    ``run_as=context.user_id`` and must keep acting as it.

    Raises ``RunAsError`` as ``authorize_run_as`` does, and 404 when the
    named run user no longer exists.
    """
    target = await authorize_run_as(db, principal, run_as_user_id)
    if target is not None or not _is_execution_credential(principal):
        return target
    target = await load_run_as_target(db, run_as_user_id)
    if target is None:
        raise RunAsError(404, _not_found(run_as_user_id))
    return target


def scheduled_run_as(principal: UserPrincipal, target: RunAsTarget) -> dict[str, Any] | None:
    """What a scheduled row keeps of an impersonation, so ``recheck_run_as``
    can decide it again when the row fires; None when ``target`` is the run
    user (nothing to decide). ``enforced`` is true when a person decided it.
    An execution credential's also keeps the run its decision was noted for
    (run user, workflow, execution), so a refusal is noted for it again."""
    if target.user_id == _run_user_id(principal):
        return None
    execution_credential = _is_execution_credential(principal)
    kept: dict[str, Any] = {
        "user_id": str(target.user_id),
        "authorized_by": str(principal.user_id),
        "enforced": not execution_credential,
    }
    if execution_credential:
        kept["run_user_id"] = None if principal.run_user_id is None else str(principal.run_user_id)
        kept["workflow_id"] = None if principal.workflow_id is None else str(principal.workflow_id)
        kept["execution_id"] = principal.engine_execution_id
    return kept


async def recheck_run_as(db: AsyncSession, run_as: dict[str, Any]) -> str | None:
    """Why the impersonation ``scheduled_run_as`` kept is no longer permitted,
    or None while it still is. A person's is judged again on the initiator as
    they are now (their roles, and still active and present); an execution
    credential's checks the target only. A refusal is noted (into the
    caller's collector) as a live one is."""
    run_as_user_id = UUID(run_as["user_id"])
    target = await load_run_as_target(db, run_as_user_id)
    try:
        if not run_as["enforced"]:
            _usable_for_run(target, run_as_user_id)
            return None
        initiator = await load_run_user(db, UUID(run_as["authorized_by"]))
        if target is None:
            _note_run_as(None if initiator is None else initiator.home, run_as_user_id, enforced=True)
            return _not_found(run_as_user_id)
        if initiator is None:
            _note(target, enforced=True)
            return "The user who scheduled this run no longer exists"
        trace = check_run_as(initiator, None, target)
        if trace.outcome == "success":
            return None
        _note(target, enforced=True)
        if not initiator.is_active:
            return "The user who scheduled this run is inactive"
        _raise_if_refused(trace, target)
    except RunAsError as exc:
        return exc.detail
    return None


def _is_execution_credential(principal: UserPrincipal) -> bool:
    return bool(principal.engine_execution_id or principal.service_id)


def _run_user_id(principal: UserPrincipal) -> UUID | None:
    return principal.run_user_id if _is_execution_credential(principal) else principal.user_id


def _usable_for_run(target: RunAsTarget | None, run_as_user_id: UUID) -> RunAsTarget:
    """``target`` when an execution credential may act as it (decided on the
    target alone); otherwise raise, with the refusal noted report-only."""
    if target is None:
        _note_run_as(None, run_as_user_id, enforced=False)
        raise RunAsError(404, _not_found(run_as_user_id))
    step = run_as_user_step(target)
    if step.status == "stopped":
        _note(target, enforced=False)
        raise RunAsError(400, _unusable(target, step.reason))
    return target


async def _authorize_for_person(db: AsyncSession, caller: Caller, run_as_user_id: UUID) -> RunAsTarget:
    ctx = caller.ctx
    reach = permitted_organizations(caller, IMPERSONATE_PERMISSION)
    home = caller.principal.organization_id
    if ctx is None or reach.is_empty:
        # Recorded at the caller's own organization: the user is not looked up.
        access_checks.note("run_as", home, run_as_user_id=run_as_user_id, enforced=True, held_nowhere=True)
        raise RunAsError(403, DENIED_MESSAGE)
    target = await load_run_as_target(db, run_as_user_id)
    if target is None or not reach.covers(target.organization_id):
        # An unknown user and one outside the caller's Impersonate Users are
        # refused alike. The caller's organization records only the request;
        # an existing user's own organization records the full decision,
        # unless that is the caller's organization.
        access_checks.note("run_as", home, run_as_user_id=run_as_user_id, enforced=True, outside_reach=True)
        if target is not None and target.organization_id != home:
            _note(target, enforced=True)
        raise RunAsError(403, DENIED_MESSAGE)
    user_id = caller.principal.user_id
    active = (await db.execute(select(User.is_active).where(User.id == user_id))).scalar_one_or_none()
    trace = check_run_as(RunUser(user_id, ctx, None, is_active=bool(active)), None, target)
    _note(target, enforced=True)
    _raise_if_refused(trace, target)
    return target


def _not_found(run_as_user_id: UUID) -> str:
    return NOT_FOUND_MESSAGE.format(user_id=run_as_user_id)


def _raise_if_refused(trace: Trace, target: RunAsTarget) -> None:
    stopped = next((step for step in trace.steps if step.status == "stopped"), None)
    if stopped is None:
        return
    if stopped.key == "run_as_user":
        raise RunAsError(400, _unusable(target, stopped.reason))
    raise RunAsError(403, DENIED_MESSAGE)


def _unusable(target: RunAsTarget, reason: str) -> str:
    return f"Run As user '{target.user_id}' {_UNUSABLE[reason]}"


def _note_run_as(org: UUID | None, run_as_user_id: UUID, *, enforced: bool) -> None:
    """The ``run_as`` decision at ``org``. The writer judges it on the user
    as they are then, and records a gap naming them when there is none."""
    # A run's report-only note keeps the inputs it has always had.
    if enforced:
        access_checks.note("run_as", org, run_as_user_id=run_as_user_id, enforced=True)
    else:
        access_checks.note("run_as", org, run_as_user_id=run_as_user_id)


def _note(target: RunAsTarget, *, enforced: bool) -> None:
    org = target.organization_id
    subject = f"user:{target.user_id}"
    _note_run_as(org, target.user_id, enforced=enforced)
    access_checks.note_power(IMPERSONATE_PERMISSION, org, subject=subject)
    if target.privileged:
        access_checks.note_power(PRIVILEGED_PERMISSION, org, subject=subject)

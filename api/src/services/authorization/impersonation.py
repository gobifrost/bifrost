"""Authorize Run As without changing the original user's reach or workflow powers.

Every launch checks the original user's current roles plus configured workflow
capabilities. The target must be an active person in reach. Privileged targets
also require Read and Write Privileged Access. Delayed runs repeat the decision
against current original-user roles and the originating workflow's powers.
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
    check_permission,
    check_run_as_outside_reach,
    load_run_as_target,
    load_run_user,
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
    if run_as_user_id == _run_user_id(principal):
        return None
    return await _authorize_for_caller(db, await load_caller(db, principal), run_as_user_id)


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
    user (nothing to decide). The original user and originating workflow
    are retained so delayed execution repeats the same authorization rule."""
    if target.user_id == _run_user_id(principal):
        return None
    runtime = principal.runtime_authority
    return {
        "user_id": str(target.user_id),
        "authorized_by": str(_run_user_id(principal)),
        "enforced": True,
        "workflow_id": None if runtime is None or runtime.workflow_id is None else str(runtime.workflow_id),
    }


async def recheck_run_as(db: AsyncSession, run_as: dict[str, Any]) -> str | None:
    """Why the impersonation ``scheduled_run_as`` kept is no longer permitted,
    or None while it still is. The original user's current roles, originating
    workflow powers, and target state decide again when the schedule fires."""
    run_as_user_id = UUID(run_as["user_id"])
    target = await load_run_as_target(db, run_as_user_id)
    try:
        initiator = await load_run_user(db, UUID(run_as["authorized_by"]))
        if target is None:
            _note_run_as(None if initiator is None else initiator.home, run_as_user_id)
            return _not_found(run_as_user_id)
        if initiator is None:
            _note(target)
            return "The user who scheduled this run no longer exists"
        powers = None
        if run_as.get("workflow_id"):
            from src.models.orm.workflows import Workflow
            from src.services.authorization.explain import load_powers

            workflow = await db.get(Workflow, UUID(run_as["workflow_id"]))
            if workflow is None:
                return "The workflow that authorized this run no longer exists"
            powers = await load_powers(db, workflow.id)
        trace = check_run_as(initiator, powers, target)
        _note(target, decision=(initiator, trace))
        if trace.outcome == "success":
            return None
        if not initiator.is_active:
            return "The user who scheduled this run is inactive"
        _raise_if_refused(trace, target)
    except RunAsError as exc:
        return exc.detail
    return None


def _is_execution_credential(principal: UserPrincipal) -> bool:
    return principal.runtime_authority is not None


def _run_user_id(principal: UserPrincipal) -> UUID | None:
    return principal.runtime_authority.run_user.user_id if principal.runtime_authority is not None else principal.user_id


async def _authorize_for_caller(db: AsyncSession, caller: Caller, run_as_user_id: UUID) -> RunAsTarget:
    ctx = caller.ctx
    if ctx is None:
        raise RunAsError(403, DENIED_MESSAGE)
    runtime = caller.principal.runtime_authority
    if runtime is None:
        user_id = caller.principal.user_id
        active = (await db.execute(select(User.is_active).where(User.id == user_id))).scalar_one_or_none()
        run_user = RunUser(user_id, ctx, None, is_active=bool(active))
        powers = None
    else:
        run_user, powers = runtime.run_user, runtime.powers
    home = run_user.home
    reach = permitted_organizations(caller, IMPERSONATE_PERMISSION)
    if reach.is_empty:
        # Refuse without looking up the requested user.
        access_checks.note_decision(
            "run_as", home, run_user=run_user,
            trace=check_permission(run_user, powers, IMPERSONATE_PERMISSION, home),
            run_as_user_id=run_as_user_id, held_nowhere=True,
        )
        raise RunAsError(403, DENIED_MESSAGE)
    target = await load_run_as_target(db, run_as_user_id)
    if target is None or not reach.covers(target.organization_id):
        # Unknown and unreachable users receive the same response.
        access_checks.note_decision(
            "run_as", home, run_user=run_user, trace=check_run_as_outside_reach(run_user),
            run_as_user_id=run_as_user_id, outside_reach=True,
        )
        if target is not None and target.organization_id != home:
            _note(target, decision=(run_user, check_run_as(run_user, powers, target)))
        raise RunAsError(403, DENIED_MESSAGE)
    trace = check_run_as(run_user, powers, target)
    _note(target, decision=(run_user, trace))
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


def _note_run_as(org: UUID | None, run_as_user_id: UUID) -> None:
    """Keep an incomplete decision for the writer to explain as a gap."""
    access_checks.note("run_as", org, run_as_user_id=run_as_user_id, enforced=True)


def _note(target: RunAsTarget, *, decision: tuple[RunUser, Trace] | None = None) -> None:
    org = target.organization_id
    subject = f"user:{target.user_id}"
    if decision is None:
        _note_run_as(org, target.user_id)
    else:
        run_user, trace = decision
        access_checks.note_decision(
            "run_as", org, run_user=run_user, trace=trace, run_as_user_id=target.user_id,
        )
    access_checks.note_power(IMPERSONATE_PERMISSION, org, subject=subject)
    if target.privileged:
        access_checks.note_power(PRIVILEGED_PERMISSION, org, subject=subject)

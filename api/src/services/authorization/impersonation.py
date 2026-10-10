"""Running a workflow or an agent as another user (``run_as``).

``authorize_run_as`` is the one decision every launch path makes before it
changes the acting user. It changes nothing else: the run user (whose reach
the run has), the initiator and the lineage stay the caller's.

- A person (their own token, the CLI, the SDK, an MCP user token, or the MCP
  bridge token of an agent run's acting user) needs Impersonate Users in the
  target user's organization (a Global target at the platform boundary), and
  Read and Write Privileged Access there too when the target holds privileged
  access. ``explain.check_run_as`` decides, so the enforced decision and the
  recorded trace are the same. Someone who holds Impersonate Users nowhere is
  refused before any user is looked up, so they learn nothing about which
  users exist; the refusal is recorded at their own organization.
- An execution credential (a workflow's engine token, a service token) is
  decided by the staged rule ``enforce.decide_for`` applies to every
  execution credential: allowed iff the token is a superuser token. The check
  against the run user's roles is recorded report-only.
- An embedded session is always refused.

Every target must be an active person: not the system account and not a
managed identity (assign the identity to the workflow or agent instead).
Naming the run user itself is not impersonation: nothing changes, nothing is
noted.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from src.core.principal import UserPrincipal
from src.services.authorization.enforce import Caller, load_caller, permitted_organizations
from src.services.authorization.explain import (
    RunAsTarget,
    RunUser,
    check_run_as,
    load_run_as_target,
    run_as_user_step,
)

IMPERSONATE_PERMISSION = "users.impersonate"
PRIVILEGED_PERMISSION = "privilegedaccess.readwrite"

DENIED_MESSAGE = "You don't have permission to run as this user"

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

    Raises ``RunAsError``: 403 when the caller may not act as the user, 404
    when no such user exists, 400 when the user is inactive, the system
    account or a managed identity.
    """
    if principal.embed:
        raise RunAsError(403, DENIED_MESSAGE)
    execution_credential = bool(principal.engine_execution_id or principal.service_id)
    run_user_id = principal.run_user_id if execution_credential else principal.user_id
    if run_as_user_id == run_user_id:
        return None
    if execution_credential:
        return await _authorize_for_run(db, principal, run_as_user_id)
    return await _authorize_for_person(db, await load_caller(db, principal), run_as_user_id)


async def _authorize_for_run(
    db: AsyncSession, principal: UserPrincipal, run_as_user_id: UUID
) -> RunAsTarget:
    if not principal.is_superuser:
        raise RunAsError(403, DENIED_MESSAGE)
    target = await _load(db, run_as_user_id)
    step = run_as_user_step(target)
    if step.status == "stopped":
        raise RunAsError(400, _unusable(target, step.reason))
    _note(target, enforced=False)
    return target


async def _authorize_for_person(db: AsyncSession, caller: Caller, run_as_user_id: UUID) -> RunAsTarget:
    ctx = caller.ctx
    if ctx is None or permitted_organizations(caller, IMPERSONATE_PERMISSION).is_empty:
        # Recorded at the caller's own organization: the user is not looked up.
        access_checks.note(
            "run_as",
            caller.principal.organization_id,
            run_as_user_id=run_as_user_id,
            enforced=True,
            held_nowhere=True,
        )
        raise RunAsError(403, DENIED_MESSAGE)
    target = await _load(db, run_as_user_id)
    trace = check_run_as(RunUser(caller.principal.user_id, ctx, None), None, target)
    _note(target, enforced=True)
    stopped = next((step for step in trace.steps if step.status == "stopped"), None)
    if stopped is None:
        return target
    if stopped.key == "run_as_user":
        raise RunAsError(400, _unusable(target, stopped.reason))
    raise RunAsError(403, DENIED_MESSAGE)


async def _load(db: AsyncSession, run_as_user_id: UUID) -> RunAsTarget:
    target = await load_run_as_target(db, run_as_user_id)
    if target is None:
        raise RunAsError(404, f"Run As user '{run_as_user_id}' not found")
    return target


def _unusable(target: RunAsTarget, reason: str) -> str:
    return f"Run As user '{target.user_id}' {_UNUSABLE[reason]}"


def _note(target: RunAsTarget, *, enforced: bool) -> None:
    org = target.organization_id
    subject = f"user:{target.user_id}"
    # A run's report-only note keeps the inputs it has always had.
    if enforced:
        access_checks.note("run_as", org, run_as_user_id=target.user_id, enforced=True)
    else:
        access_checks.note("run_as", org, run_as_user_id=target.user_id)
    access_checks.note_power(IMPERSONATE_PERMISSION, org, subject=subject)
    if target.privileged:
        access_checks.note_power(PRIVILEGED_PERMISSION, org, subject=subject)

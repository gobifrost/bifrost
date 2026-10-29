"""Visibility and diagnostic detail for execution reads."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, true

from shared.execution_visibility import is_own_execution, own_executions
from src.services.authorization.enforce import (
    Caller,
    allows_permission,
    permitted_organizations,
    user_reach,
)


@dataclass(frozen=True)
class ExecutionReadAccess:
    visible: bool
    diagnostics: bool


def _and_reach_filters(*filters):
    present = [filter_ for filter_ in filters if filter_ is not None]
    # ``OrgReach.where`` uses None to mean an unrestricted predicate.  Keep
    # that meaning as SQL true here so a platform-scoped read-all grant does
    # not accidentally collapse to the owner-only branch.
    return and_(*present) if present else true()


def execution_reader_id(caller: Caller) -> UUID:
    runtime = caller.principal.runtime_authority
    return caller.principal.user_id if runtime is None else runtime.run_user.user_id


def execution_read_access(caller: Caller, execution) -> ExecutionReadAccess:
    """Own basic history stays private; named permissions add detail or others' runs."""
    organization_id = execution.organization_id
    if not user_reach(caller).covers(organization_id):
        return ExecutionReadAccess(False, False)
    others = allows_permission(caller, "executions.read.all", organization_id)
    own = is_own_execution(
        execution_reader_id(caller), execution_id=execution.id,
        executed_by=execution.executed_by, started_by_user_id=execution.started_by_user_id,
        root_execution_id=execution.root_execution_id,
    )
    return ExecutionReadAccess(
        visible=own or others,
        diagnostics=(own or others) and (
            others or allows_permission(caller, "executions.read", organization_id)
        ),
    )


async def current_execution_read_access(
    caller: Caller, execution
) -> ExecutionReadAccess:
    """Read access using either current caller policy or an embed capability.

    Embed sessions can read only an execution mapped to their server-owned JTI
    and only at their exact persisted organization. They never receive
    diagnostics. Human and runtime callers retain the canonical policy path.
    """
    principal = caller.principal
    if not principal.embed:
        return execution_read_access(caller, execution)

    is_execution_capability = (
        principal.is_verified
        and (
            principal.embed_kind == "app"
            or (principal.embed_kind == "form" and principal.grant == "hmac")
        )
    )
    if (
        not is_execution_capability
        or principal.jti is None
        or principal.organization_id != execution.organization_id
    ):
        return ExecutionReadAccess(False, False)

    from src.core.embed_middleware import embed_session_owns_execution

    if not await embed_session_owns_execution(principal.jti, str(execution.id)):
        return ExecutionReadAccess(False, False)
    return ExecutionReadAccess(True, False)


def execution_cancel_allowed(caller: Caller, execution) -> bool:
    """A caller cancels an own reachable run or holds scoped Read/Write All."""
    organization_id = execution.organization_id
    if not user_reach(caller).covers(organization_id):
        return False
    own = is_own_execution(
        execution_reader_id(caller),
        execution_id=execution.id,
        executed_by=execution.executed_by,
        started_by_user_id=execution.started_by_user_id,
        root_execution_id=execution.root_execution_id,
    )
    return own or allows_permission(caller, "executions.readwrite.all", organization_id)


def execution_list_filter(caller: Caller, execution_model):
    """SQL visibility predicate applied before execution-history paging.

    ``executions.read.all`` is intersected with original-user reach so a
    permission never widens the durable actor's current organization reach.
    """
    reach_filter = user_reach(caller).where(execution_model.organization_id)
    own_filter = _and_reach_filters(
        reach_filter,
        # ``execution_reader_id`` intentionally resolves to the original run
        # user for typed runtime credentials, never the effects actor.
        own_executions(execution_reader_id(caller)),
    )
    all_filter = _and_reach_filters(
        reach_filter,
        permitted_organizations(caller, "executions.read.all").where(
            execution_model.organization_id
        ),
    )
    return or_(own_filter, all_filter)

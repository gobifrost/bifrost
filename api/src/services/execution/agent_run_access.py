"""Shared visibility policy for agent-run records."""

from sqlalchemy import or_, select
from sqlalchemy.sql.elements import ColumnElement

from src.core.principal import UserPrincipal
from src.models.orm.organizations import Organization
from src.models.orm.agent_runs import AgentRun
from src.services.authorization import AuthorizationBoundaryKind, AuthorizationContext


def agent_run_visibility_conditions_for_authorization(
    user: UserPrincipal,
    authorization: AuthorizationContext,
) -> tuple[ColumnElement[bool], ...]:
    """Return boundary-aware SQL conditions limiting visible agent runs.

    A human always keeps visibility to their own runs. Broader run visibility
    requires ``executions.read`` in the selected authorization boundary.
    Platform is Global-only; only ``platform.superuser`` is wildcard.
    """

    if authorization.has_capability("platform.superuser"):
        return ()

    visible: list[ColumnElement[bool]] = [AgentRun.caller_user_id == str(user.user_id)]
    if authorization.has_capability("executions.read"):
        boundary = authorization.selected_boundary
        if boundary.kind is AuthorizationBoundaryKind.PLATFORM:
            visible.append(AgentRun.org_id.is_(None))
        elif boundary.kind is AuthorizationBoundaryKind.ORGANIZATION:
            visible.append(AgentRun.org_id == boundary.organization_id)
        elif boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
            visible.append(
                AgentRun.org_id.in_(
                    select(Organization.id).where(Organization.is_provider.is_(False))
                )
            )

    return (or_(*visible), *_agent_run_delegation_conditions(user))


def _agent_run_delegation_conditions(
    user: UserPrincipal,
) -> tuple[ColumnElement[bool], ...]:
    return (
        or_(
            AgentRun.trigger_type != "delegation",
            AgentRun.parent_run_id.is_not(None),
            AgentRun.caller_user_id == str(user.user_id),
        ),
    )

"""Shared visibility policy for agent-run records."""

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import AgentRun


def agent_run_visibility_conditions(
    user: UserPrincipal,
) -> tuple[ColumnElement[bool], ...]:
    """Return SQL conditions limiting runs to those visible to ``user``.

    Autonomous delegations have a parent run and remain visible anywhere
    their parent orchestration is visible. Chat has no parent ``AgentRun``, so
    its delegated child is a root delegation row. Those rows inherit the
    private conversation's owner boundary through ``caller_user_id``.
    """
    if user.is_superuser:
        return ()

    conditions: list[ColumnElement[bool]] = []
    if user.organization_id is not None:
        conditions.append(AgentRun.org_id == user.organization_id)
    conditions.append(
        or_(
            AgentRun.trigger_type != "delegation",
            AgentRun.parent_run_id.is_not(None),
            AgentRun.caller_user_id == str(user.user_id),
        )
    )
    return tuple(conditions)

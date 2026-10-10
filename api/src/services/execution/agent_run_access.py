"""Shared visibility policy for agent-run records."""

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from shared import access_checks
from shared.access_checks import ALL_ORGS
from shared.scope_resolver import has_scope_bypass
from src.core.principal import UserPrincipal
from src.models.orm.agent_runs import AgentRun


def agent_run_visibility_conditions(
    user: UserPrincipal,
) -> tuple[ColumnElement[bool], ...]:
    """Return SQL conditions limiting runs to those visible to ``user``.

    A non-bypass caller sees only runs they started — mirroring how
    ``executions.py`` restricts to ``executed_by == user`` — never another
    user's runs in the same org. A delegated child run (``trigger_type ==
    "delegation"``, has a ``parent_run_id``) is visible only when the
    PARENT run's own caller is this user (a correlated subquery against the
    parent row), not merely because some ``parent_run_id`` is set.
    """
    if has_scope_bypass(
        is_platform_admin=user.is_platform_admin,
        is_provider_org=user.is_provider_org,
    ):
        # No filter: every user's runs in every organization.
        access_checks.note_power("agentruns.read.all", ALL_ORGS, subject="agent_runs")
        return ()

    caller_id = str(user.user_id)
    ParentRun = aliased(AgentRun)
    parent_is_caller = (
        select(1)
        .where(ParentRun.id == AgentRun.parent_run_id)
        .where(ParentRun.caller_user_id == caller_id)
        .exists()
    )

    conditions: list[ColumnElement[bool]] = []
    if user.organization_id is not None:
        conditions.append(AgentRun.org_id == user.organization_id)
    conditions.append(
        or_(
            AgentRun.caller_user_id == caller_id,
            and_(AgentRun.parent_run_id.is_not(None), parent_is_caller),
        )
    )
    return tuple(conditions)

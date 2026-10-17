"""Shared visibility policy for agent-run records."""

from sqlalchemy import and_, or_, select, true
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from src.models.orm.agent_runs import AgentRun
from src.services.agent_write_policy import caller_user_id
from src.services.authorization.enforce import Caller, permitted_organizations, user_reach


def agent_run_visibility_conditions(
    caller: Caller,
) -> tuple[ColumnElement[bool], ...]:
    """Return SQL conditions limiting runs to those visible to ``caller``.

    A caller always sees their own run history.  Other callers' history
    requires ``agentruns.read.all`` at the run's actual organization.  The
    original run user drives both decisions for a typed runtime credential;
    the worker/actor identity never broadens history.
    """
    original_user_id = caller_user_id(caller)
    if original_user_id is None:
        return (AgentRun.id.is_(None),)
    caller_id = str(original_user_id)
    ParentRun = aliased(AgentRun)
    parent_is_caller = (
        select(1)
        .where(ParentRun.id == AgentRun.parent_run_id)
        .where(ParentRun.caller_user_id == caller_id)
        .exists()
    )

    own = or_(
        AgentRun.caller_user_id == caller_id,
        and_(AgentRun.parent_run_id.is_not(None), parent_is_caller),
    )
    original_reach = user_reach(caller).where(AgentRun.org_id)
    other_reach = permitted_organizations(caller, "agentruns.read.all").where(
        AgentRun.org_id
    )
    # ``None`` means unrestricted, not absent.  The named-power branch is
    # still intersected with original reach below, so Full/runtime power and
    # a platform admin never make a row outside original reach visible.
    other = true() if other_reach is None else other_reach
    within_original_reach = true() if original_reach is None else original_reach
    return (and_(within_original_reach, or_(own, other)),)

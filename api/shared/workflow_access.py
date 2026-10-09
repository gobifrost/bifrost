"""Single source of truth for whether a non-bypass caller can use a workflow tool.

Workflow ``access_level`` has three values (there is no "private" tier for
workflows, unlike forms/agents/apps):

- ``"everyone"``: any authenticated user, including external (portal/guest)
  users.
- ``"authenticated"``: any authenticated user except external users (EXT-1
  rule 2 — externals get no "authenticated"-tier entitlement).
- ``"role_based"``: the caller needs at least one role in common with the
  workflow's assigned roles (``WorkflowRole``).

This predicate is the one place the rule is written down. It backs both
save-time validation (``api/src/services/agent_write_policy.py::validate_user_tool_access``,
which rejects an agent referencing a tool the caller can't use) and the tool
list a non-admin sees (``WorkflowRepository.list_tools_for_filter``), so the
two can never drift.
"""

from uuid import UUID


def user_can_access_workflow(
    *,
    access_level: str,
    is_external: bool,
    user_role_ids: set[UUID],
    workflow_role_ids: set[UUID],
) -> bool:
    """True if a user with these roles can use this workflow as a tool.

    Callers with scope bypass (platform admin) should skip this predicate
    entirely rather than pass through it — it only expresses the non-bypass
    rule.
    """
    if access_level == "everyone":
        return True
    if access_level == "authenticated":
        return not is_external
    if access_level == "role_based":
        return bool(workflow_role_ids & user_role_ids)
    return False

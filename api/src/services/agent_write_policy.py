"""Authorization and reference-validation policy for Agent create/update.

Business rule: a client-supplied field is either applied or explicitly
refused (403/422) — never silently dropped or overridden. This module is
the single place that enforces that rule for the Agent write surface
(``routers/agents.py``), keeping the router itself a thin HTTP handler.

Two independent gates:

* **Privileged / budget fields** — a non-admin caller may not set
  ``system_tools``, ``knowledge_sources``, ``delegated_agent_ids``,
  ``role_ids``, ``mcp_connection_ids`` (create and update), ``clear_roles``
  (update), or the budget fields ``llm_max_tokens`` / ``max_iterations`` /
  ``max_token_budget`` (create and update). Setting one of these to a
  non-empty/non-null value raises 403 naming every offending field in one
  message.
* **Reference validation** — every reference field (``tool_ids``,
  ``delegated_agent_ids``, ``role_ids``, ``mcp_connection_ids``) is checked
  for existence, activity, type, and org-membership regardless of caller
  (admin included), raising a single 422 with a structured error list.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

from src.core.db_deps import DbSession
from src.models.contracts.agents import AgentCreate, AgentUpdate
from src.models.orm import Agent, MCPConnection, Role, Workflow

#: Fields a non-admin caller may never set to a non-empty/non-null value.
PRIVILEGED_LIST_FIELDS: tuple[str, ...] = (
    "system_tools",
    "knowledge_sources",
    "delegated_agent_ids",
    "role_ids",
    "mcp_connection_ids",
)

#: Fields a non-admin caller may never set (create or update).
BUDGET_FIELDS: tuple[str, ...] = (
    "llm_max_tokens",
    "max_iterations",
    "max_token_budget",
)


def _budget_fields_set(agent_data: AgentCreate | AgentUpdate) -> list[str]:
    return [
        field_name
        for field_name in BUDGET_FIELDS
        if field_name in agent_data.model_fields_set
        and getattr(agent_data, field_name) is not None
    ]


def enforce_non_admin_create(agent_data: AgentCreate, *, caller_org_id: UUID | None) -> None:
    """Enforce the non-admin ``create_agent`` gates (spec Part 2, create 1-4).

    Raises ``HTTPException`` (403) and mutates nothing — callers apply the
    forced values (own org, empty privileged lists) themselves after this
    passes, so a field is either accepted as-is or refused, never rewritten
    behind the caller's back.
    """

    from src.models.contracts.agents import AgentAccessLevel

    # 1. access_level gate (checked first, as today).
    if agent_data.access_level != AgentAccessLevel.PRIVATE:
        raise HTTPException(403, "Non-admin users can only create private agents")

    # 2. Privileged list fields: non-empty is refused; empty/omitted is a
    # no-op (it already equals the forced value).
    privileged_fields = [
        field_name
        for field_name in PRIVILEGED_LIST_FIELDS
        if getattr(agent_data, field_name)
    ]

    # 3. Budget fields: create has no legitimate non-admin use, so any
    # explicitly-set non-null value is refused (new gate — create
    # previously had none).
    privileged_fields.extend(_budget_fields_set(agent_data))

    if privileged_fields:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only platform administrators can set Agent fields: "
                + ", ".join(privileged_fields)
            ),
        )

    # 4. organization_id: only the caller's own org (or omitted) is allowed.
    # An explicit null (global) or a foreign org UUID is refused rather than
    # silently overridden.
    if (
        "organization_id" in agent_data.model_fields_set
        and agent_data.organization_id != caller_org_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Non-admin users can only create agents in their own organization",
        )


def enforce_non_admin_update(agent: Agent, agent_data: AgentUpdate) -> None:
    """Enforce the non-admin ``update_agent`` gates (spec Part 2, update 5-6).

    Assumes the budget-fields gate, ownership gate, and access-level gate
    (rules already in the router, unchanged in order) have already passed.
    """

    # A non-null privileged field (including an explicit ``[]``) is refused
    # for update — unlike create, an explicit ``[]`` here is still a
    # deliberate mutation (it clears existing grants), not a no-op.
    privileged_fields = [
        field_name
        for field_name in PRIVILEGED_LIST_FIELDS
        if getattr(agent_data, field_name) is not None
    ]
    if agent_data.clear_roles:
        privileged_fields.append("clear_roles")

    if privileged_fields:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only platform administrators can set Agent fields: "
                + ", ".join(privileged_fields)
            ),
        )

    # New gap fix: a non-admin may never move their private agent to
    # another org (or to global) — previously applied unguarded.
    if (
        "organization_id" in agent_data.model_fields_set
        and agent_data.organization_id != agent.organization_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Non-admin users cannot move an agent to another organization",
        )


def needs_admin_to_create(agent_data: AgentCreate, *, caller_org_id: UUID | None) -> bool:
    """Whether a non-admin would be refused this create by the gates above:
    what the platform-admin branch unlocks (report-only use)."""
    try:
        enforce_non_admin_create(agent_data, caller_org_id=caller_org_id)
    except HTTPException:
        return True
    return False


def needs_admin_to_update(agent: Agent, agent_data: AgentUpdate) -> bool:
    """Whether the owner of a private agent would be refused this update:
    budget fields, a change of access level, privileged fields or a move
    (report-only use; the router's owner gates, without raising)."""
    from src.models.contracts.agents import AgentAccessLevel

    if any(field_name in agent_data.model_fields_set for field_name in BUDGET_FIELDS):
        return True
    if agent_data.access_level is not None and agent_data.access_level != AgentAccessLevel.PRIVATE:
        return True
    try:
        enforce_non_admin_update(agent, agent_data)
    except HTTPException:
        return True
    return False


async def validate_agent_references(
    db: DbSession,
    *,
    tool_ids: list[str] | None,
    delegated_agent_ids: list[str] | None,
    role_ids: list[str] | None,
    mcp_connection_ids: list[str] | None,
    organization_id: UUID | None,
    agent_id: UUID | None = None,
    clear_roles: bool = False,
) -> None:
    """Validate every Agent reference field, raising a single 422.

    Applies to every caller, admins included:

    - ``tool_ids``: each must reference an existing, active, ``type="tool"``
      Workflow.
    - ``delegated_agent_ids``: each must reference an existing, active
      Agent (and not the Agent being created/updated — no self-delegation).
    - ``role_ids``: each must reference an existing ``Role`` row.
    - ``mcp_connection_ids``: a global Agent (``organization_id is None``)
      may not carry any connection; each connection must exist and belong
      to ``organization_id``.
    - Every list also rejects duplicate entries.
    - ``clear_roles=True`` together with a non-null ``role_ids`` is
      contradictory (update only; ``clear_roles`` doesn't exist on create).
    """

    errors: list[str] = []

    for field_name, values in (
        ("tool_ids", tool_ids),
        ("delegated_agent_ids", delegated_agent_ids),
        ("role_ids", role_ids),
        ("mcp_connection_ids", mcp_connection_ids),
    ):
        if not values:
            continue
        seen: set[str] = set()
        for value in values:
            normalized = str(value)
            if normalized in seen:
                errors.append(f"{field_name} contains duplicate reference '{normalized}'")
            seen.add(normalized)

    if tool_ids:
        for tool_id in tool_ids:
            try:
                workflow_uuid = UUID(tool_id)
                result = await db.execute(select(Workflow).where(Workflow.id == workflow_uuid))
                workflow = result.scalar_one_or_none()
                if workflow is None:
                    errors.append(f"tool_id '{tool_id}' does not reference an existing workflow")
                elif not workflow.is_active:
                    errors.append(f"tool_id '{tool_id}' references an inactive workflow")
                elif workflow.type != "tool":
                    errors.append(f"tool_id '{tool_id}' references a {workflow.type}, not a tool")
            except ValueError:
                errors.append(f"tool_id '{tool_id}' is not a valid UUID")

    if delegated_agent_ids:
        for delegate_id in delegated_agent_ids:
            try:
                delegate_uuid = UUID(delegate_id)
                if agent_id and delegate_uuid == agent_id:
                    errors.append(f"Agent cannot delegate to itself ('{delegate_id}')")
                    continue
                result = await db.execute(select(Agent).where(Agent.id == delegate_uuid))
                delegate = result.scalar_one_or_none()
                if delegate is None:
                    errors.append(
                        f"delegated_agent_id '{delegate_id}' does not reference an existing agent"
                    )
                elif not delegate.is_active:
                    errors.append(
                        f"delegated_agent_id '{delegate_id}' references an inactive agent"
                    )
            except ValueError:
                errors.append(f"delegated_agent_id '{delegate_id}' is not a valid UUID")

    if role_ids:
        for role_id in role_ids:
            try:
                role_uuid = UUID(role_id)
                result = await db.execute(select(Role.id).where(Role.id == role_uuid))
                if result.scalar_one_or_none() is None:
                    errors.append(f"role_id '{role_id}' does not reference an existing role")
            except ValueError:
                errors.append(f"role_id '{role_id}' is not a valid UUID")

    if mcp_connection_ids:
        if organization_id is None:
            errors.append("Global Agents cannot be granted organization MCP connections")
        else:
            for connection_id in mcp_connection_ids:
                try:
                    connection_uuid = (
                        connection_id
                        if isinstance(connection_id, UUID)
                        else UUID(str(connection_id))
                    )
                except ValueError:
                    errors.append(f"mcp_connection_id '{connection_id}' is not a valid UUID")
                    continue
                result = await db.execute(
                    select(MCPConnection).where(MCPConnection.id == connection_uuid)
                )
                connection = result.scalar_one_or_none()
                if connection is None:
                    errors.append(
                        f"mcp_connection_id '{connection_id}' does not reference an existing connection"
                    )
                elif connection.organization_id != organization_id:
                    errors.append(
                        f"mcp_connection_id '{connection_id}' belongs to a different organization"
                    )

    if clear_roles and role_ids is not None:
        errors.append("clear_roles and role_ids cannot be provided together")

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": errors, "message": "Invalid agent references"},
        )


__all__ = [
    "BUDGET_FIELDS",
    "PRIVILEGED_LIST_FIELDS",
    "enforce_non_admin_create",
    "enforce_non_admin_update",
    "validate_agent_references",
]

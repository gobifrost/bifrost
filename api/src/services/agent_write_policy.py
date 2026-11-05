"""Authorization and reference-validation policy for Agent create/update.

Business rule: a client-supplied field is either applied or explicitly
refused (403/422) — never silently dropped or overridden. This module is
the single place that enforces that rule for the Agent write surface
(``routers/agents.py``), keeping the router itself a thin HTTP handler.

Two independent gates:

* **Privileged / budget fields** — only a caller holding
  ``agents.readwrite`` at the Agent's actual organization may set
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

from dataclasses import replace
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.scope_resolver import UNSET, RequestedScope
from shared.resource_access import ResourceAccessResolver, ResourceKind, can_write_resource
from src.core.db_deps import DbSession
from src.models.contracts.agents import AgentCreate, AgentUpdate
from src.models.orm import Agent, MCPConnection, Role, Workflow
from src.repositories.workflows import WorkflowRepository
from src.services.authorization.enforce import Caller


def caller_user_id(caller: Caller) -> UUID | None:
    """The original user for a direct or verified runtime caller."""
    return None if caller.ctx is None else caller.ctx.user_id


def agent_query_in_reach(caller: Caller):
    """Filter Agent candidates by original reach before loading or paging."""
    from src.services.authorization.enforce import user_reach

    query = select(Agent)
    reach = user_reach(caller).where(Agent.organization_id)
    return query if reach is None else query.where(reach)


async def can_read_agent(
    db: AsyncSession,
    caller: Caller,
    agent: Agent,
    *,
    selected_organization_id: RequestedScope = UNSET,
) -> bool:
    """Use the common resource resolver, including private Agent policy."""
    return await ResourceAccessResolver(db, caller).can_read(
        ResourceKind.AGENT,
        agent,
        selected_organization_id=selected_organization_id,
    )


def can_write_agent(caller: Caller, agent: Agent) -> bool:
    """Use the common resolver at the Agent's persisted organization."""
    return can_write_resource(ResourceKind.AGENT, caller, agent)


def can_configure_agent(caller: Caller, agent: Agent) -> bool:
    """Whether the caller may set an Agent's privileged configuration.

    Owning a private Agent remains sufficient for ordinary personal edits.
    Configuration that affects capabilities, role sharing, or execution
    budgets requires ``agents.readwrite`` at the persisted organization.  A
    different person's private Agent remains protected by its additional
    ``agents.readwrite.all`` resource gate.
    """
    from src.services.authorization.enforce import allows_permission

    return can_write_agent(caller, agent) and allows_permission(
        caller, "agents.readwrite", agent.organization_id
    )

#: Fields an ordinary private-Agent owner may never set to a non-empty/non-null value.
PRIVILEGED_LIST_FIELDS: tuple[str, ...] = (
    "system_tools",
    "knowledge_sources",
    "delegated_agent_ids",
    "role_ids",
    "mcp_connection_ids",
)

#: Fields an ordinary private-Agent owner may never set (create or update).
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


def enforce_personal_agent_create(agent_data: AgentCreate, *, caller_org_id: UUID | None) -> None:
    """Enforce the ordinary user's personal ``create_agent`` path.

    Raises ``HTTPException`` (403) and mutates nothing — callers apply the
    forced values (own org, empty privileged lists) themselves after this
    passes, so a field is either accepted as-is or refused, never rewritten
    behind the caller's back.
    """

    from src.models.contracts.agents import AgentAccessLevel

    # 1. access_level gate (checked first, as today).
    if agent_data.access_level != AgentAccessLevel.PRIVATE:
        raise HTTPException(403, "Personal users can only create private agents")

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
                "You need permission to manage agents to set Agent fields: "
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
            detail="Personal agents can only be created in your own organization",
        )


def enforce_personal_agent_update(agent: Agent, agent_data: AgentUpdate) -> None:
    """Enforce ordinary edits to a caller-owned private Agent.

    The router has checked ownership and access level. Privileged
    configuration, including clearing a budget, requires management access.
    """

    budget_fields = [
        field_name
        for field_name in BUDGET_FIELDS
        if field_name in agent_data.model_fields_set
    ]
    if budget_fields:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You need permission to manage agents to change budget fields: "
                + ", ".join(budget_fields)
            ),
        )

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
                "You need permission to manage agents to set Agent fields: "
                + ", ".join(privileged_fields)
            ),
        )

    # An ordinary private-Agent owner may never move their Agent to
    # another org (or to global) — previously applied unguarded.
    if (
        "organization_id" in agent_data.model_fields_set
        and agent_data.organization_id != agent.organization_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Personal agents cannot move to another organization",
        )


async def validate_user_tool_access(
    db: DbSession,
    caller: Caller,
    tool_ids: list[str],
) -> None:
    """Validate the original caller can attach every requested tool.

    Tool attachment is configuration, never workflow execution.  Resolve
    each tool through the canonical workflow resource policy using the
    original caller with runtime powers removed, so a workflow's temporary
    capability grant cannot configure a personal Agent.
    """
    if not tool_ids:
        return
    if caller.ctx is None or caller_user_id(caller) is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have role access to tools")

    configuration_caller = Caller(
        replace(caller.principal, runtime_authority=None),
        caller.ctx,
    )
    repository = WorkflowRepository(
        db,
        configuration_caller.ctx.home_organization_id,
        caller=configuration_caller,
    )

    for tool_id in tool_ids:
        try:
            workflow_uuid = UUID(tool_id)
        except ValueError:
            raise HTTPException(422, f"Invalid tool ID: {tool_id}")

        workflow = await repository.get(id=workflow_uuid)
        if workflow is None:
            raise HTTPException(403, f"You do not have role access to tool '{tool_id}'")
        if not workflow.is_active:
            raise HTTPException(422, f"Tool '{workflow.name}' is inactive")
        if workflow.type != "tool":
            raise HTTPException(422, f"Workflow '{workflow.name}' is not a tool")


async def may_use_tools(db: AsyncSession, user_id: UUID, tool_ids: list[str]) -> bool:
    """Re-evaluate a stored user-owned tool note from current DB authority.

    Access-check replay has no request actor. It constructs the original
    person's current authorization context, rather than accepting an actor
    flag or bypass boolean.
    """
    from sqlalchemy.exc import NoResultFound

    from src.core.principal import UserPrincipal
    from src.services.authorization.context import build_authorization_context
    from src.services.authorization.enforce import Caller

    try:
        ctx = await build_authorization_context(db, user_id)
    except NoResultFound:
        return False
    caller = Caller(
        UserPrincipal(
            user_id=user_id,
            email="access-check-replay",
            organization_id=ctx.home_organization_id,
        ),
        ctx,
    )
    try:
        await validate_user_tool_access(db, caller, tool_ids)
    except HTTPException:
        return False
    return True


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
    "can_configure_agent",
    "enforce_personal_agent_create",
    "enforce_personal_agent_update",
    "validate_agent_references",
    "validate_user_tool_access",
]

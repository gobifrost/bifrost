"""Compute unified setup status for a Solution install.

Covers two requirement kinds:
- config declarations (SolutionConfigSchema): is_set = a Config value exists in
  the install's org scope (NULL org == global install).
- connection declarations (SolutionConnectionSchema): is_set = a GLOBAL
  Integration with that name exists. has_oauth (template carried OAuth) is
  warn-only and never gates setup_complete.

setup_complete = all required configs set AND all declared integrations exist.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solutions import SolutionSetupItem, SolutionSetupStatus
from src.models.orm.config import Config
from src.models.orm.integrations import Integration
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow


async def compute_setup_status(db: AsyncSession, solution: Solution) -> SolutionSetupStatus:
    decls = (
        await db.execute(
            select(SolutionConfigSchema)
            .where(SolutionConfigSchema.solution_id == solution.id)
            .order_by(SolutionConfigSchema.position)
        )
    ).scalars().all()

    # Mirror the pattern used in the /entities endpoint: match Config rows by
    # key in the install's org scope (NULL org == global install). Scope the
    # query to only the declared keys — cheaper and semantically tighter on the
    # install write path.
    set_keys: set[str] = set()
    if decls:
        org_pred = (
            Config.organization_id == solution.organization_id
            if solution.organization_id is not None
            else Config.organization_id.is_(None)
        )
        set_keys_q = (
            select(Config.key)
            .where(org_pred)
            .where(Config.key.in_([d.key for d in decls]))
        )
        set_keys = set((await db.execute(set_keys_q)).scalars().all())

    items = [
        SolutionSetupItem(
            key=d.key,
            type=d.type,
            required=d.required,
            is_set=d.key in set_keys,
            description=d.description,
            default=d.default,
            kind="config",
        )
        for d in decls
    ]

    # Connection declarations: an item is_set purely when a GLOBAL Integration
    # with that name exists (integrations are global — no org filter). has_oauth
    # is a warn-only flag; connected is informational. Neither gates completion.
    conn_decls = (
        await db.execute(
            select(SolutionConnectionSchema)
            .where(SolutionConnectionSchema.solution_id == solution.id)
            .order_by(SolutionConnectionSchema.position)
        )
    ).scalars().all()
    if conn_decls:
        names = [d.integration_name for d in conn_decls]
        existing = set(
            (
                await db.execute(
                    select(Integration.name).where(Integration.name.in_(names))
                )
            ).scalars().all()
        )
        for d in conn_decls:
            items.append(
                SolutionSetupItem(
                    key=d.integration_name,
                    type="integration",
                    required=True,
                    is_set=d.integration_name in existing,
                    description=None,
                    kind="connection",
                    has_oauth=bool((d.template or {}).get("oauth")),
                    connected=False,
                )
            )

    # Endpoint workflows are externally callable when endpoint_enabled=True. If
    # they are not public, the existing auth model requires an active per-workflow
    # API key. There is intentionally no separate manifest auth declaration.
    endpoint_workflows = (
        await db.execute(
            select(Workflow)
            .where(Workflow.solution_id == solution.id)
            .where(Workflow.is_active == True)  # noqa: E712
            .where(Workflow.endpoint_enabled == True)  # noqa: E712
            .where(Workflow.public_endpoint.is_not(True))
            .order_by(Workflow.name)
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for wf in endpoint_workflows:
        key_active = (
            bool(wf.api_key_hash)
            and bool(wf.api_key_enabled)
            and (wf.api_key_expires_at is None or wf.api_key_expires_at > now)
        )
        items.append(
            SolutionSetupItem(
                key=str(wf.id),
                type="workflow_endpoint_key",
                required=True,
                is_set=key_active,
                description="Generate an API key before external callers can use this endpoint.",
                kind="workflow_endpoint_key",
                workflow_id=str(wf.id),
                workflow_name=wf.display_name or wf.name,
                allowed_methods=wf.allowed_methods or ["POST"],
            )
        )

    complete = (
        all(i.is_set for i in items if i.kind == "config" and i.required)
        and all(i.is_set for i in items if i.kind == "connection")
        and all(i.is_set for i in items if i.kind == "workflow_endpoint_key")
    )
    return SolutionSetupStatus(setup_complete=complete, items=items)


async def recompute_and_persist_setup_complete(
    db: AsyncSession, solution: Solution
) -> SolutionSetupStatus:
    """Recompute setup status and mirror it onto the Solution row.

    Setup values such as config secrets and workflow endpoint keys are
    instance-owned runtime state. Whenever a setup mutation knows the owning
    install, it should call this so list/detail surfaces do not keep showing the
    stale install-time setup flag.
    """
    status = await compute_setup_status(db, solution)
    solution.setup_complete = status.setup_complete
    await db.flush()
    return status

"""Execution context construction for form-owned workflow launches."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from shared.run_lineage import RunLineage
from shared.unattended_execution import create_unattended_context
from src.sdk.context import ExecutionContext, Organization


async def create_form_execution_context(
    db: Any,
    *,
    user: Any,
    lineage: RunLineage,
    execution_id: str,
    organization_id: UUID | None,
    is_platform_admin: bool,
    startup: Any | None = None,
    form_inputs: dict[str, Any] | None = None,
) -> ExecutionContext:
    """Build a form context without letting an embed token become its actor."""
    if user.embed:
        context = await create_unattended_context(
            db,
            lineage=lineage,
            execution_id=execution_id,
            org_id=str(organization_id) if organization_id else None,
            is_function_key=False,
        )
    else:
        organization = (
            Organization(id=str(organization_id), name="", is_active=True)
            if organization_id
            else None
        )
        context = ExecutionContext(
            user_id=str(user.user_id),
            name=user.name,
            email=user.email,
            scope=str(organization_id) if organization_id else "GLOBAL",
            organization=organization,
            is_platform_admin=is_platform_admin,
            is_function_key=False,
            execution_id=execution_id,
        )
    context.startup = startup
    context.form_inputs = form_inputs or {}
    context.embed = user.verified_context or {}
    return context

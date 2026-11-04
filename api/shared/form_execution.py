"""Execution context construction for form-owned workflow launches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from shared.run_lineage import RunLineage
from shared.scope_resolver import (
    UNSET,
    RequestedScope,
    ScopeNotAllowed,
    resolve_effective_scope,
)
from shared.unattended_execution import create_unattended_context
from src.sdk.context import ExecutionContext, Organization


class FormExecutionScopeError(Exception):
    """A form execution scope request that cannot be honored."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class FormExecutionScope:
    """The one organization that every stage of a form launch must use."""

    organization_id: UUID | None
    caller: Any | None


async def resolve_form_execution_scope(
    db: Any,
    *,
    user: Any,
    form_organization_id: UUID | None,
    requested_organization_id: RequestedScope = UNSET,
    embed_organization_id: UUID | None = None,
    caller: Any | None = None,
) -> FormExecutionScope:
    """Resolve and authorize the selected organization for a form launch.

    Global forms may execute in any organization the authenticated caller can
    reach and run workflows in. Org-scoped forms are fixed to their persisted
    organization. Embed capabilities are resource-bound and cannot select an
    alternate target.
    """
    from src.services.authorization.enforce import load_caller, require_permission

    if user.embed:
        if requested_organization_id is not UNSET:
            raise FormExecutionScopeError(
                403, "Embedded form sessions cannot select an organization"
            )
        return FormExecutionScope(form_organization_id or embed_organization_id, None)

    if (
        form_organization_id is not None
        and requested_organization_id is not UNSET
        and requested_organization_id != form_organization_id
    ):
        raise FormExecutionScopeError(
            422, "Organization does not match this form's fixed organization"
        )

    if caller is None:
        caller = await load_caller(db, user)

    if form_organization_id is None:
        try:
            organization_id = resolve_effective_scope(
                caller=caller, requested_scope=requested_organization_id
            )
        except ScopeNotAllowed as exc:
            raise FormExecutionScopeError(403, str(exc)) from None
    else:
        organization_id = form_organization_id
    require_permission(caller, "workflows.execute", organization_id)
    return FormExecutionScope(organization_id, caller)


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

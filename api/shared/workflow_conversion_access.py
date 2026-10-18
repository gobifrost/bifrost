"""Explain recorded requirements that a new User-base identity would lose.

Uses the same recorded evidence as Recommended Access. It never grants reach
or role membership, and recorded absence cannot prove a workflow needs none.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.access_checks import ALL_ORGS
from src.models.orm.workflows import Workflow
from src.models.orm.audit import AuditLog, named_workflow_id
from src.services.recommended_access import CheckedAction, _recorded_actions


def source_identity_requirements(
    analysis: dict[str, Any], organization_id: UUID | None
) -> list[str]:
    """Account for source scope selectors even on unobserved SDK branches."""
    from shared.workflow_conversion_sdk import sdk_call_contract

    scope_fields = {"scope", "organization_id", "org_id"}
    scope_overrides = {
        call
        for call in analysis.get("sdk_calls", [])
        for contract in (sdk_call_contract(call),)
        if contract is not None and contract.effect == "scope_override"
    }
    scope_can_change = bool(scope_overrides)
    requirements: set[str] = set()
    described_calls = {context["call"] for context in analysis.get("resource_calls", [])}
    for call in scope_overrides - described_calls:
        requirements.add(f"Source SDK scope override target is unavailable for {call}")
    for context in analysis.get("resource_calls", []):
        call = context["call"]
        contract = sdk_call_contract(call)
        if contract is None or not scope_fields.intersection(contract.parameters):
            continue
        selectors = context.get("selectors", {})
        if call.startswith("bifrost.files.") and selectors.get("location") == "workspace":
            continue
        label = f"{context['path']}:{context['line']}"
        dynamic = scope_fields.intersection(context.get("dynamic_selectors", []))
        if dynamic:
            requirements.add(f"{label}: Source SDK organization reach is determined at runtime")
            continue
        explicit = scope_fields.intersection(selectors)
        if scope_can_change and not explicit:
            requirements.add(f"{label}: Source SDK scope can change within the workflow")
        for field in explicit:
            target = selectors[field]
            # Event emission normalizes its historical uppercase sentinel
            # before invoking the shared, case-sensitive scope resolver.
            if call == "bifrost.events.emit" and target == "GLOBAL":
                target = "global"
            if target in (None, "", "global"):
                continue
            try:
                target_org = UUID(str(target))
            except ValueError:
                requirements.add(f"{label}: Source SDK organization selector needs a bounded reach contract")
                continue
            if target_org != organization_id:
                requirements.add(
                    f"{label}: Source SDK access reaches organization {target_org}; a new identity in this workflow's organization does not"
                )
    return sorted(requirements)


async def recorded_permission_requirements(
    db: AsyncSession, workflow_id: UUID, *, window_days: int
) -> list[str]:
    """Use successful recorded permission decisions, including elevated branches.

    A route template cannot identify permissions selected by request data,
    such as reading another user's private run. The recorded decision can.
    Both compact and expanded traces retain the same reached-step facts.
    """
    from src.models.contracts.permissions import parse_permission

    rows = await db.stream_scalars(
        select(AuditLog.details).where(
            AuditLog.action == "access.check",
            named_workflow_id() == str(workflow_id),
            AuditLog.created_at >= datetime.now(timezone.utc) - timedelta(days=window_days),
            AuditLog.outcome == "success",
        )
    )
    permissions: set[str] = set()
    async for details in rows:
        if not isinstance(details, dict):
            continue
        trace = details.get("trace") or {}
        for step in trace.get("steps", []):
            if step.get("key") not in {"permission", "secret", "privileged_target"} or step.get("status") != "passed":
                continue
            permission = (step.get("facts") or {}).get("permission")
            if not isinstance(permission, str):
                continue
            # The closed vocabulary excludes wildcard authority and stale or
            # malformed audit facts from becoming declaration candidates.
            try:
                parse_permission(permission)
            except ValueError:
                continue
            permissions.add(permission)
    await rows.close()
    return sorted(permissions)


def new_identity_requirements(
    actions: list[CheckedAction], organization_id: UUID | None
) -> list[str]:
    requirements: set[str] = set()
    for action in actions:
        if action.kind in {"scope_switch", "child_run"}:
            if action.target == ALL_ORGS:
                requirements.add(
                    "Observed access spans all organizations; a new User-base identity does not have that reach"
                )
            elif isinstance(action.target, UUID) and action.target != organization_id:
                requirements.add(
                    f"Observed access reaches organization {action.target}; a new identity in this workflow's organization does not"
                )
        # Recorded ``missing`` policy facts collect references in every rule,
        # including unused OR branches and different actions. They cannot prove
        # a role or claim is required. Resource checks resolve current policies
        # and evaluate the prospective identity per selected action instead.
    return sorted(requirements)


async def recorded_identity_requirements(
    db: AsyncSession,
    workflow: Workflow,
    *,
    window_days: int,
) -> list[str]:
    from shared.run_lineage import unattended_lineage

    lineage = await unattended_lineage(db, workflow.id)
    actions, _runs = await _recorded_actions(
        db,
        workflow.id,
        datetime.now(timezone.utc) - timedelta(days=window_days),
        run_user_id=lineage.run_user_id,
    )
    return new_identity_requirements(actions, workflow.organization_id)

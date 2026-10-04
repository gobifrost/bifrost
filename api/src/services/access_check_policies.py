"""Table and file policies evaluated again against the run's user (report-only).

Today a run's table and file calls are evaluated against the execution
credential. The model evaluates them against the run's user: their own roles
and claims, with the admin predicate true when the user is a Platform Admin
or the workflow is Full. Each helper notes the model's outcome, today's, and
what the run user is missing (roles named by ``has_role``, claims that
resolved to nothing). Helpers do nothing outside a run's request and never
raise into it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.claims.preresolve import preresolve_for_policies
from shared.claims.registry import referenced_claim_names
from shared.policies.probe import evaluate_action
from shared.role_cache import get_user_roles
from src.core.principal import UserPrincipal
from src.models.contracts.policies import TablePolicies
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.models.orm.organizations import Organization
from src.models.orm.tables import Table
from src.models.orm.users import User
from src.services.authorization.explain import load_powers, load_run_user

_PRINCIPAL = "run_user_principal"


async def _run_user_principal(db: AsyncSession, collector: access_checks.Collector) -> UserPrincipal | None:
    """The run user as a policy principal, loaded once per request; None when
    the request carries no run user or the user is gone (the writer records
    the gap)."""
    if _PRINCIPAL in collector.cache:
        return collector.cache[_PRINCIPAL]
    principal = None
    run_user = None if collector.run_user_id is None else await load_run_user(db, collector.run_user_id)
    if run_user is not None:
        powers = await load_powers(db, collector.workflow_id)
        email, is_external, is_provider = (
            await db.execute(
                select(User.email, User.is_external, Organization.is_provider)
                .outerjoin(Organization, Organization.id == User.organization_id)
                .where(User.id == run_user.user_id)
            )
        ).one()
        role_ids, role_names = await get_user_roles(run_user.user_id, db)
        principal = UserPrincipal(
            user_id=run_user.user_id,
            email=email,
            organization_id=run_user.home,
            # The policy admin predicate: a Platform Admin, or a Full workflow.
            is_superuser=run_user.is_platform_admin or powers.mode is WorkflowPermissionMode.FULL,
            is_provider_org=bool(is_provider),
            is_external=bool(is_external),
            role_ids=list(role_ids),
            role_names=list(role_names),
        )
    collector.cache[_PRINCIPAL] = principal
    return principal


def _has_role_args(node: Any, found: set[str]) -> None:
    node = getattr(node, "root", node)
    if isinstance(node, dict):
        if node.get("call") == "has_role":
            found.update(arg for arg in node.get("args", []) if isinstance(arg, str))
        for value in node.values():
            _has_role_args(value, found)
    elif isinstance(node, list):
        for value in node:
            _has_role_args(value, found)


def _missing(policies: TablePolicies | Any, principal: UserPrincipal) -> list[str]:
    roles: set[str] = set()
    claims: set[str] = set()
    for policy in policies.policies:
        when = getattr(policy, "when", None)
        if when is not None:
            _has_role_args(when, roles)
            claims |= referenced_claim_names(when)
    held = set(principal.role_names) | {str(role_id) for role_id in principal.role_ids}
    resolved = getattr(principal, "claims", None) or {}
    return [f"role:{name}" for name in sorted(roles - held)] + [
        f"claim:{name}" for name in sorted(claims) if resolved.get(name) in (None, [])
    ]


async def _policy_principal(
    db: AsyncSession, collector: access_checks.Collector, policies: Any, org_id: UUID | None, solution_id: UUID | None
) -> UserPrincipal | None:
    base = await _run_user_principal(db, collector)
    if base is None:
        return None
    # Claims are resolved per organization; never reuse them across tables.
    principal = replace(base)
    await preresolve_for_policies(principal, policies, db, org_id, solution_id)
    return principal


async def check_table_write(
    db: AsyncSession,
    action: str,
    table: Table,
    rows: list[dict[str, Any]],
    policies: TablePolicies,
    *,
    allowed_today: bool,
) -> None:
    """A row-level policy decision (create, delete, reading one row, or an
    update's pre- and post-image): allowed when every row passes."""
    collector = access_checks.current()
    if collector is None:
        return
    facts = {"table": str(table.id), "action": action, "today": allowed_today}
    try:
        principal = await _policy_principal(db, collector, policies, table.organization_id, table.solution_id)
        if principal is None:
            access_checks.note("policy", table.organization_id, **facts)
            return
        model = all(evaluate_action(action, policies, row, principal) for row in rows)
        access_checks.note(
            "policy", table.organization_id, **facts, model=model, missing=_missing(policies, principal)
        )
    except Exception as exc:
        access_checks.note_failure("policy", table.organization_id, exc)


async def check_table_rows(
    db: AsyncSession, table: Table, policies: TablePolicies, rows: list[dict[str, Any]]
) -> None:
    """A query's returned rows: how many the run user would not see."""
    collector = access_checks.current()
    if collector is None:
        return
    facts = {"table": str(table.id), "action": "read", "today": True}
    try:
        principal = await _policy_principal(db, collector, policies, table.organization_id, table.solution_id)
        if principal is None:
            access_checks.note("policy", table.organization_id, **facts)
            return
        hidden = sum(1 for row in rows if not evaluate_action("read", policies, row, principal))
        access_checks.note(
            "policy",
            table.organization_id,
            **facts,
            model=hidden == 0,
            missing=_missing(policies, principal),
            hidden=hidden,
            returned=len(rows),
        )
    except Exception as exc:
        access_checks.note_failure("policy", table.organization_id, exc)


async def check_file(
    db: AsyncSession,
    action: str,
    *,
    organization_id: UUID | None,
    location: str,
    path: str,
    solution_id: UUID | None,
    allowed_today: bool,
) -> None:
    """A file policy decision."""
    collector = access_checks.current()
    if collector is None:
        return
    from src.services.file_policy_service import FilePolicyService

    facts = {"location": location, "path": path, "action": action, "today": allowed_today}
    try:
        principal = await _run_user_principal(db, collector)
        if principal is None:
            access_checks.note("policy", organization_id, **facts)
            return
        model = await FilePolicyService(db).is_allowed(
            action,  # type: ignore[arg-type]
            organization_id=organization_id,
            location=location,
            path=path,
            user=replace(principal),
            solution_id=solution_id,
        )
        access_checks.note("policy", organization_id, **facts, model=model, missing=[])
    except Exception as exc:
        access_checks.note_failure("policy", organization_id, exc)

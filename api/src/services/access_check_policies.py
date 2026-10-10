"""Table and file policies evaluated again against the run's user (report-only).

Today a run's table and file calls are evaluated against the execution
credential. The model evaluates them against the run's user: their own roles
and claims, with the admin predicate true when the user is a Platform Admin
or the workflow is Full. Each helper notes the model's outcome, today's, and
what the run user is missing (roles named by ``has_role``, claims that
resolved to nothing). Helpers do nothing outside a run's request (a person's
own policies already decide for them) and never raise into it.
"""

from __future__ import annotations

from collections.abc import Callable
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


async def load_policy_principal(
    db: AsyncSession, run_user_id: UUID, workflow_id: UUID | None
) -> UserPrincipal | None:
    """``run_user_id`` as a policy principal; None when the user is gone."""
    run_user = await load_run_user(db, run_user_id)
    if run_user is None:
        return None
    powers = await load_powers(db, workflow_id)
    email, is_external, is_provider = (
        await db.execute(
            select(User.email, User.is_external, Organization.is_provider)
            .outerjoin(Organization, Organization.id == User.organization_id)
            .where(User.id == run_user.user_id)
        )
    ).one()
    role_ids, role_names = await get_user_roles(run_user.user_id, db)
    return UserPrincipal(
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


async def _run_user_principal(db: AsyncSession, collector: access_checks.Collector) -> UserPrincipal | None:
    """The run user as a policy principal, loaded once per request; None when
    the request carries no run user or the user is gone (the writer records
    the gap)."""
    if _PRINCIPAL in collector.cache:
        return collector.cache[_PRINCIPAL]
    principal = (
        None
        if collector.run_user_id is None
        else await load_policy_principal(db, collector.run_user_id, collector.workflow_id)
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
    """The run user with claims resolved for ``org_id``/``solution_id``,
    cached per request: claims depend on the organization and Solution."""
    key = f"policy_principal:{org_id}:{solution_id}"
    if key in collector.cache:
        principal = collector.cache[key]
    else:
        base = await _run_user_principal(db, collector)
        principal = None if base is None else replace(base)
        collector.cache[key] = principal
    if principal is not None:
        await preresolve_for_policies(principal, policies, db, org_id, solution_id)
    return principal


async def check_table_rule(
    db: AsyncSession,
    table: Table,
    policies: TablePolicies,
    *,
    action: str,
    allowed_today: bool,
    model_allows: Callable[[UserPrincipal], dict[str, Any]],
) -> None:
    """Note a table policy decision for the run user: ``model_allows`` returns
    the model's facts (``model`` and anything else worth keeping) for a
    principal. Runs in a savepoint so a failed query never affects the
    request's transaction."""
    collector = access_checks.current()
    if collector is None or collector.direct:
        return
    facts = {"table": str(table.id), "action": action, "today": allowed_today}
    try:
        async with db.begin_nested():
            principal = await _policy_principal(db, collector, policies, table.organization_id, table.solution_id)
        if principal is None:
            access_checks.note("policy", table.organization_id, **facts)
            return
        access_checks.note(
            "policy",
            table.organization_id,
            **facts,
            **model_allows(principal),
            missing=_missing(policies, principal),
        )
    except Exception as exc:
        access_checks.note_failure("policy", table.organization_id, exc)


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
    await check_table_rule(
        db,
        table,
        policies,
        action=action,
        allowed_today=allowed_today,
        model_allows=lambda principal: {
            "model": all(evaluate_action(action, policies, row, principal) for row in rows)
        },
    )


async def check_table_rows(
    db: AsyncSession, table: Table, policies: TablePolicies, rows: list[dict[str, Any]]
) -> None:
    """A query's returned rows: how many the run user would not see."""

    def hidden(principal: UserPrincipal) -> dict[str, Any]:
        count = sum(1 for row in rows if not evaluate_action("read", policies, row, principal))
        return {"model": count == 0, "hidden": count, "returned": len(rows)}

    await check_table_rule(db, table, policies, action="read", allowed_today=True, model_allows=hidden)


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
    """A file policy decision, in a savepoint like the table checks."""
    collector = access_checks.current()
    if collector is None or collector.direct:
        return
    from src.services.file_policy_service import FilePolicyService

    facts = {
        "location": location,
        "path": path,
        "action": action,
        "solution_id": str(solution_id) if solution_id else None,
        "today": allowed_today,
    }
    try:
        async with db.begin_nested():
            principal = await _policy_principal(db, collector, None, organization_id, solution_id)
            model = (
                None
                if principal is None
                else await FilePolicyService(db).is_allowed(
                    action,  # type: ignore[arg-type]
                    organization_id=organization_id,
                    location=location,
                    path=path,
                    user=principal,
                    solution_id=solution_id,
                )
            )
        if model is None:
            access_checks.note("policy", organization_id, **facts)
            return
        access_checks.note("policy", organization_id, **facts, model=model, missing=[])
    except Exception as exc:
        access_checks.note_failure("policy", organization_id, exc)

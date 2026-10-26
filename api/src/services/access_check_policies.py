"""Policy principals and enforced policy-decision recording.

Live table and file checks evaluate one policy principal. The recorder keeps
that exact outcome and its missing role/claim diagnostics before a mutation;
it never evaluates the policy again. ``load_policy_principal`` remains for
the historical "Test Again Now" reader.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from shared.claims.registry import referenced_claim_names
from shared.role_cache import get_user_roles
from src.core.principal import UserPrincipal
from src.models.contracts.policies import FilePolicies, TablePolicies
from src.models.orm.executions import Execution
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.models.orm.organizations import Organization
from src.models.orm.tables import Table
from src.models.orm.users import User
from src.services.authorization.explain import check_policy, load_powers, load_run_user


async def load_policy_principal(
    db: AsyncSession, run_user_id: UUID, execution_id: UUID | None
) -> UserPrincipal | None:
    """``run_user_id`` as a policy principal; None when the user is gone.

    Execution policy authority comes from the execution row, not the token's
    workflow lineage claim. A missing execution has no workflow authority.
    """
    run_user = await load_run_user(db, run_user_id)
    if run_user is None:
        return None
    execution = None if execution_id is None else await db.get(Execution, execution_id)
    powers = (
        None
        if execution is None or execution.workflow_id is None
        else await load_powers(db, execution.workflow_id)
    )
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
        # The policy admin predicate is role membership, never workflow powers.
        is_superuser=PLATFORM_ADMIN_ROLE_ID in role_ids,
        is_provider_org=bool(is_provider),
        is_external=bool(is_external),
        role_ids=list(role_ids),
        role_names=list(role_names),
        policy_authorities=(
            frozenset({"platform_admin"})
            if powers is not None and powers.mode is WorkflowPermissionMode.FULL
            else frozenset()
        ),
    )


async def enforced_policy_principal(
    db: AsyncSession, principal: UserPrincipal
) -> UserPrincipal:
    """Return the original user facts used to enforce table/file policies.

    A typed runtime credential is authenticated as a transport identity, but
    policy predicates always see the durable runtime's run user. Full workflow
    authority is represented only by the policy authority token; it never
    invents role membership or changes the user's admin fact.
    """
    runtime = getattr(principal, "runtime_authority", None)
    if runtime is None:
        return principal

    full = (
        runtime.kind in {"workflow_execution", "service_attempt"}
        and runtime.powers is not None
        and runtime.powers.mode is WorkflowPermissionMode.FULL
    )
    enforced = await load_policy_user_principal(
        db,
        runtime.run_user.user_id,
        frozenset({"platform_admin"}) if full else frozenset(),
    )
    if enforced is None:
        raise LookupError("the runtime's run user no longer exists")
    return enforced


async def load_policy_user_principal(
    db: AsyncSession, user_id: UUID, policy_authorities: frozenset[str]
) -> UserPrincipal | None:
    """``user_id``'s current policy facts with ``policy_authorities``; None when gone.

    The same facts :func:`enforced_policy_principal` builds for a runtime's
    run user, for deferred work (a ``files.move`` job) that enforces a policy
    for the user who requested it.
    """
    row = (
        await db.execute(
            select(User.organization_id, User.email, User.is_external, Organization.is_provider)
            .outerjoin(Organization, Organization.id == User.organization_id)
            .where(User.id == user_id)
        )
    ).one_or_none()
    if row is None:
        return None
    home, email, is_external, is_provider = row
    role_ids, role_names = await get_user_roles(user_id, db)
    return UserPrincipal(
        user_id=user_id,
        email=email,
        organization_id=home,
        is_superuser=PLATFORM_ADMIN_ROLE_ID in role_ids,
        is_provider_org=bool(is_provider),
        is_external=bool(is_external),
        role_ids=list(role_ids),
        role_names=list(role_names),
        policy_authorities=policy_authorities,
    )


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


def _missing(policies: TablePolicies | FilePolicies, principal: UserPrincipal) -> list[str]:
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


async def _record_policy(
    db: AsyncSession,
    *,
    organization_id: UUID | None,
    policies: TablePolicies | FilePolicies,
    principal: UserPrincipal,
    allowed: bool,
    facts: dict[str, Any],
) -> None:
    """Record the already-enforced policy outcome without re-evaluating it."""
    collector = access_checks.current()
    if collector is None or collector.direct:
        return
    if collector.run_user_id is None:
        access_checks.note("policy", organization_id, enforced=True, **facts)
        return
    # Loaded once per request: a bulk call (a 500-item files.move enqueue)
    # records up to three file decisions per item.
    if access_checks.POLICY_RECORD_SUBJECT_CACHE_KEY not in collector.cache:
        loaded_user = await load_run_user(db, collector.run_user_id)
        loaded_powers = (
            None
            if loaded_user is None or collector.workflow_id is None
            else await load_powers(db, collector.workflow_id)
        )
        collector.cache[access_checks.POLICY_RECORD_SUBJECT_CACHE_KEY] = (
            loaded_user,
            loaded_powers,
        )
    run_user, powers = collector.cache[access_checks.POLICY_RECORD_SUBJECT_CACHE_KEY]
    if run_user is None:
        access_checks.note("policy", organization_id, enforced=True, **facts)
        return
    access_checks.note_decision(
        "policy",
        organization_id,
        run_user=run_user,
        trace=check_policy(
            run_user,
            powers,
            today=allowed,
            model=allowed,
            missing=_missing(policies, principal),
        ),
        **facts,
    )


async def record_table_policy(
    db: AsyncSession,
    table: Table,
    policies: TablePolicies,
    principal: UserPrincipal,
    *,
    action: str,
    allowed: bool,
    hidden: int | None = None,
    returned: int | None = None,
) -> None:
    """Record a table decision that the caller has already enforced."""
    facts: dict[str, Any] = {
        "table": str(table.id),
        "action": action,
        "today": allowed,
        "model": allowed,
        "missing": _missing(policies, principal),
    }
    if hidden is not None:
        facts["hidden"] = hidden
    if returned is not None:
        facts["returned"] = returned
    await _record_policy(
        db,
        organization_id=table.organization_id,
        policies=policies,
        principal=principal,
        allowed=allowed,
        facts=facts,
    )


async def record_file_policy(
    db: AsyncSession,
    *,
    organization_id: UUID | None,
    location: str,
    path: str,
    solution_id: UUID | None,
    action: str,
    policies: FilePolicies,
    principal: UserPrincipal,
    allowed: bool,
) -> None:
    """Record a file decision that the caller has already enforced."""
    facts = {
        "location": location,
        "path": path,
        "action": action,
        "solution_id": str(solution_id) if solution_id else None,
        "today": allowed,
        "model": allowed,
        "missing": _missing(policies, principal),
    }
    await _record_policy(
        db,
        organization_id=organization_id,
        policies=policies,
        principal=principal,
        allowed=allowed,
        facts=facts,
    )

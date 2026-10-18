"""Read-only resource-policy checks for a proposed dedicated identity.

SDK transport mapping identifies an operation, not the resources its policies
admit. Resolve bounded source selectors through the ordinary metadata resolvers
and check the prospective principal without Full workflow authority. No runtime
credential, execution, identity, role, or policy is created by this preview.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
from shared.file_access import (
    FileServiceError,
    ctx_solution_id,
    file_org_id,
    install_org_id,
    resolve_effective_scope,
)
from shared.policy_rules import PolicyRuleDomainMismatch, PolicyRuleNotFound, resolve_policy_refs
from shared.table_resolution import assert_solution_write_targets_owned_table, get_table_or_404
from shared.workflow_conversion_sdk import (
    SdkRouteCandidate,
    recorded_route_candidates,
    sdk_call_contract,
    sdk_route_candidates,
)
from src.core.auth import ExecutionContext
from src.core.constants import PROVIDER_ORG_ID
from src.core.principal import UserPrincipal
from src.models.contracts.access_list import AccessClass
from src.models.contracts.policies import Action, FileAction, FilePolicies
from src.models.contracts.workflow_permissions import WorkflowGrant, WorkflowPermissionMode
from src.models.orm.workflows import Workflow
from src.repositories.policy_rule import PolicyRuleRepository
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import RuntimeAuthority
from src.services.file_policy_service import FilePolicyService
from src.services.table_policy_loader import load_resolved_table_policies


_PROSPECTIVE_ID = UUID(int=0)
_KNOWN_USER_FIELDS = frozenset({
    "organization_id", "is_platform_admin", "is_provider_org", "is_external",
    "role_ids", "role_names",
})


@dataclass(frozen=True)
class ResourceRequirements:
    permissions: tuple[str, ...]
    unknowns: tuple[str, ...]


def _prospective_context(
    db: AsyncSession, workflow: Workflow, permissions: list[str]
) -> ExecutionContext:
    """Describe a hypothetical execution solely for read-only resolution.

    The resolvers consume typed original-user reach and caller-install facts.
    A synthetic ID never represents an authenticated user or durable execution;
    predicates using its future ID/email remain unknown in policy analysis.
    """
    authorization = AuthorizationContext(
        user_id=_PROSPECTIVE_ID,
        home_organization_id=workflow.organization_id,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=frozenset(USER_BASE_PERMISSIONS),
    )
    principal = UserPrincipal(
        user_id=_PROSPECTIVE_ID,
        email="",
        organization_id=workflow.organization_id,
        is_provider_org=workflow.organization_id == PROVIDER_ORG_ID,
    )
    principal.runtime_authority = RuntimeAuthority(
        kind="workflow_execution",
        credential_id=_PROSPECTIVE_ID,
        run_user=RunUser(_PROSPECTIVE_ID, authorization, "custom", is_active=True),
        powers=Powers(
            WorkflowPermissionMode.RESTRICTED,
            tuple(WorkflowGrant(permission=value) for value in permissions),
        ),
        acting_user_id=_PROSPECTIVE_ID,
        organization_id=workflow.organization_id,
        workflow_id=workflow.id,
        started_by_user_id=None,
        root_execution_id=None,
        solution_id=workflow.solution_id,
    )
    return ExecutionContext(
        user=principal,
        org_id=workflow.organization_id,
        db=db,
        solution_id=str(workflow.solution_id) if workflow.solution_id else None,
    )


def _table_actions(route: SdkRouteCandidate) -> tuple[Action, ...]:
    if not route.path.startswith("/api/tables/") or "/documents" not in route.path:
        return ()
    suffix = route.path.split("/documents", 1)[1]
    if route.method == "GET" or suffix == "/query":
        return ("read",)
    if route.method == "DELETE" or suffix == "/batch-delete":
        return ("delete",)
    if suffix in {"/upsert", "/bulk-upsert"} or (
        suffix == "/batch" and route.call.rsplit(".", 1)[-1] in {"upsert_batch", "bulk_upsert"}
    ):
        return ("create", "update")
    if route.method in {"PATCH", "PUT"}:
        return ("update",)
    if route.method == "POST":
        return ("create",)
    return ()


def _file_action(route: SdkRouteCandidate, selectors: dict[str, Any]) -> FileAction | None:
    if route.path == "/api/files/signed-url":
        signed_actions: dict[str, FileAction] = {"GET": "read", "PUT": "write"}
        method = selectors.get("method")
        return signed_actions.get(method) if isinstance(method, str) else None
    actions: dict[str, FileAction] = {
        "/api/files/read": "read",
        "/api/files/stat": "read",
        "/api/files/exists": "read",
        "/api/files/write": "write",
        "/api/files/delete": "delete",
        "/api/files/list": "list",
    }
    return actions.get(route.path)


async def _solution_target_requirements(
    db: AsyncSession, workflow: Workflow, analysis: dict[str, Any]
) -> set[str]:
    """Check explicit non-file/table install targets without emitting or running."""
    from src.services.solution_scope import check_inbound_allowed, resolve_solution_ref

    requirements: set[str] = set()
    policy_calls = {
        route.call for route in sdk_route_candidates(analysis.get("sdk_calls", []))
        if route.access_class == AccessClass.TABLE_POLICY
    }
    for resource in analysis.get("resource_calls", []):
        call = resource["call"]
        # These selectors pass through the resource resolvers below, including
        # their original-caller reach and inbound-install gates.
        if call in policy_calls:
            continue
        selectors = resource.get("selectors", {})
        dynamic = set(resource.get("dynamic_selectors", []))
        label = f"{resource['path']}:{resource['line']}"
        if dynamic & {"solution", "solution_id"}:
            requirements.add(f"{label}: Source SDK Solution target is determined at runtime")
            continue
        target_ref = selectors.get("solution", selectors.get("solution_id"))
        if not target_ref:
            continue
        if call not in {"bifrost.events.emit", "bifrost.workflows.execute"}:
            requirements.add(f"{label}: Source SDK Solution target needs an admission contract")
            continue
        # Organization uncertainty is reported by source_identity_requirements;
        # do not guess which namespace a slug would resolve inside.
        if dynamic & {"scope", "organization_id", "org_id"}:
            continue
        selected_org = workflow.organization_id
        for field in ("scope", "organization_id", "org_id"):
            if field not in selectors:
                continue
            scope = selectors[field]
            if call == "bifrost.events.emit" and scope == "GLOBAL":
                scope = "global"
            if scope in (None, ""):
                continue
            if scope == "global":
                selected_org = None
                continue
            try:
                selected_org = UUID(str(scope))
            except ValueError:
                break
        else:
            target_id = await resolve_solution_ref(db, str(target_ref), selected_org)
            if target_id is not None and await check_inbound_allowed(
                db, target_id, workflow.solution_id
            ):
                continue
            requirements.add(f"{label}: Source SDK Solution target is unavailable to this workflow")
    return requirements


async def source_resource_requirements(
    db: AsyncSession,
    workflow: Workflow,
    analysis: dict[str, Any],
    permissions: list[str],
    observed_operations: list[str],
) -> ResourceRequirements:
    """Return precise unresolved resource requirements; never grant authority."""
    from shared.workflow_conversion_policy import (
        analyze_file_policies,
        analyze_table_policies,
    )

    routes_by_call: dict[str, list[SdkRouteCandidate]] = defaultdict(list)
    scope_can_change = any(
        contract is not None and contract.effect == "scope_override"
        for call in analysis.get("sdk_calls", [])
        for contract in (sdk_call_contract(call),)
    )
    for route in sdk_route_candidates(analysis.get("sdk_calls", [])):
        if route.access_class == AccessClass.TABLE_POLICY:
            routes_by_call[route.call].append(route)
    requirements = await _solution_target_requirements(db, workflow, analysis)
    resource_permissions: set[str] = set()
    source_routes = {
        (route.method, route.path)
        for routes in routes_by_call.values()
        for route in routes
    }
    for route in recorded_route_candidates(observed_operations):
        if route.access_class == AccessClass.TABLE_POLICY and (route.method, route.path) not in source_routes:
            requirements.add(f"Resource policy target is unavailable for observed operation {route.call}")
    covered_calls: set[str] = set()
    for resource in analysis.get("resource_calls", []):
        call = resource["call"]
        routes = routes_by_call.get(call)
        if not routes:
            continue
        covered_calls.add(call)
        label = f"{resource['path']}:{resource['line']}"
        selectors = resource.get("selectors", {})
        dynamic = set(resource.get("dynamic_selectors", []))
        workspace_file = call.startswith("bifrost.files.") and selectors.get("location") == "workspace"
        if not workspace_file and scope_can_change and selectors.get("scope") is None:
            requirements.add(f"{label}: Resource policy scope can change within the workflow")
            continue
        if not workspace_file and dynamic & {"scope", "solution", "solution_id", "organization_id", "org_id"}:
            requirements.add(f"{label}: Resource policy scope is determined at runtime")
            continue
        ctx = _prospective_context(db, workflow, permissions)
        target_solution = selectors.get("solution", selectors.get("solution_id"))
        if target_solution is not None:
            ctx.solution_id = str(target_solution)
        scope = selectors.get("scope")
        if scope is not None and not isinstance(scope, str):
            requirements.add(f"{label}: Resource policy scope is not a valid static selector")
            continue
        for route in routes:
            table_actions = _table_actions(route)
            if table_actions:
                table_ref = selectors.get("table", selectors.get("table_id"))
                if not isinstance(table_ref, str) or dynamic & {"table", "table_id"}:
                    requirements.add(f"{label}: Resource policy table is determined at runtime")
                    continue
                try:
                    table = await get_table_or_404(ctx, table_ref, scope)
                    if any(action != "read" for action in table_actions):
                        await assert_solution_write_targets_owned_table(ctx, table)
                except HTTPException as exc:
                    if exc.status_code not in {403, 404, 422}:
                        raise
                    requirements.add(f"{label}: Resource policy table cannot be resolved within the proposed identity's reach")
                    continue
                policies = await load_resolved_table_policies(table, db)
                for action in table_actions:
                    result = analyze_table_policies(
                        policies, action, ctx.user, known_user_fields=_KNOWN_USER_FIELDS
                    )
                    if result.kind != "allowed":
                        requirements.add(
                            f"{label}: Resource policy table {table.name!r} {action} is {result.kind} for the proposed identity"
                        )
                continue
            action = _file_action(route, selectors)
            path, location = selectors.get("path"), selectors.get("location")
            if action is None:
                requirements.add(f"{label}: Resource policy operation needs a resource contract")
                continue
            if location == "workspace" and "location" not in dynamic:
                if ctx.solution_id is not None:
                    requirements.add(f"{label}: Resource policy workspace files are unavailable within a Solution execution")
                else:
                    resource_permissions.add(
                        "repository.read" if action in {"read", "list"} else "repository.readwrite"
                    )
                continue
            if not isinstance(path, str) or not isinstance(location, str) or dynamic & {"path", "location"}:
                requirements.add(f"{label}: Resource policy file target is determined at runtime")
                continue
            try:
                await resolve_effective_scope(ctx, location, scope)
                solution_id = ctx_solution_id(ctx, location)
                org_id = (
                    await install_org_id(ctx, solution_id)
                    if solution_id else await file_org_id(ctx, location, scope)
                )
            except FileServiceError:
                requirements.add(f"{label}: Resource policy file is outside the proposed identity's reach")
                continue
            policy_row = await FilePolicyService(db).load_policy(
                organization_id=org_id, location=location, path=path, solution_id=solution_id
            )
            policies = FilePolicies()
            if policy_row is not None:
                try:
                    policies = FilePolicies.model_validate(policy_row.policies)
                    await resolve_policy_refs(
                        policies,
                        repo=PolicyRuleRepository(db, org_id=org_id),
                        action_domain="file",
                    )
                except (ValidationError, PolicyRuleNotFound, PolicyRuleDomainMismatch):
                    requirements.add(f"{label}: Resource policy file rules cannot be resolved")
                    continue
            result = analyze_file_policies(
                policies, action, ctx.user,
                known_user_fields=_KNOWN_USER_FIELDS,
                known_file_fields={"path": path, "location": location},
            )
            if result.kind != "allowed":
                requirements.add(f"{label}: Resource policy file {action} is {result.kind} for the proposed identity")
    for call in routes_by_call.keys() - covered_calls:
        requirements.add(f"Resource policy target is unavailable for SDK call {call}")
    return ResourceRequirements(
        permissions=tuple(sorted(resource_permissions)),
        unknowns=tuple(sorted(requirements)),
    )

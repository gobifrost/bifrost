"""Canonical authorization capabilities shared by every Bifrost surface.

Capabilities answer *what* an actor may do. Role-assignment boundaries answer
*where* a human capability applies, and resource grants/policies answer *which
object*. Reach is therefore never encoded in a capability name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


PLATFORM_SUPERUSER_SCOPE = "platform.superuser"

BUILDER_READ_SCOPE = "builder.read"
BUILDER_EXECUTE_SCOPE = "builder.execute"

SOLUTIONS_READ_SCOPE = "solutions.read"
SOLUTIONS_READWRITE_SCOPE = "solutions.readwrite"
SOLUTIONS_BUILD_EXECUTE_SCOPE = "solutions.build.execute"
SOLUTIONS_DEPLOY_EXECUTE_SCOPE = "solutions.deploy.execute"
SOLUTIONS_PUBLISH_READ_SCOPE = "solutions.publish.read"
SOLUTIONS_PUBLISH_EXECUTE_SCOPE = "solutions.publish.execute"

REPOSITORY_READ_SCOPE = "repository.read"
REPOSITORY_READWRITE_SCOPE = "repository.readwrite"
REPOSITORY_ACCESS_READ_SCOPE = "repository.access.read"
REPOSITORY_ACCESS_READWRITE_SCOPE = "repository.access.readwrite"

TABLE_DOCUMENTS_READ_SCOPE = "tabledocuments.read"
TABLE_DOCUMENTS_WRITE_SCOPE = "tabledocuments.readwrite"
FILE_CONTENT_READ_SCOPE = "managedfiles.read"
FILE_CONTENT_WRITE_SCOPE = "managedfiles.readwrite"
WORKFLOWS_EXECUTE_SCOPE = "workflows.execute"
EXECUTIONS_READ_SCOPE = "executions.read"
KNOWLEDGE_READ_SCOPE = "knowledge.read"
KNOWLEDGE_READWRITE_SCOPE = "knowledge.readwrite"
AUDIT_READ_SCOPE = "audit.read"
METRICS_READ_SCOPE = "metrics.read"
METRICS_READWRITE_SCOPE = "metrics.readwrite"

# A Solution app token uses the same capability language as human roles, but
# remains a separately typed principal whose Solution/app/resource binding is
# checked on every request. Possessing one of these keys never removes that
# binding or a table/file policy decision.
SOLUTION_APP_RUNTIME_SCOPES = frozenset(
    {
        TABLE_DOCUMENTS_READ_SCOPE,
        TABLE_DOCUMENTS_WRITE_SCOPE,
        FILE_CONTENT_READ_SCOPE,
        FILE_CONTENT_WRITE_SCOPE,
        WORKFLOWS_EXECUTE_SCOPE,
        EXECUTIONS_READ_SCOPE,
    }
)

_CAPABILITY_RE = re.compile(
    r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)*\."
    r"(?:read|readwrite|execute)$"
)


@dataclass(frozen=True, slots=True)
class AuthorizationScopeDefinition:
    """Code-owned metadata for one assignable authorization capability."""

    key: str
    display_name: str
    description: str
    category: str
    is_privileged: bool = False
    assignable_to_custom_roles: bool = True


def _capability(
    key: str,
    display_name: str,
    description: str,
    category: str,
    *,
    privileged: bool = False,
    assignable: bool = True,
) -> AuthorizationScopeDefinition:
    return AuthorizationScopeDefinition(
        key=key,
        display_name=display_name,
        description=description,
        category=category,
        is_privileged=privileged,
        assignable_to_custom_roles=assignable,
    )


AUTHORIZATION_SCOPE_CATALOG: tuple[AuthorizationScopeDefinition, ...] = (
    _capability(
        PLATFORM_SUPERUSER_SCOPE,
        "Full platform administration",
        "Satisfies every capability, boundary, and resource authorization check.",
        "Platform",
        privileged=True,
        assignable=False,
    ),
    _capability(
        BUILDER_READ_SCOPE,
        "Review builds",
        "Find and inspect Builder projects and sessions admitted by the active boundary and resource grants.",
        "Builder",
    ),
    _capability(
        BUILDER_EXECUTE_SCOPE,
        "Use Builder",
        "Start and continue coding sessions against an otherwise-authorized target.",
        "Builder",
        privileged=True,
    ),
    _capability(
        SOLUTIONS_READ_SCOPE,
        "Read Solutions",
        "List and inspect admitted Solutions.",
        "Solutions",
    ),
    _capability(
        SOLUTIONS_READWRITE_SCOPE,
        "Manage Solutions",
        "Create, edit, share, and delete admitted Solution source projects.",
        "Solutions",
    ),
    _capability(
        SOLUTIONS_BUILD_EXECUTE_SCOPE,
        "Build Solutions",
        "Create an immutable build from an admitted Solution revision.",
        "Solutions",
        privileged=True,
    ),
    _capability(
        SOLUTIONS_DEPLOY_EXECUTE_SCOPE,
        "Deploy Solutions",
        "Deploy an admitted Solution build, including a fenced private preview.",
        "Solutions",
        privileged=True,
    ),
    _capability(
        SOLUTIONS_PUBLISH_READ_SCOPE,
        "Review Solution publication",
        "Read the publication queue and source material admitted by the active boundary.",
        "Solutions",
    ),
    _capability(
        SOLUTIONS_PUBLISH_EXECUTE_SCOPE,
        "Publish Solutions",
        "Promote a reviewed Solution build into the active organization or Platform destination.",
        "Solutions",
        privileged=True,
    ),
    _capability(
        REPOSITORY_READ_SCOPE,
        "Read platform source",
        "Read and search source in the platform _repo workspace.",
        "Repository",
        privileged=True,
    ),
    _capability(
        REPOSITORY_READWRITE_SCOPE,
        "Manage platform source",
        "Create, change, and delete source in the platform _repo workspace.",
        "Repository",
        privileged=True,
    ),
    _capability(
        REPOSITORY_ACCESS_READ_SCOPE,
        "Review repository access",
        "Inspect runtime grants to the platform _repo workspace.",
        "Repository",
        privileged=True,
    ),
    _capability(
        REPOSITORY_ACCESS_READWRITE_SCOPE,
        "Manage repository access",
        "Delegate or revoke runtime access to the platform _repo workspace.",
        "Repository",
        privileged=True,
    ),
    _capability(
        "agents.read", "Read Agents", "List and inspect admitted Agents.", "Agents"
    ),
    _capability(
        "agents.readwrite",
        "Manage Agents",
        "Create, change, and delete admitted Agents.",
        "Agents",
    ),
    _capability(
        "agents.execute", "Run Agents", "Start an admitted Agent execution.", "Agents"
    ),
    _capability(
        "apps.read",
        "Read applications",
        "List and inspect admitted applications.",
        "Applications",
    ),
    _capability(
        "apps.readwrite",
        "Manage applications",
        "Create, change, and delete admitted applications.",
        "Applications",
    ),
    _capability(
        "apps.deploy.execute",
        "Deploy applications",
        "Build and publish an admitted application release.",
        "Applications",
        privileged=True,
    ),
    _capability(
        "forms.read", "Read forms", "List and inspect admitted forms.", "Forms"
    ),
    _capability(
        "forms.readwrite",
        "Manage forms",
        "Create, change, and delete admitted forms.",
        "Forms",
    ),
    _capability(
        "tables.read",
        "Read table definitions",
        "List and inspect admitted table definitions.",
        "Tables",
    ),
    _capability(
        "tables.readwrite",
        "Manage table definitions",
        "Create, change, and delete admitted table definitions.",
        "Tables",
    ),
    _capability(
        TABLE_DOCUMENTS_READ_SCOPE,
        "Read table documents",
        "Read table documents admitted by the active boundary, resource binding, and table policies.",
        "Table data",
    ),
    _capability(
        TABLE_DOCUMENTS_WRITE_SCOPE,
        "Manage table documents",
        "Create, change, and delete table documents admitted by the active boundary, resource binding, and table policies.",
        "Table data",
    ),
    _capability(
        "workflows.read",
        "Read workflows",
        "List and inspect admitted workflows.",
        "Workflows",
    ),
    _capability(
        "workflows.readwrite",
        "Manage workflows",
        "Create, change, and delete admitted workflows.",
        "Workflows",
    ),
    _capability(
        WORKFLOWS_EXECUTE_SCOPE,
        "Run workflows",
        "Start an admitted workflow execution.",
        "Workflows",
    ),
    _capability(
        EXECUTIONS_READ_SCOPE,
        "Read executions",
        "Inspect admitted execution status, output, and logs.",
        "Executions",
    ),
    _capability(
        "organizations.read",
        "Read organizations",
        "List and inspect organizations admitted by the active boundary.",
        "Organizations",
    ),
    _capability(
        "organizations.readwrite",
        "Manage organizations",
        "Create, change, and delete organizations admitted by the active boundary.",
        "Organizations",
        privileged=True,
    ),
    _capability(
        "organizationgroups.read",
        "Read organization groups",
        "List and inspect provider-owned organization groups.",
        "Organizations",
    ),
    _capability(
        "organizationgroups.readwrite",
        "Manage organization groups",
        "Create and maintain provider-owned organization groups and memberships.",
        "Organizations",
        privileged=True,
    ),
    _capability(
        "roles.read",
        "Read roles",
        "List roles, assignments, and effective-access explanations admitted by the active boundary.",
        "Roles",
    ),
    _capability(
        "roles.readwrite",
        "Manage roles",
        "Create and change roles, assignments, and boundary selections.",
        "Roles",
        privileged=True,
    ),
    _capability(
        "integrations.read",
        "Read integrations",
        "List and inspect admitted integrations and mappings.",
        "Integrations",
    ),
    _capability(
        "integrations.readwrite",
        "Manage integrations",
        "Create, change, and delete admitted integrations and mappings.",
        "Integrations",
    ),
    _capability(
        "configs.read",
        "Read configuration",
        "List and inspect admitted configuration definitions and values.",
        "Configuration",
    ),
    _capability(
        "configs.readwrite",
        "Manage configuration",
        "Create, change, and delete admitted configuration definitions and values.",
        "Configuration",
        privileged=True,
    ),
    _capability(
        "events.read",
        "Read events",
        "List and inspect admitted event sources and subscriptions.",
        "Events",
    ),
    _capability(
        "events.readwrite",
        "Manage events",
        "Create, change, and delete admitted event sources and subscriptions.",
        "Events",
    ),
    _capability(
        "claims.read",
        "Read custom claims",
        "List and inspect admitted custom claims.",
        "Custom claims",
    ),
    _capability(
        "claims.readwrite",
        "Manage custom claims",
        "Create, change, and delete admitted custom claims.",
        "Custom claims",
    ),
    _capability(
        FILE_CONTENT_READ_SCOPE,
        "Read managed files",
        "Read managed file data admitted by file policy.",
        "Managed files",
    ),
    _capability(
        FILE_CONTENT_WRITE_SCOPE,
        "Manage managed files",
        "Create, change, and delete managed file data admitted by file policy.",
        "Managed files",
    ),
    _capability(
        "filepolicies.read",
        "Read file policies",
        "List and inspect admitted file-policy definitions.",
        "File policies",
    ),
    _capability(
        "filepolicies.readwrite",
        "Manage file policies",
        "Create, change, and delete admitted file-policy definitions.",
        "File policies",
    ),
    _capability(
        "policyrules.read",
        "Read policy rules",
        "List and inspect admitted reusable policy-rule definitions.",
        "Policy rules",
    ),
    _capability(
        "policyrules.readwrite",
        "Manage policy rules",
        "Create, change, and delete admitted reusable policy-rule definitions.",
        "Policy rules",
    ),
    _capability(
        KNOWLEDGE_READ_SCOPE,
        "Search knowledge",
        "Search admitted knowledge namespaces.",
        "Knowledge",
    ),
    _capability(
        KNOWLEDGE_READWRITE_SCOPE,
        "Manage knowledge",
        "Create, change, and delete admitted knowledge namespaces and documents.",
        "Knowledge",
    ),
    _capability(
        AUDIT_READ_SCOPE,
        "Read the Audit Log",
        "Inspect platform audit events in the selected administrative context.",
        "Audit",
        privileged=True,
    ),
    _capability(
        METRICS_READ_SCOPE,
        "Read platform metrics",
        "Inspect platform usage, cost, ROI, and operational metrics.",
        "Metrics",
        privileged=True,
    ),
    _capability(
        METRICS_READWRITE_SCOPE,
        "Manage metric settings",
        "Change pricing and ROI settings used by platform reporting.",
        "Metrics",
        privileged=True,
    ),
    _capability(
        "platformjobs.read",
        "Read platform jobs",
        "Inspect admitted durable platform operations.",
        "Platform jobs",
    ),
    _capability(
        "platformjobs.execute",
        "Control platform jobs",
        "Cancel or retry admitted durable platform operations.",
        "Platform jobs",
        privileged=True,
    ),
)

_SCOPE_BY_KEY = {scope.key: scope for scope in AUTHORIZATION_SCOPE_CATALOG}


def is_valid_scope_key(key: str) -> bool:
    """Whether ``key`` uses the approved noun/subresource/action grammar."""

    return key == PLATFORM_SUPERUSER_SCOPE or bool(_CAPABILITY_RE.fullmatch(key))


def get_authorization_scope(key: str) -> AuthorizationScopeDefinition | None:
    """Return catalog metadata for ``key``, or ``None`` when it is unknown."""

    return _SCOPE_BY_KEY.get(key)


def implied_scopes(scopes: Iterable[str]) -> frozenset[str]:
    """Expand the sole v1 implication: ``readwrite`` implies ``read``."""

    expanded = set(scopes)
    for scope in tuple(expanded):
        if scope.endswith(".readwrite"):
            expanded.add(f"{scope.removesuffix('.readwrite')}.read")
    return frozenset(expanded)


def validate_catalog() -> None:
    """Fail fast when catalog keys are duplicated or violate naming rules."""

    keys = [scope.key for scope in AUTHORIZATION_SCOPE_CATALOG]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError("Duplicate authorization scope(s): " + ", ".join(duplicates))
    invalid = sorted(key for key in keys if not is_valid_scope_key(key))
    if invalid:
        raise ValueError("Invalid authorization scope name(s): " + ", ".join(invalid))


def validate_role_scopes(scopes: Iterable[str], *, custom_role: bool) -> list[str]:
    """Validate and normalize capabilities stored on a role or actor token."""

    normalized = sorted(set(scopes))
    unknown = [scope for scope in normalized if scope not in _SCOPE_BY_KEY]
    if unknown:
        raise ValueError(f"Unknown authorization scope(s): {', '.join(unknown)}")

    if custom_role:
        reserved = [
            scope
            for scope in normalized
            if not _SCOPE_BY_KEY[scope].assignable_to_custom_roles
        ]
        if reserved:
            raise ValueError(
                "Scope(s) reserved for Bifrost-managed roles or credentials: "
                + ", ".join(reserved)
            )

    return normalized


validate_catalog()

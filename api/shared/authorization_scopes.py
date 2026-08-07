"""Validated authorization-scope catalog.

Roles and attenuated actor tokens both consume these stable keys. Display
metadata lives here so REST, CLI, MCP, token minting, and role-management UI
cannot invent parallel permission vocabularies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


PLATFORM_SUPERUSER_SCOPE = "platform.superuser"
ORGANIZATION_IMPERSONATION_SCOPE = "organization.impersonation"
SOLUTIONS_BUILD_SCOPE = "solutions.build"
SOLUTION_BUILD_JOBS_EXECUTE_SCOPE = "solutions.jobs.execute"

TABLE_DOCUMENTS_READ_SCOPE = "tables.documents.read"
TABLE_DOCUMENTS_WRITE_SCOPE = "tables.documents.write"
FILE_CONTENT_READ_SCOPE = "files.content.read"
FILE_CONTENT_WRITE_SCOPE = "files.content.write"
WORKFLOWS_EXECUTE_SCOPE = "workflows.execute"
EXECUTIONS_READ_SCOPE = "executions.read"

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

# Graph-inspired Bifrost action-scope grammar:
# <resource>[.<subresource>].<action>[.all]
#
# Reserved system scopes are listed explicitly below because they express a
# compatibility wildcard or organization-context grant rather than an ordinary
# route action.
_ACTION_SCOPE_RE = re.compile(
    r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)*\.(?:"
    r"read|write|execute|build|publish|manage"
    r")(?:\.all)?$"
)
_RESERVED_SYSTEM_SCOPES = {
    PLATFORM_SUPERUSER_SCOPE,
    ORGANIZATION_IMPERSONATION_SCOPE,
}


@dataclass(frozen=True, slots=True)
class AuthorizationScopeDefinition:
    """Code-owned metadata for one authorization scope."""

    key: str
    display_name: str
    description: str
    category: str
    is_privileged: bool
    assignable_to_custom_roles: bool


AUTHORIZATION_SCOPE_CATALOG: tuple[AuthorizationScopeDefinition, ...] = (
    AuthorizationScopeDefinition(
        key=PLATFORM_SUPERUSER_SCOPE,
        display_name="Full platform administration",
        description=(
            "Satisfies every authorization-scope check. Business invariants "
            "such as provider eligibility and Solution ownership still apply."
        ),
        category="Platform",
        is_privileged=True,
        assignable_to_custom_roles=False,
    ),
    AuthorizationScopeDefinition(
        key=ORGANIZATION_IMPERSONATION_SCOPE,
        display_name="Work across organizations",
        description=(
            "Allows an eligible provider member to select another organization "
            "context while retaining their own identity and resource permissions."
        ),
        category="Organizations",
        is_privileged=True,
        assignable_to_custom_roles=True,
    ),
    AuthorizationScopeDefinition(
        key=SOLUTIONS_BUILD_SCOPE,
        display_name="Build Solutions",
        description=(
            "Create and modify Builder projects and private Solution source "
            "within the project boundary that separately admits the user."
        ),
        category="Solutions",
        is_privileged=True,
        assignable_to_custom_roles=True,
    ),
    AuthorizationScopeDefinition(
        key=SOLUTION_BUILD_JOBS_EXECUTE_SCOPE,
        display_name="Execute Solution build jobs",
        description=(
            "Allows a Bifrost build coordinator capability to transfer "
            "artifacts and report progress for its one bound build job."
        ),
        category="Internal services",
        is_privileged=True,
        assignable_to_custom_roles=False,
    ),
    AuthorizationScopeDefinition(
        key=TABLE_DOCUMENTS_READ_SCOPE,
        display_name="Read table documents",
        description=(
            "Read table documents admitted by the credential's organization, "
            "resource binding, and table policies."
        ),
        category="App runtime",
        is_privileged=False,
        assignable_to_custom_roles=False,
    ),
    AuthorizationScopeDefinition(
        key=TABLE_DOCUMENTS_WRITE_SCOPE,
        display_name="Write table documents",
        description=(
            "Create, update, and delete table documents admitted by the "
            "credential's resource binding and table policies."
        ),
        category="App runtime",
        is_privileged=True,
        assignable_to_custom_roles=False,
    ),
    AuthorizationScopeDefinition(
        key=FILE_CONTENT_READ_SCOPE,
        display_name="Read file content",
        description=(
            "Read and list file content admitted by the credential's declared "
            "locations, resource binding, and file policies."
        ),
        category="App runtime",
        is_privileged=False,
        assignable_to_custom_roles=False,
    ),
    AuthorizationScopeDefinition(
        key=FILE_CONTENT_WRITE_SCOPE,
        display_name="Write file content",
        description=(
            "Write and delete file content admitted by the credential's "
            "declared locations, resource binding, and file policies."
        ),
        category="App runtime",
        is_privileged=True,
        assignable_to_custom_roles=False,
    ),
    AuthorizationScopeDefinition(
        key=WORKFLOWS_EXECUTE_SCOPE,
        display_name="Execute workflows",
        description=(
            "Execute workflows admitted by the credential's resource binding "
            "and ordinary workflow access policy."
        ),
        category="App runtime",
        is_privileged=True,
        assignable_to_custom_roles=False,
    ),
    AuthorizationScopeDefinition(
        key=EXECUTIONS_READ_SCOPE,
        display_name="Read executions",
        description=(
            "Read execution status, result, and logs admitted by the "
            "credential's execution binding."
        ),
        category="App runtime",
        is_privileged=False,
        assignable_to_custom_roles=False,
    ),
)

_SCOPE_BY_KEY = {scope.key: scope for scope in AUTHORIZATION_SCOPE_CATALOG}


def is_valid_scope_key(key: str) -> bool:
    """Whether ``key`` follows the canonical grammar or is a reserved scope."""

    return key in _RESERVED_SYSTEM_SCOPES or bool(_ACTION_SCOPE_RE.fullmatch(key))


def get_authorization_scope(key: str) -> AuthorizationScopeDefinition | None:
    """Return catalog metadata for ``key``, or ``None`` when it is unknown."""

    return _SCOPE_BY_KEY.get(key)


def validate_catalog() -> None:
    """Fail fast when catalog keys are duplicated or violate naming rules."""

    keys = [scope.key for scope in AUTHORIZATION_SCOPE_CATALOG]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(
            "Duplicate authorization scope(s): " + ", ".join(duplicates)
        )
    invalid = sorted(key for key in keys if not is_valid_scope_key(key))
    if invalid:
        raise ValueError(
            "Invalid authorization scope name(s): " + ", ".join(invalid)
        )


def validate_role_scopes(
    scopes: Iterable[str], *, custom_role: bool
) -> list[str]:
    """Validate and normalize scopes stored on a role or actor credential."""

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

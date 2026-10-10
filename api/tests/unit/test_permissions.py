"""The permission grammar, the closed resource vocabulary, display names and
the privileged set (``src.models.contracts.permissions``)."""

from __future__ import annotations

import pytest

from shared.builtin_roles import (
    DECRYPTION_ROLE_PERMISSIONS,
    PLATFORM_OPERATOR_PERMISSIONS,
    USER_BASE_PERMISSIONS,
)
from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from src.models.contracts.permissions import (
    DECRYPT_PERMISSION,
    PERMISSION_DOMAINS,
    PRIVILEGED_PERMISSIONS,
    WILDCARD_EXCLUDED_PERMISSIONS,
    ParsedPermission,
    domain_display_names,
    parse_permission,
    permission_display_name,
)
from src.services.access_list import ACCESS_LIST
from src.services.operation_catalog import OPERATION_CATALOG

# Every permission a role can hold. Adding a resource, a verb, a basic view
# or a private resource changes this list; make it a deliberate edit here.
VOCABULARY = frozenset(
    {
        "agentruns.read", "agentruns.read.all", "agentruns.readwrite", "agentruns.readwrite.all",
        "agents.execute", "agents.read", "agents.read.all", "agents.readbasic",
        "agents.readwrite", "agents.readwrite.all",
        "ai.execute", "ai.read", "ai.readwrite",
        "apps.publish", "apps.read", "apps.readbasic", "apps.readwrite",
        "artifacts.read", "artifacts.read.all", "artifacts.readwrite", "artifacts.readwrite.all",
        "claims.read", "claims.readwrite",
        "configs.read", "configs.readwrite",
        "events.read", "events.readwrite",
        "executions.read", "executions.read.all", "executions.readbasic",
        "executions.readwrite", "executions.readwrite.all",
        "filepolicies.read", "filepolicies.readwrite",
        "forms.read", "forms.readbasic", "forms.readwrite",
        "home.read", "home.read.all", "home.readwrite", "home.readwrite.all",
        "integrations.read", "integrations.readwrite",
        "knowledge.read", "knowledge.readwrite",
        "mcp.read", "mcp.readbasic", "mcp.readwrite",
        "metrics.read", "metrics.readwrite",
        "organizations.read", "organizations.readwrite",
        "platform.read", "platform.readwrite",
        "platformjobs.read", "platformjobs.read.all", "platformjobs.readwrite", "platformjobs.readwrite.all",
        "policyrules.read", "policyrules.readwrite",
        "privilegedaccess.read", "privilegedaccess.readwrite",
        "reports.read", "reports.readwrite",
        "repository.read", "repository.readwrite",
        "roleassignments.read", "roleassignments.readwrite",
        "roles.read", "roles.readwrite",
        "secrets.read", "secrets.readwrite",
        "settings.read", "settings.readbasic", "settings.readwrite",
        "solutions.build", "solutions.deploy", "solutions.read", "solutions.readwrite",
        "tableattribution.read", "tableattribution.readwrite",
        "tables.read", "tables.readwrite",
        "userlifecycle.read", "userlifecycle.readwrite",
        "users.impersonate", "users.read", "users.readwrite",
        "workflows.execute", "workflows.read", "workflows.readwrite",
    }
)  # fmt: skip


def test_the_vocabulary_is_the_reviewed_list() -> None:
    allowed = {p for domain in PERMISSION_DOMAINS for p in domain_display_names(domain)}
    assert allowed == VOCABULARY


@pytest.mark.parametrize("permission", sorted(VOCABULARY))
def test_accepts_every_permission_in_the_vocabulary(permission: str) -> None:
    parse_permission(permission)


@pytest.mark.parametrize(
    ("permission", "parsed"),
    [
        ("agents.read", ParsedPermission("agents", "read", False)),
        ("agents.readbasic", ParsedPermission("agents", "readbasic", False)),
        ("workflows.execute", ParsedPermission("workflows", "execute", False)),
        ("apps.publish", ParsedPermission("apps", "publish", False)),
        ("executions.read.all", ParsedPermission("executions", "read", True)),
        ("home.readwrite.all", ParsedPermission("home", "readwrite", True)),
        ("userlifecycle.readwrite", ParsedPermission("userlifecycle", "readwrite", False)),
        ("secrets.read", ParsedPermission("secrets", "read", False)),
    ],
)
def test_parses_resource_action_and_all_suffix(permission: str, parsed: ParsedPermission) -> None:
    assert parse_permission(permission) == parsed


@pytest.mark.parametrize(
    "permission",
    [
        # A dotted resource.
        "users.lifecycle.readwrite",
        "apps.deploy.execute",
        "solutions.deploy.execute",
        # .all on a resource without private items, or after readbasic or a verb.
        "reports.read.all",
        "apps.read.all",
        "apps.readbasic.all",
        "agents.readbasic.all",
        "agents.execute.all",
        # readbasic where the resource has no basic view.
        "roles.readbasic",
        # A verb the resource doesn't declare.
        "workflows.impersonate",
        "workflows.publish",
        # Case, shape and unknown resources.
        "Agents.Read",
        "agents.READ",
        "agents.read.ALL",
        "agentsread",
        "agents",
        "agents.",
        "agents.delete",
        "agents.all",
        "agents.all.read",
        "agents.read.all.all",
        ".read",
        "not_a_domain.read",
        "*",
    ],
)
def test_rejects_anything_outside_the_grammar(permission: str) -> None:
    with pytest.raises(ValueError):
        parse_permission(permission)


@pytest.mark.parametrize(
    ("permission", "name"),
    [
        ("apps.readbasic", "Read Basic Apps"),
        ("executions.read.all", "Read All Workflow Runs"),
        ("agents.readwrite.all", "Read and Write All Agents"),
        ("users.impersonate", "Impersonate Users"),
        ("apps.publish", "Publish Apps"),
        ("userlifecycle.readwrite", "Read and Write User Lifecycle"),
        ("privilegedaccess.readwrite", "Read and Write Privileged Access"),
        ("ai.execute", "Use AI"),
        ("ai.read", "Read AI Model Information"),
        ("workflows.execute", "Run Workflows"),
        ("agents.execute", "Run Agents"),
        ("solutions.deploy", "Deploy Solutions"),
        ("solutions.build", "Build Solutions"),
        ("tables.readwrite", "Read and Write Tables"),
    ],
)
def test_display_names(permission: str, name: str) -> None:
    assert permission_display_name(permission) == name


def test_every_permission_the_platform_names_is_valid() -> None:
    named = {entry.permission for entry in ACCESS_LIST if entry.permission}
    named |= {scope for operation in OPERATION_CATALOG for scope in operation.action_scopes}
    named |= USER_BASE_PERMISSIONS | PLATFORM_OPERATOR_PERMISSIONS | DECRYPTION_ROLE_PERMISSIONS
    named |= PRIVILEGED_PERMISSIONS
    assert sorted(named - VOCABULARY) == []


def test_access_entry_accepts_the_all_suffix_on_a_private_resource() -> None:
    entry = AccessEntry(
        method="GET",
        path="/x",
        access_class=AccessClass.PERMISSION,
        current_gate=CurrentGate.SUPERUSER,
        permission="agentruns.read.all",
        boundary="organization",
        reason="test",
    )
    assert entry.permission == "agentruns.read.all"


@pytest.mark.parametrize("permission", ["apps.all", "apps.read.all", "apps.read.everything", "nope.read"])
def test_access_entry_rejects_what_the_grammar_rejects(permission: str) -> None:
    with pytest.raises(ValueError):
        AccessEntry(
            method="GET",
            path="/x",
            access_class=AccessClass.PERMISSION,
            current_gate=CurrentGate.SUPERUSER,
            permission=permission,
            boundary="organization",
            reason="test",
        )


def test_privileged_set_is_the_reviewed_list() -> None:
    # Adding or removing a privileged permission changes who is a protected
    # target; make it a deliberate edit here, not a side effect.
    assert PRIVILEGED_PERMISSIONS == {
        "users.readwrite",
        "users.impersonate",
        "userlifecycle.readwrite",
        "privilegedaccess.readwrite",
        "roles.readwrite",
        "roleassignments.readwrite",
        "organizations.readwrite",
        "secrets.read",
        "configs.readwrite",
        "integrations.readwrite",
        "settings.readwrite",
        "platform.read",
        "platform.readwrite",
        "repository.read",
        "repository.readwrite",
        "claims.readwrite",
        "filepolicies.readwrite",
        "policyrules.readwrite",
        "solutions.deploy",
        "agents.readwrite.all",
        "executions.readwrite",
        "executions.read.all",
        "executions.readwrite.all",
        "artifacts.read.all",
        "artifacts.readwrite.all",
        "tableattribution.readwrite",
        "mcp.readwrite",
    }


@pytest.mark.parametrize("permission", sorted(PRIVILEGED_PERMISSIONS))
def test_every_privileged_permission_is_valid_vocabulary(permission: str) -> None:
    parse_permission(permission)


# Privileged permissions that elevated checks inside handlers name, which no
# access-list entry checks (yet).
_PRIVILEGED_WITHOUT_AN_ENTRY = frozenset(
    {
        "users.impersonate",
        "privilegedaccess.readwrite",
        "agents.readwrite.all",
        "tableattribution.readwrite",
    }
)


def test_every_privileged_permission_gates_some_operation() -> None:
    # A privileged permission no access-list entry uses is almost certainly a
    # typo for one that does.
    used = {entry.permission for entry in ACCESS_LIST if entry.permission}
    assert PRIVILEGED_PERMISSIONS - used == _PRIVILEGED_WITHOUT_AN_ENTRY


def test_secret_decryption_is_excluded_from_the_wildcard_and_privileged() -> None:
    assert WILDCARD_EXCLUDED_PERMISSIONS == {DECRYPT_PERMISSION}
    assert WILDCARD_EXCLUDED_PERMISSIONS <= PRIVILEGED_PERMISSIONS

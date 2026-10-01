"""The permission grammar, the closed domain vocabulary, and the privileged set
(``src.models.contracts.permissions``)."""

from __future__ import annotations

import pytest

from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from src.models.contracts.permissions import (
    DECRYPT_PERMISSION,
    PERMISSION_DOMAINS,
    PRIVILEGED_PERMISSIONS,
    WILDCARD_EXCLUDED_PERMISSIONS,
    ParsedPermission,
    parse_permission,
)
from src.services.access_list import ACCESS_LIST


@pytest.mark.parametrize(
    ("permission", "parsed"),
    [
        ("agents.read", ParsedPermission("agents", "read", False)),
        ("workflows.execute", ParsedPermission("workflows", "execute", False)),
        ("apps.read.all", ParsedPermission("apps", "read", True)),
        ("forms.readwrite.all", ParsedPermission("forms", "readwrite", True)),
        ("solutions.deploy.execute", ParsedPermission("solutions.deploy", "execute", False)),
        ("users.lifecycle.readwrite", ParsedPermission("users.lifecycle", "readwrite", False)),
        ("roleassignments.readwrite", ParsedPermission("roleassignments", "readwrite", False)),
        ("reports.read.all", ParsedPermission("reports", "read", True)),
        ("secrets.read", ParsedPermission("secrets", "read", False)),
    ],
)
def test_parses_domain_action_and_extended_suffix(permission: str, parsed: ParsedPermission) -> None:
    assert parse_permission(permission) == parsed


@pytest.mark.parametrize(
    "permission",
    [
        "agentsread",
        "agents.delete",
        "agents.all",
        "agents.all.read",
        "agents.read.all.all",
        "agents.read.ALL",
        ".read",
        "users.lifecycle",
        "not_a_domain.read",
        "not_a_domain.read.all",
        "*",
    ],
)
def test_rejects_malformed_or_unknown(permission: str) -> None:
    with pytest.raises(ValueError):
        parse_permission(permission)


def test_access_entry_accepts_the_extended_suffix() -> None:
    entry = AccessEntry(
        method="GET",
        path="/x",
        access_class=AccessClass.PERMISSION,
        current_gate=CurrentGate.SUPERUSER,
        permission="apps.read.all",
        boundary="organization",
        reason="test",
    )
    assert entry.permission == "apps.read.all"


@pytest.mark.parametrize("permission", ["apps.all", "apps.read.everything", "nope.read"])
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


def test_identity_and_secret_domains_are_in_the_vocabulary() -> None:
    assert {"users", "users.lifecycle", "roleassignments", "reports", "secrets"} <= set(PERMISSION_DOMAINS)


def test_privileged_set_is_the_reviewed_list() -> None:
    # Adding or removing a privileged permission changes who is a protected
    # target; make it a deliberate edit here, not a side effect.
    assert PRIVILEGED_PERMISSIONS == {
        "users.readwrite",
        "users.lifecycle.readwrite",
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
        "solutions.deploy.execute",
        "executions.readwrite",
        "mcp.readwrite",
    }


@pytest.mark.parametrize("permission", sorted(PRIVILEGED_PERMISSIONS))
def test_every_privileged_permission_is_valid_vocabulary(permission: str) -> None:
    parse_permission(permission)


def test_every_privileged_permission_gates_some_operation() -> None:
    # A privileged permission no access-list entry uses is almost certainly a
    # typo for one that does.
    used = {entry.permission for entry in ACCESS_LIST if entry.permission}
    assert PRIVILEGED_PERMISSIONS - used == set()


def test_secret_decryption_is_excluded_from_the_wildcard_and_privileged() -> None:
    assert WILDCARD_EXCLUDED_PERMISSIONS == {DECRYPT_PERMISSION}
    assert WILDCARD_EXCLUDED_PERMISSIONS <= PRIVILEGED_PERMISSIONS

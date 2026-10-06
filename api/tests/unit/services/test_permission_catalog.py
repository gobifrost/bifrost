"""The permission catalog is derived from the access list and the closed
domain vocabulary; these tests pin each derivation rule against a hand-built
access list, plus the vocabulary's own completeness."""

from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from shared.builtin_roles import (
    DECRYPTION_ROLE_PERMISSIONS,
    PLATFORM_OPERATOR_PERMISSIONS,
    USER_BASE_PERMISSIONS,
)
from src.models.contracts.permissions import (
    PERMISSION_DOMAINS,
    parse_permission,
    permission_display_name,
)
from src.services.operation_catalog import OPERATION_CATALOG
from src.services.permission_catalog import build_catalog


def _entry(
    path: str,
    permission: str,
    boundary: str,
    gate: CurrentGate = CurrentGate.SUPERUSER,
) -> AccessEntry:
    return AccessEntry(
        method="GET",
        path=path,
        access_class=AccessClass.PERMISSION,
        current_gate=gate,
        permission=permission,
        boundary=boundary,
        reason="test",
    )


def _by_domain(access_list: list[AccessEntry]) -> dict:
    return {entry.domain: entry for entry in build_catalog(access_list)}


def test_only_organization_entries_are_per_organization() -> None:
    catalog = _by_domain([_entry("/a", "tables.read", "organization")])
    assert catalog["tables"].scope == "per_organization"


def test_only_platform_entries_are_platform_wide() -> None:
    catalog = _by_domain([_entry("/a", "settings.read", "platform")])
    assert catalog["settings"].scope == "platform_wide"


def test_both_boundaries_vary() -> None:
    catalog = _by_domain(
        [
            _entry("/a", "tables.read", "organization"),
            _entry("/b", "tables.readwrite", "platform"),
        ]
    )
    assert catalog["tables"].scope == "varies"


def test_a_domain_no_route_checks_varies() -> None:
    catalog = _by_domain([])
    assert catalog["claims"].scope == "varies"
    assert catalog["claims"].enforced is False


def test_enforced_only_when_an_entry_is_decided_by_the_evaluator() -> None:
    catalog = _by_domain(
        [
            _entry("/a", "tables.read", "organization", CurrentGate.EVALUATOR),
            _entry("/b", "forms.read", "organization", CurrentGate.SUPERUSER),
        ]
    )
    assert catalog["tables"].enforced is True
    assert catalog["forms"].enforced is False


def test_actions_union_entries_and_privileged_only_actions() -> None:
    catalog = _by_domain(
        [
            _entry("/a", "workflows.execute", "organization"),
            _entry("/b", "workflows.read", "organization"),
            _entry("/c", "workflows.read.all", "organization"),
            _entry("/d", "platform.read", "platform"),
        ]
    )
    assert catalog["workflows"].actions == ["read", "read.all", "execute"]
    # platform.readwrite is privileged but no route checks it.
    assert catalog["platform"].actions == ["read", "readwrite"]


def test_privileged_lists_the_domains_privileged_permissions() -> None:
    catalog = _by_domain([])
    assert catalog["users"].privileged == ["users.readwrite"]
    assert catalog["users.lifecycle"].privileged == ["users.lifecycle.readwrite"]
    assert catalog["platform"].privileged == ["platform.read", "platform.readwrite"]
    assert catalog["tables"].privileged == []


def test_entries_of_a_dotted_domain_do_not_count_toward_its_parent() -> None:
    catalog = _by_domain([_entry("/a", "users.lifecycle.readwrite", "platform")])
    assert catalog["users.lifecycle"].scope == "platform_wide"
    assert catalog["users"].scope == "varies"


def test_every_domain_is_present_with_a_title_and_area() -> None:
    catalog = build_catalog([])
    assert {entry.domain for entry in catalog} == set(PERMISSION_DOMAINS)
    assert all(entry.title and entry.area for entry in catalog)


def test_sorted_by_area_then_title() -> None:
    catalog = build_catalog([])
    keys = [(entry.area, entry.title) for entry in catalog]
    assert keys == sorted(keys)


def test_areas_are_title_case() -> None:
    assert {entry.area for entry in build_catalog([])} == {
        "Identity & Access",
        "Automation",
        "Data & Content",
        "Integrations & Secrets",
        "Platform",
    }


def test_names_are_verb_then_resource() -> None:
    catalog = _by_domain(
        [
            _entry("/a", "tables.read", "organization"),
            _entry("/b", "tables.readwrite", "organization"),
            _entry("/c", "agentruns.read.all", "organization"),
        ]
    )
    assert catalog["tables"].names["tables.read"] == "Read Tables"
    assert catalog["tables"].names["tables.readwrite"] == "Read and Write Tables"
    assert catalog["agentruns"].names["agentruns.read.all"] == "Read All Agent Runs"


def test_every_read_and_write_permission_is_named_whether_or_not_a_route_checks_it() -> None:
    # A role can hold any well-formed permission, not only the ones routes check.
    for entry in build_catalog([]):
        for action in ("read", "read.all", "readwrite", "readwrite.all"):
            assert f"{entry.domain}.{action}" in entry.names


def test_execute_permissions_are_named_by_their_own_verb() -> None:
    catalog = _by_domain(
        [
            _entry("/a", "workflows.execute", "organization"),
            _entry("/b", "apps.deploy.execute", "organization"),
            _entry("/c", "solutions.build.execute", "platform"),
        ]
    )
    assert catalog["workflows"].names["workflows.execute"] == "Run Workflows"
    assert catalog["apps.deploy"].names["apps.deploy.execute"] == "Publish Apps"
    assert catalog["solutions.build"].names["solutions.build.execute"] == "Build Solutions"
    assert catalog["solutions.deploy"].names["solutions.deploy.execute"] == "Deploy Solutions"


def test_user_lifecycle_names_what_it_manages() -> None:
    catalog = _by_domain([])
    assert catalog["users.lifecycle"].title == "User Lifecycle"
    assert (
        catalog["users.lifecycle"].names["users.lifecycle.readwrite"]
        == "Manage User Lifecycle (move, delete, change base role)"
    )


def test_display_name_reads_any_permission_of_a_domain() -> None:
    assert permission_display_name("executions.read") == "Read Workflow Runs"
    assert permission_display_name("secrets.read") == "Read Secret Values"
    assert permission_display_name("mcp.read.all") == "Read All MCP Servers"


def test_the_checked_in_access_list_builds_a_catalog() -> None:
    catalog = build_catalog()
    assert len(catalog) == len(PERMISSION_DOMAINS)
    assert next(e for e in catalog if e.domain == "roles").enforced is True
    # Every action the catalog lists has a name.
    for entry in catalog:
        assert {f"{entry.domain}.{action}" for action in entry.actions} <= set(entry.names)


def _named(catalog: list, permission: str) -> bool:
    domain = parse_permission(permission).domain
    return permission in next(e for e in catalog if e.domain == domain).names


def test_every_permission_a_builtin_role_holds_has_a_name() -> None:
    catalog = build_catalog()
    held = USER_BASE_PERMISSIONS | PLATFORM_OPERATOR_PERMISSIONS | DECRYPTION_ROLE_PERMISSIONS
    assert [p for p in sorted(held) if not _named(catalog, p)] == []


def test_every_execute_permission_an_operation_checks_has_a_name() -> None:
    catalog = build_catalog()
    executes = {
        scope
        for operation in OPERATION_CATALOG
        for scope in operation.action_scopes
        if parse_permission(scope).action == "execute"
    }
    assert executes
    assert [p for p in sorted(executes) if not _named(catalog, p)] == []

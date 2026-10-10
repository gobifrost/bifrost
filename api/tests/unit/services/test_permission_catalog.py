"""The permission catalog is derived from the access list and the closed
domain vocabulary; these tests pin each derivation rule against a hand-built
access list, plus the vocabulary's own completeness."""

from src.models.contracts.access_list import (
    AccessClass,
    AccessEntry,
    CurrentGate,
    InlineEffect,
)
from shared.builtin_roles import (
    DECRYPTION_ROLE_PERMISSIONS,
    PLATFORM_OPERATOR_PERMISSIONS,
    USER_BASE_PERMISSIONS,
)
from src.models.contracts.permissions import (
    PERMISSION_DOMAINS,
    domain_actions,
    parse_permission,
    permission_display_name,
)
from src.services.access_list import ACCESS_LIST
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
            _entry("/a", "agents.execute", "organization"),
            _entry("/b", "agents.read", "organization"),
            _entry("/c", "agents.read.all", "organization"),
            _entry("/d", "agents.readbasic", "organization"),
            _entry("/e", "platform.read", "platform"),
        ]
    )
    # agents.readwrite.all is privileged but no route checks it.
    assert catalog["agents"].actions == ["read", "readbasic", "execute", "read.all", "readwrite.all"]
    # platform.readwrite is privileged but no route checks it.
    assert catalog["platform"].actions == ["read", "readwrite"]


def test_a_widening_permission_on_a_personal_entry_is_listed() -> None:
    widening = AccessEntry(
        method="GET",
        path="/api/home",
        access_class=AccessClass.PERSONAL,
        current_gate=CurrentGate.AUTHENTICATED,
        inline_checks=("is_platform_admin",),
        inline_effect=InlineEffect.WIDENS_FOR_SUPERUSER,
        permission="home.read.all",
        boundary="organization",
        reason="test",
    )
    catalog = _by_domain([widening])
    assert catalog["home"].actions == ["read.all"]
    assert catalog["home"].scope == "per_organization"


def test_the_checked_in_catalog_lists_widening_permissions() -> None:
    catalog = _by_domain(ACCESS_LIST)
    assert "read.all" in catalog["home"].actions
    assert {"read.all", "readwrite.all"} <= set(catalog["platformjobs"].actions)


def test_privileged_lists_the_domains_privileged_permissions() -> None:
    catalog = _by_domain([])
    assert catalog["users"].privileged == ["users.impersonate", "users.readwrite"]
    assert catalog["userlifecycle"].privileged == ["userlifecycle.readwrite"]
    assert catalog["platform"].privileged == ["platform.read", "platform.readwrite"]
    assert catalog["tables"].privileged == []


def test_user_lifecycle_entries_do_not_count_toward_users() -> None:
    catalog = _by_domain([_entry("/a", "userlifecycle.readwrite", "platform")])
    assert catalog["userlifecycle"].scope == "platform_wide"
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


def test_every_allowed_permission_is_named_whether_or_not_a_route_checks_it() -> None:
    # A role can hold any well-formed permission, not only the ones routes check.
    for entry in build_catalog([]):
        assert set(entry.names) == {f"{entry.domain}.{action}" for action in domain_actions(entry.domain)}
        assert {f"{entry.domain}.read", f"{entry.domain}.readwrite"} <= set(entry.names)


def test_verb_permissions_are_named_by_their_own_verb() -> None:
    catalog = _by_domain(
        [
            _entry("/a", "workflows.execute", "organization"),
            _entry("/b", "apps.publish", "organization"),
            _entry("/c", "solutions.build", "platform"),
        ]
    )
    assert catalog["workflows"].names["workflows.execute"] == "Run Workflows"
    assert catalog["apps"].names["apps.publish"] == "Publish Apps"
    assert catalog["solutions"].names["solutions.build"] == "Build Solutions"
    assert catalog["solutions"].names["solutions.deploy"] == "Deploy Solutions"


def test_user_lifecycle_names_what_it_manages() -> None:
    catalog = _by_domain([])
    assert catalog["userlifecycle"].title == "User Lifecycle"
    assert (
        catalog["userlifecycle"].names["userlifecycle.readwrite"]
        == "Read and Write User Lifecycle"
    )


def test_display_name_reads_any_permission_of_a_domain() -> None:
    assert permission_display_name("executions.read") == "Read Workflow Runs"
    assert permission_display_name("secrets.read") == "Read Secret Values"
    assert permission_display_name("executions.read.all") == "Read All Workflow Runs"


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


def _is_verb(permission: str) -> bool:
    parsed = parse_permission(permission)
    return parsed.action in PERMISSION_DOMAINS[parsed.domain].verbs


def test_every_verb_permission_an_operation_checks_has_a_name() -> None:
    catalog = build_catalog()
    verbs = {
        scope
        for operation in OPERATION_CATALOG
        for scope in operation.action_scopes
        if _is_verb(scope)
    }
    assert verbs
    assert [p for p in sorted(verbs) if not _named(catalog, p)] == []


def test_users_describes_impersonate_users() -> None:
    description = _by_domain([])["users"].description
    impersonation = description.partition("to sign out. ")[2]
    assert impersonation == (
        "Impersonate Users runs a workflow or an agent as another user in an organization where the "
        "holder has this permission. Running as a user who holds privileged access also needs Manage "
        "Privileged Access."
    )

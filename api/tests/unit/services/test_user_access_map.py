"""The access map is built from an ``AuthorizationContext`` by a pure
function; each test builds the context a kind of person has and pins what the
map says about it."""

from uuid import UUID, uuid4

from shared.builtin_roles import (
    DECRYPTION_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_ROLE_ID,
)
from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from src.models.contracts.user_access import UserAccessMap
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.permission_catalog import build_catalog
from src.services.user_access_map import build_access_map

CONTOSO = uuid4()
FABRIKAM = uuid4()
HELPDESK_ROLE_ID = uuid4()

NAMES = {CONTOSO: "Contoso", FABRIKAM: "Fabrikam"}
ROLE_NAMES = {
    USER_ROLE_ID: "User",
    HELPDESK_ROLE_ID: "Helpdesk",
    PLATFORM_OPERATOR_ROLE_ID: "Platform Operator",
    PLATFORM_ADMIN_ROLE_ID: "Platform Admin",
    DECRYPTION_ROLE_ID: "Secrets Reader",
}


def _entry(path: str, permission: str, boundary: str) -> AccessEntry:
    return AccessEntry(
        method="GET",
        path=path,
        access_class=AccessClass.PERMISSION,
        current_gate=CurrentGate.EVALUATOR,
        permission=permission,
        boundary=boundary,
        reason="test",
    )


CATALOG = {
    entry.domain: entry
    for entry in build_catalog(
        [
            _entry("/a", "tables.read", "organization"),
            _entry("/b", "settings.read", "platform"),
            _entry("/c", "forms.read", "organization"),
            _entry("/d", "forms.read", "platform"),
        ]
    )
}


def _ctx(
    home: UUID | None,
    base_permissions: set[str],
    *grants: RoleGrant,
) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid4(),
        home_organization_id=home,
        base_role_id=USER_ROLE_ID,
        is_external=False,
        base_permissions=frozenset(base_permissions),
        role_grants=grants,
    )


def _build(ctx: AuthorizationContext, held: set[str] | None = None) -> UserAccessMap:
    return build_access_map(
        ctx,
        user_name="Avery Example",
        user_email="avery@example.com",
        held=frozenset(held if held is not None else ctx.held_permissions),
        names=NAMES,
        role_names=ROLE_NAMES,
        catalog=CATALOG,
    )


def _org(organization_id: UUID) -> Boundary:
    return Boundary(BoundaryKind.ORGANIZATION, organization_id)


MANAGED = Boundary(BoundaryKind.MANAGED_ORGANIZATIONS)
PLATFORM = Boundary(BoundaryKind.PLATFORM)


def _summary(access_map: UserAccessMap) -> list[tuple[str, str, list[str]]]:
    return [
        (row.place.kind, row.place.label, [g.permission for g in row.grants])
        for row in access_map.rows
    ]


def test_a_customer_has_a_home_row_and_global_in_reach_without_a_global_row() -> None:
    access_map = _build(_ctx(CONTOSO, {"tables.read", "forms.read"}))

    assert _summary(access_map) == [("home", "Contoso (home)", ["forms.read", "tables.read"])]
    assert [(p.kind, p.label) for p in access_map.reach] == [
        ("home", "Contoso (home)"),
        ("platform", "Global"),
    ]
    assert access_map.home_organization is not None
    assert (access_map.home_organization.id, access_map.home_organization.name) == (
        CONTOSO,
        "Contoso",
    )
    assert (access_map.name, access_map.email) == ("Avery Example", "avery@example.com")
    assert not access_map.is_platform_admin and not access_map.is_protected


def test_a_base_grant_names_its_role_and_how_it_is_held() -> None:
    access_map = _build(_ctx(CONTOSO, {"tables.read"}))

    [grant] = access_map.rows[0].grants
    assert [(s.role_id, s.role_name, s.via) for s in grant.sources] == [
        (USER_ROLE_ID, "User", "base")
    ]


def test_a_role_at_another_organization_adds_a_row_and_reach() -> None:
    helpdesk = RoleGrant(HELPDESK_ROLE_ID, frozenset({"tables.read"}), (_org(FABRIKAM),))
    access_map = _build(_ctx(CONTOSO, {"tables.read"}, helpdesk))

    assert _summary(access_map) == [
        ("home", "Contoso (home)", ["tables.read"]),
        ("organization", "Fabrikam", ["tables.read"]),
    ]
    fabrikam = access_map.rows[1]
    assert fabrikam.place.organization_id == FABRIKAM
    assert fabrikam.place.organization_name == "Fabrikam"
    assert [(s.role_name, s.via) for s in fabrikam.grants[0].sources] == [
        ("Helpdesk", "additional")
    ]
    assert [p.label for p in access_map.reach] == ["Contoso (home)", "Fabrikam", "Global"]


def test_the_same_permission_through_two_roles_lists_both_sources_base_first() -> None:
    helpdesk = RoleGrant(HELPDESK_ROLE_ID, frozenset({"tables.read"}), (_org(CONTOSO),))
    access_map = _build(_ctx(CONTOSO, {"tables.read"}, helpdesk))

    [row] = access_map.rows
    assert row.place.kind == "home"
    [grant] = row.grants
    assert [(s.role_name, s.via) for s in grant.sources] == [
        ("User", "base"),
        ("Helpdesk", "additional"),
    ]


def test_a_role_at_managed_organizations_is_one_row_for_all_customers() -> None:
    operator = RoleGrant(PLATFORM_OPERATOR_ROLE_ID, frozenset({"users.read"}), (MANAGED,))
    access_map = _build(_ctx(CONTOSO, set(), operator))

    assert _summary(access_map) == [
        ("managed_organizations", "All customer organizations", ["users.read"])
    ]
    assert [p.kind for p in access_map.reach] == ["home", "managed_organizations", "platform"]


def test_reach_lists_a_placement_whose_role_carries_no_permissions() -> None:
    sharing = RoleGrant(HELPDESK_ROLE_ID, frozenset(), (_org(FABRIKAM),))
    access_map = _build(_ctx(CONTOSO, set(), sharing))

    assert access_map.rows == []
    assert [p.label for p in access_map.reach] == ["Contoso (home)", "Fabrikam", "Global"]


def test_a_platform_admin_is_one_wildcard_row_for_all_organizations() -> None:
    admin = RoleGrant(PLATFORM_ADMIN_ROLE_ID, frozenset({"*"}), (PLATFORM,))
    access_map = _build(_ctx(CONTOSO, {"tables.read"}, admin))

    assert access_map.is_platform_admin and access_map.is_protected
    assert access_map.privileged_permissions == ["*"]
    [row] = access_map.rows
    assert (row.place.kind, row.place.label) == ("platform", "All organizations")
    [grant] = row.grants
    assert (grant.permission, grant.domain, grant.action) == ("*", "*", "*")
    assert [(s.role_name, s.via) for s in grant.sources] == [("Platform Admin", "additional")]
    assert access_map.reach[-1].label == "All organizations"


def test_the_wildcard_does_not_claim_secrets_but_an_explicit_grant_shows() -> None:
    admin = RoleGrant(PLATFORM_ADMIN_ROLE_ID, frozenset({"*"}), (PLATFORM,))
    without = _build(_ctx(None, set(), admin))
    assert [g.permission for row in without.rows for g in row.grants] == ["*"]

    reader = RoleGrant(
        DECRYPTION_ROLE_ID, frozenset({"secrets.read"}), (PLATFORM, MANAGED, _org(CONTOSO))
    )
    with_secrets = _build(_ctx(None, set(), admin, reader))
    assert _summary(with_secrets) == [
        ("organization", "Contoso", ["secrets.read"]),
        ("managed_organizations", "All customer organizations", ["secrets.read"]),
        ("platform", "All organizations", ["*", "secrets.read"]),
    ]


def test_a_global_user_has_a_global_base_row_and_no_home_label() -> None:
    access_map = _build(_ctx(None, {"settings.read"}))

    assert _summary(access_map) == [("platform", "Global", ["settings.read"])]
    assert access_map.home_organization is None
    assert [(p.kind, p.label) for p in access_map.reach] == [("platform", "Global")]
    assert not any("(home)" in p.label for p in access_map.reach)


def test_scope_is_copied_from_the_catalog() -> None:
    helpdesk = RoleGrant(
        HELPDESK_ROLE_ID, frozenset({"settings.read", "forms.read"}), (_org(FABRIKAM),)
    )
    access_map = _build(_ctx(CONTOSO, {"tables.read"}, helpdesk))

    scopes = {g.permission: g.scope for row in access_map.rows for g in row.grants}
    assert scopes == {
        "tables.read": "per_organization",
        "settings.read": "platform_wide",
        "forms.read": "varies",
    }


def test_domain_and_action_come_from_the_permission() -> None:
    access_map = _build(_ctx(CONTOSO, {"workflows.read.all", "users.lifecycle.readwrite"}))

    parts = {g.permission: (g.domain, g.action) for g in access_map.rows[0].grants}
    assert parts == {
        "workflows.read.all": ("workflows", "read.all"),
        "users.lifecycle.readwrite": ("users.lifecycle", "readwrite"),
    }


def test_a_privileged_holder_is_protected_and_lists_why() -> None:
    access_map = _build(_ctx(CONTOSO, {"tables.read", "configs.readwrite", "users.readwrite"}))

    assert access_map.is_protected
    assert access_map.privileged_permissions == ["configs.readwrite", "users.readwrite"]


def test_protection_counts_what_is_held_even_where_it_applies_nowhere() -> None:
    access_map = _build(_ctx(CONTOSO, {"tables.read"}), held={"tables.read", "roles.readwrite"})

    assert access_map.is_protected
    assert access_map.privileged_permissions == ["roles.readwrite"]


def test_an_unprivileged_holder_lists_nothing_privileged() -> None:
    access_map = _build(_ctx(CONTOSO, {"tables.read", "users.read"}))

    assert not access_map.is_protected
    assert access_map.privileged_permissions == []

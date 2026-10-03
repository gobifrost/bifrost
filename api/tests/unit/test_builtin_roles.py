"""Pin `shared.builtin_roles`'s frozen literals against their derivation.

`USER_BASE_PERMISSIONS` is a frozen copy the R2b migration seeds (migrations
must not import live/derived code — see the module docstring). This test
fails loudly the moment `derive_user_base_permissions(ACCESS_LIST)` would
produce something different, forcing an explicit decision: correct the
frozen copy in a new migration, or fix the access-list entry that drifted.
"""

from __future__ import annotations

from uuid import UUID

from shared.builtin_roles import (
    BASE_ROLE_IDS,
    BUILTIN_ROLE_IDS,
    DECRYPTION_ROLE_ID,
    DECRYPTION_ROLE_PERMISSIONS,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
    WILDCARD_PERMISSION,
    derive_user_base_permissions,
    is_builtin_role_id,
)
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.permissions import WILDCARD_EXCLUDED_PERMISSIONS, parse_permission
from src.services.access_list import ACCESS_LIST
from src.services.authorization.context import Boundary, BoundaryKind
from src.services.user_role_assignments import SECRETS_READER_BOUNDARIES


def test_seeded_user_permissions_match_derivation():
    assert USER_BASE_PERMISSIONS == derive_user_base_permissions(ACCESS_LIST), (
        "USER_BASE_PERMISSIONS drifted from derive_user_base_permissions(ACCESS_LIST). "
        "Either the frozen literal is stale (fix it in a NEW migration, not this one) "
        "or an access-list entry moved out of the User base role's criteria."
    )


_ALL_BUILTIN = (PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID, DECRYPTION_ROLE_ID)


def test_fixed_ids_are_distinct_and_well_known():
    assert len(set(_ALL_BUILTIN)) == 4
    for role_id in _ALL_BUILTIN:
        assert isinstance(role_id, UUID)
    # ...0003/...0004 belonged to the withdrawn Builder roles and are
    # permanently forbidden (see tests/e2e/platform/test_withdrawn_builder_migrations.py).
    forbidden = {UUID("00000000-0000-0000-0000-000000000003"), UUID("00000000-0000-0000-0000-000000000004")}
    assert not forbidden & set(_ALL_BUILTIN)


def test_base_and_builtin_sets():
    assert BASE_ROLE_IDS == {USER_ROLE_ID}
    assert PLATFORM_ADMIN_ROLE_ID not in BASE_ROLE_IDS
    assert BUILTIN_ROLE_IDS == set(_ALL_BUILTIN)
    assert PLATFORM_OPERATOR_ROLE_ID not in BASE_ROLE_IDS
    assert DECRYPTION_ROLE_ID not in BASE_ROLE_IDS


def test_is_builtin_role_id():
    assert is_builtin_role_id(PLATFORM_ADMIN_ROLE_ID)
    assert is_builtin_role_id(USER_ROLE_ID)
    assert is_builtin_role_id(PLATFORM_OPERATOR_ROLE_ID)
    assert is_builtin_role_id(DECRYPTION_ROLE_ID)
    assert not is_builtin_role_id(UUID(int=0x1234))


def test_platform_admin_wildcard_is_stored_data_in_the_admin_additional_migration():
    # Platform Admin's access is the wildcard, stored as its one
    # role_permissions row (never derived from the access list).
    migration = _load_migration("20261002_r3a_admin_additional.py")
    assert migration.WILDCARD_PERMISSION == WILDCARD_PERMISSION == "*"


def test_derived_permissions_are_read_only_organization_scoped():
    """Sanity check on the derivation's own filter, independent of the
    frozen copy: every permission it derives is a `.read` action, and the
    User role must never gain a write permission (see access_list.py's own
    'never grant a write permission to the User base role' principle)."""
    derived = derive_user_base_permissions(ACCESS_LIST)
    assert derived, "expected at least one derived permission"
    for permission in derived:
        assert permission.endswith(".read"), permission


def test_platform_operator_permissions_are_user_support_reads_and_execution():
    """The Operator role gets read visibility plus user support, role
    assignment (constrained at the cutover to permissionless roles on
    unprivileged users) and running workflows. Never secret decryption,
    elevated user lifecycle, role authoring, platform-wide permissions, or
    extended management detail."""
    writes = {p for p in PLATFORM_OPERATOR_PERMISSIONS if not p.endswith(".read")}
    assert writes == {"users.readwrite", "roleassignments.readwrite", "workflows.execute"}
    for forbidden in ("secrets.read", "users.lifecycle.readwrite", "roles.readwrite"):
        assert forbidden not in PLATFORM_OPERATOR_PERMISSIONS
    assert not any(parse_permission(p).domain == "platform" for p in PLATFORM_OPERATOR_PERMISSIONS)
    assert not any(parse_permission(p).extended for p in PLATFORM_OPERATOR_PERMISSIONS)


def test_secrets_reader_holds_only_the_wildcard_excluded_permission():
    """Secrets Reader is the explicit assignment that `secrets.read` needs:
    exactly that permission, which the Platform Admin wildcard never
    satisfies."""
    assert DECRYPTION_ROLE_PERMISSIONS == {"secrets.read"}
    assert DECRYPTION_ROLE_PERMISSIONS == WILDCARD_EXCLUDED_PERMISSIONS


def _load_migration(filename: str):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert "from shared" not in path.read_text() and "from src" not in path.read_text()
    return migration


def test_migration_frozen_copies_match_live_constants():
    """Each migration carries its own frozen copy (it must not import live
    code). The latest migration that seeds each builtin role must equal the
    live constant: `20260929_user_base_perm_fix` for the User role and
    `20261003_r3_operator_secrets` for Platform Operator and
    `20261001_r3a_operator_perms` for Secrets Reader. After a deliberate
    change to the live values, update them through a NEW migration and adjust
    this test to pin the new revision instead. The R2b migration stays pinned
    to what it seeded."""
    fix = _load_migration("20260929_user_base_perm_fix.py")
    r2b = _load_migration("20260929_r2b_roles.py")
    operator = _load_migration("20261001_r3a_operator_perms.py")
    latest = _load_migration("20261003_r3_operator_secrets.py")

    assert fix.down_revision == "20260929_r2b_wf_permissions"
    assert fix.USER_ROLE_ID == USER_ROLE_ID
    assert fix.USER_BASE_PERMISSIONS == USER_BASE_PERMISSIONS
    assert (r2b.USER_BASE_PERMISSIONS - fix.REMOVED_PERMISSIONS) | fix.ADDED_PERMISSIONS == USER_BASE_PERMISSIONS

    assert r2b.PLATFORM_ADMIN_ROLE_ID == PLATFORM_ADMIN_ROLE_ID
    assert r2b.USER_ROLE_ID == USER_ROLE_ID
    assert r2b.PLATFORM_OPERATOR_ROLE_ID == PLATFORM_OPERATOR_ROLE_ID

    admin = _load_migration("20261002_r3a_admin_additional.py")
    assert admin.down_revision == "20261001_r3a_operator_desc"
    assert admin.PLATFORM_ADMIN_ROLE_ID == PLATFORM_ADMIN_ROLE_ID
    assert admin.USER_ROLE_ID == USER_ROLE_ID

    assert operator.down_revision == "20260929_user_base_perm_fix"
    assert operator.PLATFORM_OPERATOR_ROLE_ID == PLATFORM_OPERATOR_ROLE_ID
    assert r2b.PLATFORM_OPERATOR_PERMISSIONS | operator.ADDED_PERMISSIONS == operator.PLATFORM_OPERATOR_PERMISSIONS
    assert not r2b.PLATFORM_OPERATOR_PERMISSIONS & operator.ADDED_PERMISSIONS
    assert operator.DECRYPTION_ROLE_ID == DECRYPTION_ROLE_ID
    assert operator.DECRYPTION_ROLE_PERMISSIONS == DECRYPTION_ROLE_PERMISSIONS

    assert latest.down_revision == "20261002_r3a_admin_additional"
    assert latest.PLATFORM_ADMIN_ROLE_ID == PLATFORM_ADMIN_ROLE_ID
    assert latest.PLATFORM_OPERATOR_ROLE_ID == PLATFORM_OPERATOR_ROLE_ID
    assert latest.DECRYPTION_ROLE_ID == DECRYPTION_ROLE_ID
    assert latest.PROVIDER_ORG_ID == PROVIDER_ORG_ID
    assert latest.PLATFORM_OPERATOR_PERMISSIONS == PLATFORM_OPERATOR_PERMISSIONS
    assert (
        operator.PLATFORM_OPERATOR_PERMISSIONS | latest.ADDED_OPERATOR_PERMISSIONS
        == PLATFORM_OPERATOR_PERMISSIONS
    )
    assert not operator.PLATFORM_OPERATOR_PERMISSIONS & latest.ADDED_OPERATOR_PERMISSIONS
    desc = _load_migration("20261001_r3a_operator_desc.py")
    assert latest.PREVIOUS_OPERATOR_DESCRIPTION == desc.DESCRIPTION
    assert latest.PREVIOUS_DECRYPTION_DESCRIPTION == operator.DECRYPTION_ROLE_DESCRIPTION


def test_migration_assigns_at_boundaries_the_assignment_service_allows():
    """The migration writes Secrets Reader at exactly the boundaries the
    assignment service fixes for it, and Platform Operator at all customer
    organizations."""
    latest = _load_migration("20261003_r3_operator_secrets.py")
    assert {
        Boundary(BoundaryKind(kind), UUID(organization_id) if organization_id else None)
        for kind, organization_id in latest._DECRYPTION_BOUNDARIES
    } == SECRETS_READER_BOUNDARIES
    assert latest._OPERATOR_BOUNDARIES == (("managed_organizations", None),)

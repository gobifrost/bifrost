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
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
    WILDCARD_PERMISSION,
    derive_user_base_permissions,
    is_builtin_role_id,
)
from src.services.access_list import ACCESS_LIST


def test_seeded_user_permissions_match_derivation():
    assert USER_BASE_PERMISSIONS == derive_user_base_permissions(ACCESS_LIST), (
        "USER_BASE_PERMISSIONS drifted from derive_user_base_permissions(ACCESS_LIST). "
        "Either the frozen literal is stale (fix it in a NEW migration, not this one) "
        "or an access-list entry moved out of the User base role's criteria."
    )


def test_fixed_ids_are_distinct_and_well_known():
    assert len({PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID}) == 3
    for role_id in (PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID):
        assert isinstance(role_id, UUID)


def test_base_and_builtin_sets():
    assert BASE_ROLE_IDS == {PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID}
    assert BUILTIN_ROLE_IDS == {PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID}
    assert PLATFORM_OPERATOR_ROLE_ID not in BASE_ROLE_IDS


def test_is_builtin_role_id():
    assert is_builtin_role_id(PLATFORM_ADMIN_ROLE_ID)
    assert is_builtin_role_id(USER_ROLE_ID)
    assert is_builtin_role_id(PLATFORM_OPERATOR_ROLE_ID)
    assert not is_builtin_role_id(UUID(int=0x1234))


def test_platform_admin_holds_no_stored_permission_rows():
    # Platform Admin's access is the wildcard, in code — never derived from
    # the access list and never stored as role_permissions rows.
    assert PLATFORM_ADMIN_ROLE_ID not in {}
    assert WILDCARD_PERMISSION == "*"


def test_derived_permissions_are_read_only_organization_scoped():
    """Sanity check on the derivation's own filter, independent of the
    frozen copy: every permission it derives is a `.read` action, and the
    User role must never gain a write permission (see access_list.py's own
    'never grant a write permission to the User base role' principle)."""
    derived = derive_user_base_permissions(ACCESS_LIST)
    assert derived, "expected at least one derived permission"
    for permission in derived:
        assert permission.endswith(".read"), permission


def test_platform_operator_permissions_are_read_only():
    for permission in PLATFORM_OPERATOR_PERMISSIONS:
        assert permission.endswith(".read"), permission


def test_r2b_migration_frozen_copy_matches_live_constants():
    """The R2b migration carries its own frozen copy (it must not import live
    code). Today that copy must equal the live constants; after a deliberate
    change to the live values, update them through a NEW migration and adjust
    this test to pin the new revision instead."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260929_r2b_roles.py"
    )
    spec = importlib.util.spec_from_file_location("r2b_roles_migration", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.PLATFORM_ADMIN_ROLE_ID == PLATFORM_ADMIN_ROLE_ID
    assert migration.USER_ROLE_ID == USER_ROLE_ID
    assert migration.PLATFORM_OPERATOR_ROLE_ID == PLATFORM_OPERATOR_ROLE_ID
    assert migration.USER_BASE_PERMISSIONS == USER_BASE_PERMISSIONS
    assert migration.PLATFORM_OPERATOR_PERMISSIONS == PLATFORM_OPERATOR_PERMISSIONS
    source = path.read_text()
    assert "from shared" not in source and "from src" not in source

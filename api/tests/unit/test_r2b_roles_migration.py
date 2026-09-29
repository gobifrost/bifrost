"""Behavioral coverage for `alembic/versions/20260929_r2b_roles.py`.

The test stack applies every migration once at boot against a fresh (empty)
database, so by the time these tests run, `20260929_r2b_roles` has already
executed — there is no user/role data pre-dating it to replay a backfill
against. These tests instead pin the END STATE the migration is required to
produce (builtins present with fixed ids + exact seeded permissions,
dropped columns/tables truly gone, the base_role_id/is_superuser invariant
holding for real users) against the live, already-migrated schema, plus a
source-level check that the migration's safety guards
(`_assert_no_promote_agent_grant` / `_assert_knowledge_namespace_roles_empty`)
target the right columns/tables.

This does NOT replay the historical migration against a seeded pre-upgrade
snapshot (seed legacy users/roles of every shape, run `upgrade()`, assert) —
no isolated-schema alembic-op replay harness exists elsewhere in this repo
to build that on, and constructing one reliably was out of scope for this
change. Flagged as a follow-up gap.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from shared.builtin_roles import (
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_PERMISSIONS,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_BASE_PERMISSIONS,
    USER_ROLE_ID,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "20260929_r2b_roles.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("_r2b_roles_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_builtin_roles_present_with_fixed_ids_and_flags(db_session):
    rows = (
        await db_session.execute(
            text("SELECT id, name, is_base, is_builtin, created_by FROM roles WHERE id = ANY(:ids)"),
            {"ids": [str(PLATFORM_ADMIN_ROLE_ID), str(USER_ROLE_ID), str(PLATFORM_OPERATOR_ROLE_ID)]},
        )
    ).mappings().all()
    by_id = {UUID(str(r["id"])): r for r in rows}

    assert set(by_id) == {PLATFORM_ADMIN_ROLE_ID, USER_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID}

    admin = by_id[PLATFORM_ADMIN_ROLE_ID]
    assert admin["name"] == "Platform Admin"
    assert admin["is_base"] is True
    assert admin["is_builtin"] is True

    user = by_id[USER_ROLE_ID]
    assert user["name"] == "User"
    assert user["is_base"] is True
    assert user["is_builtin"] is True

    operator = by_id[PLATFORM_OPERATOR_ROLE_ID]
    assert operator["name"] == "Platform Operator"
    assert operator["is_base"] is False
    assert operator["is_builtin"] is True


@pytest.mark.asyncio
async def test_platform_admin_has_no_stored_permission_rows(db_session):
    count = await db_session.scalar(
        text("SELECT COUNT(*) FROM role_permissions WHERE role_id = :rid"),
        {"rid": str(PLATFORM_ADMIN_ROLE_ID)},
    )
    assert count == 0


@pytest.mark.asyncio
async def test_user_role_has_exactly_the_seeded_permissions(db_session):
    rows = (
        await db_session.execute(
            text("SELECT permission FROM role_permissions WHERE role_id = :rid"),
            {"rid": str(USER_ROLE_ID)},
        )
    ).scalars().all()
    assert set(rows) == USER_BASE_PERMISSIONS


@pytest.mark.asyncio
async def test_platform_operator_has_exactly_the_seeded_permissions(db_session):
    rows = (
        await db_session.execute(
            text("SELECT permission FROM role_permissions WHERE role_id = :rid"),
            {"rid": str(PLATFORM_OPERATOR_ROLE_ID)},
        )
    ).scalars().all()
    assert set(rows) == PLATFORM_OPERATOR_PERMISSIONS


@pytest.mark.asyncio
async def test_roles_permissions_column_is_gone(db_session):
    exists = await db_session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'roles' AND column_name = 'permissions')"
        )
    )
    assert exists is False


@pytest.mark.asyncio
async def test_knowledge_namespace_roles_table_is_gone(db_session):
    exists = await db_session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'knowledge_namespace_roles')"
        )
    )
    assert exists is False


@pytest.mark.asyncio
async def test_users_base_role_id_matches_is_superuser_for_every_existing_user(db_session):
    """The invariant the migration backfilled (and set_user_base_role
    maintains going forward) holds for every user in the seeded stack,
    including the seeded dev superuser."""
    rows = (
        await db_session.execute(text("SELECT is_superuser, base_role_id FROM users"))
    ).all()
    assert rows, "expected at least the seeded dev user"
    for is_superuser, base_role_id in rows:
        expected = PLATFORM_ADMIN_ROLE_ID if is_superuser else USER_ROLE_ID
        assert UUID(str(base_role_id)) == expected


@pytest.mark.asyncio
async def test_user_role_boundaries_table_shape(db_session):
    """The boundaries table exists with the documented kind vocabulary
    enforced at the DB level (a bad kind is rejected)."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id) "
                    "SELECT gen_random_uuid(), u.id, :rid, 'not_a_real_kind', NULL FROM users u LIMIT 1"
                ),
                {"rid": str(USER_ROLE_ID)},
            )


def test_promote_agent_guard_targets_the_legacy_permissions_column():
    """Source-level check that the safety guard which must run before
    `roles.permissions` is dropped actually reads that column — a drift
    here would silently defang the guard."""
    module = _load_migration_module()
    import inspect

    source = inspect.getsource(module._assert_no_promote_agent_grant)
    assert "permissions ->> 'can_promote_agent'" in source
    assert "FROM roles" in source


def test_knowledge_namespace_roles_guard_targets_the_right_table():
    module = _load_migration_module()
    import inspect

    source = inspect.getsource(module._assert_knowledge_namespace_roles_empty)
    assert "FROM knowledge_namespace_roles" in source

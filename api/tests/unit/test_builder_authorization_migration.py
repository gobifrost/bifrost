"""Regression coverage for the forward-only Builder authorization migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock


MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "20260819_builder_authorization_boundaries.py"
)
SPEC = importlib.util.spec_from_file_location(
    "builder_authorization_migration",
    MIGRATION_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_existing_provider_staff_receive_sticky_operator_boundary(monkeypatch) -> None:
    """The upgrade preserves provider support access as an explicit Role."""

    connection = MagicMock()
    migration_op = MagicMock()
    migration_op.get_bind.return_value = connection
    monkeypatch.setattr(MIGRATION, "op", migration_op)

    MIGRATION._migrate_role_assignments()

    executed = [
        (str(call.args[0]), call.args[1] if len(call.args) > 1 else {})
        for call in connection.execute.call_args_list
    ]
    operator_backfill = next(
        (sql, params)
        for sql, params in executed
        if "organization.is_provider IS TRUE" in sql
    )
    admin_backfill = next(
        (sql, params)
        for sql, params in executed
        if "member.is_superuser IS TRUE" in sql
    )
    boundary_backfill = next(
        (sql, params)
        for sql, params in executed
        if "INSERT INTO role_assignment_boundaries" in sql
    )

    assert admin_backfill[1]["role_id"] == str(MIGRATION.PLATFORM_ADMIN_ROLE_ID)
    assert "member.is_system IS NOT TRUE" in admin_backfill[0]
    assert "ON CONFLICT (user_id, role_id) DO NOTHING" in admin_backfill[0]
    assert executed.index(admin_backfill) < executed.index(boundary_backfill)
    assert operator_backfill[1]["role_id"] == str(
        MIGRATION.PLATFORM_OPERATOR_ROLE_ID
    )
    assert "member.is_superuser IS NOT TRUE" in operator_backfill[0]
    assert "ON CONFLICT (user_id, role_id) DO NOTHING" in operator_backfill[0]
    assert "THEN 'managed_organizations'" in boundary_backfill[0]
    assert "WHEN assignment.role_id = CAST(:admin_role_id AS uuid)" in boundary_backfill[0]
    assert "THEN 'platform'" in boundary_backfill[0]
    assert boundary_backfill[0].count("THEN 'platform'") == 2
    assert boundary_backfill[0].count("CAST(:admin_role_id AS uuid)") == 2
    assert "ON CONFLICT DO NOTHING" in boundary_backfill[0]
    assert boundary_backfill[1]["admin_role_id"] == str(
        MIGRATION.PLATFORM_ADMIN_ROLE_ID
    )
    assert boundary_backfill[1]["operator_role_id"] == str(
        MIGRATION.PLATFORM_OPERATOR_ROLE_ID
    )

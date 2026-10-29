"""Rehearse canonical admin-bypass migration against edited policy documents."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e

PREVIOUS_REVISION = "20261012_oauth_ownership"

_TABLE_SEED = {
    "name": "admin_bypass",
    "description": "Platform admins bypass all checks. Edit or delete to enforce stricter audit.",
    "actions": ["read", "create", "update", "delete"],
    "when": {"user": "is_platform_admin"},
}
_FILE_SEED = {
    "name": "admin_bypass",
    "description": "Platform admins bypass all file checks. Edit or delete to enforce stricter audit.",
    "actions": ["read", "write", "delete", "list"],
    "when": {"user": "is_platform_admin"},
}
_CUSTOM_ADMIN = {
    "name": "custom_admin",
    "description": "A customer-owned legacy expression.",
    "actions": ["read"],
    "when": {"user": "is_platform_admin"},
}


async def _seed(database_url: str) -> None:
    async def seed(connection: AsyncConnection) -> None:
        for name, access in {
            "table-seed-and-custom": {"policies": [_TABLE_SEED, _CUSTOM_ADMIN]},
            "table-custom-only": {"policies": [_CUSTOM_ADMIN]},
        }.items():
            await connection.execute(
                sa.text(
                    "INSERT INTO tables (id, name, access) "
                    "VALUES (gen_random_uuid(), :name, CAST(:access AS jsonb))"
                ),
                {"name": name, "access": json.dumps(access)},
            )
        for path, policies in {
            "seed-and-custom": {"policies": [_FILE_SEED, _CUSTOM_ADMIN]},
            "custom-only": {"policies": [_CUSTOM_ADMIN]},
        }.items():
            await connection.execute(
                sa.text(
                    "INSERT INTO file_policies (id, location, path, policies) "
                    "VALUES (gen_random_uuid(), 'workspace', :path, CAST(:policies AS jsonb))"
                ),
                {"path": path, "policies": json.dumps(policies)},
            )
        for domain in ("table", "file"):
            await connection.execute(
                sa.text(
                    "INSERT INTO policy_rules (id, name, domain, body, is_builtin, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'admin_bypass', :domain, "
                    "'{\"actions\": [\"read\"], \"when\": {\"user\": \"is_platform_admin\"}}'::jsonb, TRUE, now(), now())"
                ),
                {"domain": domain},
            )

    await _run_in_database(database_url, seed)


async def _read(database_url: str) -> dict[str, Any]:
    async def read(connection: AsyncConnection) -> dict[str, Any]:
        tables = {
            row.name: row.access
            for row in await connection.execute(
                sa.text("SELECT name, access FROM tables WHERE name LIKE 'table-%'")
            )
        }
        files = {
            row.path: row.policies
            for row in await connection.execute(
                sa.text("SELECT path, policies FROM file_policies WHERE location = 'workspace'")
            )
        }
        builtins = {
            row.domain: row.body
            for row in await connection.execute(
                sa.text(
                    "SELECT domain, body FROM policy_rules "
                    "WHERE is_builtin IS TRUE AND organization_id IS NULL AND name = 'admin_bypass'"
                )
            )
        }
        return {"tables": tables, "files": files, "builtins": builtins}

    return await _run_in_database(database_url, read)


def test_upgrade_rewrites_only_canonical_seed_nodes() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed(database_url))
        _upgrade(database_url, "head")
        result = asyncio.run(_read(database_url))

        seeded_table = result["tables"]["table-seed-and-custom"]["policies"]
        assert seeded_table[0]["when"] == {"authority": "platform_admin"}
        assert seeded_table[1] == _CUSTOM_ADMIN
        assert result["tables"]["table-custom-only"] == {"policies": [_CUSTOM_ADMIN]}

        seeded_file = result["files"]["seed-and-custom"]["policies"]
        assert seeded_file[0]["when"] == {"authority": "platform_admin"}
        assert seeded_file[1] == _CUSTOM_ADMIN
        assert result["files"]["custom-only"] == {"policies": [_CUSTOM_ADMIN]}

        assert {body["when"].get("authority") for body in result["builtins"].values()} == {"platform_admin"}
    finally:
        asyncio.run(_drop_database(database_name))

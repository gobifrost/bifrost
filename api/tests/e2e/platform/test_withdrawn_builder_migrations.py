"""Regression coverage for the forward Builder schema reinstatement."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "20260807_reinstate_builder_schema.py"
)

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000003")
PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000004")


def _load_reinstate_migration():
    spec = importlib.util.spec_from_file_location(
        "reinstate_builder_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _rerun_reinstate_migration(db_session: AsyncSession) -> None:
    module = _load_reinstate_migration()

    def run_upgrade(connection):
        context = MigrationContext.configure(connection)
        module.op = Operations(context)
        module.upgrade()

    connection = await db_session.connection()
    await connection.run_sync(run_upgrade)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_fresh_database_installs_reinstated_builder_schema(
    db_session: AsyncSession,
) -> None:
    revision = (
        await db_session.execute(text("SELECT version_num FROM alembic_version"))
    ).scalar_one()
    assert revision == "20260810_builder_global"

    builder_tables = (
        await db_session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'solution_build_jobs',
                      'solution_builder_projects',
                      'solution_builder_collaborators',
                      'solution_builder_sessions',
                      'solution_builder_turns',
                      'solution_source_revisions'
                  )
                ORDER BY table_name
                """
            )
        )
    ).scalars().all()
    assert builder_tables == [
        "solution_build_jobs",
        "solution_builder_collaborators",
        "solution_builder_projects",
        "solution_builder_sessions",
        "solution_builder_turns",
        "solution_source_revisions",
    ]

    expected_columns = {
        "agents": {"bundle_path"},
        "roles": {"key", "scopes", "is_builtin", "assignable_to_resources"},
        "solution_build_jobs": {"claimed_at", "last_progress_at"},
        "solution_builder_projects": {
            "promotion_revision_id",
            "promotion_requested_by",
            "promotion_requested_at",
        },
        "solution_deploy_jobs": {
            "kind",
            "encrypted_options",
            "input_key",
            "input_sha256",
        },
        "platform_jobs": {
            "external_provider",
            "external_run_id",
            "external_started_at",
        },
    }
    for table_name, column_names in expected_columns.items():
        rows = (
            await db_session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                    """
                ),
                {"table_name": table_name},
            )
        ).scalars().all()
        assert column_names.issubset(set(rows))

    indexes = (
        await db_session.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN (
                      'ix_solution_build_jobs_reuse_key',
                      'ix_solution_source_revisions_solution_created',
                      'ix_solution_builder_turns_status',
                      'uq_roles_key'
                  )
                ORDER BY indexname
                """
            )
        )
    ).scalars().all()
    assert indexes == [
        "ix_solution_build_jobs_reuse_key",
        "ix_solution_builder_turns_status",
        "ix_solution_source_revisions_solution_created",
        "uq_roles_key",
    ]

    build_job_app_fks = (
        await db_session.execute(
            text(
                """
                SELECT conname
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_attribute a
                  ON a.attrelid = t.oid
                 AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'f'
                  AND t.relname = 'solution_build_jobs'
                  AND a.attname = 'app_id'
                """
            )
        )
    ).scalars().all()
    assert build_job_app_fks == []

    roles = (
        await db_session.execute(
            text(
                """
                SELECT id, key, scopes, is_builtin, assignable_to_resources
                FROM roles
                WHERE id IN (:admin_id, :operator_id)
                ORDER BY key
                """
            ),
            {
                "admin_id": PLATFORM_ADMIN_ROLE_ID,
                "operator_id": PLATFORM_OPERATOR_ROLE_ID,
            },
        )
    ).mappings().all()
    assert [role["key"] for role in roles] == [
        "platform_admin",
        "platform_operator",
    ]
    assert all(role["is_builtin"] for role in roles)
    assert all(not role["assignable_to_resources"] for role in roles)
    assert roles[0]["scopes"] == ["platform.superuser"]
    assert roles[1]["scopes"] == ["organization.impersonation"]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_reinstate_builder_migration_preserves_existing_legacy_footprint(
    db_session: AsyncSession,
) -> None:
    solution_id = uuid4()
    revision_id = uuid4()
    build_job_id = uuid4()
    role_id = uuid4()

    await db_session.execute(
        text(
            """
            INSERT INTO solutions (id, slug, name)
            VALUES (:id, :slug, 'Legacy Builder')
            """
        ),
        {"id": solution_id, "slug": f"legacy-builder-{solution_id}"},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO solution_source_revisions (
                id, solution_id, source_sha256, size_bytes, summary
            ) VALUES (
                :id, :solution_id, :sha, 42, 'legacy source'
            )
            """
        ),
        {"id": revision_id, "solution_id": solution_id, "sha": "a" * 64},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO solution_build_jobs (
                id, solution_id, source_revision_id, source_sha256,
                toolchain_version, dependency_digest, status, output_manifest
            ) VALUES (
                :id, :solution_id, :revision_id, :sha,
                'vite-legacy', 'b' || repeat('0', 63), 'queued', '[]'::jsonb
            )
            """
        ),
        {
            "id": build_job_id,
            "solution_id": solution_id,
            "revision_id": revision_id,
            "sha": "c" * 64,
        },
    )
    await db_session.execute(
        text(
            """
            INSERT INTO roles (id, name, description, permissions, created_by)
            VALUES (
                :id, 'Legacy Builder', NULL,
                '{"solutions.build": true}'::jsonb,
                'tester'
            )
            """
        ),
        {"id": role_id},
    )
    await db_session.commit()

    await _rerun_reinstate_migration(db_session)
    await _rerun_reinstate_migration(db_session)

    build_job = (
        await db_session.execute(
            text(
                """
                SELECT source_revision_id, source_sha256, toolchain_version,
                       dependency_digest, status, output_manifest
                FROM solution_build_jobs
                WHERE id = :id
                """
            ),
            {"id": build_job_id},
        )
    ).mappings().one()
    assert build_job["source_revision_id"] == revision_id
    assert build_job["source_sha256"] == "c" * 64
    assert build_job["toolchain_version"] == "vite-legacy"
    assert build_job["dependency_digest"] == "b" + ("0" * 63)
    assert build_job["status"] == "queued"
    assert build_job["output_manifest"] == []

    role = (
        await db_session.execute(
            text(
                """
                SELECT permissions, scopes
                FROM roles
                WHERE id = :id
                """
            ),
            {"id": role_id},
        )
    ).mappings().one()
    assert role["permissions"] == {}
    assert role["scopes"] == ["solutions.build"]

    builtin_count = (
        await db_session.execute(
            text(
                """
                SELECT count(*)
                FROM roles
                WHERE id IN (:admin_id, :operator_id)
                """
            ),
            {
                "admin_id": PLATFORM_ADMIN_ROLE_ID,
                "operator_id": PLATFORM_OPERATOR_ROLE_ID,
            },
        )
    ).scalar_one()
    assert builtin_count == 2

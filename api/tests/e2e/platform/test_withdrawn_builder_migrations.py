"""Regression coverage for the forward-only Builder schema reinstatement."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_fresh_database_installs_reinstated_builder_schema(
    db_session: AsyncSession,
) -> None:
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
        "solution_builder_projects",
        "solution_builder_sessions",
        "solution_builder_turns",
        "solution_source_revisions",
    ]

    builder_roles = (
        await db_session.execute(
            text(
                """
                SELECT id
                FROM roles
                WHERE id IN (
                    '00000000-0000-0000-0000-000000000003',
                    '00000000-0000-0000-0000-000000000004'
                )
                """
            )
        )
    ).scalars().all()
    assert {str(role_id) for role_id in builder_roles} == {
        "00000000-0000-0000-0000-000000000003",
        "00000000-0000-0000-0000-000000000004",
    }

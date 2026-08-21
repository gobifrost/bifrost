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


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_fresh_database_installs_boundary_authorization_schema(
    db_session: AsyncSession,
) -> None:
    authorization_tables = set(
        (
            await db_session.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name IN (
                          'role_assignments',
                          'role_assignment_boundaries',
                          'organization_groups',
                          'organization_group_members',
                          'solution_user_grants',
                          'solution_role_grants'
                      )
                    """
                )
            )
        ).scalars()
    )
    assert authorization_tables == {
        "role_assignments",
        "role_assignment_boundaries",
        "organization_groups",
        "organization_group_members",
        "solution_user_grants",
        "solution_role_grants",
    }

    role_columns = set(
        (
            await db_session.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'roles'
                    """
                )
            )
        ).scalars()
    )
    assert "capabilities" in role_columns
    assert "scopes" not in role_columns
    assert "permissions" in role_columns

    default_roles = dict(
        (
            await db_session.execute(
                text(
                    """
                    SELECT key, capabilities
                    FROM roles
                    WHERE key IN (
                        'platform_admin', 'platform_operator', 'builder',
                        'platform_builder', 'organization_member'
                    )
                    """
                )
            )
        ).all()
    )
    assert set(default_roles) == {
        "platform_admin",
        "platform_operator",
        "builder",
        "platform_builder",
        "organization_member",
    }
    assert default_roles["platform_admin"] == ["platform.superuser"]
    assert "builder.execute" in default_roles["builder"]
    assert "repository.readwrite" in default_roles["platform_builder"]
    assert "builder.execute" not in default_roles["platform_operator"]

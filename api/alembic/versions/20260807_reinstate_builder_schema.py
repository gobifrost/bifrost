"""reinstate withdrawn Builder schema forward-only

Revision ID: 20260807_reinstate_builder
Revises: 20260816_artifact_workspace

The old Builder migration bodies remain tombstones. This forward migration
reinstates the final schema idempotently for both fresh databases and databases
that briefly observed some or all of the withdrawn revisions.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260807_reinstate_builder"
down_revision: str = "20260816_artifact_workspace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_ADMIN_ROLE_ID = "00000000-0000-0000-0000-000000000003"
PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000004"
SYSTEM_ACTOR = "system@internal.gobifrost.com"


def _table_exists(inspector: sa.Inspector, table: str) -> bool:
    return inspector.has_table(table)


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    if not _table_exists(inspector, table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector: sa.Inspector, table: str) -> set[str]:
    if not _table_exists(inspector, table):
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def _foreign_keys(inspector: sa.Inspector, table: str) -> set[str]:
    if not _table_exists(inspector, table):
        return set()
    return {fk["name"] for fk in inspector.get_foreign_keys(table) if fk["name"]}


def _foreign_key_defs(inspector: sa.Inspector, table: str) -> list[dict]:
    if not _table_exists(inspector, table):
        return []
    return inspector.get_foreign_keys(table)


def _check_constraints(inspector: sa.Inspector, table: str) -> set[str]:
    if not _table_exists(inspector, table):
        return set()
    return {ck["name"] for ck in inspector.get_check_constraints(table) if ck["name"]}


def _add_column_if_missing(
    inspector: sa.Inspector,
    table: str,
    column: sa.Column,
) -> None:
    if column.name not in _columns(inspector, table):
        op.add_column(table, column)


def _create_index_if_missing(
    inspector: sa.Inspector,
    name: str,
    table: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    if name not in _indexes(inspector, table):
        op.create_index(name, table, columns, unique=unique)


def _create_fk_if_missing(
    inspector: sa.Inspector,
    name: str,
    source: str,
    referent: str,
    local_cols: list[str],
    remote_cols: list[str],
    *,
    ondelete: str | None = None,
) -> None:
    if name not in _foreign_keys(inspector, source):
        op.create_foreign_key(
            name,
            source,
            referent,
            local_cols,
            remote_cols,
            ondelete=ondelete,
        )


def _create_solution_source_revisions(inspector: sa.Inspector) -> None:
    if not _table_exists(inspector, "solution_source_revisions"):
        op.create_table(
            "solution_source_revisions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("solution_id", sa.Uuid(), nullable=False),
            sa.Column("parent_revision_id", sa.Uuid(), nullable=True),
            sa.Column("restored_from_revision_id", sa.Uuid(), nullable=True),
            sa.Column("conversation_id", sa.Uuid(), nullable=True),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("source_sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["parent_revision_id"],
                ["solution_source_revisions.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["restored_from_revision_id"],
                ["solution_source_revisions.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["conversations.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        inspector,
        "ix_solution_source_revisions_solution_id",
        "solution_source_revisions",
        ["solution_id"],
    )
    _create_index_if_missing(
        inspector,
        "ix_solution_source_revisions_solution_created",
        "solution_source_revisions",
        ["solution_id", "created_at"],
    )


def _create_solution_builder_projects(inspector: sa.Inspector) -> None:
    if not _table_exists(inspector, "solution_builder_projects"):
        op.create_table(
            "solution_builder_projects",
            sa.Column("solution_id", sa.Uuid(), nullable=False),
            sa.Column("current_revision_id", sa.Uuid(), nullable=True),
            sa.Column("deployed_revision_id", sa.Uuid(), nullable=True),
            sa.Column(
                "promotion_status",
                sa.String(length=16),
                server_default="none",
                nullable=False,
            ),
            sa.Column("promotion_revision_id", sa.Uuid(), nullable=True),
            sa.Column("promotion_requested_by", sa.Uuid(), nullable=True),
            sa.Column("promotion_requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["current_revision_id"],
                ["solution_source_revisions.id"],
                name="fk_solution_builder_projects_current_revision_id",
                ondelete="SET NULL",
                use_alter=True,
            ),
            sa.ForeignKeyConstraint(
                ["deployed_revision_id"],
                ["solution_source_revisions.id"],
                name="fk_solution_builder_projects_deployed_revision_id",
                ondelete="SET NULL",
                use_alter=True,
            ),
            sa.ForeignKeyConstraint(
                ["promotion_revision_id"],
                ["solution_source_revisions.id"],
                name="fk_solution_builder_projects_promotion_revision_id",
                ondelete="SET NULL",
                use_alter=True,
            ),
            sa.ForeignKeyConstraint(
                ["promotion_requested_by"],
                ["users.id"],
                name="fk_solution_builder_projects_promotion_requested_by",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("solution_id"),
        )
        return

    _add_column_if_missing(
        inspector,
        "solution_builder_projects",
        sa.Column("promotion_revision_id", sa.Uuid(), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "solution_builder_projects",
        sa.Column("promotion_requested_by", sa.Uuid(), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "solution_builder_projects",
        sa.Column("promotion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    _create_fk_if_missing(
        inspector,
        "fk_solution_builder_projects_current_revision_id",
        "solution_builder_projects",
        "solution_source_revisions",
        ["current_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _create_fk_if_missing(
        inspector,
        "fk_solution_builder_projects_deployed_revision_id",
        "solution_builder_projects",
        "solution_source_revisions",
        ["deployed_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _create_fk_if_missing(
        inspector,
        "fk_solution_builder_projects_promotion_revision_id",
        "solution_builder_projects",
        "solution_source_revisions",
        ["promotion_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    _create_fk_if_missing(
        inspector,
        "fk_solution_builder_projects_promotion_requested_by",
        "solution_builder_projects",
        "users",
        ["promotion_requested_by"],
        ["id"],
        ondelete="SET NULL",
    )


def _create_builder_sessions_and_turns(inspector: sa.Inspector) -> None:
    if not _table_exists(inspector, "solution_builder_sessions"):
        op.create_table(
            "solution_builder_sessions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("solution_id", sa.Uuid(), nullable=False),
            sa.Column("conversation_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        inspector,
        "ix_solution_builder_sessions_solution_id",
        "solution_builder_sessions",
        ["solution_id"],
    )
    _create_index_if_missing(
        inspector,
        "ix_solution_builder_sessions_conversation_id",
        "solution_builder_sessions",
        ["conversation_id"],
    )
    _create_index_if_missing(
        inspector,
        "ix_solution_builder_sessions_user_id",
        "solution_builder_sessions",
        ["user_id"],
    )

    if not _table_exists(inspector, "solution_builder_turns"):
        op.create_table(
            "solution_builder_turns",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("session_id", sa.Uuid(), nullable=False),
            sa.Column("requested_by", sa.Uuid(), nullable=True),
            sa.Column("base_revision_id", sa.Uuid(), nullable=True),
            sa.Column("output_revision_id", sa.Uuid(), nullable=True),
            sa.Column("build_job_id", sa.Uuid(), nullable=True),
            sa.Column("deploy_job_id", sa.Uuid(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=16),
                server_default="queued",
                nullable=False,
            ),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["session_id"],
                ["solution_builder_sessions.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["base_revision_id"],
                ["solution_source_revisions.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["output_revision_id"],
                ["solution_source_revisions.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing(
        inspector,
        "ix_solution_builder_turns_session_id",
        "solution_builder_turns",
        ["session_id"],
    )
    _create_index_if_missing(
        inspector,
        "ix_solution_builder_turns_status",
        "solution_builder_turns",
        ["status"],
    )


def _create_solution_build_jobs(inspector: sa.Inspector) -> None:
    if not _table_exists(inspector, "solution_build_jobs"):
        op.create_table(
            "solution_build_jobs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("solution_id", sa.Uuid(), nullable=False),
            sa.Column("app_id", sa.Uuid(), nullable=True),
            sa.Column("source_revision_id", sa.Uuid(), nullable=True),
            sa.Column("requested_by", sa.Uuid(), nullable=True),
            sa.Column("source_sha256", sa.String(length=64), nullable=False),
            sa.Column("toolchain_version", sa.String(length=64), nullable=False),
            sa.Column("dependency_digest", sa.String(length=64), nullable=True),
            sa.Column(
                "status", sa.String(length=16), nullable=False, server_default="queued"
            ),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("log_excerpt", sa.Text(), nullable=True),
            sa.Column("output_manifest", postgresql.JSONB(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["source_revision_id"],
                ["solution_source_revisions.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        )
    else:
        _add_column_if_missing(
            inspector,
            "solution_build_jobs",
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        )
        _add_column_if_missing(
            inspector,
            "solution_build_jobs",
            sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
        )
        for fk in _foreign_key_defs(inspector, "solution_build_jobs"):
            if (
                fk["name"]
                and fk["constrained_columns"] == ["app_id"]
                and fk["referred_table"] == "applications"
            ):
                op.drop_constraint(
                    fk["name"],
                    "solution_build_jobs",
                    type_="foreignkey",
                )

    _create_index_if_missing(
        inspector,
        "ix_solution_build_jobs_solution_id",
        "solution_build_jobs",
        ["solution_id"],
    )
    _create_index_if_missing(
        inspector,
        "ix_solution_build_jobs_solution_created",
        "solution_build_jobs",
        ["solution_id", "created_at"],
    )
    _create_index_if_missing(
        inspector,
        "ix_solution_build_jobs_reuse_key",
        "solution_build_jobs",
        ["source_sha256", "app_id", "toolchain_version"],
    )


def _update_existing_roles_with_build_scope() -> None:
    op.execute(
        sa.text(
            """
            UPDATE roles
            SET scopes = CASE
                    WHEN scopes ? 'solutions.build' THEN scopes
                    ELSE scopes || '["solutions.build"]'::jsonb
                END,
                permissions = permissions - 'solutions.build'
            WHERE permissions @> '{"solutions.build": true}'::jsonb
            """
        )
    )


def _upsert_builtin_roles() -> None:
    upsert_role = sa.text(
        """
        INSERT INTO roles (
            id, key, name, description, permissions, scopes,
            is_builtin, assignable_to_resources, created_by, created_at, updated_at
        ) VALUES (
            CAST(:id AS uuid), :key, :name, :description,
            '{}'::jsonb, CAST(:scopes AS jsonb), TRUE, FALSE,
            :created_by, NOW(), NOW()
        )
        ON CONFLICT (id) DO UPDATE
        SET key = EXCLUDED.key,
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            scopes = EXCLUDED.scopes,
            is_builtin = TRUE,
            assignable_to_resources = FALSE,
            updated_at = NOW()
        """
    )
    op.execute(
        upsert_role.bindparams(
            id=PLATFORM_ADMIN_ROLE_ID,
            key="platform_admin",
            name="Platform Admin",
            description="Full platform administration managed by Bifrost.",
            scopes='["platform.superuser"]',
            created_by=SYSTEM_ACTOR,
        )
    )
    op.execute(
        upsert_role.bindparams(
            id=PLATFORM_OPERATOR_ROLE_ID,
            key="platform_operator",
            name="Platform Operator",
            description="Cross-organization operations managed by Bifrost.",
            scopes='["organization.impersonation"]',
            created_by=SYSTEM_ACTOR,
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO user_roles (user_id, role_id, assigned_by, assigned_at)
            SELECT id, CAST(:role_id AS uuid), :assigned_by, NOW()
            FROM users
            WHERE is_superuser IS TRUE AND is_system IS NOT TRUE
            ON CONFLICT (user_id, role_id) DO NOTHING
            """
        ).bindparams(role_id=PLATFORM_ADMIN_ROLE_ID, assigned_by=SYSTEM_ACTOR)
    )


def _add_role_authorization_scopes(inspector: sa.Inspector) -> None:
    _add_column_if_missing(
        inspector,
        "roles",
        sa.Column("key", sa.String(length=100), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "roles",
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    _add_column_if_missing(
        inspector,
        "roles",
        sa.Column(
            "is_builtin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    _add_column_if_missing(
        inspector,
        "roles",
        sa.Column(
            "assignable_to_resources",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    _create_index_if_missing(inspector, "uq_roles_key", "roles", ["key"], unique=True)
    _update_existing_roles_with_build_scope()
    _upsert_builtin_roles()


def _add_deploy_job_payload_columns(inspector: sa.Inspector) -> None:
    _add_column_if_missing(
        inspector,
        "solution_deploy_jobs",
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="deploy"),
    )
    _add_column_if_missing(
        inspector,
        "solution_deploy_jobs",
        sa.Column("encrypted_options", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "solution_deploy_jobs",
        sa.Column("input_key", sa.Text(), nullable=True),
    )
    _add_column_if_missing(
        inspector,
        "solution_deploy_jobs",
        sa.Column("input_sha256", sa.String(length=64), nullable=True),
    )
    if "ck_solution_deploy_jobs_kind" not in _check_constraints(
        inspector, "solution_deploy_jobs"
    ):
        op.create_check_constraint(
            "ck_solution_deploy_jobs_kind",
            "solution_deploy_jobs",
            "kind IN ('deploy', 'install', 'install_from_repo')",
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _add_column_if_missing(
        inspector,
        "agents",
        sa.Column("bundle_path", sa.String(length=1024), nullable=True),
    )
    _create_solution_source_revisions(inspector)
    _create_solution_builder_projects(sa.inspect(bind))
    _create_builder_sessions_and_turns(sa.inspect(bind))
    _create_solution_build_jobs(sa.inspect(bind))
    _add_deploy_job_payload_columns(sa.inspect(bind))
    _add_role_authorization_scopes(sa.inspect(bind))


def downgrade() -> None:
    pass

"""Add Codex Gateway persistence tables.

Revision ID: 20260522_codex_gateway
Revises: 20260516_per_token_status
Create Date: 2026-05-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260522_codex_gateway"
down_revision: str | Sequence[str] | None = "20260516_per_token_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "codex_gateway_upstream_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=64),
            server_default="chatgpt_codex",
            nullable=False,
        ),
        sa.Column("upstream_subject", sa.String(length=512), nullable=False),
        sa.Column("upstream_email", sa.String(length=320), nullable=True),
        sa.Column("upstream_workspace_id", sa.String(length=512), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_codex_gateway_upstream_accounts_user_provider",
        "codex_gateway_upstream_accounts",
        ["user_id", "provider", "revoked_at"],
    )
    op.create_index(
        "ix_codex_gateway_upstream_accounts_subject",
        "codex_gateway_upstream_accounts",
        ["provider", "upstream_subject"],
    )

    op.create_table(
        "codex_gateway_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "allowed_models",
            postgresql.JSONB(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "denied_models",
            postgresql.JSONB(),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("daily_limit", sa.Integer(), nullable=True),
        sa.Column("monthly_limit", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_codex_gateway_keys_key_hash"),
    )
    op.create_index(
        "ix_codex_gateway_keys_user_status",
        "codex_gateway_keys",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_codex_gateway_keys_project_status",
        "codex_gateway_keys",
        ["project_id", "status"],
    )

    op.create_table(
        "codex_gateway_request_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gateway_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("oauth_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column(
            "streaming",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("provider_error_code", sa.String(length=128), nullable=True),
        sa.Column("input_token_count", sa.Integer(), nullable=True),
        sa.Column("output_token_count", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("policy_decision", sa.String(length=32), nullable=False),
        sa.Column("denied_reason", sa.Text(), nullable=True),
        sa.Column("source_ip", sa.String(length=45), nullable=True),
        sa.Column("client_user_agent", sa.Text(), nullable=True),
        sa.Column(
            "request_metadata",
            postgresql.JSONB(),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("captured_prompt", sa.Text(), nullable=True),
        sa.Column("captured_response", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["gateway_key_id"],
            ["codex_gateway_keys.id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["oauth_account_id"],
            ["codex_gateway_upstream_accounts.id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", name="uq_codex_gateway_request_logs_request_id"
        ),
    )
    op.create_index(
        "ix_codex_gateway_request_logs_user_time",
        "codex_gateway_request_logs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_codex_gateway_request_logs_project_time",
        "codex_gateway_request_logs",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_codex_gateway_request_logs_decision_time",
        "codex_gateway_request_logs",
        ["policy_decision", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_codex_gateway_request_logs_decision_time",
        table_name="codex_gateway_request_logs",
    )
    op.drop_index(
        "ix_codex_gateway_request_logs_project_time",
        table_name="codex_gateway_request_logs",
    )
    op.drop_index(
        "ix_codex_gateway_request_logs_user_time",
        table_name="codex_gateway_request_logs",
    )
    op.drop_table("codex_gateway_request_logs")
    op.drop_index(
        "ix_codex_gateway_keys_project_status",
        table_name="codex_gateway_keys",
    )
    op.drop_index("ix_codex_gateway_keys_user_status", table_name="codex_gateway_keys")
    op.drop_table("codex_gateway_keys")
    op.drop_index(
        "ix_codex_gateway_upstream_accounts_subject",
        table_name="codex_gateway_upstream_accounts",
    )
    op.drop_index(
        "ix_codex_gateway_upstream_accounts_user_provider",
        table_name="codex_gateway_upstream_accounts",
    )
    op.drop_table("codex_gateway_upstream_accounts")

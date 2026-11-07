"""Add optional deployment-owned GitHub App connections and delivery identity."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261106_github_app"
down_revision = "20261106_chat_execute"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_app_registrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("encrypted_private_key", sa.Text()),
        sa.Column("encrypted_webhook_secret", sa.Text()),
        sa.Column("generation", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovery_cursor", sa.DateTime(timezone=True)),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("recovery_gap", sa.Boolean(), nullable=False),
        sa.Column("redelivery_requests", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("id = 1", name="github_app_single_registration"),
    )
    op.create_table(
        "github_app_installations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("app_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_source_id", sa.UUID(), sa.ForeignKey("event_sources.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("account_login", sa.String(255), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), nullable=False),
        sa.Column("repositories", postgresql.JSONB(), nullable=False),
        sa.Column("generation", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(255)),
        sa.UniqueConstraint("installation_id", name="github_app_installation_claim"),
    )
    op.create_index("ix_github_app_installations_organization_id", "github_app_installations", ["organization_id"])
    op.create_table(
        "github_app_deliveries",
        sa.Column("app_id", sa.BigInteger(), primary_key=True),
        sa.Column("delivery_id", sa.String(100), primary_key=True),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("event_id", sa.UUID(), sa.ForeignKey("events.id", ondelete="SET NULL")),
        sa.Column("installation_id", sa.BigInteger()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_github_app_deliveries_received_at", "github_app_deliveries", ["received_at"])
    op.create_index("ix_github_app_deliveries_event_id", "github_app_deliveries", ["event_id"])


def downgrade() -> None:
    op.drop_table("github_app_deliveries")
    op.drop_table("github_app_installations")
    op.drop_table("github_app_registrations")

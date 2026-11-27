"""Add independent integration connection instances.

Revision ID: 20261127_integration_instances
Revises: 20261127_integration_links
"""

from alembic import op
import sqlalchemy as sa


revision = "20261127_integration_instances"
down_revision = "20261127_integration_links"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "integrations",
        sa.Column(
            "parent_integration_id",
            sa.UUID(),
            sa.ForeignKey("integrations.id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "integrations",
        sa.Column("instance_key", sa.String(length=80), server_default="default", nullable=False),
    )
    op.add_column(
        "integrations",
        sa.Column("instance_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "integrations",
        sa.Column(
            "authentication_method",
            sa.String(length=16),
            server_default="config",
            nullable=False,
        ),
    )
    op.execute("UPDATE integrations SET instance_name = name")
    op.execute(
        """
        UPDATE integrations
        SET authentication_method = 'oauth'
        WHERE EXISTS (
            SELECT 1 FROM oauth_providers
            WHERE oauth_providers.integration_id = integrations.id
        )
        """
    )
    op.create_index(
        "ix_integrations_parent_integration_id", "integrations", ["parent_integration_id"]
    )
    op.create_index(
        "uq_integration_instance_key",
        "integrations",
        ["parent_integration_id", "instance_key"],
        unique=True,
    )
    op.create_check_constraint(
        "ck_integrations_default_instance",
        "integrations",
        "(parent_integration_id IS NULL AND instance_key = 'default') OR "
        "(parent_integration_id IS NOT NULL AND instance_key <> 'default')",
    )
    op.create_check_constraint(
        "ck_integrations_authentication_method",
        "integrations",
        "authentication_method IN ('config', 'oauth')",
    )


def downgrade():
    children = op.get_bind().execute(
        sa.text("SELECT count(*) FROM integrations WHERE parent_integration_id IS NOT NULL")
    ).scalar_one()
    if children:
        raise RuntimeError(
            "Cannot downgrade integration instances while named connection instances exist; "
            "permanently remove named instance records, including deleted records, before downgrading."
        )
    op.drop_constraint("ck_integrations_default_instance", "integrations")
    op.drop_constraint("ck_integrations_authentication_method", "integrations")
    op.drop_index("uq_integration_instance_key", table_name="integrations")
    op.drop_index("ix_integrations_parent_integration_id", table_name="integrations")
    op.drop_column("integrations", "authentication_method")
    op.drop_column("integrations", "instance_name")
    op.drop_column("integrations", "instance_key")
    op.drop_column("integrations", "parent_integration_id")

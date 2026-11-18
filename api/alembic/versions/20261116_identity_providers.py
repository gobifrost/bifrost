"""Add identity provider connections, organization domain claims and the
directory state SCIM provisioning keeps in step.

Revision ID: 20261116_identity_providers
Revises: 20261117_integ_secret_encrypt
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261116_identity_providers"
down_revision = "20261117_integ_secret_encrypt"
branch_labels = None
depends_on = None


def _timestamps(*, updated: bool) -> list[sa.Column]:
    columns = [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")
        )
    ]
    if updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            )
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "organization_domains",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("verification_token", sa.String(64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(updated=False),
        sa.UniqueConstraint("organization_id", "domain", name="uq_organization_domains_org_domain"),
        sa.CheckConstraint("domain = lower(domain)", name="ck_organization_domains_lowercase"),
    )
    op.create_index(
        "uq_organization_domains_verified_domain",
        "organization_domains",
        ["domain"],
        unique=True,
        postgresql_where=sa.text("verified_at IS NOT NULL"),
    )

    op.create_table(
        "identity_providers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("vendor", sa.String(32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "provision_as_external", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("settings", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(updated=True),
        sa.CheckConstraint(
            "source_type IN ('scim', 'google_directory')", name="ck_identity_providers_source_type"
        ),
        sa.CheckConstraint(
            "vendor IN ('entra', 'okta', 'jumpcloud', 'google', 'other')",
            name="ck_identity_providers_vendor",
        ),
    )
    op.create_index(
        "ix_identity_providers_organization_id", "identity_providers", ["organization_id"]
    )

    op.create_table(
        "identity_provider_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "identity_provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_hint", sa.String(8), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(updated=False),
    )
    op.create_index(
        "ix_identity_provider_tokens_identity_provider_id",
        "identity_provider_tokens",
        ["identity_provider_id"],
    )

    op.create_table(
        "identity_provider_users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "identity_provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("user_name", sa.String(320), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("attributes", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_user", sa.Boolean(), nullable=False),
        *_timestamps(updated=True),
        sa.UniqueConstraint(
            "identity_provider_id", "external_id", name="uq_identity_provider_users_external_id"
        ),
        sa.UniqueConstraint(
            "identity_provider_id", "user_name", name="uq_identity_provider_users_user_name"
        ),
    )

    op.create_table(
        "identity_provider_groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "identity_provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("attributes", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_timestamps(updated=True),
        sa.UniqueConstraint(
            "identity_provider_id", "external_id", name="uq_identity_provider_groups_external_id"
        ),
        sa.UniqueConstraint(
            "identity_provider_id",
            "display_name",
            name="uq_identity_provider_groups_display_name",
        ),
    )

    op.create_table(
        "identity_provider_group_members",
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity_provider_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "identity_provider_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity_provider_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_identity_provider_group_members_user",
        "identity_provider_group_members",
        ["identity_provider_user_id"],
    )

    op.create_table(
        "identity_provider_group_mappings",
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity_provider_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_by", sa.String(255), nullable=False),
        *_timestamps(updated=False),
    )

    op.create_table(
        "identity_provider_group_roles",
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey("identity_provider_group_mappings.group_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.add_column(
        "user_roles",
        sa.Column(
            "identity_provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "identity_providers.id",
                ondelete="SET NULL",
                name="fk_user_roles_identity_provider_id",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_constraint("fk_user_roles_identity_provider_id", "user_roles", type_="foreignkey")
    op.drop_column("user_roles", "identity_provider_id")
    op.drop_table("identity_provider_group_roles")
    op.drop_table("identity_provider_group_mappings")
    op.drop_index("ix_identity_provider_group_members_user", "identity_provider_group_members")
    op.drop_table("identity_provider_group_members")
    op.drop_table("identity_provider_groups")
    op.drop_table("identity_provider_users")
    op.drop_index(
        "ix_identity_provider_tokens_identity_provider_id", "identity_provider_tokens"
    )
    op.drop_table("identity_provider_tokens")
    op.drop_index("ix_identity_providers_organization_id", "identity_providers")
    op.drop_table("identity_providers")
    op.drop_index("uq_organization_domains_verified_domain", "organization_domains")
    op.drop_table("organization_domains")

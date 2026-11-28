"""Add inbound workload federation mappings for identities.

Revision ID: 20261128_federated_credentials
Revises: 20261127_integration_instances

The table is additive. Image rollback leaves it in place but removes the
token-exchange endpoint; a schema downgrade intentionally deletes mappings
and must not be used as a routine rollback.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261128_federated_credentials"
down_revision = "20261127_integration_instances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "federated_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("discovery_url", sa.Text(), nullable=True),
        sa.Column("audience", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=1024), nullable=False),
        sa.Column("claims", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("subject_token_type", sa.String(length=96), nullable=False),
        sa.Column("token_ttl_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_federated_credentials_identity_id", "federated_credentials", ["identity_id"])
    op.create_index("ix_federated_credentials_exchange", "federated_credentials", ["enabled", "subject_token_type"])
    # Federation access tokens carry role facts and a mapping revision. Any
    # direct mapping mutation therefore invalidates their epoch immediately.
    op.execute("""
        CREATE CONSTRAINT TRIGGER authz_epoch_ins AFTER INSERT ON federated_credentials
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION authz_epoch_bump_trigger()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON federated_credentials
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        WHEN (to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW)) EXECUTE FUNCTION authz_epoch_bump_trigger()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER authz_epoch_del AFTER DELETE ON federated_credentials
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION authz_epoch_bump_trigger()
    """)
    op.execute("""
        CREATE TRIGGER authz_epoch_truncate AFTER TRUNCATE ON federated_credentials
        FOR EACH STATEMENT EXECUTE FUNCTION authz_epoch_bump_trigger()
    """)


def downgrade() -> None:
    op.drop_table("federated_credentials")

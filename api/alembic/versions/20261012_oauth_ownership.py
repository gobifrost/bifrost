"""Remove OAuth credentials belonging to soft-deleted integrations.

Revision ID: 20261012_oauth_ownership
Revises: 20261010_agent_run_as_user

Standalone and MCP providers are legitimate consumers and remain intact.
Reject unexpected shared ownership before deleting anything, so an operator
can give the affected MCP servers their own OAuth configuration first.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261012_oauth_ownership"
down_revision = "20261010_agent_run_as_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    shared_servers = connection.execute(sa.text("""
        SELECT s.id FROM mcp_servers s JOIN oauth_providers p ON p.id = s.oauth_provider_id
        WHERE p.integration_id IS NOT NULL
    """)).scalars().all()
    shared_tokens = connection.execute(sa.text("""
        SELECT t.id FROM oauth_tokens t JOIN oauth_providers p ON p.id = t.provider_id
        WHERE p.integration_id IS NOT NULL AND (
            EXISTS (SELECT 1 FROM mcp_connections c WHERE c.service_oauth_token_id = t.id)
            OR EXISTS (SELECT 1 FROM user_mcp_credentials u WHERE u.oauth_token_id = t.id)
        )
    """)).scalars().all()
    if shared_servers or shared_tokens:
        raise RuntimeError(
            "Cannot clean integration OAuth credentials while MCP shares their ownership. "
            "Configure independent MCP OAuth providers and reconnect before upgrading. "
            f"MCP server IDs: {shared_servers}; shared token IDs: {shared_tokens}"
        )
    op.execute("""
        UPDATE integration_mappings SET oauth_token_id = NULL, updated_at = NOW()
        WHERE oauth_token_id IN (
            SELECT t.id FROM oauth_tokens t JOIN oauth_providers p ON p.id = t.provider_id
            JOIN integrations i ON i.id = p.integration_id WHERE i.is_deleted IS TRUE
        )
    """)
    op.execute("""
        DELETE FROM oauth_tokens t USING oauth_providers p, integrations i
        WHERE t.provider_id = p.id AND p.integration_id = i.id AND i.is_deleted IS TRUE
    """)
    op.execute("""
        DELETE FROM oauth_providers p USING integrations i
        WHERE p.integration_id = i.id AND i.is_deleted IS TRUE
    """)


def downgrade() -> None:
    """Deleted credentials cannot be restored; no schema changes to reverse."""

"""promote provider-org-stamped tokens of global connections back to global

Revision ID: 20260601_promote_global_tok
Revises: 20260526_drop_developer_contexts
Create Date: 2026-06-01

Data fix companion to the refresh_token scope fix (api/src/routers/
oauth_connections.py). Before that fix, the connections /refresh handler
stored the refreshed token under the *caller's* org instead of the
connection's own scope. A platform admin in the provider org refreshing a
GLOBAL connection (oauth_providers.organization_id IS NULL) created an
org-level token (user_id IS NULL) stamped with the provider org's id rather
than NULL. The SDK read cascade only falls back to organization_id IS NULL,
so every other org failed to resolve the token.

This migration heals rows already mis-stamped, scoped tightly so it cannot
touch a legitimately org-scoped token:

  - the token's provider is itself GLOBAL (oauth_providers.organization_id
    IS NULL) — never touches a per-org connection's provider;
  - the token is org-level (user_id IS NULL);
  - the token is stamped with the provider org specifically;
  - no true-global (NULL) token already exists for that provider — so we
    never collide with or shadow an existing correct row.

When a provider has BOTH a provider-org-stamped token and a real NULL token
(e.g. someone already cleared one manually and a refresh re-inserted the
Covi one), we delete the redundant provider-org row instead of promoting it,
to avoid two org-level rows for the same global provider.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260601_promote_global_tok"
down_revision: Union[str, Sequence[str]] = "20260526_drop_developer_contexts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop redundant provider-org-stamped org-level tokens when a correct
    #    global (NULL) token already exists for the same global provider.
    op.execute(
        """
        DELETE FROM oauth_tokens t
        USING organizations o, oauth_providers p
        WHERE t.organization_id = o.id
          AND o.is_provider = true
          AND t.user_id IS NULL
          AND p.id = t.provider_id
          AND p.organization_id IS NULL
          AND EXISTS (
              SELECT 1 FROM oauth_tokens g
              WHERE g.provider_id = t.provider_id
                AND g.organization_id IS NULL
                AND g.user_id IS NULL
          )
        """
    )

    # 2. Promote the remaining provider-org-stamped org-level tokens of global
    #    providers to global (organization_id = NULL).
    op.execute(
        """
        UPDATE oauth_tokens t
        SET organization_id = NULL
        FROM organizations o, oauth_providers p
        WHERE t.organization_id = o.id
          AND o.is_provider = true
          AND t.user_id IS NULL
          AND p.id = t.provider_id
          AND p.organization_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM oauth_tokens g
              WHERE g.provider_id = t.provider_id
                AND g.organization_id IS NULL
                AND g.user_id IS NULL
          )
        """
    )


def downgrade() -> None:
    # No restore. Re-stamping these tokens with the provider org would
    # reintroduce the cross-org resolution bug this migration fixes, and the
    # original (deleted) rows cannot be reconstructed. Intentionally a no-op.
    pass

"""Move canonical admin-bypass policies to execution authority.

Revision ID: 20261016_policy_authority
Revises: 20261012_oauth_ownership
Create Date: 2026-10-16 00:00:00.000000

Only the exact historical seed is migrated. Custom policies that use the
legacy user field remain role-based user expressions.
"""

from alembic import op


revision = "20261016_policy_authority"
down_revision = "20261012_oauth_ownership"
branch_labels = None
depends_on = None


_OLD_TABLE_POLICY = """
{
  "name": "admin_bypass",
  "description": "Platform admins bypass all checks. Edit or delete to enforce stricter audit.",
  "actions": ["read", "create", "update", "delete"],
  "when": {"user": "is_platform_admin"}
}
"""

_NEW_TABLE_POLICY = """
{
  "name": "admin_bypass",
  "description": "Platform admins bypass all checks. Edit or delete to enforce stricter audit.",
  "actions": ["read", "create", "update", "delete"],
  "when": {"authority": "platform_admin"}
}
"""

_OLD_FILE_POLICY = """
{
  "name": "admin_bypass",
  "description": "Platform admins bypass all file checks. Edit or delete to enforce stricter audit.",
  "actions": ["read", "write", "delete", "list"],
  "when": {"user": "is_platform_admin"}
}
"""

_NEW_FILE_POLICY = """
{
  "name": "admin_bypass",
  "description": "Platform admins bypass all file checks. Edit or delete to enforce stricter audit.",
  "actions": ["read", "write", "delete", "list"],
  "when": {"authority": "platform_admin"}
}
"""


def _replace_canonical_policy(column: str, old_policy: str, new_policy: str) -> str:
    """Replace an exact seeded node while preserving surrounding policy rules."""
    return f"""
        jsonb_set(
            {column},
            '{{policies}}',
            (
                SELECT jsonb_agg(
                    CASE WHEN policy = '{old_policy.strip()}'::jsonb
                        THEN '{new_policy.strip()}'::jsonb
                        ELSE policy
                    END
                    ORDER BY ordinal
                )
                FROM jsonb_array_elements({column} -> 'policies')
                    WITH ORDINALITY AS entries(policy, ordinal)
            )
        )
    """


def upgrade() -> None:
    op.execute(
        "UPDATE tables SET access = "
        f"{_replace_canonical_policy('access', _OLD_TABLE_POLICY, _NEW_TABLE_POLICY)} "
        f"WHERE access @> '{{\"policies\": [{_OLD_TABLE_POLICY.strip()}]}}'::jsonb"
    )
    op.execute(
        "UPDATE file_policies SET policies = "
        f"{_replace_canonical_policy('policies', _OLD_FILE_POLICY, _NEW_FILE_POLICY)} "
        f"WHERE policies @> '{{\"policies\": [{_OLD_FILE_POLICY.strip()}]}}'::jsonb"
    )
    op.execute(
        "UPDATE policy_rules SET body = jsonb_set(body, '{when}', "
        "'{\"authority\": \"platform_admin\"}'::jsonb) "
        "WHERE is_builtin IS TRUE AND organization_id IS NULL "
        "AND name = 'admin_bypass' "
        "AND domain IN ('table', 'file') "
        "AND body -> 'when' = '{\"user\": \"is_platform_admin\"}'::jsonb"
    )


def downgrade() -> None:
    # Canonical seed upgrades are intentionally one-way: restoring the old
    # expression would make Full workflow policy authority disappear.
    pass

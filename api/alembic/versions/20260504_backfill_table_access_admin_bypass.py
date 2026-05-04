"""backfill admin_bypass policy on tables created before access JSONB existed

Revision ID: 20260504_backfill_table_access
Revises: 20260502_drop_table_app
Create Date: 2026-05-04 00:00:00.000000

The 20260429 migration added the `access` JSONB column as nullable, no default.
Tables created before that migration ran kept `access = NULL`. The runtime
policy loader treats NULL as an empty `TablePolicies` — which is default deny
for everyone except platform admins... except we don't have admin bypass in
the empty set either, so it's default deny full stop.

Result: every table created before 2026-04-29 became unreadable for every
non-superuser the moment the table-policies feature shipped.

This migration backfills those rows with the same `admin_bypass`-only seed
that `make_seed_admin_bypass()` produces for new tables, so the runtime
behavior matches what the create handler does today.

Idempotent: only touches `access IS NULL`. Re-running is a no-op.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260504_backfill_table_access"
down_revision = "20260502_drop_table_app"
branch_labels = None
depends_on = None


SEED_ADMIN_BYPASS = """
{
  "policies": [
    {
      "name": "admin_bypass",
      "description": "Platform admins bypass all checks. Edit or delete to enforce stricter audit.",
      "actions": ["read", "create", "update", "delete"],
      "when": {"user": "is_platform_admin"}
    }
  ]
}
"""


def upgrade() -> None:
    op.execute(
        f"UPDATE tables "
        f"SET access = '{SEED_ADMIN_BYPASS.strip()}'::jsonb "
        f"WHERE access IS NULL"
    )


def downgrade() -> None:
    # Intentionally a no-op. We can't tell which rows were backfilled by this
    # migration vs. legitimately set to the same seed by hand. Reverting blind
    # would risk wiping a user's deliberate admin_bypass-only policy.
    pass

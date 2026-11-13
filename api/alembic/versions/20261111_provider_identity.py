"""Enforce the existing single, canonical provider organization identity."""

from alembic import op
import sqlalchemy as sa

revision = "20261111_provider_identity"
down_revision = "20261111_integration_notes"
branch_labels = None
depends_on = None

PROVIDER_ID = "00000000-0000-0000-0000-000000000002"
CONSTRAINT = "ck_organizations_provider_identity"


def upgrade() -> None:
    connection = op.get_bind()
    # Keep validation and constraint installation on the same stable snapshot.
    connection.execute(sa.text("LOCK TABLE organizations IN ACCESS EXCLUSIVE MODE"))
    invalid = connection.execute(sa.text(
        "SELECT id FROM organizations "
        f"WHERE is_provider IS DISTINCT FROM (id = '{PROVIDER_ID}'::uuid) "
        "ORDER BY id"
    )).scalars().all()
    provider_exists = connection.execute(sa.text(
        f"SELECT EXISTS (SELECT 1 FROM organizations WHERE id = '{PROVIDER_ID}'::uuid)"
    )).scalar_one()
    if invalid or not provider_exists:
        raise RuntimeError(
            "Provider organization identity is inconsistent. "
            f"Expected provider {PROVIDER_ID}; inconsistent organization IDs: "
            f"{', '.join(str(value) for value in invalid) or 'none'}; "
            f"canonical provider exists: {provider_exists}. "
            "Reconcile these rows with the existing provider identity before upgrading; "
            "no organizations or permissions have been changed."
        )
    op.create_check_constraint(
        CONSTRAINT, "organizations",
        f"is_provider = (id = '{PROVIDER_ID}'::uuid)",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "organizations", type_="check")

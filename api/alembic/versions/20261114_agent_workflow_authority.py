"""Retain the permission source of workflow-launched agent runs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261114_agent_wf_authority"
down_revision = "20261110_form_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("permission_workflow_id", postgresql.UUID(as_uuid=True), nullable=True))
    # Workflow deletion retains the row and grants. Revoke tokens holding
    # those grants when the source workflow is deactivated.
    op.execute("DROP TRIGGER authz_epoch_upd ON workflows")
    op.execute("""
        CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON workflows
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            WHEN (OLD.id IS DISTINCT FROM NEW.id
                OR OLD.solution_id IS DISTINCT FROM NEW.solution_id
                OR OLD.permission_mode IS DISTINCT FROM NEW.permission_mode
                OR OLD.is_active IS DISTINCT FROM NEW.is_active)
            EXECUTE FUNCTION authz_epoch_bump_trigger()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER authz_epoch_upd ON workflows")
    op.execute("""
        CREATE CONSTRAINT TRIGGER authz_epoch_upd AFTER UPDATE ON workflows
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
            WHEN (OLD.id IS DISTINCT FROM NEW.id
                OR OLD.solution_id IS DISTINCT FROM NEW.solution_id
                OR OLD.permission_mode IS DISTINCT FROM NEW.permission_mode)
            EXECUTE FUNCTION authz_epoch_bump_trigger()
    """)
    op.drop_column("agent_runs", "permission_workflow_id")

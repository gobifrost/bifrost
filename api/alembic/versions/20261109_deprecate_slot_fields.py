"""Stop application dependence on legacy slot columns without dropping them.

Older images still read/write both columns during a rolling upgrade. Keep
them, default worker slots for new inserts, and bridge old scheduler slot
writes with the explicit process-role flag. A later, separately gated
migration can remove the retained columns and this database bridge together.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261109_deprecate_slot_fields"
down_revision: str | None = "20261108_workspace_field_utc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("execution_workers", "slots", server_default="0")
    op.add_column(
        "scheduler_replicas", sa.Column("runs_platform_jobs", sa.Boolean(), nullable=True)
    )
    op.execute(
        "UPDATE scheduler_replicas SET runs_platform_jobs = (job_slots > 0)"
    )
    op.execute("""
        CREATE FUNCTION sync_scheduler_replica_role() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.runs_platform_jobs IS NULL THEN
                    NEW.runs_platform_jobs := NEW.job_slots > 0;
                ELSE
                    NEW.job_slots := CASE WHEN NEW.runs_platform_jobs THEN 1 ELSE 0 END;
                END IF;
            ELSIF NEW.runs_platform_jobs IS DISTINCT FROM OLD.runs_platform_jobs THEN
                NEW.job_slots := CASE WHEN NEW.runs_platform_jobs THEN 1 ELSE 0 END;
            ELSIF NEW.job_slots IS DISTINCT FROM OLD.job_slots THEN
                NEW.runs_platform_jobs := NEW.job_slots > 0;
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER scheduler_replica_role_compatibility
        BEFORE INSERT OR UPDATE OF job_slots, runs_platform_jobs ON scheduler_replicas
        FOR EACH ROW EXECUTE FUNCTION sync_scheduler_replica_role()
    """)
    op.alter_column("scheduler_replicas", "runs_platform_jobs", nullable=False)


def downgrade() -> None:
    op.execute("DROP TRIGGER scheduler_replica_role_compatibility ON scheduler_replicas")
    op.execute("DROP FUNCTION sync_scheduler_replica_role()")
    op.drop_column("scheduler_replicas", "runs_platform_jobs")
    op.alter_column("execution_workers", "slots", server_default=None)

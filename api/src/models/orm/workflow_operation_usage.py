"""
WorkflowOperationUsage ORM model.

A cheap daily counter of which catalog operations (reads included) a
workflow's engine-token-authenticated SDK calls actually touched. Populated
by src/core/app_wiring.py from a Redis buffer flushed every 15 minutes (see
src/jobs/schedulers/workflow_operation_usage_flush.py). Attribution only —
informs a later "what permissions does this workflow actually need" pass;
never read for authorization.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import BigInteger, Date, ForeignKey, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkflowOperationUsage(Base):
    """One workflow's call count for one catalog operation on one UTC day."""

    __tablename__ = "workflow_operation_usage"

    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE")
    )
    # Catalog operation id when the route has one; otherwise
    # "<METHOD> <route path template>" (see the flush job for the exact rule).
    operation_key: Mapped[str] = mapped_column(String(256))
    day: Mapped[date] = mapped_column(Date)
    count: Mapped[int] = mapped_column(BigInteger, default=0)

    __table_args__ = (
        PrimaryKeyConstraint("workflow_id", "operation_key", "day"),
    )

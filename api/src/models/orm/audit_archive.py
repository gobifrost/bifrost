"""
AuditArchiveSegment ORM model.

Catalog of audit events archived to object storage: one row per
(organization, day) segment object.
"""

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


# Identity-adjacent catalog (belongs with AuditLog) — not name-cascade resolved.
# See api/src/repositories/README.md. organization_id and platform_job_id carry
# no foreign keys: segments must outlive the org and the job that wrote them.
class AuditArchiveSegment(Base):
    """One archived day of audit events for one organization (or global)."""

    __tablename__ = "audit_archive_segments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(default=None)
    day: Mapped[date] = mapped_column(Date)
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    row_count: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    first_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_id: Mapped[UUID]
    last_id: Mapped[UUID]
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    platform_job_id: Mapped[UUID | None] = mapped_column(default=None)

    __table_args__ = (
        Index("ix_audit_archive_segments_org_day", "organization_id", "day"),
        Index("ix_audit_archive_segments_last_created", "last_created_at"),
    )

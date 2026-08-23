"""Environment-local learned memory requirements for platform-job workloads."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class PlatformJobMemoryProfile(Base):
    """One learned memory requirement keyed by a stable workload fingerprint."""

    __tablename__ = "platform_job_memory_profiles"

    profile_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    memory_required_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    observed_high_water_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
